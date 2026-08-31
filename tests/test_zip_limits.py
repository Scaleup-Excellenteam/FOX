from __future__ import annotations

import io
import zipfile

import pytest

import autocomplete.build_snapshot as build_snapshot
from autocomplete.build_snapshot import (
    ZipExtractionLimits,
    ZipInputError,
    extract_zip_corpus,
)


def _make_zip(path, entries):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in entries:
            archive.writestr(name, content)


def _limits(**overrides):
    values = {
        "max_entries": 100,
        "max_total_uncompressed_bytes": 10_000,
        "max_entry_uncompressed_bytes": 10_000,
        "max_compression_ratio": 100.0,
    }
    values.update(overrides)
    return ZipExtractionLimits(**values)


def test_zip_entry_count_limit_cleans_destination(tmp_path):
    archive = tmp_path / "many.zip"
    destination = tmp_path / "extracted"
    _make_zip(archive, [("one.txt", b"1"), ("two.txt", b"2")])

    with pytest.raises(ZipInputError, match="entry count limit"):
        extract_zip_corpus(
            archive, destination, limits=_limits(max_entries=1)
        )

    assert not destination.exists()


def test_zip_declared_entry_size_limit_cleans_destination(tmp_path):
    archive = tmp_path / "large-entry.zip"
    destination = tmp_path / "extracted"
    _make_zip(archive, [("large.txt", b"12345678901")])

    with pytest.raises(ZipInputError, match="per-entry uncompressed size limit.*large.txt"):
        extract_zip_corpus(
            archive,
            destination,
            limits=_limits(max_entry_uncompressed_bytes=10),
        )

    assert not destination.exists()


def test_zip_total_declared_size_limit_cleans_destination(tmp_path):
    archive = tmp_path / "large-total.zip"
    destination = tmp_path / "extracted"
    _make_zip(archive, [("one.txt", b"123456"), ("two.txt", b"abcdef")])

    with pytest.raises(ZipInputError, match="total uncompressed size limit.*two.txt"):
        extract_zip_corpus(
            archive,
            destination,
            limits=_limits(max_total_uncompressed_bytes=10),
        )

    assert not destination.exists()


class _LyingInfo:
    filename = "bomb.txt"
    file_size = 1
    compress_size = 1
    flag_bits = 0
    compress_type = zipfile.ZIP_STORED
    external_attr = 0

    def is_dir(self):
        return False


class _LyingArchive:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def infolist(self):
        return [_LyingInfo()]

    def open(self, _info):
        return io.BytesIO(b"x" * 20)


def test_zip_actual_bytes_over_declared_limit_cleans_partial_output(monkeypatch, tmp_path):
    archive = tmp_path / "lying.zip"
    archive.write_bytes(b"placeholder")
    destination = tmp_path / "extracted"
    monkeypatch.setattr(build_snapshot.zipfile, "ZipFile", lambda _path: _LyingArchive())

    with pytest.raises(ZipInputError, match="actual per-entry size limit.*bomb.txt"):
        extract_zip_corpus(
            archive,
            destination,
            limits=_limits(max_entry_uncompressed_bytes=10),
        )

    assert not destination.exists()


def test_build_entry_point_forwards_zip_limits_and_cleans_temp_dir(
    builder, tmp_path, monkeypatch
):
    archive = tmp_path / "many.zip"
    _make_zip(archive, [("one.txt", b"1"), ("two.txt", b"2")])
