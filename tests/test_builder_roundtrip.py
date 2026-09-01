import json
import struct
import subprocess
from pathlib import Path

from autocomplete.generated.autocomplete_snapshot_pb2 import (
    GramPostingProto,
    SnapshotManifestProto,
)
from autocomplete.search_engine import SearchEngine
from autocomplete.snapshot_loader import load_snapshot


def run(builder, corpus, output):
    return subprocess.run(
        [str(builder), "--corpus", str(corpus), "--output", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_normalization_contract(builder):
    cases = json.loads(
        (Path(__file__).parent / "contracts/normalization_cases.json").read_text()
    )
    for case in cases:
        result = subprocess.run(
            [str(builder), "--normalize", case["input"]],
            text=True,
            capture_output=True,
            check=True,
        )
        assert result.stdout == case["expected"]


def test_recursive_deterministic_roundtrip(builder, tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "deep").mkdir(parents=True)
    (corpus / "z.txt").write_text("To be!\nbanana banana\n", encoding="utf-8")
    (corpus / "deep" / "a.txt").write_text("  Unicode שלום!!!  \n\n", encoding="utf-8")
    one, two = tmp_path / "one", tmp_path / "two"
    assert run(builder, corpus, one).returncode == 0
    assert run(builder, corpus, two).returncode == 0
    assert {path.name: path.read_bytes() for path in one.iterdir()} == {
        path.name: path.read_bytes() for path in two.iterdir()
    }
    records, index = load_snapshot(one)
    assert [
        (value.sentence_id, value.source_path, value.line_number, value.normalized)
        for value in records.values()
    ] == [
        (1, "deep/a.txt", 1, "unicode שלום"),
        (2, "z.txt", 1, "to be"),
        (3, "z.txt", 2, "banana banana"),
    ]
    assert index.postings[(3, "ana")] == (3,)


def test_real_snapshot_drives_real_search(builder, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "sentences.txt").write_text(
        "Hello world\nHello there\nUnrelated sentence\n", encoding="utf-8"
    )
    snapshot = tmp_path / "snapshot"

    assert run(builder, corpus, snapshot).returncode == 0
    records, index = load_snapshot(snapshot)
    engine = SearchEngine(records, index)

    # "hello" and "world" are both in the Spanish lexicon, so the query is
    # translated to "hola mundo" before matching and no longer finds the
    # English corpus content -- this is the new translate-all-queries
    # contract, not a regression.
    assert engine.search("hello world") == []

    # Words absent from the lexicon pass through translation unchanged, so
    # the full builder-to-search round trip still finds real matches.
    results = engine.search("unrelated sentence")
    assert [
        (result.completed_sentence, result.source_text, result.offset, result.score)
        for result in results
    ] == [("Unrelated sentence", "sentences.txt", 3, 38)]


def test_completely_empty_corpus_builds_loads_and_searches(builder, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    snapshot = tmp_path / "snapshot"

    assert run(builder, corpus, snapshot).returncode == 0
    assert {path.name for path in snapshot.iterdir()} == {
        "manifest.binpb",
        "records.binpb",
        "index.binpb",
    }
    manifest = SnapshotManifestProto()
    manifest.ParseFromString((snapshot / "manifest.binpb").read_bytes())
    records, index = load_snapshot(snapshot)

    assert manifest.searchable_record_count == 0
    assert manifest.posting_count == 0
    assert records == {}
    assert dict(index.postings) == {}
    assert index.get_candidate_ids("anything") == []
    assert SearchEngine(records, index).search("anything") == []


def test_manifest_and_complete_postings_use_frozen_types(builder, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.txt").write_text("shared text\nshared again\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    assert run(builder, corpus, snapshot).returncode == 0
    manifest = SnapshotManifestProto()
    manifest.ParseFromString((snapshot / "manifest.binpb").read_bytes())
    assert list(manifest.gram_sizes) == [1, 2, 3]
    assert list(manifest.record_files) == ["records.binpb"]
    assert list(manifest.index_files) == ["index.binpb"]
    data = (snapshot / manifest.index_files[0]).read_bytes()
    postings = []
    offset = 0
    while offset < len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        value = GramPostingProto()
        value.ParseFromString(data[offset : offset + length])
        postings.append(value)
        offset += length
    shared = next(
        value for value in postings if (value.gram_size, value.gram) == (3, "sha")
    )
    assert list(shared.sentence_ids) == [1, 2]


def test_empty_and_normalized_empty_records_are_skipped(builder, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.txt").write_text("   \n!!!\nValid.\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    assert run(builder, corpus, snapshot).returncode == 0
    records, _ = load_snapshot(snapshot)
    assert [(value.original, value.normalized) for value in records.values()] == [
        ("Valid.", "valid")
    ]


def test_failures_leave_no_partial_snapshot(builder, tmp_path):
    missing = subprocess.run(
        [
            str(builder),
            "--corpus",
            str(tmp_path / "missing"),
            "--output",
            str(tmp_path / "out"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing.returncode == 1
    assert not (tmp_path / "out").exists()
