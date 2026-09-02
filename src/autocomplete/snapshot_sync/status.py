"""Serving policy for current, stale, and expired snapshots."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from autocomplete.snapshot_pointer import SnapshotPointerError, current_snapshot


class SnapshotState(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    NO_VERIFIED_CURRENT_SNAPSHOT = "NO_VERIFIED_CURRENT_SNAPSHOT"


class SnapshotUnavailableError(RuntimeError):
    """Search cannot be served because no unexpired snapshot is verified."""


@dataclass(frozen=True)
class SnapshotStatus:
    state: SnapshotState
    active_snapshot_id: str | None
    expected_snapshot_id: str | None
    missing_chunks: tuple[int, ...] = ()

    @property
    def can_serve(self) -> bool:
        return self.state in (SnapshotState.CURRENT, SnapshotState.STALE)

    def require_serving(self) -> None:
        if not self.can_serve:
            raise SnapshotUnavailableError("NO VERIFIED CURRENT SNAPSHOT")


def assess_status(
    snapshot_root: Path,
    verified_manifests: Mapping[str, object],
    *,
    now: int | None = None,
    expected_snapshot_id: str | None = None,
    missing_chunks: Sequence[int] = (),
) -> SnapshotStatus:
    try:
        current = current_snapshot(snapshot_root)
    except SnapshotPointerError:
        current = None
    if current is None:
        return SnapshotStatus(
            SnapshotState.NO_VERIFIED_CURRENT_SNAPSHOT,
            None,
            expected_snapshot_id,
            tuple(missing_chunks),
        )
    active_id = current[0]
    manifest = verified_manifests.get(active_id)
    current_time = int(time.time()) if now is None else now
    if manifest is None or current_time >= manifest.valid_until:
        return SnapshotStatus(
            SnapshotState.NO_VERIFIED_CURRENT_SNAPSHOT,
            active_id,
            expected_snapshot_id,
            tuple(missing_chunks),
        )
    state = (
        SnapshotState.STALE
        if expected_snapshot_id is not None and expected_snapshot_id != active_id
        else SnapshotState.CURRENT
    )
    return SnapshotStatus(state, active_id, expected_snapshot_id, tuple(missing_chunks))
