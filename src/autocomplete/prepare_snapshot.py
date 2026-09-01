from __future__ import annotations

import argparse
import fcntl
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .build_snapshot import (
    BuildError,
    ZipExtractionLimits,
    ZipInputError,
    build_snapshot_from_input,
    extract_zip_corpus,
)
from .generated.autocomplete_snapshot_pb2 import SnapshotManifestProto
from .snapshot_loader import GRAM_SIZES, VERSIONS, load_snapshot, load_snapshot_manifest
from .snapshot_pointer import (
    CURRENT_POINTER_NAME,
    SnapshotPointerError,
    activate_snapshot,
    current_snapshot,
)

_INSPECTION_PATTERN = re.compile(
    r"corpus_digest_sha256=(?P<digest>[0-9a-f]{64}) "
    r"files=(?P<files>\d+) lines=(?P<lines>\d+) "
    r"accepted=(?P<accepted>\d+) skipped=(?P<skipped>\d+)\s*"
)


class PreparationError(RuntimeError):
    """Snapshot preparation failed before safe activation completed."""


class PreparationStatus(StrEnum):
    REUSED = "REUSED"
    BUILT_AND_ACTIVATED = "BUILT_AND_ACTIVATED"


@dataclass(frozen=True)
class CorpusInspection:
    corpus_digest_sha256: str
    file_count: int
    line_count: int
    searchable_record_count: int
    skipped_record_count: int


@dataclass(frozen=True)
class PreparationResult:
    status: PreparationStatus
    snapshot_id: str
    snapshot_path: Path
    corpus_digest_sha256: str


def _manifest_matches(
    manifest: SnapshotManifestProto, inspection: CorpusInspection
) -> bool:
    versions = (
        manifest.schema_version,
        manifest.normalization_version,
        manifest.index_strategy_version,
    )
    return (
        manifest.corpus_digest_sha256 == inspection.corpus_digest_sha256
        and versions == VERSIONS
        and tuple(manifest.gram_sizes) == GRAM_SIZES
    )


def inspect_corpus(
    builder: Path,
    corpus: Path,
    *,
    timeout_seconds: float = 600.0,
) -> CorpusInspection:
    if timeout_seconds <= 0:
        raise PreparationError("inspection timeout must be greater than zero seconds")
    command = [str(Path(builder)), "--inspect-corpus", str(Path(corpus))]
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise PreparationError(
            f"corpus inspection timed out after {timeout_seconds:g} seconds"
        ) from exc
    except OSError as exc:
        raise PreparationError(
            f"cannot execute C++ snapshot builder {builder}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise PreparationError(f"corpus inspection failed: {detail}")
    match = _INSPECTION_PATTERN.fullmatch(result.stdout)
    if match is None:
        raise PreparationError("corpus inspection returned malformed output")
    return CorpusInspection(
        match.group("digest"),
        int(match.group("files")),
        int(match.group("lines")),
        int(match.group("accepted")),
        int(match.group("skipped")),
    )


@contextmanager
def _prepared_corpus(
    source: Path,
    *,
    zip_limits: ZipExtractionLimits | None,
) -> Iterator[Path]:
    source = Path(source)
    if source.is_dir():
        yield source
        return
    if source.suffix.lower() != ".zip":
        raise PreparationError(
            f"input must be a corpus directory or .zip file: {source}"
        )
    with tempfile.TemporaryDirectory(prefix="fox-prepare-corpus-") as temporary:
        extracted = Path(temporary) / "corpus"
        extract_zip_corpus(source, extracted, limits=zip_limits)
        yield extracted


@contextmanager
def _preparation_lock(snapshot_root: Path) -> Iterator[None]:
    lock_path = snapshot_root / ".prepare.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _validated_manifest(snapshot: Path) -> SnapshotManifestProto:
    # Full loading is deliberately required before a snapshot can be reused or
    # activated; manifest compatibility alone does not establish integrity.
    load_snapshot(snapshot)
    return load_snapshot_manifest(snapshot)


def _valid_current(snapshot_root: Path) -> tuple[Path, SnapshotManifestProto] | None:
    try:
        current = current_snapshot(snapshot_root)
        if current is None:
            return None
        _, snapshot = current
        return snapshot, _validated_manifest(snapshot)
    except (OSError, SnapshotPointerError, ValueError):
        return None


def _build_candidate(
    builder: Path,
    corpus: Path,
    candidate: Path,
    *,
    builder_timeout_seconds: float,
) -> None:
    try:
        result = build_snapshot_from_input(
            builder,
            corpus,
            candidate,
            builder_timeout_seconds=builder_timeout_seconds,
        )
    except (BuildError, ZipInputError) as exc:
        raise PreparationError(f"snapshot build failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise PreparationError(f"snapshot build failed: {detail}")


def _publish_candidate(
    candidate: Path,
    published: Path,
    snapshot_id: str,
) -> Path:
    if not published.exists():
        candidate.replace(published)
        return published

    try:
        published_manifest = _validated_manifest(published)
    except (OSError, ValueError):
        # A corrupt directory no longer satisfies the immutable-snapshot
        # contract. Move it aside before publishing the validated replacement,
        # and restore it if publication unexpectedly fails.
        quarantine = published.parent / (f".corrupt-{snapshot_id}-{uuid.uuid4().hex}")
        published.replace(quarantine)
        try:
            candidate.replace(published)
        except BaseException:
            quarantine.replace(published)
            raise
        shutil.rmtree(quarantine, ignore_errors=True)
        return published

    if published_manifest.snapshot_id != snapshot_id:
        raise PreparationError(f"immutable snapshot identity mismatch: {published}")
    return published


def prepare_snapshot(
    builder: Path,
    corpus_or_zip: Path,
    snapshot_root: Path,
    *,
    zip_limits: ZipExtractionLimits | None = None,
    inspection_timeout_seconds: float = 600.0,
    builder_timeout_seconds: float = 600.0,
) -> PreparationResult:
    """Reuse or transactionally build, validate, publish, and activate a snapshot."""
    builder = Path(builder)
    corpus_or_zip = Path(corpus_or_zip)
    snapshot_root = Path(snapshot_root).resolve()
    snapshot_root.mkdir(parents=True, exist_ok=True)

    with _preparation_lock(snapshot_root):
        pointer = snapshot_root / CURRENT_POINTER_NAME
        if pointer.is_dir():
            raise PreparationError(
                f"snapshot pointer path is an existing directory: {pointer}; "
                "use a new snapshot root or move the legacy directory first"
            )
        with _prepared_corpus(corpus_or_zip, zip_limits=zip_limits) as corpus:
            inspection = inspect_corpus(
                builder, corpus, timeout_seconds=inspection_timeout_seconds
            )
            current = _valid_current(snapshot_root)
            if current is not None:
                current_path, current_manifest = current
                if _manifest_matches(current_manifest, inspection):
                    return PreparationResult(
                        PreparationStatus.REUSED,
                        current_manifest.snapshot_id,
                        current_path,
                        inspection.corpus_digest_sha256,
                    )

            workspace = Path(tempfile.mkdtemp(prefix=".prepare-", dir=snapshot_root))
            try:
                candidate = workspace / "snapshot"
                _build_candidate(
                    builder,
                    corpus,
                    candidate,
                    builder_timeout_seconds=builder_timeout_seconds,
                )
                try:
                    candidate_manifest = _validated_manifest(candidate)
                except (OSError, ValueError) as exc:
                    raise PreparationError(
                        f"new snapshot validation failed: {exc}"
                    ) from exc
                if not _manifest_matches(candidate_manifest, inspection):
                    raise PreparationError(
                        "new snapshot does not match the inspected corpus and versions"
                    )

                snapshot_id = candidate_manifest.snapshot_id
                published = snapshot_root / snapshot_id
                published = _publish_candidate(candidate, published, snapshot_id)

                try:
                    activated = activate_snapshot(snapshot_root, snapshot_id)
                except (OSError, SnapshotPointerError) as exc:
                    raise PreparationError(
                        f"snapshot activation failed: {exc}"
                    ) from exc
                return PreparationResult(
                    PreparationStatus.BUILT_AND_ACTIVATED,
                    snapshot_id,
                    activated,
                    inspection.corpus_digest_sha256,
                )
            finally:
                shutil.rmtree(workspace, ignore_errors=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and atomically activate an immutable FOX snapshot"
    )
    parser.add_argument("--builder", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        result = prepare_snapshot(
            arguments.builder, arguments.corpus, arguments.snapshot_root
        )
    except (OSError, PreparationError, ZipInputError) as exc:
        _build_parser().exit(1, f"error: {exc}\n")
    print(
        f"status={result.status} snapshot_id={result.snapshot_id} "
        f"snapshot_path={result.snapshot_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
