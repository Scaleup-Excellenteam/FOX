from __future__ import annotations

import hashlib
import re
import struct
from bisect import bisect_left
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from google.protobuf.message import DecodeError, Message

from .generated.autocomplete_snapshot_pb2 import (
    GramPostingProto,
    PrecomputedGramTopKProto,
    SentenceRecordProto,
    SnapshotManifestProto,
)
from .index import FrozenPosting, PostingArray, PrecomputedExactTopK, SearchIndex
from .models import SentenceRecord

VERSIONS = (1, 1, 2)
SUPPORTED_VERSIONS = {(1, 1, 1), VERSIONS}
GRAM_SIZES = (1, 2, 3)
MAX_PAYLOAD = 8 * 1024 * 1024
MAX_SENTENCE_ID = 0xFFFFFFFF
MessageType = TypeVar("MessageType", bound=Message)
DIGEST_BUFFER_BYTES = 64 * 1024
DIGEST_IDS_PER_CHUNK = DIGEST_BUFFER_BYTES // 8
U32_BE = struct.Struct(">I")
U64_BE = struct.Struct(">Q")


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


class _BufferedDigest:
    def __init__(self, digest: Any, capacity: int = DIGEST_BUFFER_BYTES) -> None:
        self._digest = digest
        self._capacity = capacity
        self._buffer = bytearray()

    def update(self, value: bytes) -> None:
        if len(value) <= self._capacity - len(self._buffer):
            self._buffer.extend(value)
            if len(self._buffer) == self._capacity:
                self.flush()
            return
        self.flush()
        for offset in range(0, len(value), self._capacity):
            chunk = value[offset : offset + self._capacity]
            if len(chunk) == self._capacity:
                self._digest.update(chunk)
            else:
                self._buffer.extend(chunk)

    def update_u32(self, value: int) -> None:
        self.update(U32_BE.pack(value))

    def update_u64(self, value: int) -> None:
        self.update(U64_BE.pack(value))

    def update_u64s(self, values: Any) -> None:
        for offset in range(0, len(values), DIGEST_IDS_PER_CHUNK):
            chunk = values[offset : offset + DIGEST_IDS_PER_CHUNK]
            self.update(struct.pack(f">{len(chunk)}Q", *chunk))

    def update_string(self, value: str) -> None:
        encoded = value.encode()
        self.update_u64(len(encoded))
        self.update(encoded)

    def flush(self) -> None:
        if self._buffer:
            self._digest.update(self._buffer)
            self._buffer.clear()


def _validate_manifest(manifest: SnapshotManifestProto) -> None:
    actual = (
        manifest.schema_version,
        manifest.normalization_version,
        manifest.index_strategy_version,
    )
    if actual not in SUPPORTED_VERSIONS:
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
    names = [*manifest.record_files, *manifest.index_files, *manifest.topk_files]
    if not manifest.record_files or not manifest.index_files:
        raise SnapshotError("manifest must list record and index files")
    if len(names) != len(set(names)):
        raise SnapshotError("duplicate snapshot file name")
    for name in names:
        _safe_file(Path(), name)
    if manifest.index_strategy_version == 1:
        if (
            manifest.topk_files
            or manifest.topk_entry_count
            or manifest.topk_digest_sha256
        ):
            raise SnapshotError("V1 manifest must declare Top-K data absent")
    elif not manifest.topk_files or not re.fullmatch(
        r"[0-9a-f]{64}", manifest.topk_digest_sha256
    ):
        raise SnapshotError("V2 manifest requires complete Top-K metadata")


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


def load_snapshot(snapshot_path: Path) -> tuple[dict[int, SentenceRecord], SearchIndex]:
    root = Path(snapshot_path)
    manifest = load_snapshot_manifest(root)

    exact_top_k: dict[tuple[int, str], PrecomputedExactTopK] = {}
    selected_topk_ids: set[int] = set()
    if manifest.index_strategy_version == 2:
        topk_digest = hashlib.sha256()
        buffered_topk = _BufferedDigest(topk_digest)
        last_topk_key: tuple[int, bytes] | None = None
        for name in manifest.topk_files:
            for payload in _frames(_safe_file(root, name)):
                value = _parse(PrecomputedGramTopKProto(), payload, "Top-K entry")
                key = (value.gram_size, value.gram)
                order_key = (value.gram_size, value.gram.encode())
                ids = tuple(value.top_sentence_ids)
                expected_count = min(5, value.exact_occurrence_count)
                if (
                    value.gram_size not in GRAM_SIZES
                    or len(value.gram) != value.gram_size
                    or last_topk_key is not None
                    and order_key <= last_topk_key
                    or not value.exact_occurrence_count
                    or len(ids) != expected_count
                    or len(ids) != len(set(ids))
                    or any(
                        not 0 < sentence_id <= MAX_SENTENCE_ID for sentence_id in ids
                    )
                ):
                    raise SnapshotError("invalid precomputed Top-K entry")
                exact_top_k[key] = PrecomputedExactTopK(
                    value.exact_occurrence_count,
                    FrozenPosting(ids),
                )
                selected_topk_ids.update(ids)
                last_topk_key = order_key
                buffered_topk.update_u32(value.gram_size)
                buffered_topk.update_string(value.gram)
                buffered_topk.update_u64(value.exact_occurrence_count)
                buffered_topk.update_u64(len(ids))
                buffered_topk.update_u64s(ids)
        buffered_topk.flush()
        if len(exact_top_k) != manifest.topk_entry_count:
            raise SnapshotError("Top-K entry count mismatch")
        if topk_digest.hexdigest() != manifest.topk_digest_sha256:
            raise SnapshotError("Top-K digest mismatch")

    records: dict[int, SentenceRecord] = {}
    selected_ranking_keys: dict[int, tuple[str, str, int, int]] = {}
    corpus = hashlib.sha256()
    buffered_corpus = _BufferedDigest(corpus)
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
            if value.sentence_id in selected_topk_ids:
                selected_ranking_keys[value.sentence_id] = (
                    value.original,
                    value.source_path,
                    value.line_number,
                    value.sentence_id,
                )
            buffered_corpus.update_u64(value.sentence_id)
            buffered_corpus.update_string(value.source_path)
            buffered_corpus.update_u64(value.line_number)
            buffered_corpus.update_string(value.original)
            buffered_corpus.update_string(value.normalized)
    if list(records) != sorted(records):
        raise SnapshotError("record identifiers are not ordered")
    if len(records) != manifest.searchable_record_count:
        raise SnapshotError("record count mismatch")
    buffered_corpus.flush()
    if corpus.hexdigest() != manifest.corpus_digest_sha256:
        raise SnapshotError("corpus digest mismatch")

    postings: dict[tuple[int, str], PostingArray] = {}
    last_key: tuple[int, bytes] | None = None
    index_digest = hashlib.sha256()
    buffered_index = _BufferedDigest(index_digest)
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
            buffered_index.update_u32(value.gram_size)
            buffered_index.update_string(value.gram)
            buffered_index.update_u64(len(value.sentence_ids))
            previous = 0
            for sentence_id in value.sentence_ids:
                if sentence_id <= previous or sentence_id > MAX_SENTENCE_ID:
                    raise SnapshotError("posting IDs are not strictly increasing")
                if sentence_id not in records:
                    raise SnapshotError("posting references unknown sentence ID")
                ids.append(sentence_id)
                previous = sentence_id
            buffered_index.update_u64s(value.sentence_ids)
            postings[key] = ids
            last_key = order_key
    if len(postings) != manifest.posting_count:
        raise SnapshotError("posting count mismatch")
    buffered_index.flush()
    if index_digest.hexdigest() != manifest.index_digest_sha256:
        raise SnapshotError("index digest mismatch")

    if manifest.index_strategy_version == 2:
        if set(exact_top_k) != set(postings):
            raise SnapshotError("Top-K entries do not cover postings")
        if len(selected_ranking_keys) != len(selected_topk_ids):
            raise SnapshotError("precomputed Top-K references unknown sentence ID")
        for key, precomputed in exact_top_k.items():
            posting = postings[key]
            ids = tuple(precomputed.sentence_ids)
            ids_are_members = all(
                (position := bisect_left(posting, sentence_id)) < len(posting)
                and posting[position] == sentence_id
                for sentence_id in ids
            )
            if (
                precomputed.exact_occurrence_count != len(posting)
                or not ids_are_members
            ):
                raise SnapshotError("invalid precomputed Top-K entry")
            expected_ids = tuple(sorted(ids, key=selected_ranking_keys.__getitem__))
            if ids != expected_ids:
                raise SnapshotError("invalid precomputed Top-K ordering")

    identity = (
        f"corpus_digest_sha256={manifest.corpus_digest_sha256}\n"
        f"index_digest_sha256={manifest.index_digest_sha256}\n"
    )
    if manifest.index_strategy_version == 2:
        identity += f"topk_digest_sha256={manifest.topk_digest_sha256}\n"
    identity += (
        "schema_version=1\nnormalization_version=1\n"
        f"index_strategy_version={manifest.index_strategy_version}\n"
        "gram_sizes=1,2,3\n"
    )
    if hashlib.sha256(identity.encode()).hexdigest() != manifest.snapshot_id:
        raise SnapshotError("snapshot ID mismatch")
    return records, SearchIndex._from_validated_postings(
        postings, PostingArray(records), exact_top_k
    )
