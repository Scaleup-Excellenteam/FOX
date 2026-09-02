"""Standalone, runnable demo for Secure Resumable Snapshot Sync.

Usage:
    python demo_snapshot_sync.py

This builds two tiny throwaway snapshots (an "old" one and a "new" one),
activates the old one as the currently-served snapshot, then encrypts and
transfers the new one in chunks - demonstrating:

  1. A disconnect partway through: the old snapshot keeps serving, the
     partial new one is never activated.
  2. Resuming the transfer: only the missing chunks are sent.
  3. A tampered chunk being rejected by Tink authentication.
  4. Completing the transfer: atomic activation flips to the new snapshot.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from autocomplete.artifact_store import LocalArtifactStore
from autocomplete.snapshot_loader import load_snapshot, load_snapshot_manifest
from autocomplete.snapshot_pointer import activate_snapshot, current_snapshot
from autocomplete.snapshot_sync.chunker import split_snapshot
from autocomplete.snapshot_sync.crypto import (
    decrypt_chunk,
    encrypt_chunk,
    new_test_keyset,
    primitive,
)
from autocomplete.snapshot_sync.manifest import SnapshotChunk, build_manifest
from autocomplete.snapshot_sync.transfer_session import TransferError, TransferManager

REPO_ROOT = Path(__file__).parent
REFERENCE_BUILDER = REPO_ROOT / "tests" / "reference_builder.py"

MISSION_ID = "SUNCATCHER-DEMO"
SATELLITE_ID = "SAT-07"


def build_snapshot(corpus_dir: Path, destination: Path, sentence: str) -> str:
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "sentences.txt").write_text(sentence + "\n")
    subprocess.run(
        [sys.executable, str(REFERENCE_BUILDER), str(corpus_dir), str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )
    return load_snapshot_manifest(destination).snapshot_id


def make_encrypted_chunks(source: Path, snapshot_id: str, streaming_primitive):
    plaintext_chunks = split_snapshot(source, chunk_size=80)
    chunks = [
        SnapshotChunk(
            mission_id=MISSION_ID,
            satellite_id=SATELLITE_ID,
            snapshot_id=snapshot_id,
            chunk_number=number,
            total_chunks=len(plaintext_chunks),
            encrypted_payload=encrypt_chunk(
                payload, streaming_primitive, MISSION_ID, SATELLITE_ID, snapshot_id
            ),
        )
        for number, payload in enumerate(plaintext_chunks)
    ]
    return plaintext_chunks, chunks


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        store = LocalArtifactStore()
        snapshot_root = tmp_path / "snapshots"

        print("=== Step 1: activate an initial ('old') snapshot v_old ===")
        old_source = tmp_path / "old-source"
        old_id = build_snapshot(
            tmp_path / "old-corpus",
            old_source,
            "Old verified snapshot remains loadable",
        )
        old_destination = store.materialize_snapshot(
            str(old_source), snapshot_root / old_id
        )
        activate_snapshot(snapshot_root, old_id)
        print(f"Active snapshot: {current_snapshot(snapshot_root)[0]}")

        print("\n=== Step 2: prepare an encrypted, chunked 'new' snapshot v_new ===")
        new_source = tmp_path / "new-source"
        new_id = build_snapshot(
            tmp_path / "new-corpus",
            new_source,
            "New verified snapshot arrived securely",
        )
        keyset = new_test_keyset()
        streaming_primitive = primitive(keyset)
        plaintext_chunks, chunks = make_encrypted_chunks(
            new_source, new_id, streaming_primitive
        )
        manifest = build_manifest(
            new_id,
            plaintext_chunks,
            corpus_version=13,
            valid_until=2_000_000_000,
            based_on_snapshot_id=old_id,
        )
        print(f"New snapshot split into {len(chunks)} encrypted chunks")

        manager = TransferManager(snapshot_root, tmp_path / "transfer-staging", store)
        manager.begin(
            manifest,
            mission_id=MISSION_ID,
            satellite_id=SATELLITE_ID,
            primitive=streaming_primitive,
        )

        print("\n=== Step 3: simulate a disconnect partway through ===")
        split_at = max(1, len(chunks) * 3 // 5)
        for chunk in chunks[:split_at]:
            manager.receive(chunk)
        print(f"Received {split_at}/{len(chunks)} chunks, then connection lost.")
        print(f"Active snapshot is still: {current_snapshot(snapshot_root)[0]}")
        print(f"Missing chunks: {manager.missing_chunks(new_id)}")

        print("\n=== Step 4: tamper with a chunk before resuming ===")
        tampered = chunks[split_at]
        corrupted_payload = bytearray(tampered.encrypted_payload)
        corrupted_payload[-1] ^= 0xFF
        tampered_chunk = SnapshotChunk(
            mission_id=tampered.mission_id,
            satellite_id=tampered.satellite_id,
            snapshot_id=tampered.snapshot_id,
            chunk_number=tampered.chunk_number,
            total_chunks=tampered.total_chunks,
            encrypted_payload=bytes(corrupted_payload),
        )
        try:
            manager.receive(tampered_chunk)
            print("UNEXPECTED: tampered chunk was accepted!")
        except TransferError as error:
            print(f"Tampered chunk correctly rejected: {error}")

        print("\n=== Step 5: resume with the correct remaining chunks ===")
        complete = False
        for chunk in chunks[split_at:]:
            complete = manager.receive(chunk)
        print(f"Transfer complete: {complete}")
        print(f"Active snapshot is now: {current_snapshot(snapshot_root)[0]}")

        print("\n=== Step 6: confirm the old snapshot is untouched and still loadable ===")
        load_snapshot(old_destination)
        load_snapshot(snapshot_root / new_id)
        print(f"Old snapshot directory still exists: {old_destination.is_dir()}")
        print("Both old and new snapshots load correctly. Done.")


if __name__ == "__main__":
    main()
