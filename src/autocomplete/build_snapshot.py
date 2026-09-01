from __future__ import annotations

import argparse
import logging
import re
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .generated.autocomplete_snapshot_pb2 import SnapshotManifestProto
from .observability import (
    event,
    get_config,
    human_bytes,
    safe_name,
    safe_reason,
    short_id,
)

SUPPORTED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


class ZipInputError(ValueError):
    """Raised when a ZIP is malformed, unsafe, or uses an unsupported entry."""


class BuildError(RuntimeError):
    """Raised when the external snapshot builder cannot complete safely."""


@dataclass(frozen=True)
class ZipExtractionStats:
    entries: int
    processed_files: int
    records: int
    skipped_directories: int
    skipped_unsupported_files: int
    elapsed_ms: float
    uncompressed_bytes: int
    archive_bytes: int


@dataclass(frozen=True)
class ZipExtractionLimits:
    """Resource ceilings for untrusted corpus archives."""

    max_entries: int = 100_000
    max_total_uncompressed_bytes: int = 2 * 1024**3
    max_entry_uncompressed_bytes: int = 200 * 1024**2
    max_compression_ratio: float = 100.0


_EXTRACTION_BLOCK_BYTES = 1024 * 1024


def _validated_relative_path(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and ":" in path.parts[0])
    ):
        raise ZipInputError(f"unsafe ZIP entry path: {name!r}")
    mode = info.external_attr >> 16
    if mode and stat.S_ISLNK(mode):
        raise ZipInputError(f"symbolic-link ZIP entry is not supported: {name!r}")
    return path


def extract_zip_corpus(
    archive_path: Path,
    destination: Path,
    *,
    limits: ZipExtractionLimits | None = None,
) -> ZipExtractionStats:
    """Safely extract supported corpus entries into an empty destination."""
    archive_path = Path(archive_path)
    destination = Path(destination)
    limits = limits or ZipExtractionLimits()
    if (
        limits.max_entries < 0
        or limits.max_total_uncompressed_bytes < 0
        or limits.max_entry_uncompressed_bytes < 0
        or limits.max_compression_ratio <= 0
    ):
        raise ZipInputError(
            "ZIP extraction limits must be non-negative and ratio positive"
        )
    started = time.perf_counter()
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ZipInputError(f"cannot open ZIP {archive_path}: {exc}") from exc
    destination_created = False
    try:
        with archive:
            infos = archive.infolist()
            if len(infos) > limits.max_entries:
                raise ZipInputError(
                    "ZIP entry count limit exceeded: "
                    f"{len(infos)} > {limits.max_entries}"
                )
            validated = [(info, _validated_relative_path(info)) for info in infos]
            file_paths = [path for info, path in validated if not info.is_dir()]
            if len(file_paths) != len(set(file_paths)):
                raise ZipInputError("ZIP contains duplicate file paths")

            declared_total = 0
            for info, relative in validated:
                if info.is_dir() or relative.suffix.lower() != ".txt":
                    continue
                if info.file_size > limits.max_entry_uncompressed_bytes:
                    raise ZipInputError(
                        "ZIP per-entry uncompressed size limit exceeded for "
                        f"{info.filename!r}: {info.file_size} > "
                        f"{limits.max_entry_uncompressed_bytes}"
                    )
                ratio = (
                    info.file_size / info.compress_size
                    if info.compress_size
                    else (float("inf") if info.file_size else 0.0)
                )
                if ratio > limits.max_compression_ratio:
                    raise ZipInputError(
                        f"ZIP compression ratio limit exceeded for {info.filename!r}: "
                        f"{ratio:.2f} > {limits.max_compression_ratio:.2f}"
                    )
                declared_total += info.file_size
                if declared_total > limits.max_total_uncompressed_bytes:
                    raise ZipInputError(
                        "ZIP total uncompressed size limit exceeded at "
                        f"{info.filename!r}: {declared_total} > "
                        f"{limits.max_total_uncompressed_bytes}"
                    )

            destination.mkdir(parents=True, exist_ok=False)
            destination_created = True
            processed = records = directories = unsupported = uncompressed_bytes = 0
            for info, relative in validated:
                if info.is_dir():
                    directories += 1
                    continue
                if relative.suffix.lower() != ".txt":
                    unsupported += 1
                    continue
                if info.flag_bits & 0x1:
                    raise ZipInputError(
                        f"encrypted ZIP entry is not supported: {info.filename!r}"
                    )
                if info.compress_type not in SUPPORTED_COMPRESSION:
                    raise ZipInputError(
                        f"unsupported ZIP compression method {info.compress_type}: "
                        f"{info.filename!r}"
                    )
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                entry_bytes = 0
                try:
                    with archive.open(info) as source, target.open("wb") as output:
                        while block := source.read(_EXTRACTION_BLOCK_BYTES):
                            entry_bytes += len(block)
                            prospective_total = uncompressed_bytes + entry_bytes
                            if entry_bytes > limits.max_entry_uncompressed_bytes:
                                raise ZipInputError(
                                    "ZIP actual per-entry size limit exceeded for "
                                    f"{info.filename!r}: {entry_bytes} > "
                                    f"{limits.max_entry_uncompressed_bytes}"
                                )
                            if prospective_total > limits.max_total_uncompressed_bytes:
                                raise ZipInputError(
                                    "ZIP actual total size limit exceeded at "
                                    f"{info.filename!r}: {prospective_total} > "
                                    f"{limits.max_total_uncompressed_bytes}"
                                )
                            output.write(block)
                except ZipInputError:
                    raise
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    raise ZipInputError(
                        f"cannot extract ZIP entry {info.filename!r}: {exc}"
                    ) from exc
                processed += 1
                uncompressed_bytes += entry_bytes
            elapsed_ms = (time.perf_counter() - started) * 1_000
            return ZipExtractionStats(
                len(infos),
                processed,
                records,
                directories,
                unsupported,
                elapsed_ms,
                uncompressed_bytes,
                archive_path.stat().st_size,
            )
    except BaseException:
        if destination_created:
            shutil.rmtree(destination, ignore_errors=True)
        raise


def _build_snapshot_from_input_impl(
    builder: Path,
    corpus_or_zip: Path,
    snapshot: Path,
    *,
    zip_limits: ZipExtractionLimits | None = None,
    builder_timeout_seconds: float = 600.0,
) -> subprocess.CompletedProcess[str]:
    """Run the production C++ builder on a directory or safely extracted ZIP."""
    builder = Path(builder)
    corpus_or_zip = Path(corpus_or_zip)
    snapshot = Path(snapshot)
    if builder_timeout_seconds <= 0:
        raise BuildError("builder timeout must be greater than zero seconds")

    def run(corpus: Path) -> subprocess.CompletedProcess[str]:
        command = [
            str(builder),
            "--corpus",
            str(corpus),
            "--output",
            str(snapshot),
        ]
        try:
            return subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=builder_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise BuildError(
                "C++ snapshot builder timed out after "
                f"{builder_timeout_seconds:g} seconds and was killed"
            ) from exc
        except OSError as exc:
            raise BuildError(
                f"cannot execute C++ snapshot builder {builder}: {exc}"
            ) from exc

    if corpus_or_zip.is_dir():
        return run(corpus_or_zip)
    if corpus_or_zip.suffix.lower() != ".zip":
        raise ZipInputError(
            f"input must be a corpus directory or .zip file: {corpus_or_zip}"
        )
    with tempfile.TemporaryDirectory(prefix="fox-corpus-") as temporary:
        extracted = Path(temporary) / "corpus"
        stats = extract_zip_corpus(corpus_or_zip, extracted, limits=zip_limits)
        result = run(extracted)
        sentence_match = re.search(r"(?:^| )sentences=(\d+)(?: |$)", result.stdout)
        record_count = int(sentence_match.group(1)) if sentence_match else 0
        ratio = (
            stats.uncompressed_bytes / stats.archive_bytes
            if stats.archive_bytes
            else 0.0
        )
        prefix = (
            "[PYTHON BUILDER] [ZIP EXTRACTION] -> "
            f"Completed archive extraction in {stats.elapsed_ms:,.2f} ms | "
            f"Files Processed: {stats.processed_files:,} | "
            f"Uncompressed Data: {stats.uncompressed_bytes / 1_000_000:,.2f} MB "
            f"(Archive Size: {stats.archive_bytes / 1_000_000:,.2f} MB, "
            f"Ratio: {ratio:,.2f}x).\n"
            f"zip_entries={stats.entries} processed_files={stats.processed_files} "
            f"zip_records={record_count} "
            f"uncompressed_bytes={stats.uncompressed_bytes} "
            f"zip_processing_ms={stats.elapsed_ms:.3f} "
            f"skipped_directories={stats.skipped_directories} "
            f"skipped_unsupported_files={stats.skipped_unsupported_files}\n"
        )
        return subprocess.CompletedProcess(
            result.args, result.returncode, prefix + result.stdout, result.stderr
        )


_BUILDER_SUMMARY = re.compile(
    r"complete files=(?P<files>\d+) lines=(?P<lines>\d+) accepted=(?P<accepted>\d+) "
    r"skipped=(?P<skipped>\d+) grams=(?P<grams>\d+)(?: grams_1=(?P<g1>\d+) "
    r"grams_2=(?P<g2>\d+) grams_3=(?P<g3>\d+))? posting_ids=(?P<ids>\d+) "
    r"elapsed_seconds=(?P<seconds>[0-9.]+)"
)


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _known_good_snapshot(path: Path) -> bool:
    if not _exists(path):
        return False
    try:
        from .snapshot_loader import load_snapshot

        load_snapshot(path)
        return True
    except Exception:
        return False


def _snapshot_metadata(
    output: Path,
) -> tuple[SnapshotManifestProto | None, dict[str, int | bool | str]]:
    """Best-effort post-build metadata that is never part of build correctness."""

    try:
        manifest = SnapshotManifestProto()
        manifest.ParseFromString((output / "manifest.binpb").read_bytes())
    except Exception:
        return None, {"snapshot_metrics_available": False}
    try:
        records_size = sum(
            (output / name).stat().st_size for name in manifest.record_files
        )
        index_size = sum(
            (output / name).stat().st_size for name in manifest.index_files
        )
        manifest_size = (output / "manifest.binpb").stat().st_size
        total_size = records_size + index_size + manifest_size
        return manifest, {
            "snapshot_metrics_available": True,
            "record_file_count": len(manifest.record_files),
            "index_file_count": len(manifest.index_files),
            "records_size_bytes": records_size,
            "index_size_bytes": index_size,
            "manifest_size_bytes": manifest_size,
            "total_snapshot_size_bytes": total_size,
            "total_snapshot_size_human": human_bytes(total_size),
        }
    except Exception:
        return manifest, {"snapshot_metrics_available": False}


def _log_successful_build(
    *,
    build_id: str,
    builder: Path,
    output: Path,
    result: subprocess.CompletedProcess[str],
    started: int,
    compressed_bytes: int,
    output_preexisting: bool,
) -> None:
    """Emit success telemetry without allowing enrichment to escape."""

    try:
        zip_match = re.search(
            r"zip_entries=(\d+) processed_files=(\d+) zip_records=(\d+) "
            r"uncompressed_bytes=(\d+) zip_processing_ms=([0-9.]+) "
            r"skipped_directories=(\d+) skipped_unsupported_files=(\d+)",
            result.stdout,
        )
        if zip_match:
            entries, files, _, extracted_raw, processing_raw, directories, ignored = (
                zip_match.groups()
            )
            event(
                "offline",
                "zip.processed",
                build_id=build_id,
                archive_entry_count=int(entries),
                accepted_text_file_count=int(files),
                ignored_non_text_count=int(ignored),
                skipped_directory_count=int(directories),
                compressed_size_bytes=compressed_bytes,
                extracted_corpus_size_bytes=int(extracted_raw),
                validation_and_extraction_ms=float(processing_raw),
                temporary_path_exposed=False,
                status="success",
            )

        summary = _BUILDER_SUMMARY.search(result.stderr)
        fields = summary.groupdict() if summary else {}
        event(
            "offline",
            "builder.completed",
            build_id=build_id,
            builder_identity=safe_name(Path(builder)),
            cpp_exit_code=result.returncode,
            text_files_processed=int(fields.get("files") or 0),
            physical_lines_processed=int(fields.get("lines") or 0),
            retained_sentences=int(fields.get("accepted") or 0),
            skipped_normalized_empty_lines=int(fields.get("skipped") or 0),
            cpp_builder_ms=float(fields.get("seconds") or 0) * 1000,
            status="success",
        )

        manifest, size_fields = _snapshot_metadata(output)
        published = not output_preexisting and _exists(output)
        if manifest is not None:
            event(
                "offline",
                "snapshot.published",
                build_id=build_id,
                snapshot_published_by_invocation=published,
                previous_known_good_snapshot_remains_available=False,
                snapshot_id=manifest.snapshot_id,
                corpus_digest=manifest.corpus_digest_sha256,
                index_digest=manifest.index_digest_sha256,
                records_written=manifest.searchable_record_count,
                posting_lists_written=manifest.posting_count,
                total_posting_ids=int(fields.get("ids") or 0),
                unique_1gram_count=int(fields.get("g1") or 0),
                unique_2gram_count=int(fields.get("g2") or 0),
                unique_3gram_count=int(fields.get("g3") or 0),
                snapshot_destination=safe_name(output),
            )
        event(
            "offline",
            "build.completed",
            build_id=build_id,
            archive_entries=int(zip_match.group(1)) if zip_match else 0,
            text_files_processed=int(fields.get("files") or 0),
            compressed_zip_size_bytes=compressed_bytes,
            physical_lines_processed=int(fields.get("lines") or 0),
            retained_sentences=int(fields.get("accepted") or 0),
            skipped_normalized_empty_lines=int(fields.get("skipped") or 0),
            unique_1gram_count=int(fields.get("g1") or 0),
            unique_2gram_count=int(fields.get("g2") or 0),
            unique_3gram_count=int(fields.get("g3") or 0),
            total_posting_lists=manifest.posting_count if manifest is not None else 0,
            total_posting_ids=int(fields.get("ids") or 0),
            records_written=(
                manifest.searchable_record_count if manifest is not None else 0
            ),
            cpp_builder_ms=float(fields.get("seconds") or 0) * 1000,
            offline_compute_ms=(time.perf_counter_ns() - started) / 1_000_000,
            snapshot_id=manifest.snapshot_id if manifest is not None else "unavailable",
            snapshot_published_by_invocation=published,
            previous_known_good_snapshot_remains_available=False,
            temporary_extraction_cleanup="complete",
            **size_fields,
            status="success",
        )
    except Exception:
        event(
            "offline",
            "build.completed",
            build_id=build_id,
            snapshot_metrics_available=False,
            snapshot_published_by_invocation=(
                not output_preexisting and _exists(output)
            ),
            previous_known_good_snapshot_remains_available=False,
            status="success",
        )


def build_snapshot_from_input(
    builder: Path,
    corpus_or_zip: Path,
    snapshot: Path,
    *,
    zip_limits: ZipExtractionLimits | None = None,
    builder_timeout_seconds: float = 600.0,
) -> subprocess.CompletedProcess[str]:
    config = get_config()
    if not config.enables(logging.CRITICAL):
        return _build_snapshot_from_input_impl(
            builder,
            corpus_or_zip,
            snapshot,
            zip_limits=zip_limits,
            builder_timeout_seconds=builder_timeout_seconds,
        )

    build_id = short_id()
    started = time.perf_counter_ns()
    source = Path(corpus_or_zip)
    output = Path(snapshot)
    output_preexisting = _exists(output)
    try:
        previous_known_good = (
            _known_good_snapshot(output) if output_preexisting else False
        )
    except Exception:
        previous_known_good = False
    is_zip = source.suffix.lower() == ".zip" and not source.is_dir()
    try:
        compressed_bytes = _file_size(source) if is_zip else 0
    except Exception:
        compressed_bytes = 0
    try:
        event(
            "offline",
            "build.started",
            build_id=build_id,
            input_type="ZIP" if is_zip else "directory",
            input_name=safe_name(source),
            compressed_zip_size_bytes=compressed_bytes,
            compressed_zip_size_human=human_bytes(compressed_bytes),
            snapshot_destination=safe_name(output),
            snapshot_format_version=1,
            normalization_version=1,
            index_version=1,
            gram_sizes="1,2,3",
            start_timestamp_utc=__import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat(),
        )
    except Exception:
        pass
    try:
        result = _build_snapshot_from_input_impl(
            builder,
            source,
            output,
            zip_limits=zip_limits,
            builder_timeout_seconds=builder_timeout_seconds,
        )
        if result.returncode != 0:
            event(
                "offline",
                "build.failed",
                logging.ERROR,
                build_id=build_id,
                failed_stage="cpp_builder",
                error_category="builder_exit",
                reason_code="builder_nonzero_exit",
                cpp_exit_code=result.returncode,
                offline_compute_ms=(time.perf_counter_ns() - started) / 1_000_000,
                snapshot_published_by_invocation=(
                    not output_preexisting and _exists(output)
                ),
                previous_known_good_snapshot_remains_available=(
                    previous_known_good and _exists(output)
                ),
                staging_cleanup="best_effort",
                temporary_extraction_cleanup="complete",
                status="failed",
            )
            return result
        try:
            _log_successful_build(
                build_id=build_id,
                builder=Path(builder),
                output=output,
                result=result,
                started=started,
                compressed_bytes=compressed_bytes,
                output_preexisting=output_preexisting,
            )
        except Exception:
            # Enrichment is deliberately outside the build's success contract.
            pass
        return result
    except Exception as error:
        event(
            "offline",
            "build.failed",
            logging.ERROR,
            build_id=build_id,
            failed_stage="offline_build",
            error_category=type(error).__name__,
            reason_code=safe_reason(error),
            offline_compute_ms=(time.perf_counter_ns() - started) / 1_000_000,
            snapshot_published_by_invocation=(
                not output_preexisting and _exists(output)
            ),
            previous_known_good_snapshot_remains_available=(
                previous_known_good and _exists(output)
            ),
            staging_cleanup="best_effort",
            temporary_extraction_cleanup="best_effort",
            status="failed",
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a FOX snapshot from a corpus directory or ZIP"
    )
    parser.add_argument("builder", type=Path, help="path to autocomplete_builder")
    parser.add_argument("corpus_or_zip", type=Path)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    try:
        result = build_snapshot_from_input(
            args.builder, args.corpus_or_zip, args.snapshot
        )
    except (ZipInputError, BuildError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=__import__("sys").stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
