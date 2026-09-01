from __future__ import annotations

import json
import struct
import subprocess
import zipfile
from pathlib import Path

import pytest

import autocomplete.build_snapshot as build_module
import autocomplete.snapshot_loader as loader_module
from autocomplete.build_snapshot import build_snapshot_from_input
from autocomplete.generated.autocomplete_snapshot_pb2 import SnapshotManifestProto
from autocomplete.observability import reset_for_tests
from autocomplete.snapshot_loader import load_snapshot


@pytest.fixture(autouse=True)
def isolated_logging(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DIRECTORY", str(tmp_path / "logs"))
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.delenv("DETAILED_PROFILING", raising=False)
    reset_for_tests()
    yield tmp_path / "logs"
    reset_for_tests()


def _corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir(parents=True)
    (corpus / "sentences.txt").write_text(
        "hello world\nhelp me\nunrelated sentence\n",
        encoding="utf-8",
    )
    return corpus


def _event(directory: Path, kind: str, name: str) -> dict:
    values = [
        json.loads(line)
        for line in (directory / f"{kind}.log").read_text().splitlines()
    ]
    return [value for value in values if value["event"] == name][-1]


def _split_after_first_frame(data: bytes) -> tuple[bytes, bytes]:
    first_length = struct.unpack_from(">I", data)[0]
    boundary = 4 + first_length
    assert boundary < len(data)
    return data[:boundary], data[boundary:]


def _shard_snapshot(snapshot: Path) -> SnapshotManifestProto:
    manifest = SnapshotManifestProto()
    manifest.ParseFromString((snapshot / "manifest.binpb").read_bytes())
    record_parts = _split_after_first_frame((snapshot / "records.binpb").read_bytes())
    index_parts = _split_after_first_frame((snapshot / "index.binpb").read_bytes())
    record_names = ["records-000.binpb", "records-001.binpb"]
    index_names = ["index-000.binpb", "index-001.binpb"]
    for name, payload in zip(record_names, record_parts, strict=True):
        (snapshot / name).write_bytes(payload)
    for name, payload in zip(index_names, index_parts, strict=True):
        (snapshot / name).write_bytes(payload)
    (snapshot / "records.binpb").unlink()
    (snapshot / "index.binpb").unlink()
    manifest.record_files[:] = record_names
    manifest.index_files[:] = index_names
    (snapshot / "manifest.binpb").write_bytes(
        manifest.SerializeToString(deterministic=True)
    )
    return manifest


def test_successful_build_survives_all_optional_enrichment_failure(
    monkeypatch, builder, tmp_path
):
    output = tmp_path / "snapshot"
    monkeypatch.setattr(
        build_module,
        "_log_successful_build",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("telemetry")),
    )

    result = build_snapshot_from_input(builder, _corpus(tmp_path), output)

    assert result.returncode == 0
    assert (output / "manifest.binpb").exists()
    records, _ = load_snapshot(output)
    assert len(records) == 3


def test_off_build_and_load_skip_observability_work(
    monkeypatch, builder, tmp_path, isolated_logging
):
    monkeypatch.setenv("LOG_LEVEL", "OFF")
    reset_for_tests()

    def forbidden(*args, **kwargs):
        raise AssertionError("OFF executed observability work")

    monkeypatch.setattr(build_module, "short_id", forbidden)
    output = tmp_path / "snapshot"
    result = build_snapshot_from_input(builder, _corpus(tmp_path), output)
    monkeypatch.setattr(loader_module.time, "perf_counter_ns", forbidden)
    records, _ = load_snapshot(output)

    assert result.returncode == 0
    assert len(records) == 3
    assert not isolated_logging.exists()


def test_successful_build_survives_manifest_metrics_parser_failure(
    monkeypatch, builder, tmp_path
):
    class BrokenManifest:
        def ParseFromString(self, payload):
            raise RuntimeError("telemetry parser")

    monkeypatch.setattr(build_module, "SnapshotManifestProto", BrokenManifest)
    output = tmp_path / "snapshot"

    result = build_snapshot_from_input(builder, _corpus(tmp_path), output)

    assert result.returncode == 0
    assert (output / "manifest.binpb").exists()


def test_zip_stat_and_formatting_failures_do_not_change_success(
    monkeypatch, builder, tmp_path
):
    archive = tmp_path / "corpus.zip"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("sentences.txt", "hello world\n")
    monkeypatch.setattr(
        build_module,
        "_file_size",
        lambda path: (_ for _ in ()).throw(OSError("stat")),
    )
    monkeypatch.setattr(
        build_module,
        "human_bytes",
        lambda value: (_ for _ in ()).throw(ValueError("formatting")),
    )

    result = build_snapshot_from_input(builder, archive, tmp_path / "snapshot")

    assert result.returncode == 0


def test_failed_rebuild_reports_previous_known_good_snapshot(
    builder, tmp_path, isolated_logging
):
    output = tmp_path / "snapshot"
    first = build_snapshot_from_input(builder, _corpus(tmp_path), output)
    second = build_snapshot_from_input(builder, tmp_path / "corpus", output)

    assert first.returncode == 0
    assert second.returncode != 0
    value = _event(isolated_logging, "offline", "build.failed")
    assert value["snapshot_published_by_invocation"] is False
    assert value["previous_known_good_snapshot_remains_available"] is True
    assert load_snapshot(output)[0]


def test_loader_reuses_single_manifest_parse(builder, tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    subprocess.run(
        [str(builder), "--corpus", str(_corpus(tmp_path)), "--output", str(snapshot)],
        check=True,
    )
    original = loader_module.load_snapshot_manifest
    calls = 0

    def counted(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(loader_module, "load_snapshot_manifest", counted)
    records, _ = load_snapshot(snapshot)
    assert records
    assert calls == 1


def test_loader_success_survives_size_enrichment_failure(
    builder, tmp_path, monkeypatch, isolated_logging
):
    snapshot = tmp_path / "snapshot"
    subprocess.run(
        [str(builder), "--corpus", str(_corpus(tmp_path)), "--output", str(snapshot)],
        check=True,
    )
    monkeypatch.setattr(
        loader_module,
        "_snapshot_size_fields",
        lambda *args: (_ for _ in ()).throw(OSError("stat")),
    )

    records, _ = load_snapshot(snapshot)

    assert len(records) == 3
    value = _event(isolated_logging, "runtime", "snapshot.ready")
    assert value["size_metrics_available"] is False


def test_sharded_manifest_sizes_are_aggregated_for_load_and_build_metadata(
    builder, tmp_path, isolated_logging
):
    snapshot = tmp_path / "snapshot"
    subprocess.run(
        [str(builder), "--corpus", str(_corpus(tmp_path)), "--output", str(snapshot)],
        check=True,
    )
    manifest = _shard_snapshot(snapshot)
    expected_records = sum(
        (snapshot / name).stat().st_size for name in manifest.record_files
    )
    expected_index = sum(
        (snapshot / name).stat().st_size for name in manifest.index_files
    )

    records, _ = load_snapshot(snapshot)
    parsed, build_fields = build_module._snapshot_metadata(snapshot)
    load_value = _event(isolated_logging, "runtime", "snapshot.ready")

    assert len(records) == 3
    assert parsed is not None
    assert load_value["record_file_count"] == 2
    assert load_value["index_file_count"] == 2
    assert load_value["records_size_bytes"] == expected_records
    assert load_value["index_size_bytes"] == expected_index
    assert build_fields["records_size_bytes"] == expected_records
    assert build_fields["index_size_bytes"] == expected_index
    assert "integrity_validation_ms" not in load_value
    assert load_value["load_unaccounted_ms"] >= 0
