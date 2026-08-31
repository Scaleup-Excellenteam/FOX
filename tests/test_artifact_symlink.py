from __future__ import annotations

import subprocess

import pytest

from autocomplete.artifact_store import ArtifactError, LocalArtifactStore


def test_local_materialization_rejects_symlink_without_copying_target(
    builder, tmp_path
):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "records.txt").write_text("to be\n", encoding="utf-8")
    source = tmp_path / "source"
    subprocess.run(
        [str(builder), str(corpus), str(source), "1024"],
        check=True,
    )
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must never be copied", encoding="utf-8")
    (source / "leaked.txt").symlink_to(outside)
    destination = tmp_path / "destination"

    with pytest.raises(ArtifactError, match="symbolic link is not allowed"):
        LocalArtifactStore().materialize_snapshot(str(source), destination)

    assert not destination.exists()
    assert not list(tmp_path.glob(".destination-*"))
    assert outside.read_text(encoding="utf-8") == "must never be copied"
    assert (source / "leaked.txt").is_symlink()
    assert not any(
        path.name == "leaked.txt"
        for path in tmp_path.rglob("leaked.txt")
        if path != source / "leaked.txt"
    )
