"""Secure, resumable delivery of immutable FOX snapshots.

Deployment keysets are loaded from ``TINK_KEYSET_JSON``. Production deployments
should protect that data-encryption key with Google Cloud KMS; KMS integration is
intentionally outside this local transport package.
"""

from autocomplete.snapshot_sync.manifest import (
    SnapshotChunk,
    SnapshotUpdateManifest,
    build_manifest,
)
from autocomplete.snapshot_sync.status import SnapshotState, SnapshotStatus
from autocomplete.snapshot_sync.transfer_session import TransferManager, TransferState

__all__ = [
    "SnapshotChunk",
    "SnapshotState",
    "SnapshotStatus",
    "SnapshotUpdateManifest",
    "TransferManager",
    "TransferState",
    "build_manifest",
]
