from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

SNAPSHOT_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
# A relative ID file avoids symlink privilege and portability differences while
# keeping snapshot roots movable between machines.
CURRENT_POINTER_NAME = "current"


class SnapshotPointerError(ValueError):
    """The current-snapshot pointer is malformed or references no snapshot."""


class SnapshotPointerDurabilityError(OSError):
    """The pointer was replaced, but its directory entry may not be durable."""


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_snapshot_pointer(pointer: Path) -> tuple[str, Path]:
    pointer = Path(pointer)
    try:
        snapshot_id = pointer.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise SnapshotPointerError(
            f"cannot read snapshot pointer {pointer}: {exc}"
        ) from exc
    if SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id) is None:
        raise SnapshotPointerError(f"invalid snapshot ID in pointer {pointer}")
    snapshot = pointer.parent / snapshot_id
    if not snapshot.is_dir():
        raise SnapshotPointerError(
            f"snapshot pointer {pointer} references missing snapshot {snapshot_id}"
        )
    return snapshot_id, snapshot


def current_snapshot(snapshot_root: Path) -> tuple[str, Path] | None:
    pointer = Path(snapshot_root) / CURRENT_POINTER_NAME
    if not pointer.exists():
        return None
    return read_snapshot_pointer(pointer)


def resolve_snapshot_path(reference: Path) -> Path:
    """Resolve a pointer file while leaving ordinary snapshot paths unchanged."""
    reference = Path(reference)
    if reference.is_file():
        return read_snapshot_pointer(reference)[1]
    return reference


def activate_snapshot(snapshot_root: Path, snapshot_id: str) -> Path:
    """Atomically activate an immutable snapshot using a relative pointer file."""
    snapshot_root = Path(snapshot_root)
    if SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id) is None:
        raise SnapshotPointerError(f"invalid snapshot ID: {snapshot_id!r}")
    snapshot = snapshot_root / snapshot_id
    if not snapshot.is_dir():
        raise SnapshotPointerError(f"cannot activate missing snapshot: {snapshot}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{CURRENT_POINTER_NAME}-", dir=snapshot_root
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as output:
            output.write(snapshot_id + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, snapshot_root / CURRENT_POINTER_NAME)
        try:
            _sync_directory(snapshot_root)
        except OSError as exc:
            raise SnapshotPointerDurabilityError(
                "snapshot pointer was replaced, but snapshot-root directory "
                f"sync failed: {exc}"
            ) from exc
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return snapshot
