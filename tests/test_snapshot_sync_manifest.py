import pytest

from autocomplete.snapshot_sync.manifest import (
    ManifestError,
    build_manifest,
    verify_manifest,
    verify_plaintext_chunk,
)


def test_correct_plaintext_chunk_hashes_verify() -> None:
    chunks = [b"first", b"second", b"third"]
    manifest = build_manifest(
        "a" * 64, chunks, corpus_version=13, valid_until=2_000_000_000
    )

    verify_manifest(manifest, len(chunks))
    for number, chunk in enumerate(chunks):
        verify_plaintext_chunk(manifest, number, chunk)


def test_wrong_hash_rejects_chunk_after_successful_decryption() -> None:
    chunks = [b"first", b"second"]
    manifest = build_manifest(
        "b" * 64, chunks, corpus_version=13, valid_until=2_000_000_000
    )
    manifest.chunk_hashes[1] = b"\x00" * 32

    with pytest.raises(ManifestError, match="hash mismatch"):
        verify_plaintext_chunk(manifest, 1, chunks[1])
