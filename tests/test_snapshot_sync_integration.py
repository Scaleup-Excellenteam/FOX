import subprocess
import sys

from autocomplete.artifact_store import LocalArtifactStore
from autocomplete.snapshot_loader import load_snapshot, load_snapshot_manifest
from autocomplete.snapshot_pointer import activate_snapshot, current_snapshot
from autocomplete.snapshot_sync.chunker import split_snapshot
from autocomplete.snapshot_sync.crypto import encrypt_chunk, new_test_keyset, primitive
from autocomplete.snapshot_sync.manifest import SnapshotChunk, build_manifest
from autocomplete.snapshot_sync.transfer_session import TransferManager


def build_snapshot(reference_builder, corpus, destination, sentence: str):
    corpus.mkdir()
    (corpus / "sentences.txt").write_text(sentence + "\n")
    subprocess.run(
        [sys.executable, str(reference_builder), str(corpus), str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )
    return load_snapshot_manifest(destination).snapshot_id


def test_encrypted_transfer_resumes_then_atomically_activates(
    tmp_path, reference_builder
) -> None:
    old_source = tmp_path / "old-source"
    new_source = tmp_path / "new-source"
    old_id = build_snapshot(
        reference_builder,
        tmp_path / "old-corpus",
        old_source,
        "Old verified snapshot remains loadable",
    )
    new_id = build_snapshot(
        reference_builder,
        tmp_path / "new-corpus",
        new_source,
        "New verified snapshot arrived securely",
    )
    snapshot_root = tmp_path / "snapshots"
    store = LocalArtifactStore()
    old_destination = store.materialize_snapshot(
        str(old_source), snapshot_root / old_id
    )
    activate_snapshot(snapshot_root, old_id)
    plaintext_chunks = split_snapshot(new_source, chunk_size=80)
    manifest = build_manifest(
        new_id,
        plaintext_chunks,
        corpus_version=13,
        valid_until=2_000_000_000,
        based_on_snapshot_id=old_id,
    )
    streaming_primitive = primitive(new_test_keyset())
    encrypted_chunks = [
        SnapshotChunk(
            mission_id="MISSION-ALPHA",
            satellite_id="SAT-07",
            snapshot_id=new_id,
            chunk_number=number,
            total_chunks=len(plaintext_chunks),
            encrypted_payload=encrypt_chunk(
                payload,
                streaming_primitive,
                "MISSION-ALPHA",
                "SAT-07",
                new_id,
            ),
        )
        for number, payload in enumerate(plaintext_chunks)
    ]
    manager = TransferManager(snapshot_root, tmp_path / "transfer-staging", store)
    state = manager.begin(
        manifest,
        mission_id="MISSION-ALPHA",
        satellite_id="SAT-07",
        primitive=streaming_primitive,
    )
    split_at = max(1, len(encrypted_chunks) * 3 // 5)

    for chunk in encrypted_chunks[:split_at]:
        assert manager.receive(chunk) is False

    assert current_snapshot(snapshot_root) == (old_id, old_destination)
    load_snapshot(old_destination)
    assert not (snapshot_root / new_id).exists()
    assert manager.missing_chunks(new_id) == list(
        range(split_at, len(encrypted_chunks))
    )
    assert len(state.received_chunk_numbers) == split_at

    for chunk in encrypted_chunks[split_at:]:
        complete = manager.receive(chunk)

    assert complete is True
    assert current_snapshot(snapshot_root) == (new_id, snapshot_root / new_id)
    load_snapshot(snapshot_root / new_id)
    load_snapshot(old_destination)
    assert old_destination.is_dir()
