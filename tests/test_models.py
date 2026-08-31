from dataclasses import FrozenInstanceError, fields

import pytest

from autocomplete.models import AutoCompleteData, SentenceRecord


def test_sentence_record_has_frozen_contract_fields() -> None:
    assert [field.name for field in fields(SentenceRecord)] == [
        "sentence_id",
        "original",
        "normalized",
        "source_path",
        "line_number",
    ]

    record = SentenceRecord(1, "Hello!", "hello", "nested/a.txt", 7)
    with pytest.raises(FrozenInstanceError):
        record.original = "Changed"


def test_autocomplete_data_has_mutable_contract_fields() -> None:
    assert [field.name for field in fields(AutoCompleteData)] == [
        "completed_sentence",
        "source_text",
        "offset",
        "score",
    ]

    result = AutoCompleteData("Hello!", "nested/a.txt", 7, 10)
    result.score = 12
    assert result.score == 12
