"""Protobuf contracts and verification for secure snapshot updates."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from autocomplete.snapshot_pointer import SNAPSHOT_ID_PATTERN


class ManifestError(ValueError):
    """The update manifest or a plaintext chunk is invalid."""


def _message_classes() -> tuple[type, type]:
    file_proto = descriptor_pb2.FileDescriptorProto(
        name="snapshot_sync.proto",
        package="autocomplete.snapshot_sync",
        syntax="proto3",
    )
    chunk = file_proto.message_type.add(name="SnapshotChunk")
    manifest = file_proto.message_type.add(name="SnapshotUpdateManifest")

    def field(message, name, number, field_type, *, repeated=False):
        message.field.add(
            name=name,
            number=number,
            label=(
                descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
                if repeated
                else descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
            ),
            type=field_type,
        )

    string = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    bytes_type = descriptor_pb2.FieldDescriptorProto.TYPE_BYTES
    uint32 = descriptor_pb2.FieldDescriptorProto.TYPE_UINT32
    uint64 = descriptor_pb2.FieldDescriptorProto.TYPE_UINT64
    int64 = descriptor_pb2.FieldDescriptorProto.TYPE_INT64
    field(chunk, "mission_id", 1, string)
    field(chunk, "satellite_id", 2, string)
    field(chunk, "snapshot_id", 3, string)
    field(chunk, "chunk_number", 4, uint32)
    field(chunk, "total_chunks", 5, uint32)
    field(chunk, "encrypted_payload", 6, bytes_type)
    field(manifest, "snapshot_id", 1, string)
    field(manifest, "based_on_snapshot_id", 2, string)
    field(manifest, "corpus_version", 3, uint64)
    field(manifest, "valid_until", 4, int64)
    field(manifest, "chunk_hashes", 5, bytes_type, repeated=True)
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_proto)
    return (
        message_factory.GetMessageClass(
            pool.FindMessageTypeByName("autocomplete.snapshot_sync.SnapshotChunk")
        ),
        message_factory.GetMessageClass(
            pool.FindMessageTypeByName(
                "autocomplete.snapshot_sync.SnapshotUpdateManifest"
            )
        ),
    )


SnapshotChunk, SnapshotUpdateManifest = _message_classes()


def chunk_digest(plaintext: bytes) -> bytes:
    return hashlib.sha256(plaintext).digest()


def build_manifest(
    snapshot_id: str,
    plaintext_chunks: Sequence[bytes],
    *,
    corpus_version: int,
    valid_until: int,
    based_on_snapshot_id: str = "",
):
    manifest = SnapshotUpdateManifest(
        snapshot_id=snapshot_id,
        based_on_snapshot_id=based_on_snapshot_id,
        corpus_version=corpus_version,
        valid_until=valid_until,
        chunk_hashes=[chunk_digest(chunk) for chunk in plaintext_chunks],
    )
    verify_manifest(manifest, len(plaintext_chunks))
    return manifest


def parse_manifest(payload: bytes):
    manifest = SnapshotUpdateManifest()
    try:
        manifest.ParseFromString(payload)
    except Exception as error:
        raise ManifestError("invalid snapshot update manifest protobuf") from error
    verify_manifest(manifest, len(manifest.chunk_hashes))
    return manifest


def verify_manifest(manifest, total_chunks: int) -> None:
    if SNAPSHOT_ID_PATTERN.fullmatch(manifest.snapshot_id) is None:
        raise ManifestError("manifest snapshot_id must be 64 lowercase hex characters")
    if manifest.based_on_snapshot_id and (
        SNAPSHOT_ID_PATTERN.fullmatch(manifest.based_on_snapshot_id) is None
    ):
        raise ManifestError("based_on_snapshot_id is invalid")
    if total_chunks <= 0 or len(manifest.chunk_hashes) != total_chunks:
        raise ManifestError("manifest chunk count does not match transfer")
    if manifest.corpus_version <= 0:
        raise ManifestError("corpus_version must be positive")
    if manifest.valid_until <= 0:
        raise ManifestError("valid_until must be positive")
    if any(
        len(digest) != hashlib.sha256().digest_size for digest in manifest.chunk_hashes
    ):
        raise ManifestError("every chunk hash must be a SHA-256 digest")


def verify_plaintext_chunk(manifest, chunk_number: int, plaintext: bytes) -> None:
    if not 0 <= chunk_number < len(manifest.chunk_hashes):
        raise ManifestError("chunk_number is outside the manifest")
    if chunk_digest(plaintext) != manifest.chunk_hashes[chunk_number]:
        raise ManifestError("plaintext chunk hash mismatch")
