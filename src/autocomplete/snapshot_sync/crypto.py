"""Tink Streaming AEAD helpers with mission/satellite/snapshot binding."""

from __future__ import annotations

import io
import os
from typing import BinaryIO

import tink
from tink import streaming_aead

streaming_aead.register()


class SnapshotCryptoError(ValueError):
    """A keyset or associated-data field is invalid."""


class _RetainedBytesIO(io.BytesIO):
    """Let Tink close its wrapper without making ciphertext inaccessible."""

    def close(self) -> None:
        pass


def associated_data(mission_id: str, satellite_id: str, snapshot_id: str) -> bytes:
    values = (mission_id, satellite_id, snapshot_id)
    if any(not isinstance(value, str) or not value for value in values):
        raise SnapshotCryptoError("associated-data fields must be non-empty strings")
    if any("|" in value for value in values):
        raise SnapshotCryptoError("associated-data fields cannot contain '|'")
    return f"{mission_id}|{satellite_id}|{snapshot_id}".encode()


def new_test_keyset() -> tink.KeysetHandle:
    """Generate an in-memory keyset for tests; callers must never persist it."""
    return tink.new_keyset_handle(
        streaming_aead.streaming_aead_key_templates.AES256_GCM_HKDF_4KB
    )


def load_keyset_from_environment(
    variable: str = "TINK_KEYSET_JSON",
) -> tink.KeysetHandle:
    serialized = os.environ.get(variable)
    if not serialized:
        raise SnapshotCryptoError(f"{variable} is not configured")
    try:
        return tink.json_proto_keyset_format.parse(
            serialized, tink._secret_key_access.TOKEN
        )
    except Exception as error:
        raise SnapshotCryptoError(f"{variable} is not a valid Tink keyset") from error


def primitive(keyset: tink.KeysetHandle) -> streaming_aead.StreamingAead:
    return keyset.primitive(streaming_aead.StreamingAead)


def encrypt_chunk(
    plaintext: bytes,
    primitive: streaming_aead.StreamingAead,
    mission_id: str,
    satellite_id: str,
    snapshot_id: str,
) -> bytes:
    destination = _RetainedBytesIO()
    stream: BinaryIO = primitive.new_encrypting_stream(
        destination, associated_data(mission_id, satellite_id, snapshot_id)
    )
    stream.write(plaintext)
    stream.close()
    return destination.getvalue()


def decrypt_chunk(
    ciphertext: bytes,
    primitive: streaming_aead.StreamingAead,
    mission_id: str,
    satellite_id: str,
    snapshot_id: str,
) -> bytes:
    source = io.BytesIO(ciphertext)
    stream: BinaryIO = primitive.new_decrypting_stream(
        source, associated_data(mission_id, satellite_id, snapshot_id)
    )
    try:
        return stream.read()
    finally:
        stream.close()
