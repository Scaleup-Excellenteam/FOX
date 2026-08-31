import hashlib
import shutil
import struct
import subprocess

import pytest

from autocomplete.autocomplete_snapshot_pb2 import PostingChunk, SentenceRecord, SnapshotManifest
from autocomplete.snapshot_loader import SnapshotError, _crc32c, load_snapshot


def build(builder, tmp_path, lines=("to be or not to be", "abcdefghi")):
    corpus = tmp_path / "corpus"
    corpus.mkdir(parents=True)
    (corpus / "a.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    subprocess.run([str(builder), str(corpus), str(snapshot), "1024"], check=True)
    return snapshot


def manifest(snapshot):
    value = SnapshotManifest()
    value.ParseFromString((snapshot / "manifest.binpb").read_bytes())
    return value


def write_manifest(snapshot, value):
    (snapshot / "manifest.binpb").write_bytes(value.SerializeToString(deterministic=True))


def rewrite_frame(snapshot, shard, frame_index, message):
    path = snapshot / shard.file_name
    data = path.read_bytes()
    header = data[:16]
    offset = 16
    frames = []
    while offset < len(data):
        length = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        frames.append(data[offset : offset + length])
        offset += length + 4
    frames[frame_index] = message.SerializeToString(deterministic=True)
    rebuilt = bytearray(header)
    for payload in frames:
        rebuilt.extend(struct.pack("<I", len(payload)))
        rebuilt.extend(payload)
        rebuilt.extend(struct.pack("<I", _crc32c(payload)))
    path.write_bytes(rebuilt)
    shard.framed_size_bytes = len(rebuilt)
    shard.sha256 = hashlib.sha256(rebuilt).digest()


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [("schema_version", 99, "schema"), ("framing_version", 99, "framing")],
)
def test_rejects_manifest_versions(builder, tmp_path, attribute, value, message):
    snapshot = build(builder, tmp_path)
    value_message = manifest(snapshot)
    setattr(value_message, attribute, value)
    write_manifest(snapshot, value_message)
    with pytest.raises(SnapshotError, match=message):
        load_snapshot(snapshot)


def test_rejects_contract_versions_and_algorithms(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    value = manifest(snapshot)
    value.normalization.version = 9
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="normalization version"):
        load_snapshot(snapshot)
    value.normalization.version = 1
    value.normalization.algorithm = "different"
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="normalization algorithm"):
        load_snapshot(snapshot)
    value.normalization.algorithm = "ascii-v1"
    value.ngram_index.version = 9
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="index strategy"):
        load_snapshot(snapshot)


def test_rejects_index_configuration_and_identity(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    value = manifest(snapshot)
    del value.ngram_index.gram_codepoints[:]
    value.ngram_index.gram_codepoints.extend([3])
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="gram sizes"):
        load_snapshot(snapshot)
    value.ngram_index.gram_codepoints[:] = [1, 2, 3]
    value.ngram_index.min_selective_query_codepoints = 3
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="short-query"):
        load_snapshot(snapshot)
    value.ngram_index.min_selective_query_codepoints = 2
    value.snapshot_id = "0" * 64
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="snapshot ID mismatch"):
        load_snapshot(snapshot)


def test_missing_corrupt_and_unsafe_shards(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    value = manifest(snapshot)
    (snapshot / value.record_shards[0].file_name).unlink()
    with pytest.raises(SnapshotError, match="cannot read shard"):
        load_snapshot(snapshot)

    corrupt = tmp_path / "corrupt"
    shutil.copytree(build(builder, tmp_path / "other"), corrupt)
    value = manifest(corrupt)
    path = corrupt / value.index_shards[0].file_name
    data = bytearray(path.read_bytes())
    data[-5] ^= 1
    path.write_bytes(data)
    with pytest.raises(SnapshotError, match="checksum mismatch"):
        load_snapshot(corrupt)

    unsafe = build(builder, tmp_path / "unsafe")
    value = manifest(unsafe)
    value.record_shards[0].file_name = "../outside.binpb"
    write_manifest(unsafe, value)
    with pytest.raises(SnapshotError, match="unsafe shard"):
        load_snapshot(unsafe)


def test_corrupt_and_missing_manifest(tmp_path):
    with pytest.raises(SnapshotError, match="cannot read manifest"):
        load_snapshot(tmp_path / "missing")
    snapshot = tmp_path / "s"
    snapshot.mkdir()
    (snapshot / "manifest.binpb").write_bytes(b"\xff")
    with pytest.raises(SnapshotError, match="corrupt manifest"):
        load_snapshot(snapshot)


def test_duplicate_and_invalid_record_ids_are_rejected(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    value = manifest(snapshot)
    shard = value.record_shards[0]
    raw = (snapshot / shard.file_name).read_bytes()
    first_length = struct.unpack_from("<I", raw, 16)[0]
    second_offset = 16 + 4 + first_length + 4
    second_length = struct.unpack_from("<I", raw, second_offset)[0]
    record = SentenceRecord()
    record.ParseFromString(raw[second_offset + 4 : second_offset + 4 + second_length])
    record.sentence_id = 1
    rewrite_frame(snapshot, shard, 1, record)
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="invalid or duplicate record identifier"):
        load_snapshot(snapshot)


def test_posting_unknown_id_and_bad_bounds_are_rejected(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    value = manifest(snapshot)
    shard = value.index_shards[0]
    raw = (snapshot / shard.file_name).read_bytes()
    length = struct.unpack_from("<I", raw, 16)[0]
    posting = PostingChunk()
    posting.ParseFromString(raw[20 : 20 + length])
    posting.sentence_ids.append(999)
    rewrite_frame(snapshot, shard, 0, posting)
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="unknown sentence ID"):
        load_snapshot(snapshot)

    snapshot = build(builder, tmp_path / "bounds")
    value = manifest(snapshot)
    value.index_shards[0].first_gram = "wrong"
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="key bounds"):
        load_snapshot(snapshot)


def test_corpus_digest_detects_semantic_record_tampering(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    value = manifest(snapshot)
    shard = value.record_shards[0]
    raw = (snapshot / shard.file_name).read_bytes()
    length = struct.unpack_from("<I", raw, 16)[0]
    record = SentenceRecord()
    record.ParseFromString(raw[20 : 20 + length])
    record.original_text = "tampered but checksummed"
    rewrite_frame(snapshot, shard, 0, record)
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="corpus digest mismatch"):
        load_snapshot(snapshot)


def test_unterminated_posting_chunk_is_rejected(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    value = manifest(snapshot)
    shard = value.index_shards[-1]
    raw = (snapshot / shard.file_name).read_bytes()
    offset = 16
    payloads = []
    while offset < len(raw):
        length = struct.unpack_from("<I", raw, offset)[0]
        offset += 4
        payloads.append(raw[offset : offset + length])
        offset += length + 4
    posting = PostingChunk()
    posting.ParseFromString(payloads[-1])
    posting.is_last_chunk = False
    rewrite_frame(snapshot, shard, len(payloads) - 1, posting)
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="unterminated posting chunk"):
        load_snapshot(snapshot)


def test_duplicate_shard_names_are_rejected(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    value = manifest(snapshot)
    duplicate = value.record_shards.add()
    duplicate.CopyFrom(value.record_shards[0])
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="duplicate shard"):
        load_snapshot(snapshot)


def test_manifest_cannot_hide_a_missing_index_shard(builder, tmp_path):
    lines = tuple(f"shared text {number:04d}" for number in range(180))
    snapshot = build(builder, tmp_path, lines)
    value = manifest(snapshot)
    assert len(value.index_shards) > 1
    removable = next(
        position
        for position in range(len(value.index_shards))
        if (
            position == 0
            or value.index_shards[position - 1].last_gram != value.index_shards[position].first_gram
        )
        and (
            position + 1 == len(value.index_shards)
            or value.index_shards[position].last_gram != value.index_shards[position + 1].first_gram
        )
    )
    del value.index_shards[removable]
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="index digest mismatch"):
        load_snapshot(snapshot)
