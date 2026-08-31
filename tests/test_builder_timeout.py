from __future__ import annotations

import subprocess
import time
import zipfile
from pathlib import Path

import pytest

import autocomplete.build_snapshot as build_snapshot
from autocomplete.build_snapshot import BuildError, build_snapshot_from_input


def test_builder_timeout_kills_process_and_cleans_extraction(monkeypatch, tmp_path):
    archive = tmp_path / "corpus.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as value:
        value.writestr("corpus.txt", "one sentence\n")

    slow_builder = tmp_path / "slow_builder.py"
    slow_builder.write_text(
        "#!/usr/bin/env python3\n"
        "import time\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    slow_builder.chmod(0o755)

    created_temporary_directories = []
    real_temporary_directory = build_snapshot.tempfile.TemporaryDirectory

    def tracked_temporary_directory(*args, **kwargs):
        temporary = real_temporary_directory(*args, **kwargs)
        created_temporary_directories.append(Path(temporary.name))
        return temporary

    monkeypatch.setattr(
        build_snapshot.tempfile,
        "TemporaryDirectory",
        tracked_temporary_directory,
    )
    started = time.perf_counter()
    with pytest.raises(BuildError, match="timed out after 0.05 seconds and was killed") as error:
        build_snapshot_from_input(
            slow_builder,
            archive,
            tmp_path / "snapshot",
            builder_timeout_seconds=0.05,
        )
    elapsed = time.perf_counter() - started

    assert isinstance(error.value.__cause__, subprocess.TimeoutExpired)
    assert elapsed < 5
    assert created_temporary_directories
