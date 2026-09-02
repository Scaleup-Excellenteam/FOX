import pytest

from autocomplete.snapshot_pointer import activate_snapshot
from autocomplete.snapshot_sync.manifest import build_manifest
from autocomplete.snapshot_sync.status import (
    SnapshotState,
    SnapshotUnavailableError,
    assess_status,
)


def active_snapshot(tmp_path, *, valid_until: int):
    snapshot_id = "a" * 64
    snapshot_root = tmp_path / "snapshots"
    (snapshot_root / snapshot_id).mkdir(parents=True)
    activate_snapshot(snapshot_root, snapshot_id)
    manifest = build_manifest(
        snapshot_id,
        [b"chunk"],
        corpus_version=12,
        valid_until=valid_until,
    )
    return snapshot_root, snapshot_id, manifest


def test_valid_non_expired_current_snapshot_is_current(tmp_path) -> None:
    root, snapshot_id, manifest = active_snapshot(tmp_path, valid_until=200)

    status = assess_status(root, {snapshot_id: manifest}, now=100)

    assert status.state is SnapshotState.CURRENT
    assert status.can_serve is True
    status.require_serving()


def test_expired_snapshot_refuses_search(tmp_path) -> None:
    root, snapshot_id, manifest = active_snapshot(tmp_path, valid_until=100)

    status = assess_status(root, {snapshot_id: manifest}, now=100)

    assert status.state is SnapshotState.NO_VERIFIED_CURRENT_SNAPSHOT
    assert status.can_serve is False
    with pytest.raises(SnapshotUnavailableError):
        status.require_serving()


def test_no_current_snapshot_refuses_search(tmp_path) -> None:
    status = assess_status(tmp_path / "snapshots", {}, now=100)

    assert status.state is SnapshotState.NO_VERIFIED_CURRENT_SNAPSHOT
    with pytest.raises(SnapshotUnavailableError):
        status.require_serving()


def test_incomplete_newer_snapshot_marks_active_one_stale(tmp_path) -> None:
    root, snapshot_id, manifest = active_snapshot(tmp_path, valid_until=200)
    expected = "b" * 64

    status = assess_status(
        root,
        {snapshot_id: manifest},
        now=100,
        expected_snapshot_id=expected,
        missing_chunks=range(60, 100),
    )

    assert status.state is SnapshotState.STALE
    assert status.active_snapshot_id == snapshot_id
    assert status.expected_snapshot_id == expected
    assert status.missing_chunks == tuple(range(60, 100))
    assert status.can_serve is True
