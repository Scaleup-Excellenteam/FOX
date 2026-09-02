import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest

import autocomplete.snapshot_sync.transfer_session as transfer_module
from autocomplete.snapshot_sync.chunker import split_snapshot
from autocomplete.snapshot_sync.crypto import encrypt_chunk, new_test_keyset, primitive
from autocomplete.snapshot_sync.manifest import SnapshotChunk, build_manifest
from autocomplete.snapshot_sync.transfer_session import TransferError, TransferManager

MISSION = "MISSION-ALPHA"
SATELLITE = "SAT-07"


class CopyingStore:
    def materialize_snapshot(self, snapshot_ref: str, destination):
        shutil.copytree(snapshot_ref, destination)
        return destination


def transfer_data(tmp_path, *, count: int | None = None):
    source = tmp_path / "source"
    source.mkdir()
    (source / "manifest.binpb").write_bytes(b"test manifest")
    (source / "records.binpb").write_bytes(bytes(range(100)) * 20)
    plaintext = split_snapshot(source, 1 if count == 100 else 97)
    if count is not None:
        assert len(plaintext) >= count
        plaintext = plaintext[:count]
    snapshot_id = "a" * 64
    manifest = build_manifest(
        snapshot_id,
        plaintext,
        corpus_version=13,
        valid_until=2_000_000_000,
    )
    streaming_primitive = primitive(new_test_keyset())
    chunks = [
        SnapshotChunk(
            mission_id=MISSION,
            satellite_id=SATELLITE,
            snapshot_id=snapshot_id,
            chunk_number=number,
            total_chunks=len(plaintext),
            encrypted_payload=encrypt_chunk(
                payload, streaming_primitive, MISSION, SATELLITE, snapshot_id
            ),
        )
        for number, payload in enumerate(plaintext)
    ]
    manager = TransferManager(
        tmp_path / "snapshots", tmp_path / "staging", CopyingStore()
    )
    state = manager.begin(
        manifest,
        mission_id=MISSION,
        satellite_id=SATELLITE,
        primitive=streaming_primitive,
    )
    return manager, state, manifest, chunks


def test_all_chunks_in_order_complete(tmp_path) -> None:
    manager, state, _, chunks = transfer_data(tmp_path)

    results = [manager.receive(chunk) for chunk in chunks]

    assert results[-1] is True
    assert state.received_chunk_numbers == set(range(len(chunks)))


def test_all_chunks_out_of_order_complete(tmp_path) -> None:
    manager, state, _, chunks = transfer_data(tmp_path)

    for chunk in reversed(chunks):
        complete = manager.receive(chunk)

    assert complete is True
    assert state.received_chunk_numbers == set(range(len(chunks)))


def test_disconnect_at_60_of_100_requests_only_remaining_chunks(tmp_path) -> None:
    manager, state, _, chunks = transfer_data(tmp_path, count=100)

    for chunk in chunks[:60]:
        assert manager.receive(chunk) is False

    assert state.received_chunk_numbers == set(range(60))
    assert manager.missing_chunks(state.snapshot_id) == list(range(60, 100))
    assert len(list((state.staging_dir / "chunks").glob("*.chunk"))) == 60


def test_duplicate_chunk_is_noop_without_second_decryption(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, state, _, chunks = transfer_data(tmp_path)
    real_decrypt = transfer_module.decrypt_chunk
    calls = 0

    def tracked_decrypt(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_decrypt(*args, **kwargs)

    monkeypatch.setattr(transfer_module, "decrypt_chunk", tracked_decrypt)

    assert manager.receive(chunks[0]) is False
    assert manager.receive(chunks[0]) is False

    assert calls == 1
    assert state.received_chunk_numbers == {0}


def test_concurrent_same_snapshot_initiations_share_state(tmp_path) -> None:
    manager, state, manifest, _ = transfer_data(tmp_path)
    session_primitive = manager._sessions[state.snapshot_id].primitive

    def begin():
        return manager.begin(
            manifest,
            mission_id=MISSION,
            satellite_id=SATELLITE,
            primitive=session_primitive,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(lambda _: begin(), range(2))

    assert first is state
    assert second is state


def test_hash_mismatch_is_rejected_without_staging(tmp_path) -> None:
    manager, state, manifest, chunks = transfer_data(tmp_path)
    manifest.chunk_hashes[0] = b"\x00" * 32

    with pytest.raises(TransferError, match="hash verification"):
        manager.receive(chunks[0])

    assert state.received_chunk_numbers == set()
    assert list((state.staging_dir / "chunks").glob("*.chunk")) == []


def test_tampered_ciphertext_raises_transfer_error_without_staging(tmp_path) -> None:
    manager, state, _, chunks = transfer_data(tmp_path)
    tampered = SnapshotChunk()
    tampered.CopyFrom(chunks[0])
    ciphertext = bytearray(tampered.encrypted_payload)
    ciphertext[len(ciphertext) // 2] ^= 1
    tampered.encrypted_payload = bytes(ciphertext)

    with pytest.raises(TransferError, match="decryption failed"):
        manager.receive(tampered)

    assert state.received_chunk_numbers == set()
    assert list((state.staging_dir / "chunks").glob("*.chunk")) == []
