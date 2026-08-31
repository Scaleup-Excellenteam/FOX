import json
import string
from pathlib import Path

from google.protobuf.descriptor import FieldDescriptor

from autocomplete.generated import autocomplete_snapshot_pb2

CONTRACT_PATH = Path(__file__).parent / "contracts" / "normalization_cases.json"
ASCII_PUNCTUATION = string.punctuation


def test_normalization_contract_is_valid_and_contains_frozen_vectors() -> None:
    cases = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert all(set(case) == {"input", "expected"} for case in cases)
    assert all(isinstance(value, str) for case in cases for value in case.values())

    vectors = {case["input"]: case["expected"] for case in cases}
    assert vectors["Hello,       WORLD!!!"] == "hello world"
    assert vectors["  Leading and trailing  "] == "leading and trailing"
    assert vectors["can't-stop"] == "cantstop"
    assert vectors[ASCII_PUNCTUATION] == ""
    assert vectors[""] == ""
    assert vectors["     "] == ""
    assert vectors["!!!"] == ""


def test_generated_protobuf_package_and_messages_match_frozen_schema() -> None:
    descriptor = autocomplete_snapshot_pb2.DESCRIPTOR

    assert descriptor.package == "autocomplete.snapshot.v1"
    assert set(descriptor.message_types_by_name) == {
        "SentenceRecordProto",
        "GramPostingProto",
        "SnapshotManifestProto",
    }


def test_sentence_record_proto_fields_match_frozen_schema() -> None:
    descriptor = autocomplete_snapshot_pb2.SentenceRecordProto.DESCRIPTOR
    assert [(field.name, field.number, field.type) for field in descriptor.fields] == [
        ("sentence_id", 1, FieldDescriptor.TYPE_UINT64),
        ("original", 2, FieldDescriptor.TYPE_STRING),
        ("normalized", 3, FieldDescriptor.TYPE_STRING),
        ("source_path", 4, FieldDescriptor.TYPE_STRING),
        ("line_number", 5, FieldDescriptor.TYPE_UINT64),
    ]


def test_gram_posting_proto_fields_match_frozen_schema() -> None:
    descriptor = autocomplete_snapshot_pb2.GramPostingProto.DESCRIPTOR
    assert [(field.name, field.number, field.type) for field in descriptor.fields] == [
        ("gram_size", 1, FieldDescriptor.TYPE_UINT32),
        ("gram", 2, FieldDescriptor.TYPE_STRING),
        ("sentence_ids", 3, FieldDescriptor.TYPE_UINT64),
    ]
    assert descriptor.fields_by_name["sentence_ids"].is_repeated


def test_snapshot_manifest_proto_fields_match_frozen_schema() -> None:
    descriptor = autocomplete_snapshot_pb2.SnapshotManifestProto.DESCRIPTOR
    assert [(field.name, field.number, field.type) for field in descriptor.fields] == [
        ("schema_version", 1, FieldDescriptor.TYPE_UINT32),
        ("normalization_version", 2, FieldDescriptor.TYPE_UINT32),
        ("index_strategy_version", 3, FieldDescriptor.TYPE_UINT32),
        ("gram_sizes", 4, FieldDescriptor.TYPE_UINT32),
        ("corpus_digest_sha256", 5, FieldDescriptor.TYPE_STRING),
        ("snapshot_id", 6, FieldDescriptor.TYPE_STRING),
        ("created_at_utc", 7, FieldDescriptor.TYPE_STRING),
        ("record_files", 8, FieldDescriptor.TYPE_STRING),
        ("index_files", 9, FieldDescriptor.TYPE_STRING),
        ("searchable_record_count", 10, FieldDescriptor.TYPE_UINT64),
        ("posting_count", 11, FieldDescriptor.TYPE_UINT64),
        ("index_digest_sha256", 12, FieldDescriptor.TYPE_STRING),
    ]
    for field_name in ("gram_sizes", "record_files", "index_files"):
        assert descriptor.fields_by_name[field_name].is_repeated
