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
from .observability import event, human_bytes, safe_name, safe_reason, short_id

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
            f"extraction_ms={stats.elapsed_ms:.3f} "
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


def build_snapshot_from_input(
    builder: Path,
    corpus_or_zip: Path,
    snapshot: Path,
    *,
    zip_limits: ZipExtractionLimits | None = None,
    builder_timeout_seconds: float = 600.0,
) -> subprocess.CompletedProcess[str]:
    build_id = short_id()
    started = time.perf_counter_ns()
    source = Path(corpus_or_zip)
    output = Path(snapshot)
    is_zip = source.suffix.lower() == ".zip" and not source.is_dir()
    compressed_bytes = source.stat().st_size if is_zip and source.exists() else 0
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
    try:
        result = _build_snapshot_from_input_impl(
            builder,
            source,
            output,
            zip_limits=zip_limits,
            builder_timeout_seconds=builder_timeout_seconds,
        )
        zip_match = re.search(
            r"zip_entries=(\d+) processed_files=(\d+) zip_records=(\d+) "
            r"uncompressed_bytes=(\d+) extraction_ms=([0-9.]+) "
            r"skipped_directories=(\d+) skipped_unsupported_files=(\d+)",
            result.stdout,
        )
        if zip_match:
            entries, files, _, extracted_raw, extraction_raw, directories, ignored = (
                zip_match.groups()
            )
            entries, files, extracted_bytes, directories, ignored = map(
                int, (entries, files, extracted_raw, directories, ignored)
            )
            event(
                "offline",
                "zip.validated",
                build_id=build_id,
                validation_result="accepted",
                archive_entry_count=entries,
                accepted_text_file_count=files,
                ignored_non_text_count=ignored,
                rejected_entry_count=0,
                compressed_size_bytes=compressed_bytes,
                expected_uncompressed_size_bytes=extracted_bytes,
                validation_duration_ms=float(extraction_raw),
            )
            event(
                "offline",
                "zip.extracted",
                build_id=build_id,
                extracted_text_file_count=files,
                extracted_corpus_size_bytes=extracted_bytes,
                extracted_corpus_size_human=human_bytes(extracted_bytes),
                extraction_duration_ms=float(extraction_raw),
                temporary_path_exposed=False,
                skipped_directory_count=directories,
            )
        summary = _BUILDER_SUMMARY.search(result.stderr)
        if result.returncode != 0:
            event(
                "offline",
                "build.failed",
                logging.ERROR,
                build_id=build_id,
                failed_stage="cpp_builder",
                error_category="builder_exit",
                reason="production builder returned a non-zero exit code",
                cpp_exit_code=result.returncode,
                elapsed_ms=(time.perf_counter_ns() - started) / 1_000_000,
                partial_snapshot_published=(output / "manifest.binpb").exists(),
                staging_cleanup="best_effort",
                temporary_extraction_cleanup="complete",
                status="failed",
            )
            return result
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
        manifest = SnapshotManifestProto()
        manifest.ParseFromString((output / "manifest.binpb").read_bytes())
        sizes = {
            name: (output / name).stat().st_size
            for name in ("records.binpb", "index.binpb", "manifest.binpb")
        }
        total = sum(sizes.values())
        event(
            "offline",
            "snapshot.published",
            build_id=build_id,
            publication_status="published",
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
            invalid_lines=0,
            unique_1gram_count=int(fields.get("g1") or 0),
            unique_2gram_count=int(fields.get("g2") or 0),
            unique_3gram_count=int(fields.get("g3") or 0),
            total_posting_lists=manifest.posting_count,
            total_posting_ids=int(fields.get("ids") or 0),
            records_written=manifest.searchable_record_count,
            records_size_bytes=sizes["records.binpb"],
            index_size_bytes=sizes["index.binpb"],
            manifest_size_bytes=sizes["manifest.binpb"],
            total_snapshot_size_bytes=total,
            total_snapshot_size_human=human_bytes(total),
            cpp_builder_ms=float(fields.get("seconds") or 0) * 1000,
            total_offline_ms=(time.perf_counter_ns() - started) / 1_000_000,
            snapshot_id=manifest.snapshot_id,
            temporary_extraction_cleanup="complete",
            status="success",
        )
        return result
    except Exception as error:
        event(
            "offline",
            "build.failed",
            logging.ERROR,
            build_id=build_id,
            failed_stage="offline_build",
            error_category=type(error).__name__,
            reason=safe_reason(error),
            elapsed_ms=(time.perf_counter_ns() - started) / 1_000_000,
            partial_snapshot_published=(output / "manifest.binpb").exists(),
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
