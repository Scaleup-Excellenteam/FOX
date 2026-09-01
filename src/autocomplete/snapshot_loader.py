from __future__ import annotations

import hashlib
import logging
import re
import struct
import time
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from google.protobuf.message import DecodeError, Message

from .generated.autocomplete_snapshot_pb2 import (
    GramPostingProto,
    SentenceRecordProto,
    SnapshotManifestProto,
)
from .index import PostingArray, SearchIndex
from .models import SentenceRecord
from .observability import event, get_config, human_bytes, safe_name, safe_reason

VERSIONS = (1, 1, 1)
GRAM_SIZES = (1, 2, 3)
MAX_PAYLOAD = 8 * 1024 * 1024
MAX_SENTENCE_ID = 0xFFFFFFFF
MessageType = TypeVar("MessageType", bound=Message)


class SnapshotError(ValueError):
    """The snapshot is corrupt, incomplete, or incompatible."""


def _safe_file(root: Path, name: str) -> Path:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or len(path.parts) != 1 or path.name != name:
        raise SnapshotError(f"unsafe snapshot file name: {name!r}")
    return root / name


def _frames(path: Path) -> Iterator[bytes]:
    try:
        with path.open("rb") as stream:
            while prefix := stream.read(4):
                if len(prefix) != 4:
                    raise SnapshotError(f"truncated frame length: {path.name}")
                length = struct.unpack(">I", prefix)[0]
                if not 0 < length <= MAX_PAYLOAD:
                    raise SnapshotError(f"invalid frame length: {path.name}")
                payload = stream.read(length)
                if len(payload) != length:
                    raise SnapshotError(f"truncated frame payload: {path.name}")
                yield payload
    except OSError as exc:
        raise SnapshotError(f"cannot read snapshot file {path.name}: {exc}") from exc


def _parse(  # noqa: UP047
    message: MessageType, payload: bytes, label: str
) -> MessageType:
    try:
        message.ParseFromString(payload)
    except DecodeError as exc:
        raise SnapshotError(f"corrupt {label} protobuf") from exc
    return message


def _update_string(digest: Any, value: str) -> None:
    encoded = value.encode()
    digest.update(struct.pack(">Q", len(encoded)))
    digest.update(encoded)


def _validate_manifest(manifest: SnapshotManifestProto) -> None:
    actual = (
        manifest.schema_version,
        manifest.normalization_version,
        manifest.index_strategy_version,
    )
    if actual != VERSIONS:
        raise SnapshotError(f"unsupported snapshot versions: {actual}")
    if tuple(manifest.gram_sizes) != GRAM_SIZES:
        raise SnapshotError("unsupported gram sizes")
    for label, value in (
        ("corpus digest", manifest.corpus_digest_sha256),
        ("index digest", manifest.index_digest_sha256),
        ("snapshot ID", manifest.snapshot_id),
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise SnapshotError(f"invalid {label}")
    if not manifest.created_at_utc:
        raise SnapshotError("missing snapshot creation time")
    names = [*manifest.record_files, *manifest.index_files]
    if not manifest.record_files or not manifest.index_files:
        raise SnapshotError("manifest must list record and index files")
    if len(names) != len(set(names)):
        raise SnapshotError("duplicate snapshot file name")
    for name in names:
        _safe_file(Path(), name)


def load_snapshot_manifest(snapshot_path: Path) -> SnapshotManifestProto:
    """Read and validate the compatibility and shape of a snapshot manifest."""
    root = Path(snapshot_path)
    try:
        raw = (root / "manifest.binpb").read_bytes()
    except OSError as exc:
        raise SnapshotError(f"cannot read manifest: {exc}") from exc
    if not raw or len(raw) > MAX_PAYLOAD:
        raise SnapshotError("invalid manifest size")
    manifest = _parse(SnapshotManifestProto(), raw, "manifest")
    _validate_manifest(manifest)
    return manifest


def _load_snapshot_impl(
    snapshot_path: Path, timings: dict[str, int] | None
) -> tuple[dict[int, SentenceRecord], SearchIndex, SnapshotManifestProto]:
    root = Path(snapshot_path)
    started = time.perf_counter_ns() if timings is not None else 0
    manifest = load_snapshot_manifest(root)
    if timings is not None:
        timings["manifest_ns"] = time.perf_counter_ns() - started

    started = time.perf_counter_ns() if timings is not None else 0
    records: dict[int, SentenceRecord] = {}
    corpus = hashlib.sha256()
    for name in manifest.record_files:
        for payload in _frames(_safe_file(root, name)):
            value = _parse(SentenceRecordProto(), payload, "record")
            path = PurePosixPath(value.source_path)
            if (
                not value.source_path
                or path.is_absolute()
                or ".." in path.parts
                or path.as_posix() != value.source_path
            ):
                raise SnapshotError("invalid source path")
            if not 0 < value.sentence_id <= MAX_SENTENCE_ID:
                raise SnapshotError("record identifier is outside uint32 range")
            if value.sentence_id in records:
                raise SnapshotError("duplicate record identifier")
            if not value.line_number:
                raise SnapshotError("invalid record line number")
            records[value.sentence_id] = SentenceRecord(
                value.sentence_id,
                value.original,
                value.normalized,
                value.source_path,
                value.line_number,
            )
            corpus.update(struct.pack(">Q", value.sentence_id))
            _update_string(corpus, value.source_path)
            corpus.update(struct.pack(">Q", value.line_number))
            _update_string(corpus, value.original)
            _update_string(corpus, value.normalized)
    if list(records) != sorted(records):
        raise SnapshotError("record identifiers are not ordered")
    if len(records) != manifest.searchable_record_count:
        raise SnapshotError("record count mismatch")
    if corpus.hexdigest() != manifest.corpus_digest_sha256:
        raise SnapshotError("corpus digest mismatch")
    if timings is not None:
        timings["records_ns"] = time.perf_counter_ns() - started

    started = time.perf_counter_ns() if timings is not None else 0
    postings: dict[tuple[int, str], PostingArray] = {}
    total_posting_ids = 0
    last_key: tuple[int, bytes] | None = None
    index_digest = hashlib.sha256()
    for name in manifest.index_files:
        for payload in _frames(_safe_file(root, name)):
            value = _parse(GramPostingProto(), payload, "posting")
            key = (value.gram_size, value.gram)
            order_key = (value.gram_size, value.gram.encode())
            if (
                value.gram_size not in GRAM_SIZES
                or len(value.gram) != value.gram_size
                or not value.sentence_ids
            ):
                raise SnapshotError("invalid posting list")
            if last_key is not None and order_key <= last_key:
                raise SnapshotError("posting keys are not strictly ordered")
            ids = PostingArray()
            previous = 0
            for sentence_id in value.sentence_ids:
                if sentence_id <= previous or sentence_id > MAX_SENTENCE_ID:
                    raise SnapshotError("posting IDs are not strictly increasing")
                if sentence_id not in records:
                    raise SnapshotError("posting references unknown sentence ID")
                ids.append(sentence_id)
                previous = sentence_id
            postings[key] = ids
            if timings is not None:
                total_posting_ids += len(ids)
            last_key = order_key
            index_digest.update(struct.pack(">I", value.gram_size))
            _update_string(index_digest, value.gram)
            index_digest.update(struct.pack(">Q", len(ids)))
            for sentence_id in ids:
                index_digest.update(struct.pack(">Q", sentence_id))
    if len(postings) != manifest.posting_count:
        raise SnapshotError("posting count mismatch")
    if index_digest.hexdigest() != manifest.index_digest_sha256:
        raise SnapshotError("index digest mismatch")
    identity = (
        f"corpus_digest_sha256={manifest.corpus_digest_sha256}\n"
        f"index_digest_sha256={manifest.index_digest_sha256}\n"
        "schema_version=1\nnormalization_version=1\n"
        "index_strategy_version=1\ngram_sizes=1,2,3\n"
    )
    if hashlib.sha256(identity.encode()).hexdigest() != manifest.snapshot_id:
        raise SnapshotError("snapshot ID mismatch")
    if timings is not None:
        timings["postings_ns"] = time.perf_counter_ns() - started
    started = time.perf_counter_ns() if timings is not None else 0
    index = SearchIndex._from_validated_postings(postings, PostingArray(records))
    if timings is not None:
        timings["index_ns"] = time.perf_counter_ns() - started
        timings["total_posting_ids"] = total_posting_ids
        timings["record_count"] = len(records)
        timings["posting_count"] = len(postings)
    return records, index, manifest


def _snapshot_size_fields(
    root: Path, manifest: SnapshotManifestProto
) -> dict[str, int | float | bool | str]:
    """Best-effort size metadata derived from the authoritative file lists."""

    try:
        records_size = sum(
            _safe_file(root, name).stat().st_size for name in manifest.record_files
        )
        index_size = sum(
            _safe_file(root, name).stat().st_size for name in manifest.index_files
        )
        manifest_size = (root / "manifest.binpb").stat().st_size
        total_size = records_size + index_size + manifest_size
        return {
            "size_metrics_available": True,
            "record_file_count": len(manifest.record_files),
            "index_file_count": len(manifest.index_files),
            "records_size_bytes": records_size,
            "index_size_bytes": index_size,
            "manifest_size_bytes": manifest_size,
            "total_snapshot_size_bytes": total_size,
            "total_snapshot_size_human": human_bytes(total_size),
        }
    except Exception:
        return {"size_metrics_available": False}


def load_snapshot(snapshot_path: Path) -> tuple[dict[int, SentenceRecord], SearchIndex]:
    root = Path(snapshot_path)
    config = get_config()
    if not config.enables(logging.CRITICAL):
        records, index, _ = _load_snapshot_impl(root, None)
        return records, index

    total_started = time.perf_counter_ns()
    timings: dict[str, int] = {}
    event(
        "runtime",
        "snapshot.load_started",
        snapshot_location=safe_name(root),
    )
    try:
        records, index, manifest = _load_snapshot_impl(root, timings)
        load_compute_ns = time.perf_counter_ns() - total_started
        measured_ns = sum(
            timings.get(key, 0)
            for key in ("manifest_ns", "records_ns", "postings_ns", "index_ns")
        )
        try:
            size_fields = _snapshot_size_fields(root, manifest)
        except Exception:
            size_fields = {"size_metrics_available": False}
        event(
            "runtime",
            "snapshot.ready",
            snapshot_id=manifest.snapshot_id,
            format_version=manifest.schema_version,
            normalization_version=manifest.normalization_version,
            index_version=manifest.index_strategy_version,
            expected_record_count=manifest.searchable_record_count,
            expected_posting_list_count=manifest.posting_count,
            manifest_load_and_validation_ms=timings["manifest_ns"] / 1_000_000,
            records_load_and_validation_ms=timings["records_ns"] / 1_000_000,
            postings_load_and_validation_ms=timings["postings_ns"] / 1_000_000,
            search_index_publication_ms=timings["index_ns"] / 1_000_000,
            load_unaccounted_ms=max(
                0,
                load_compute_ns - measured_ns,
            )
            / 1_000_000,
            load_compute_ms=load_compute_ns / 1_000_000,
            loaded_record_count=timings["record_count"],
            loaded_posting_list_count=timings["posting_count"],
            total_posting_ids=timings["total_posting_ids"],
            **size_fields,
            status="ready",
        )
        return records, index
    except Exception as error:
        event(
            "runtime",
            "snapshot.load_failed",
            logging.ERROR,
            failed_stage="load_and_integrity_validation",
            error_category=type(error).__name__,
            reason_code=safe_reason(error),
            load_compute_ms=(time.perf_counter_ns() - total_started) / 1_000_000,
            search_index_published=False,
            status="failed",
        )
        raise
