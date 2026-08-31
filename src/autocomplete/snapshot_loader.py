from __future__ import annotations

import hashlib
import re
import struct
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

VERSIONS = (1, 1, 1)
GRAM_SIZES = (1, 2, 3)
MAX_PAYLOAD = 8 * 1024 * 1024
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


def load_snapshot(snapshot_path: Path) -> tuple[dict[int, SentenceRecord], SearchIndex]:
    root = Path(snapshot_path)
    try:
        raw = (root / "manifest.binpb").read_bytes()
    except OSError as exc:
        raise SnapshotError(f"cannot read manifest: {exc}") from exc
    if not raw or len(raw) > MAX_PAYLOAD:
        raise SnapshotError("invalid manifest size")
    manifest = _parse(SnapshotManifestProto(), raw, "manifest")
    _validate_manifest(manifest)

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
            if not value.sentence_id or value.sentence_id in records:
                raise SnapshotError("invalid or duplicate record identifier")
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

    postings: dict[tuple[int, str], PostingArray] = {}
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
                if sentence_id <= previous or sentence_id > 0xFFFFFFFF:
                    raise SnapshotError("posting IDs are not strictly increasing")
                if sentence_id not in records:
                    raise SnapshotError("posting references unknown sentence ID")
                ids.append(sentence_id)
                previous = sentence_id
            postings[key] = ids
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
    return records, SearchIndex._from_validated_postings(
        postings, PostingArray(records)
    )
