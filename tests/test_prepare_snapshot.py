from __future__ import annotations

import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import autocomplete.prepare_snapshot as prepare_module
import autocomplete.snapshot_pointer as pointer_module
from autocomplete.generated.autocomplete_snapshot_pb2 import SnapshotManifestProto
from autocomplete.prepare_snapshot import (
    PreparationError,
    PreparationStatus,
    prepare_snapshot,
)
from autocomplete.search_engine import SearchEngine
from autocomplete.snapshot_loader import load_snapshot, load_snapshot_manifest
from autocomplete.snapshot_pointer import (
    SnapshotPointerDurabilityError,
    activate_snapshot,
    current_snapshot,
)


def _write_corpus(root: Path, text: str = "Hello world\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sentences.txt").write_text(text, encoding="utf-8")
    return root


def _write_zip(path: Path, entries: list[tuple[str, str]], compression: int) -> None:
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, contents in entries:
            archive.writestr(name, contents)


def _pointer_id(snapshot_root: Path) -> str:
    return (snapshot_root / "current").read_text(encoding="ascii").strip()


def _snapshot_directories(snapshot_root: Path) -> set[str]:
    return {
        path.name
        for path in snapshot_root.iterdir()
        if path.is_dir() and len(path.name) == 64
    }


def test_first_prepare_builds_and_second_prepare_reuses(builder, tmp_path, monkeypatch):
    corpus = _write_corpus(tmp_path / "corpus")
    snapshot_root = tmp_path / "snapshots"

    first = prepare_snapshot(builder, corpus, snapshot_root)
    assert first.status is PreparationStatus.BUILT_AND_ACTIVATED
    assert first.snapshot_path == snapshot_root / first.snapshot_id
    assert _pointer_id(snapshot_root) == first.snapshot_id
    load_snapshot(first.snapshot_path)

    monkeypatch.setattr(
        prepare_module,
        "_build_candidate",
        lambda *args, **kwargs: pytest.fail("unchanged corpus was rebuilt"),
    )
    second = prepare_snapshot(builder, corpus, snapshot_root)

    assert second.status is PreparationStatus.REUSED
    assert second.snapshot_id == first.snapshot_id
    assert _snapshot_directories(snapshot_root) == {first.snapshot_id}


def test_legacy_current_directory_is_preserved_and_reported(builder, tmp_path):
    corpus = _write_corpus(tmp_path / "corpus")
    snapshot_root = tmp_path / "snapshots"
    legacy = snapshot_root / "current"
    legacy.mkdir(parents=True)
    marker = legacy / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(PreparationError, match="existing directory"):
        prepare_snapshot(builder, corpus, snapshot_root)

    assert marker.read_text(encoding="utf-8") == "preserve"
    assert not _snapshot_directories(snapshot_root)


def test_changed_retained_directory_corpus_rebuilds(builder, tmp_path):
    corpus = _write_corpus(tmp_path / "corpus")
    snapshot_root = tmp_path / "snapshots"
    first = prepare_snapshot(builder, corpus, snapshot_root)

    (corpus / "sentences.txt").write_text("Changed sentence\n", encoding="utf-8")
    second = prepare_snapshot(builder, corpus, snapshot_root)

    assert second.status is PreparationStatus.BUILT_AND_ACTIVATED
    assert second.snapshot_id != first.snapshot_id
    assert _pointer_id(snapshot_root) == second.snapshot_id
    assert _snapshot_directories(snapshot_root) == {
        first.snapshot_id,
        second.snapshot_id,
    }


def test_repacked_zip_with_same_semantics_reuses_snapshot(
    builder, tmp_path, monkeypatch
):
    archive = tmp_path / "corpus.zip"
    entries = [("a.txt", "Alpha\n"), ("nested/b.txt", "Beta\n")]
    _write_zip(archive, entries, zipfile.ZIP_DEFLATED)
    snapshot_root = tmp_path / "snapshots"
    first = prepare_snapshot(builder, archive, snapshot_root)

    _write_zip(archive, list(reversed(entries)), zipfile.ZIP_STORED)
    monkeypatch.setattr(
        prepare_module,
        "_build_candidate",
        lambda *args, **kwargs: pytest.fail("repacked ZIP was rebuilt"),
    )
    second = prepare_snapshot(builder, archive, snapshot_root)

    assert second.status is PreparationStatus.REUSED
    assert second.snapshot_id == first.snapshot_id


def test_path_rename_with_same_contents_rebuilds(builder, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "a.txt"
    source.write_text("Same contents\n", encoding="utf-8")
    snapshot_root = tmp_path / "snapshots"
    first = prepare_snapshot(builder, corpus, snapshot_root)

    source.rename(corpus / "renamed.txt")
    second = prepare_snapshot(builder, corpus, snapshot_root)

    assert second.status is PreparationStatus.BUILT_AND_ACTIVATED
    assert second.snapshot_id != first.snapshot_id


def test_failed_zip_extraction_leaves_previous_snapshot_active(builder, tmp_path):
    corpus = _write_corpus(tmp_path / "corpus")
    snapshot_root = tmp_path / "snapshots"
    first = prepare_snapshot(builder, corpus, snapshot_root)
    invalid_zip = tmp_path / "invalid.zip"
    invalid_zip.write_bytes(b"not a ZIP")

    with pytest.raises(ValueError, match="cannot open ZIP"):
        prepare_snapshot(builder, invalid_zip, snapshot_root)

    assert _pointer_id(snapshot_root) == first.snapshot_id
    load_snapshot(first.snapshot_path)


def test_failed_builder_leaves_previous_snapshot_active(builder, tmp_path, monkeypatch):
    corpus = _write_corpus(tmp_path / "corpus")
    snapshot_root = tmp_path / "snapshots"
    first = prepare_snapshot(builder, corpus, snapshot_root)
    (corpus / "sentences.txt").write_text("New contents\n", encoding="utf-8")

    def fail_build(*args, **kwargs):
        raise PreparationError("snapshot build failed: deliberate test failure")

    monkeypatch.setattr(prepare_module, "_build_candidate", fail_build)
    with pytest.raises(PreparationError, match="deliberate test failure"):
        prepare_snapshot(builder, corpus, snapshot_root)

    assert _pointer_id(snapshot_root) == first.snapshot_id
    assert not list(snapshot_root.glob(".prepare-*"))


def test_failed_corpus_inspection_leaves_previous_snapshot_active(
    builder, tmp_path, monkeypatch
):
    corpus = _write_corpus(tmp_path / "corpus")
    snapshot_root = tmp_path / "snapshots"
    first = prepare_snapshot(builder, corpus, snapshot_root)

    monkeypatch.setattr(
        prepare_module,
        "inspect_corpus",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PreparationError("deliberate inspection failure")
        ),
    )
    with pytest.raises(PreparationError, match="inspection failure"):
        prepare_snapshot(builder, corpus, snapshot_root)

    assert _pointer_id(snapshot_root) == first.snapshot_id


def test_invalid_new_snapshot_is_not_activated(builder, tmp_path, monkeypatch):
    corpus = _write_corpus(tmp_path / "corpus")
    snapshot_root = tmp_path / "snapshots"
    first = prepare_snapshot(builder, corpus, snapshot_root)
    (corpus / "sentences.txt").write_text("New contents\n", encoding="utf-8")
    real_load_snapshot = prepare_module.load_snapshot

    def reject_candidate(path):
        if Path(path).parent.name.startswith(".prepare-"):
            raise ValueError("deliberately invalid candidate")
        return real_load_snapshot(path)

    monkeypatch.setattr(prepare_module, "load_snapshot", reject_candidate)
    with pytest.raises(PreparationError, match="new snapshot validation failed"):
        prepare_snapshot(builder, corpus, snapshot_root)

    assert _pointer_id(snapshot_root) == first.snapshot_id
    assert not list(snapshot_root.glob(".prepare-*"))


def test_corrupt_current_snapshot_is_rebuilt_not_reused(builder, tmp_path):
    corpus = _write_corpus(tmp_path / "corpus")
    snapshot_root = tmp_path / "snapshots"
    first = prepare_snapshot(builder, corpus, snapshot_root)
    shard = first.snapshot_path / "index.binpb"
    shard.write_bytes(shard.read_bytes()[:-1])

    replacement = prepare_snapshot(builder, corpus, snapshot_root)

    assert replacement.status is PreparationStatus.BUILT_AND_ACTIVATED
    assert replacement.snapshot_id == first.snapshot_id
    assert _pointer_id(snapshot_root) == first.snapshot_id
    load_snapshot(replacement.snapshot_path)
    assert not list(snapshot_root.glob(".corrupt-*"))


def test_version_mismatch_forces_rebuild(builder, tmp_path):
    corpus = _write_corpus(tmp_path / "corpus")
    snapshot_root = tmp_path / "snapshots"
    first = prepare_snapshot(builder, corpus, snapshot_root)
    manifest_path = first.snapshot_path / "manifest.binpb"
    manifest = SnapshotManifestProto()
    manifest.ParseFromString(manifest_path.read_bytes())
    manifest.normalization_version = 99
    manifest_path.write_bytes(manifest.SerializeToString(deterministic=True))

    replacement = prepare_snapshot(builder, corpus, snapshot_root)

    assert replacement.status is PreparationStatus.BUILT_AND_ACTIVATED
    assert load_snapshot_manifest(replacement.snapshot_path).normalization_version == 1


def test_matching_immutable_snapshot_is_activated_without_duplication(
    builder, tmp_path
):
    corpus = _write_corpus(tmp_path / "corpus", "Corpus A\n")
    snapshot_root = tmp_path / "snapshots"
    first = prepare_snapshot(builder, corpus, snapshot_root)
    (corpus / "sentences.txt").write_text("Corpus B\n", encoding="utf-8")
    second = prepare_snapshot(builder, corpus, snapshot_root)
    (corpus / "sentences.txt").write_text("Corpus A\n", encoding="utf-8")

    returned = prepare_snapshot(builder, corpus, snapshot_root)

    assert returned.snapshot_id == first.snapshot_id
    assert _pointer_id(snapshot_root) == first.snapshot_id
    assert _snapshot_directories(snapshot_root) == {
        first.snapshot_id,
        second.snapshot_id,
    }


def test_activation_failure_preserves_previous_pointer(builder, tmp_path, monkeypatch):
    corpus = _write_corpus(tmp_path / "corpus", "Corpus A\n")
    snapshot_root = tmp_path / "snapshots"
    first = prepare_snapshot(builder, corpus, snapshot_root)
    (corpus / "sentences.txt").write_text("Corpus B\n", encoding="utf-8")

    def fail_activation(*args, **kwargs):
        raise OSError("deliberate activation failure")

    monkeypatch.setattr(prepare_module, "activate_snapshot", fail_activation)
    with pytest.raises(PreparationError, match="activation failed"):
        prepare_snapshot(builder, corpus, snapshot_root)

    assert _pointer_id(snapshot_root) == first.snapshot_id
    load_snapshot(first.snapshot_path)


def test_pointer_replacement_failure_is_atomic_and_cleans_temporary(
    tmp_path, monkeypatch
):
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    first_id, second_id = "a" * 64, "b" * 64
    (snapshot_root / first_id).mkdir()
    (snapshot_root / second_id).mkdir()
    activate_snapshot(snapshot_root, first_id)

    monkeypatch.setattr(
        pointer_module.os,
        "replace",
        lambda *args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    with pytest.raises(OSError, match="replace failed"):
        activate_snapshot(snapshot_root, second_id)

    assert current_snapshot(snapshot_root) == (
        first_id,
        snapshot_root / first_id,
    )
    assert not list(snapshot_root.glob(".current-*"))


def test_pointer_activation_syncs_directory_after_atomic_replace(tmp_path, monkeypatch):
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    snapshot_id = "a" * 64
    (snapshot_root / snapshot_id).mkdir()
    events = []
    real_replace = pointer_module.os.replace

    def replace(source, destination):
        events.append("replace")
        real_replace(source, destination)

    def sync_directory(directory):
        assert _pointer_id(snapshot_root) == snapshot_id
        events.append(("sync-directory", directory))

    monkeypatch.setattr(pointer_module.os, "replace", replace)
    monkeypatch.setattr(pointer_module, "_sync_directory", sync_directory)

    activate_snapshot(snapshot_root, snapshot_id)

    assert events == ["replace", ("sync-directory", snapshot_root)]


def test_directory_sync_failure_reports_durability_error_without_rollback(
    tmp_path, monkeypatch
):
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    first_id, second_id = "a" * 64, "b" * 64
    (snapshot_root / first_id).mkdir()
    (snapshot_root / second_id).mkdir()
    activate_snapshot(snapshot_root, first_id)

    def fail_sync(directory):
        raise OSError("deliberate directory sync failure")

    monkeypatch.setattr(pointer_module, "_sync_directory", fail_sync)
    with pytest.raises(SnapshotPointerDurabilityError, match="was replaced"):
        activate_snapshot(snapshot_root, second_id)

    assert _pointer_id(snapshot_root) == second_id
    assert not list(snapshot_root.glob(".current-*"))


def test_directory_sync_always_closes_descriptor(tmp_path, monkeypatch):
    events = []
    descriptor = 73

    monkeypatch.setattr(
        pointer_module.os,
        "open",
        lambda path, flags: events.append(("open", path, flags)) or descriptor,
    )

    def fail_fsync(value):
        events.append(("fsync", value))
        raise OSError("deliberate fsync failure")

    monkeypatch.setattr(pointer_module.os, "fsync", fail_fsync)
    monkeypatch.setattr(
        pointer_module.os,
        "close",
        lambda value: events.append(("close", value)),
    )

    with pytest.raises(OSError, match="deliberate fsync failure"):
        pointer_module._sync_directory(tmp_path)

    assert events == [
        ("open", tmp_path, pointer_module.os.O_RDONLY | pointer_module.os.O_DIRECTORY),
        ("fsync", descriptor),
        ("close", descriptor),
    ]


def test_concurrent_prepare_builds_once_and_reuses(builder, tmp_path):
    corpus = _write_corpus(tmp_path / "corpus")
    snapshot_root = tmp_path / "snapshots"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(prepare_snapshot, builder, corpus, snapshot_root)
            for _ in range(2)
        ]
        results = [future.result(timeout=20) for future in futures]

    assert {result.status for result in results} == {
        PreparationStatus.REUSED,
        PreparationStatus.BUILT_AND_ACTIVATED,
    }
    assert len({result.snapshot_id for result in results}) == 1
    assert len(_snapshot_directories(snapshot_root)) == 1


def test_prepare_cli_reports_result(builder, tmp_path, capsys):
    corpus = _write_corpus(tmp_path / "corpus")
    snapshot_root = tmp_path / "snapshots"

    status = prepare_module.main(
        [
            "--builder",
            str(builder),
            "--corpus",
            str(corpus),
            "--snapshot-root",
            str(snapshot_root),
        ]
    )

    assert status == 0
    output = capsys.readouterr().out
    assert "status=BUILT_AND_ACTIVATED" in output
    assert f"snapshot_id={_pointer_id(snapshot_root)}" in output


def test_search_does_not_call_preparation(monkeypatch):
    monkeypatch.setattr(
        prepare_module,
        "inspect_corpus",
        lambda *args, **kwargs: pytest.fail("query performed corpus inspection"),
    )

    class EmptyIndex:
        def get_candidate_ids(self, normalized_query):
            return []

        def iter_candidate_ids(self, normalized_query):
            return iter(())

    assert SearchEngine({}, EmptyIndex()).search("query") == []
