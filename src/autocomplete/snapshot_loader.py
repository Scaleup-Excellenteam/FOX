from __future__ import annotations

import hashlib
import re
import resource
import struct
import sys
import time
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from google.protobuf.message import DecodeError, Message

from .autocomplete_snapshot_pb2 import PostingChunk, ShardMetadata, SnapshotManifest
from .autocomplete_snapshot_pb2 import SentenceRecord as ProtoSentenceRecord
from .index import PostingArray, SearchIndex
from .models import SentenceRecord

SCHEMA_VERSION = 1
FRAMING_VERSION = 1
NORMALIZATION_VERSION = 1
NORMALIZATION_ALGORITHM = "ascii-v1"
INDEX_VERSION = 1
GRAM_SIZES = (1, 2, 3)
MIN_SELECTIVE_QUERY_CODEPOINTS = 2
MAX_PAYLOAD = 8 * 1024 * 1024
SHARD_READ_CHUNK_BYTES = 1024 * 1024
MAGIC = b"FOXSNAP1"
MessageType = TypeVar("MessageType", bound=Message)


class SnapshotError(ValueError):
    """Raised when a snapshot is corrupt, incomplete, or incompatible."""


def _crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def _safe_file(root: Path, name: str) -> Path:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or len(path.parts) != 1 or path.name != name:
        raise SnapshotError(f"unsafe shard name: {name!r}")
    return root / name


def _frames(path: Path, expected_kind: int, expected: ShardMetadata) -> Iterator[bytes]:
    if expected.framed_size_bytes < 16 or expected.frame_count == 0 or len(expected.sha256) != 32:
        raise SnapshotError(f"invalid shard metadata: {path.name}")
    try:
        with path.open("rb") as stream:
            shard_digest = hashlib.sha256()
            size = 0
            while block := stream.read(SHARD_READ_CHUNK_BYTES):
                size += len(block)
                shard_digest.update(block)
            if size != expected.framed_size_bytes or shard_digest.digest() != expected.sha256:
                raise SnapshotError(f"size/checksum mismatch: {path.name}")

            stream.seek(0)
            header = stream.read(16)
            if header[:8] != MAGIC:
                raise SnapshotError(f"invalid shard header: {path.name}")
            version, kind = struct.unpack_from("<II", header, 8)
            if version != FRAMING_VERSION or kind != expected_kind:
                raise SnapshotError(f"incompatible shard header: {path.name}")

            offset = 16
            count = 0
            while offset < size:
                if offset + 4 > size:
                    raise SnapshotError(f"truncated frame length: {path.name}")
                length_bytes = stream.read(4)
                length = struct.unpack("<I", length_bytes)[0]
                offset += 4
                if length > MAX_PAYLOAD or offset + length + 4 > size:
                    raise SnapshotError(f"invalid frame length: {path.name}")
                payload = stream.read(length)
                offset += length
                checksum = struct.unpack("<I", stream.read(4))[0]
                offset += 4
                if checksum != _crc32c(payload):
                    raise SnapshotError(f"CRC32C mismatch: {path.name}")
                count += 1
                yield payload
            if count != expected.frame_count:
                raise SnapshotError(f"frame count mismatch: {path.name}")
    except OSError as exc:
        raise SnapshotError(f"cannot read shard {path.name}: {exc}") from exc


def _parse(message: MessageType, payload: bytes, label: str) -> MessageType:
    try:
        message.ParseFromString(payload)
    except DecodeError as exc:
        raise SnapshotError(f"corrupt {label} protobuf") from exc
    return message


def _validate_manifest(manifest: SnapshotManifest) -> None:
    checks = (
        (manifest.schema_version, SCHEMA_VERSION, "schema"),
        (manifest.framing_version, FRAMING_VERSION, "framing"),
        (manifest.normalization.version, NORMALIZATION_VERSION, "normalization"),
        (manifest.ngram_index.version, INDEX_VERSION, "index strategy"),
    )
    for actual, expected, label in checks:
        if actual != expected:
            raise SnapshotError(f"unsupported {label} version: {actual}")
    if manifest.normalization.algorithm != NORMALIZATION_ALGORITHM:
        raise SnapshotError("unsupported normalization algorithm")
    if tuple(manifest.ngram_index.gram_codepoints) != GRAM_SIZES:
        raise SnapshotError("unsupported gram sizes")
    if manifest.ngram_index.min_selective_query_codepoints != MIN_SELECTIVE_QUERY_CODEPOINTS:
        raise SnapshotError("unsupported short-query policy")
    if manifest.ngram_index.shard_target_bytes < 1024:
        raise SnapshotError("invalid shard target size")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest.snapshot_id):
        raise SnapshotError("invalid snapshot ID")
    if (
        len(manifest.corpus_digest) != 32
        or len(manifest.index_digest) != 32
        or not manifest.created_at_utc
    ):
        raise SnapshotError("invalid snapshot identity metadata")
    names = [shard.file_name for shard in (*manifest.record_shards, *manifest.index_shards)]
    if len(names) != len(set(names)):
        raise SnapshotError("duplicate shard file name")


def _update_identity_string(digest: Any, value: str) -> None:
    encoded = value.encode()
    digest.update(struct.pack("<Q", len(encoded)))
    digest.update(encoded)


def _validate_record_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise SnapshotError("invalid source path")


def load_snapshot(snapshot_path: Path) -> tuple[dict[int, SentenceRecord], SearchIndex]:
    """Validate and load a complete immutable snapshot into runtime structures."""
    load_started = time.perf_counter()
    root = Path(snapshot_path)
    try:
        raw = (root / "manifest.binpb").read_bytes()
    except OSError as exc:
        raise SnapshotError(f"cannot read manifest: {exc}") from exc
    if not raw or len(raw) > MAX_PAYLOAD:
        raise SnapshotError("invalid manifest size")
    manifest = _parse(SnapshotManifest(), raw, "manifest")
    _validate_manifest(manifest)

    records_started = time.perf_counter()
    records: dict[int, SentenceRecord] = {}
    corpus_identity = hashlib.sha256()
    for shard in manifest.record_shards:
        if shard.kind != 1:
            raise SnapshotError("record shard has wrong kind")
        for payload in _frames(_safe_file(root, shard.file_name), 1, shard):
            record_message = _parse(ProtoSentenceRecord(), payload, "record")
            _validate_record_path(record_message.source_relative_path)
            if (
                record_message.sentence_id != len(records) + 1
                or record_message.source_line_number == 0
            ):
                raise SnapshotError("invalid or duplicate record identifier")
            records[record_message.sentence_id] = SentenceRecord(
                record_message.sentence_id,
                record_message.original_text,
                record_message.normalized_text,
                record_message.source_relative_path,
                record_message.source_line_number,
            )
            _update_identity_string(corpus_identity, record_message.source_relative_path)
            corpus_identity.update(struct.pack("<Q", record_message.source_line_number))
            _update_identity_string(corpus_identity, record_message.original_text)
    if len(records) != manifest.sentence_count:
        raise SnapshotError("sentence count mismatch")
    if corpus_identity.digest() != manifest.corpus_digest:
        raise SnapshotError("corpus digest mismatch")
    records_finished = time.perf_counter()

    index_started = time.perf_counter()
    postings: dict[tuple[int, str], PostingArray] = {}
    active_key: tuple[int, str] | None = None
    active_ids = PostingArray()
    expected_chunk = 0
    last_completed_key: tuple[int, str] | None = None
    for shard in manifest.index_shards:
        if shard.kind != 2:
            raise SnapshotError("index shard has wrong kind")
        shard_first: tuple[int, str] | None = None
        shard_last: tuple[int, str] | None = None
        for payload in _frames(_safe_file(root, shard.file_name), 2, shard):
            posting_message = _parse(PostingChunk(), payload, "posting")
            key = (posting_message.gram_size, posting_message.gram)
            ids = posting_message.sentence_ids
            if (
                posting_message.gram_size not in GRAM_SIZES
                or len(posting_message.gram) != posting_message.gram_size
                or not ids
            ):
                raise SnapshotError("invalid posting list")
            if shard_first is None:
                shard_first = key
            shard_last = key
            if active_key is None:
                if last_completed_key is not None and key <= last_completed_key:
                    raise SnapshotError("posting keys are not strictly ordered")
                active_key = key
            if key != active_key or posting_message.chunk_index != expected_chunk:
                raise SnapshotError("invalid posting chunk sequence")
            previous_id = active_ids[-1] if active_ids else 0
            for sentence_id in ids:
                if sentence_id <= previous_id or sentence_id > 0xFFFFFFFF:
                    raise SnapshotError("posting IDs are not strictly increasing")
                if sentence_id not in records:
                    raise SnapshotError("posting references unknown sentence ID")
                active_ids.append(sentence_id)
                previous_id = sentence_id
            expected_chunk += 1
            if posting_message.is_last_chunk:
                postings[key] = active_ids
                last_completed_key = key
                active_key = None
                active_ids = PostingArray()
                expected_chunk = 0
        if shard_first is None:
            raise SnapshotError("empty index shard")
        if (shard.first_gram_size, shard.first_gram) != shard_first or (
            shard.last_gram_size,
            shard.last_gram,
        ) != shard_last:
            raise SnapshotError("index shard key bounds mismatch")
    if active_key is not None:
        raise SnapshotError("unterminated posting chunk sequence")

    index_identity = hashlib.sha256()
    for (gram_size, gram), posting_ids in postings.items():
        index_identity.update(struct.pack("<Q", gram_size))
        _update_identity_string(index_identity, gram)
        index_identity.update(struct.pack("<Q", len(posting_ids)))
        for sentence_id in posting_ids:
            index_identity.update(struct.pack("<Q", sentence_id))
    if index_identity.digest() != manifest.index_digest:
        raise SnapshotError("index digest mismatch")
    config = (
        manifest.corpus_digest
        + manifest.index_digest
        + b"schema=1;framing=1;normalization=1;index=1;grams=1,2,3;shard="
        + str(manifest.ngram_index.shard_target_bytes).encode()
    )
    if hashlib.sha256(config).hexdigest() != manifest.snapshot_id:
        raise SnapshotError("snapshot ID mismatch")
    index = SearchIndex._from_validated_postings(postings, PostingArray(records))
    load_finished = time.perf_counter()
    posting_id_count = sum(len(ids) for ids in postings.values())
    compact_storage_bytes = sum(sys.getsizeof(ids) for ids in postings.values())
    estimated_tuple_bytes = posting_id_count * 36 + len(postings) * 40
    saved_percent = (
        (1.0 - compact_storage_bytes / estimated_tuple_bytes) * 100.0
        if estimated_tuple_bytes
        else 0.0
    )
    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    print(
        "[PYTHON LOADER] [SNAPSHOT LOAD] -> "
        f"Loaded {len(records):,} records and {len(postings):,} N-Gram keys in "
        f"{(load_finished - load_started) * 1_000:,.2f} ms | "
        f"Records: {(records_finished - records_started) * 1_000:,.2f} ms | "
        f"Index: {(load_finished - index_started) * 1_000:,.2f} ms | "
        f"Peak RSS: {peak_rss_mb:,.2f} MB."
    )
    print(
        "[PYTHON LOADER] [FLAT MEMORY JUSTIFICATION] -> "
        f"Loaded {posting_id_count:,} posting IDs using array.array('I') | "
        f"Posting Array Storage: {compact_storage_bytes / 1_000_000:,.2f} MB | "
        f"Estimated Python tuple+int Storage: {estimated_tuple_bytes / 1_000_000:,.2f} MB | "
        f"Estimated Saving: {saved_percent:,.1f}% (4-byte uint32 payload per ID)."
    )
    return records, index
