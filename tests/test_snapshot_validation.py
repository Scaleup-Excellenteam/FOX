import shutil
import struct
import subprocess

import pytest

from autocomplete.generated.autocomplete_snapshot_pb2 import (
    GramPostingProto,
    SentenceRecordProto,
    SnapshotManifestProto,
)
from autocomplete.snapshot_loader import SnapshotError, load_snapshot


def build(builder, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir(parents=True)
    (corpus / "a.txt").write_text("to be or not to be\nabcdefghi\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    subprocess.run(
        [str(builder), "--corpus", str(corpus), "--output", str(snapshot)], check=True
    )
    return snapshot


def manifest(snapshot):
    value = SnapshotManifestProto()
    value.ParseFromString((snapshot / "manifest.binpb").read_bytes())
    return value


def write_manifest(snapshot, value):
    (snapshot / "manifest.binpb").write_bytes(
        value.SerializeToString(deterministic=True)
    )


def messages(path, message_type):
    data = path.read_bytes()
    result = []
    offset = 0
    while offset < len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        value = message_type()
        value.ParseFromString(data[offset : offset + length])
        result.append(value)
        offset += length
    return result


def write_messages(path, values):
    output = bytearray()
    for value in values:
        payload = value.SerializeToString(deterministic=True)
        output.extend(struct.pack(">I", len(payload)))
        output.extend(payload)
    path.write_bytes(output)


@pytest.mark.parametrize(
    ("attribute", "message"),
    [
        ("schema_version", "versions"),
        ("normalization_version", "versions"),
        ("index_strategy_version", "versions"),
    ],
)
def test_rejects_unsupported_versions(builder, tmp_path, attribute, message):
    snapshot = build(builder, tmp_path)
    value = manifest(snapshot)
    setattr(value, attribute, 99)
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match=message):
        load_snapshot(snapshot)


def test_rejects_wrong_gram_sizes_and_counts(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    value = manifest(snapshot)
    value.gram_sizes[:] = [1, 3]
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="gram sizes"):
        load_snapshot(snapshot)

    snapshot = build(builder, tmp_path / "count")
    value = manifest(snapshot)
    value.posting_count += 1
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="posting count"):
        load_snapshot(snapshot)


def test_missing_corrupt_and_unsafe_files(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    value = manifest(snapshot)
    (snapshot / value.record_files[0]).unlink()
    with pytest.raises(SnapshotError, match="cannot read snapshot file"):
        load_snapshot(snapshot)

    corrupt = tmp_path / "corrupt"
    shutil.copytree(build(builder, tmp_path / "other"), corrupt)
    path = corrupt / "index.binpb"
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(SnapshotError, match="truncated frame"):
        load_snapshot(corrupt)

    unsafe = build(builder, tmp_path / "unsafe")
    value = manifest(unsafe)
    value.record_files[0] = "../outside.binpb"
    write_manifest(unsafe, value)
    with pytest.raises(SnapshotError, match="unsafe snapshot"):
        load_snapshot(unsafe)


def test_corrupt_and_missing_manifest(tmp_path):
    with pytest.raises(SnapshotError, match="cannot read manifest"):
        load_snapshot(tmp_path / "missing")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "manifest.binpb").write_bytes(b"\xff")
    with pytest.raises(SnapshotError, match="corrupt manifest"):
        load_snapshot(snapshot)


def test_duplicate_and_zero_record_ids_are_rejected(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    path = snapshot / "records.binpb"
    values = messages(path, SentenceRecordProto)
    values[1].sentence_id = values[0].sentence_id
    write_messages(path, values)
    with pytest.raises(SnapshotError, match="duplicate record"):
        load_snapshot(snapshot)

    snapshot = build(builder, tmp_path / "zero")
    path = snapshot / "records.binpb"
    values = messages(path, SentenceRecordProto)
    values[0].sentence_id = 0
    write_messages(path, values)
    with pytest.raises(SnapshotError, match="record identifier"):
        load_snapshot(snapshot)


def test_posting_unknown_unsorted_and_invalid_gram_are_rejected(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    path = snapshot / "index.binpb"
    values = messages(path, GramPostingProto)
    values[0].sentence_ids.append(999)
    write_messages(path, values)
    with pytest.raises(SnapshotError, match="unknown sentence ID"):
        load_snapshot(snapshot)

    snapshot = build(builder, tmp_path / "unsorted")
    path = snapshot / "index.binpb"
    values = messages(path, GramPostingProto)
    values[0].sentence_ids[:] = [2, 1]
    write_messages(path, values)
    with pytest.raises(SnapshotError, match="strictly increasing"):
        load_snapshot(snapshot)

    snapshot = build(builder, tmp_path / "gram")
    path = snapshot / "index.binpb"
    values = messages(path, GramPostingProto)
    values[0].gram_size = 9
    write_messages(path, values)
    with pytest.raises(SnapshotError, match="invalid posting"):
        load_snapshot(snapshot)


def test_canonical_digests_and_snapshot_identity_are_checked(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    value = manifest(snapshot)
    value.corpus_digest_sha256 = "0" * 64
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="corpus digest mismatch"):
        load_snapshot(snapshot)

    snapshot = build(builder, tmp_path / "index")
    value = manifest(snapshot)
    value.index_digest_sha256 = "0" * 64
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="index digest mismatch"):
        load_snapshot(snapshot)

    snapshot = build(builder, tmp_path / "identity")
    value = manifest(snapshot)
    value.snapshot_id = "0" * 64
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="snapshot ID mismatch"):
        load_snapshot(snapshot)


def test_duplicate_file_names_are_rejected(builder, tmp_path):
    snapshot = build(builder, tmp_path)
    value = manifest(snapshot)
    value.index_files[0] = value.record_files[0]
    write_manifest(snapshot, value)
    with pytest.raises(SnapshotError, match="duplicate snapshot file"):
        load_snapshot(snapshot)
