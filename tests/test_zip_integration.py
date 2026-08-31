import zipfile

import pytest

from autocomplete.build_snapshot import ZipInputError, build_snapshot_from_input
from autocomplete.snapshot_loader import SnapshotError, load_snapshot


def make_zip(path):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "a.txt",
            "To be or not to be\nbanana banana\nhi\nabc\nabcd\n",
        )
        archive.writestr("nested/b.txt", "To be again\nUnicode שלום\n")
        archive.writestr("notes.md", "not corpus data\n")
        archive.writestr("empty-dir/", b"")


def build_zip_snapshot(builder, archive, snapshot):
    result = build_snapshot_from_input(builder, archive, snapshot)
    assert result.returncode == 0, result.stderr
    return result, load_snapshot(snapshot)


def test_real_zip_to_snapshot_to_candidate_flow(builder, tmp_path):
    archive = tmp_path / "corpus.zip"
    make_zip(archive)
    result, (records, index) = build_zip_snapshot(
        builder, archive, tmp_path / "snapshot"
    )

    assert "zip_entries=4 processed_files=2 zip_records=7" in result.stdout
    assert "[PYTHON BUILDER] [ZIP EXTRACTION]" in result.stdout
    assert "Uncompressed Data:" in result.stdout
    assert "Ratio:" in result.stdout
    assert "skipped_directories=1 skipped_unsupported_files=1" in result.stdout
    assert [
        (record.source_path, record.line_number, record.original, record.normalized)
        for record in records.values()
    ] == [
        ("a.txt", 1, "To be or not to be", "to be or not to be"),
        ("a.txt", 2, "banana banana", "banana banana"),
        ("a.txt", 3, "hi", "hi"),
        ("a.txt", 4, "abc", "abc"),
        ("a.txt", 5, "abcd", "abcd"),
        ("nested/b.txt", 1, "To be again", "to be again"),
        ("nested/b.txt", 2, "Unicode שלום", "unicode שלום"),
    ]

    # Character segments shorter than, equal to, and longer than the maximum n=3.
    assert {
        (size, gram)
        for size, gram in index.postings
        if 3 in index.postings[(size, gram)]
    } == {
        (1, "h"),
        (1, "i"),
        (2, "hi"),
    }
    assert {
        (size, gram)
        for size, gram in index.postings
        if 4 in index.postings[(size, gram)]
    } == {
        (1, "a"),
        (1, "b"),
        (1, "c"),
        (2, "ab"),
        (2, "bc"),
        (3, "abc"),
    }
    assert {
        (size, gram)
        for size, gram in index.postings
        if 5 in index.postings[(size, gram)]
    } == {
        (1, "a"),
        (1, "b"),
        (1, "c"),
        (1, "d"),
        (2, "ab"),
        (2, "bc"),
        (2, "cd"),
        (3, "abc"),
        (3, "bcd"),
    }

    to_be_grams = {
        (1, "t"): (1, 6),
        (1, "o"): (1, 6, 7),
        (1, " "): (1, 2, 6, 7),
        (1, "b"): (1, 2, 4, 5, 6),
        (1, "e"): (1, 6, 7),
        (2, "to"): (1, 6),
        (2, "o "): (1, 6),
        (2, " b"): (1, 2, 6),
        (2, "be"): (1, 6),
        (3, "to "): (1, 6),
        (3, "o b"): (1, 6),
        (3, " be"): (1, 6),
    }
    for key, expected_ids in to_be_grams.items():
        assert index.postings[key] == expected_ids
    assert index.postings[(3, "ana")] == (
        2,
    )  # Repeated twice in record 2, stored once.
    assert index.get_candidate_ids("to be") == [1, 6]
    assert [
        records[identifier].original for identifier in index.get_candidate_ids("to be")
    ] == [
        "To be or not to be",
        "To be again",
    ]
    assert records[7].original == "Unicode שלום"


def test_zip_snapshot_is_byte_deterministic(builder, tmp_path):
    archive = tmp_path / "corpus.zip"
    make_zip(archive)
    first = tmp_path / "first"
    second = tmp_path / "second"
    build_zip_snapshot(builder, archive, first)
    build_zip_snapshot(builder, archive, second)
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }


def test_malformed_zip_is_rejected_by_production_entry(builder, tmp_path):
    archive = tmp_path / "bad.zip"
    archive.write_bytes(b"not a zip")
    with pytest.raises(ZipInputError, match="cannot open ZIP"):
        build_snapshot_from_input(builder, archive, tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()


def test_unsafe_zip_path_is_rejected_without_escape(builder, tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("../unsafe.txt", "must not escape")
        value.writestr("safe.txt", "safe")
    with pytest.raises(ZipInputError, match="unsafe ZIP entry path"):
        build_snapshot_from_input(builder, archive, tmp_path / "snapshot")
    assert not (tmp_path / "unsafe.txt").exists()
    assert not (tmp_path / "snapshot").exists()


def test_corrupted_real_zip_snapshot_is_rejected(builder, tmp_path):
    archive = tmp_path / "corpus.zip"
    make_zip(archive)
    snapshot = tmp_path / "snapshot"
    build_zip_snapshot(builder, archive, snapshot)
    shard = snapshot / "index.binpb"
    data = bytearray(shard.read_bytes())
    data[-5] ^= 1
    shard.write_bytes(data)
    with pytest.raises(SnapshotError, match="corrupt posting|index digest mismatch"):
        load_snapshot(snapshot)
