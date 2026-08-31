from __future__ import annotations

import subprocess

import pytest

from autocomplete.generated.autocomplete_snapshot_pb2 import SnapshotManifestProto
from autocomplete.snapshot_loader import load_snapshot


def run_builder(builder, corpus, output):
    return subprocess.run(
        [str(builder), "--corpus", str(corpus), "--output", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_production_builder_corpus_contract(builder, tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "deep").mkdir(parents=True)
    # Creation order deliberately differs from lexicographic relative-path order.
    (corpus / "z.txt").write_bytes(b"!!!\r\nLast line\r\n")
    (corpus / "ignore.md").write_text("not indexed\n", encoding="utf-8")
    (corpus / "deep" / "A.TXT").write_bytes(
        b"\xef\xbb\xbfHello, WORLD!!!\r\n   \r\n"
        b"Unicode \xd7\xa9\xd7\x9c\xd7\x95\xd7\x9d\r\n"
    )

    snapshot = tmp_path / "snapshot"
    result = run_builder(builder, corpus, snapshot)
    assert result.returncode == 0, result.stderr
    records, index = load_snapshot(snapshot)

    assert [
        (
            record.sentence_id,
            record.original,
            record.normalized,
            record.source_path,
            record.line_number,
        )
        for record in records.values()
    ] == [
        (1, "Hello, WORLD!!!", "hello world", "deep/A.TXT", 1),
        (2, "Unicode שלום", "unicode שלום", "deep/A.TXT", 3),
        (3, "Last line", "last line", "z.txt", 2),
    ]
    assert index.postings[(1, "ו")] == (2,)
    assert index.postings[(2, "של")] == (2,)
    assert index.postings[(3, "לום")] == (2,)
    assert index.get_candidate_ids("hello") == [1]

    manifest = SnapshotManifestProto()
    manifest.ParseFromString((snapshot / "manifest.binpb").read_bytes())
    assert (
        manifest.schema_version,
        manifest.normalization_version,
        manifest.index_strategy_version,
    ) == (1, 1, 1)
    assert list(manifest.gram_sizes) == [1, 2, 3]
    assert manifest.searchable_record_count == 3
    assert manifest.posting_count == len(index.postings)


def test_production_builder_is_byte_deterministic(builder, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "records.txt").write_text("banana\nto be\n", encoding="utf-8")
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert run_builder(builder, corpus, first).returncode == 0
    assert run_builder(builder, corpus, second).returncode == 0
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }


def test_production_builder_matches_reference_logically(
    builder, reference_builder, tmp_path
):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "records.txt").write_text(
        "To be!\nbanana banana\nUnicode שלום\n", encoding="utf-8"
    )
    production = tmp_path / "production"
    reference = tmp_path / "reference"
    assert run_builder(builder, corpus, production).returncode == 0
    subprocess.run([str(reference_builder), str(corpus), str(reference)], check=True)
    production_records, production_index = load_snapshot(production)
    reference_records, reference_index = load_snapshot(reference)
    assert production_records == reference_records
    assert dict(production_index.postings) == dict(reference_index.postings)


@pytest.mark.parametrize(
    ("contents", "message"),
    [(b"bad\xff\n", "invalid UTF-8"), (b"valid\n", "already exists")],
)
def test_failed_build_never_publishes_partial_snapshot(
    builder, tmp_path, contents, message
):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "records.txt").write_bytes(contents)
    output = tmp_path / "snapshot"
    if message == "already exists":
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("preserve", encoding="utf-8")
    result = run_builder(builder, corpus, output)
    assert result.returncode == 1
    assert message in result.stderr
    if message == "invalid UTF-8":
        assert not output.exists()
    else:
        assert marker.read_text(encoding="utf-8") == "preserve"
    assert not list(tmp_path.glob(".snapshot.incomplete-*"))
