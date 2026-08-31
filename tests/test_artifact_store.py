import shutil
import subprocess

import pytest

from autocomplete.artifact_store import (
    ArtifactError,
    GCSArtifactStore,
    LocalArtifactStore,
)
from autocomplete.snapshot_loader import SnapshotError, load_snapshot


def make_snapshot(builder, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "records.txt").write_text("to be\nhello world\n", encoding="utf-8")
    snapshot = tmp_path / "source"
    subprocess.run(
        [str(builder), "--corpus", str(corpus), "--output", str(snapshot)], check=True
    )
    return snapshot


def test_local_copy_identity_and_runtime_use(builder, tmp_path):
    source = make_snapshot(builder, tmp_path)
    store = LocalArtifactStore()
    destination = store.materialize_snapshot(str(source), tmp_path / "destination")
    records, index = load_snapshot(destination)
    assert [
        (identifier, record.original) for identifier, record in records.items()
    ] == [
        (1, "to be"),
        (2, "hello world"),
    ]
    assert index.get_candidate_ids("to be") == [1]
    assert store.materialize_snapshot(str(destination), destination) == destination


def test_local_missing_corrupt_and_no_overwrite(builder, tmp_path):
    store = LocalArtifactStore()
    with pytest.raises(FileNotFoundError):
        store.materialize_snapshot("missing", tmp_path / "d")
    source = make_snapshot(builder, tmp_path)
    destination = tmp_path / "d"
    destination.mkdir()
    with pytest.raises(FileExistsError):
        store.materialize_snapshot(str(source), destination)
    corrupt = tmp_path / "corrupt"
    shutil.copytree(source, corrupt)
    (corrupt / "manifest.binpb").write_bytes(b"bad")
    with pytest.raises(SnapshotError):
        store.materialize_snapshot(str(corrupt), tmp_path / "unused")
    assert not (tmp_path / "unused").exists()


class EmptyClient:
    def list_blobs(self, *args, **kwargs):
        return []


def test_gcs_reference_and_early_failure(tmp_path):
    store = GCSArtifactStore(EmptyClient())
    with pytest.raises(ValueError):
        store.materialize_snapshot("http://bad", tmp_path / "d")
    with pytest.raises(ValueError):
        store.materialize_snapshot("gs://bucket/", tmp_path / "d")
    with pytest.raises(FileNotFoundError):
        store.materialize_snapshot("gs://bucket/prefix", tmp_path / "d")
    assert not (tmp_path / "d").exists()


class FileBlob:
    def __init__(self, name, source):
        self.name = name
        self._source = source

    def download_to_filename(self, target):
        shutil.copy2(self._source, target)


class SnapshotClient:
    def __init__(self, source):
        self._source = source

    def list_blobs(self, bucket, prefix):
        assert bucket == "bucket"
        return [
            FileBlob(prefix + path.name, path)
            for path in sorted(self._source.iterdir())
        ]


def test_gcs_materializes_valid_snapshot_once_for_local_runtime(builder, tmp_path):
    source = make_snapshot(builder, tmp_path)
    destination = GCSArtifactStore(SnapshotClient(source)).materialize_snapshot(
        "gs://bucket/snapshots/id", tmp_path / "gcs-cache"
    )
    records, index = load_snapshot(destination)
    assert records[1].original == "to be"
    assert index.get_candidate_ids("to be") == [1]


class ListingBlob:
    def __init__(self, name):
        self.name = name
        self.downloaded = 0

    def download_to_filename(self, target):
        self.downloaded += 1
        target.write_bytes(b"not needed for validation test")


class ListingClient:
    def __init__(self, blobs):
        self.blobs = blobs

    def list_blobs(self, bucket, prefix):
        assert bucket == "bucket"
        assert prefix == "snapshots/id/"
        return self.blobs


def test_gcs_rejects_blob_outside_requested_prefix(tmp_path):
    blob = ListingBlob("snapshots/other/manifest.binpb")
    destination = tmp_path / "destination"
    with pytest.raises(ArtifactError, match="outside requested prefix"):
        GCSArtifactStore(ListingClient([blob])).materialize_snapshot(
            "gs://bucket/snapshots/id", destination
        )
    assert blob.downloaded == 0
    assert not destination.exists()
    assert not list(tmp_path.glob(".destination-*"))


def test_gcs_rejects_duplicate_relative_object_names(tmp_path):
    first = ListingBlob("snapshots/id/manifest.binpb")
    second = ListingBlob("snapshots/id/manifest.binpb")
    destination = tmp_path / "destination"
    with pytest.raises(ArtifactError, match="duplicate GCS snapshot object path"):
        GCSArtifactStore(ListingClient([first, second])).materialize_snapshot(
            "gs://bucket/snapshots/id", destination
        )
    assert first.downloaded == 1
    assert second.downloaded == 0
    assert not destination.exists()
    assert not list(tmp_path.glob(".destination-*"))
