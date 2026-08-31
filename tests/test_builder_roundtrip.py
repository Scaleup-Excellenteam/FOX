import json
import struct
import subprocess
from pathlib import Path

from autocomplete.autocomplete_snapshot_pb2 import PostingChunk, SnapshotManifest
from autocomplete.snapshot_loader import load_snapshot


def run(builder, corpus, out, size="1024"):
    return subprocess.run(
        [str(builder), str(corpus), str(out), size], text=True, capture_output=True, check=False
    )


def test_normalization_contract(builder):
    cases = json.loads((Path(__file__).parent / "contracts/normalization_cases.json").read_text())
    for case in cases:
        got = subprocess.run(
            [str(builder), "--normalize", case["input"]],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        assert got == case["expected"]


def test_recursive_deterministic_roundtrip(builder, tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "deep" / "more").mkdir(parents=True)
    (corpus / "z.txt").write_text("To be, or not to be!\nbanana banana\n", encoding="utf-8")
    (corpus / "deep" / "a.txt").write_text("  Unicode שלום!!!  \n\n", encoding="utf-8")
    (corpus / "deep" / "more" / "b.txt").write_text("abcdefghi\n", encoding="utf-8")
    one = tmp_path / "one"
    two = tmp_path / "two"
    assert run(builder, corpus, one).returncode == 0
    assert run(builder, corpus, two).returncode == 0
    assert {p.name: p.read_bytes() for p in one.iterdir()} == {
        p.name: p.read_bytes() for p in two.iterdir()
    }
    records, index = load_snapshot(one)
    assert [
        (r.sentence_id, r.source_path, r.line_number, r.original, r.normalized)
        for r in records.values()
    ] == [
        (1, "deep/a.txt", 1, "  Unicode שלום!!!  ", "unicode שלום"),
        (2, "deep/a.txt", 2, "", ""),
        (3, "deep/more/b.txt", 1, "abcdefghi", "abcdefghi"),
        (4, "z.txt", 1, "To be, or not to be!", "to be or not to be"),
        (5, "z.txt", 2, "banana banana", "banana banana"),
    ]
    assert index.postings[(1, "a")] == (3, 5)
    assert index.postings[(2, "to")] == (4,)
    assert index.postings[(3, "ana")] == (5,)
    assert index.get_candidate_ids("to be") == [4]


def test_multiple_shards_load(builder, tmp_path):
    corpus = tmp_path / "many"
    corpus.mkdir()
    (corpus / "many.txt").write_text(
        "\n".join(f"record {i:04d} with deterministic content" for i in range(120)) + "\n",
        encoding="utf-8",
    )
    snapshot = tmp_path / "sharded"
    assert run(builder, corpus, snapshot).returncode == 0
    assert len(list(snapshot.glob("records-*.binpb"))) > 1
    assert len(list(snapshot.glob("index-*.binpb"))) > 1
    records, index = load_snapshot(snapshot)
    assert len(records) == 120
    assert 43 in index.get_candidate_ids("record 0042")


def test_manifest_metadata_cli_and_chunked_postings(builder, tmp_path):
    corpus = tmp_path / "many-common"
    corpus.mkdir()
    lines = [f"shared repeated text {number:04d}" for number in range(220)]
    (corpus / "records.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    result = run(builder, corpus, snapshot)
    assert result.returncode == 0
    assert "sentences=220 files=1" in result.stdout
    assert "[C++ BUILDER] [FILE DISCOVERY]" in result.stdout
    assert "[C++ BUILDER] [CORPUS METRICS]" in result.stdout
    assert "Average Parsing Throughput:" in result.stdout
    assert "[C++ BUILDER] [INDEX GENERATION]" in result.stdout
    assert "C++ Peak RSS:" in result.stdout
    assert "[C++ BUILDER] [SNAPSHOT]" in result.stdout
    value = SnapshotManifest()
    value.ParseFromString((snapshot / "manifest.binpb").read_bytes())
    assert value.schema_version == value.framing_version == 1
    assert value.normalization.version == 1
    assert value.normalization.algorithm == "ascii-v1"
    assert list(value.ngram_index.gram_codepoints) == [1, 2, 3]
    assert value.ngram_index.min_selective_query_codepoints == 2
    assert value.ngram_index.shard_target_bytes == 1024
    assert len(value.snapshot_id) == 64
    assert len(value.corpus_digest) == 32
    assert len(value.index_digest) == 32
    assert value.created_at_utc == "1970-01-01T00:00:00Z"

    chunks = []
    for shard in value.index_shards:
        data = (snapshot / shard.file_name).read_bytes()
        offset = 16
        while offset < len(data):
            length = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            chunk = PostingChunk()
            chunk.ParseFromString(data[offset : offset + length])
            chunks.append(chunk)
            offset += length + 4
    shared = [chunk for chunk in chunks if (chunk.gram_size, chunk.gram) == (3, "sha")]
    assert [chunk.chunk_index for chunk in shared] == [0, 1, 2]
    assert [chunk.is_last_chunk for chunk in shared] == [False, False, True]
    records, index = load_snapshot(snapshot)
    assert len(records) == 220
    assert index.postings[(3, "sha")] == tuple(range(1, 221))


def test_blank_whitespace_punctuation_and_duplicate_records(builder, tmp_path):
    corpus = tmp_path / "edge"
    corpus.mkdir()
    (corpus / "edge.txt").write_text("   \n!!!\nSame sentence.\nSame sentence.\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    assert run(builder, corpus, snapshot).returncode == 0
    records, index = load_snapshot(snapshot)
    assert [(record.original, record.normalized) for record in records.values()] == [
        ("   ", ""),
        ("!!!", ""),
        ("Same sentence.", "same sentence"),
        ("Same sentence.", "same sentence"),
    ]
    assert index.get_candidate_ids("same sentence") == [3, 4]


def test_cli_failures_are_clear_and_leave_no_partial_snapshot(builder, tmp_path):
    usage = subprocess.run([str(builder)], text=True, capture_output=True, check=False)
    assert usage.returncode == 2
    assert "usage: fox_snapshot_builder" in usage.stderr
    missing = subprocess.run(
        [str(builder), str(tmp_path / "missing"), str(tmp_path / "out")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing.returncode == 1
    assert "corpus root is not a directory" in missing.stderr
    assert not (tmp_path / "out").exists()


def test_empty_corpus(builder, tmp_path):
    corpus = tmp_path / "empty"
    corpus.mkdir()
    out = tmp_path / "snap"
    assert run(builder, corpus, out).returncode == 0
    records, index = load_snapshot(out)
    assert records == {}
    assert index.get_candidate_ids("x") == []


def test_only_txt_and_invalid_utf8(builder, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "skip.md").write_text("skip")
    (corpus / "bad.txt").write_bytes(b"bad\xff")
    result = run(builder, corpus, tmp_path / "out")
    assert result.returncode != 0
    assert "invalid UTF-8" in result.stderr


def test_existing_destination_is_not_overwritten(builder, tmp_path):
    corpus = tmp_path / "c"
    corpus.mkdir()
    (corpus / "a.txt").write_text("x")
    out = tmp_path / "out"
    out.mkdir()
    marker = out / "keep"
    marker.write_text("yes")
    assert run(builder, corpus, out).returncode != 0
    assert marker.read_text() == "yes"
