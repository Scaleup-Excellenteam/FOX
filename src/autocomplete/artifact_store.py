from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Protocol

from .snapshot_loader import load_snapshot


class ArtifactError(RuntimeError):
    """Raised when an artifact cannot be materialized safely."""


def _reject_symlinks(root: Path) -> None:
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise ArtifactError(
                        f"symbolic link is not allowed in snapshot: {entry.path}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))


class ArtifactStore(Protocol):
    def materialize_snapshot(self, snapshot_ref: str, destination: Path) -> Path: ...


class LocalArtifactStore:
    """Copy and validate an immutable local snapshot before publication."""

    def materialize_snapshot(self, snapshot_ref: str, destination: Path) -> Path:
        source_reference = Path(snapshot_ref).expanduser()
        if source_reference.is_symlink():
            raise ArtifactError(
                f"snapshot root cannot be a symbolic link: {source_reference}"
            )
        source = source_reference.resolve()
        destination = Path(destination).resolve()
        if not source.is_dir():
            raise FileNotFoundError(f"snapshot not found: {source}")
        _reject_symlinks(source)
        if not (source / "manifest.binpb").is_file():
            raise FileNotFoundError(f"snapshot not found: {source}")
        if source == destination:
            load_snapshot(source)
            return source
        if destination.exists():
            raise FileExistsError(f"destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
        )
        try:
            staged_snapshot = staging / "snapshot"
            # Preserve links during the copy so a source-tree race cannot make
            # copytree follow a newly introduced link outside the snapshot.
            shutil.copytree(
                source, staged_snapshot, symlinks=True, copy_function=shutil.copy2
            )
            _reject_symlinks(staged_snapshot)
            load_snapshot(staged_snapshot)
            staged_snapshot.replace(destination)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return destination


class GCSArtifactStore:
    """Download a complete GCS prefix, validate it, then publish locally."""

    def __init__(self, client: Any = None) -> None:
        if client is None:
            try:
                from google.cloud import storage  # type: ignore[import-untyped]

                client = storage.Client()
            except Exception as exc:
                raise RuntimeError("GCS client initialization failed") from exc
        self._client = client

    def materialize_snapshot(self, snapshot_ref: str, destination: Path) -> Path:
        if not snapshot_ref.startswith("gs://") or "/" not in snapshot_ref[5:]:
            raise ValueError("snapshot_ref must be gs://bucket/prefix")
        bucket_name, prefix = snapshot_ref[5:].split("/", 1)
        if not bucket_name or not prefix.strip("/"):
            raise ValueError("snapshot_ref must include a bucket and non-empty prefix")
        destination = Path(destination).resolve()
        if destination.exists():
            raise FileExistsError(f"destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
        )
        try:
            object_prefix = prefix.rstrip("/") + "/"
            blobs = list(self._client.list_blobs(bucket_name, prefix=object_prefix))
            if not blobs:
                raise FileNotFoundError(f"no snapshot objects at {snapshot_ref}")
            staged_relative_names: set[str] = set()
            for blob in blobs:
                if not blob.name.startswith(object_prefix):
                    raise ArtifactError(
                        f"GCS object is outside requested prefix {object_prefix!r}: "
                        f"{blob.name!r}"
                    )
                relative = blob.name[len(object_prefix) :]
                relative_path = Path(relative)
                if (
                    not relative
                    or relative_path.is_absolute()
                    or ".." in relative_path.parts
                ):
                    raise RuntimeError(f"unsafe object name in snapshot: {blob.name}")
                relative_name = relative_path.as_posix()
                if relative_name in staged_relative_names:
                    raise ArtifactError(
                        f"duplicate GCS snapshot object path: {relative_name!r}"
                    )
                staged_relative_names.add(relative_name)
                target = staging / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                blob.download_to_filename(target)
            load_snapshot(staging)
            staging.replace(destination)
            return destination
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
