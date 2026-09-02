"""Deterministically serialize, chunk, and restore a snapshot directory."""

from __future__ import annotations

import os
import struct
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

_MAGIC = b"FOXSNAP1"
_COUNT = struct.Struct(">I")
_ENTRY = struct.Struct(">IQ")


class SnapshotArchiveError(ValueError):
    """A snapshot cannot be serialized or restored safely."""


def split_snapshot(snapshot_dir: Path, chunk_size: int) -> list[bytes]:
    """Return ordered chunks of a deterministic snapshot archive."""
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int):
        raise TypeError("chunk_size must be an integer")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    payload = serialize_snapshot(snapshot_dir)
    return [
        payload[offset : offset + chunk_size]
        for offset in range(0, len(payload), chunk_size)
    ]


def serialize_snapshot(snapshot_dir: Path) -> bytes:
    root = Path(snapshot_dir)
    if not root.is_dir() or root.is_symlink():
        raise SnapshotArchiveError("snapshot root must be a real directory")
    files: list[Path] = []
    for directory, directory_names, file_names in os.walk(root):
        current = Path(directory)
        for name in directory_names:
            if (current / name).is_symlink():
                raise SnapshotArchiveError("snapshot cannot contain symbolic links")
        for name in file_names:
            path = current / name
            if path.is_symlink() or not path.is_file():
                raise SnapshotArchiveError("snapshot cannot contain symbolic links")
            files.append(path)
    files.sort(key=lambda path: path.relative_to(root).as_posix())
    archive = bytearray(_MAGIC)
    archive.extend(_COUNT.pack(len(files)))
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        archive.extend(_ENTRY.pack(len(relative), len(content)))
        archive.extend(relative)
        archive.extend(content)
    return bytes(archive)


def restore_snapshot(chunks: Iterable[bytes], destination: Path) -> Path:
    """Restore ordered plaintext chunks to a new snapshot directory."""
    payload = b"".join(chunks)
    view = memoryview(payload)
    offset = len(_MAGIC)
    if len(view) < offset + _COUNT.size or bytes(view[:offset]) != _MAGIC:
        raise SnapshotArchiveError("invalid snapshot archive header")
    file_count = _COUNT.unpack(view[offset : offset + _COUNT.size])[0]
    offset += _COUNT.size
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    entries: list[tuple[PurePosixPath, bytes]] = []
    seen: set[PurePosixPath] = set()
    for _ in range(file_count):
        if offset + _ENTRY.size > len(view):
            raise SnapshotArchiveError("truncated snapshot archive")
        path_size, content_size = _ENTRY.unpack(view[offset : offset + _ENTRY.size])
        offset += _ENTRY.size
        end = offset + path_size + content_size
        if end > len(view):
            raise SnapshotArchiveError("truncated snapshot archive")
        try:
            relative = PurePosixPath(
                bytes(view[offset : offset + path_size]).decode("utf-8")
            )
        except UnicodeDecodeError as error:
            raise SnapshotArchiveError("invalid archive path encoding") from error
        offset += path_size
        content = bytes(view[offset : offset + content_size])
        offset += content_size
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise SnapshotArchiveError("unsafe path in snapshot archive")
        if relative in seen:
            raise SnapshotArchiveError("duplicate path in snapshot archive")
        seen.add(relative)
        entries.append((relative, content))
    if offset != len(view):
        raise SnapshotArchiveError("trailing data in snapshot archive")
    destination.mkdir(parents=True)
    try:
        for relative, content in entries:
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    except BaseException:
        import shutil

        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination
