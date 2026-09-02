"""Receive-side state machine for authenticated, resumable snapshot transfer."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from tink import streaming_aead

from autocomplete.artifact_store import ArtifactStore, LocalArtifactStore
from autocomplete.snapshot_pointer import activate_snapshot, current_snapshot
from autocomplete.snapshot_sync.chunker import restore_snapshot
from autocomplete.snapshot_sync.crypto import decrypt_chunk
from autocomplete.snapshot_sync.manifest import (
    ManifestError,
    verify_manifest,
    verify_plaintext_chunk,
)


class TransferError(ValueError):
    """A chunk is inconsistent with its authenticated transfer."""


@dataclass
class TransferState:
    snapshot_id: str
    total_chunks: int
    received_chunk_numbers: set[int]
    staging_dir: Path


@dataclass
class _Session:
    state: TransferState
    manifest: object
    mission_id: str
    satellite_id: str
    primitive: streaming_aead.StreamingAead
    activated: bool = False


class TransferManager:
    def __init__(
        self,
        snapshot_root: Path,
        staging_root: Path,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._snapshot_root = Path(snapshot_root)
        self._staging_root = Path(staging_root)
        self._store = artifact_store or LocalArtifactStore()
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()

    def begin(
        self,
        manifest,
        *,
        mission_id: str,
        satellite_id: str,
        primitive: streaming_aead.StreamingAead,
    ) -> TransferState:
        verify_manifest(manifest, len(manifest.chunk_hashes))
        with self._lock:
            existing = self._sessions.get(manifest.snapshot_id)
            if existing is not None:
                if (
                    existing.manifest.SerializeToString()
                    != manifest.SerializeToString()
                    or existing.mission_id != mission_id
                    or existing.satellite_id != satellite_id
                ):
                    raise TransferError("conflicting transfer initiation")
                return existing.state
            staging = self._staging_root / manifest.snapshot_id
            chunks_dir = staging / "chunks"
            chunks_dir.mkdir(parents=True, exist_ok=True)
            state = TransferState(
                snapshot_id=manifest.snapshot_id,
                total_chunks=len(manifest.chunk_hashes),
                received_chunk_numbers=set(),
                staging_dir=staging,
            )
            self._sessions[manifest.snapshot_id] = _Session(
                state, manifest, mission_id, satellite_id, primitive
            )
            return state

    def missing_chunks(self, snapshot_id: str) -> list[int]:
        with self._lock:
            session = self._session(snapshot_id)
            return sorted(
                set(range(session.state.total_chunks))
                - session.state.received_chunk_numbers
            )

    def receive(self, chunk) -> bool:
        """Verify and stage one chunk; return whether activation is complete."""
        with self._lock:
            session = self._session(chunk.snapshot_id)
            state = session.state
            if chunk.snapshot_id != state.snapshot_id:
                raise TransferError("chunk snapshot_id does not match transfer")
            if chunk.mission_id != session.mission_id:
                raise TransferError("chunk mission_id does not match transfer")
            if chunk.satellite_id != session.satellite_id:
                raise TransferError("chunk satellite_id does not match transfer")
            if chunk.total_chunks != state.total_chunks:
                raise TransferError("chunk total_chunks does not match transfer")
            if chunk.chunk_number >= state.total_chunks:
                raise TransferError("chunk_number is outside transfer")
            if chunk.chunk_number in state.received_chunk_numbers:
                return session.activated
            plaintext = decrypt_chunk(
                chunk.encrypted_payload,
                session.primitive,
                chunk.mission_id,
                chunk.satellite_id,
                chunk.snapshot_id,
            )
            try:
                verify_plaintext_chunk(session.manifest, chunk.chunk_number, plaintext)
            except ManifestError as error:
                raise TransferError(
                    "chunk authentication or hash verification failed"
                ) from error
            self._stage_chunk(state, chunk.chunk_number, plaintext)
            state.received_chunk_numbers.add(chunk.chunk_number)
            if len(state.received_chunk_numbers) == state.total_chunks:
                self._activate(session)
            return session.activated

    def _session(self, snapshot_id: str) -> _Session:
        try:
            return self._sessions[snapshot_id]
        except KeyError as error:
            raise TransferError(f"no transfer for snapshot {snapshot_id!r}") from error

    @staticmethod
    def _stage_chunk(state: TransferState, chunk_number: int, plaintext: bytes) -> None:
        chunks_dir = state.staging_dir / "chunks"
        temporary = chunks_dir / f".{chunk_number}.tmp"
        destination = chunks_dir / f"{chunk_number}.chunk"
        with temporary.open("wb") as output:
            output.write(plaintext)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(destination)

    def _activate(self, session: _Session) -> None:
        if session.activated:
            return
        manifest = session.manifest
        if manifest.based_on_snapshot_id:
            current = current_snapshot(self._snapshot_root)
            if current is None or current[0] != manifest.based_on_snapshot_id:
                raise TransferError(
                    "active snapshot does not match based_on_snapshot_id"
                )
        assembled = session.state.staging_dir / "assembled"
        restore_snapshot(
            (
                (session.state.staging_dir / "chunks" / f"{number}.chunk").read_bytes()
                for number in range(session.state.total_chunks)
            ),
            assembled,
        )
        destination = self._snapshot_root / session.state.snapshot_id
        self._store.materialize_snapshot(str(assembled), destination)
        activate_snapshot(self._snapshot_root, session.state.snapshot_id)
        session.activated = True
