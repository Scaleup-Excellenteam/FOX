from collections.abc import Callable

import pytest

import autocomplete.reference_engine as reference_engine_module
from autocomplete.models import AutoCompleteData, SentenceRecord
from autocomplete.reference_engine import ReferenceEngine


def make_record(
    sentence_id: int,
    *,
    original: str | None = None,
    normalized: str | None = None,
    source_path: str = "sentences.txt",
    line_number: int = 1,
) -> SentenceRecord:
    if original is None:
        original = f"Original {sentence_id}"
    if normalized is None:
        normalized = f"normalized {sentence_id}"
    return SentenceRecord(
        sentence_id=sentence_id,
        original=original,
        normalized=normalized,
        source_path=source_path,
        line_number=line_number,
    )


def patch_normalize(
    monkeypatch: pytest.MonkeyPatch,
    normalized_query: str = "normalized query",
) -> None:
    monkeypatch.setattr(
        reference_engine_module,
        "_normalize",
        lambda prefix: normalized_query,
    )


def patch_matcher(
    monkeypatch: pytest.MonkeyPatch,
    matcher: Callable[[str, str], int | None],
) -> None:
    monkeypatch.setattr(reference_engine_module, "_match_and_score", matcher)


def patch_translate(
    monkeypatch: pytest.MonkeyPatch,
    translated_text: str = "translated prefix",
) -> None:
    monkeypatch.setattr(
        reference_engine_module,
        "_translate",
        lambda prefix: translated_text,
    )


def fail_if_called(*args: object) -> None:
    pytest.fail(f"unexpected call with arguments {args!r}")


def test_prefix_is_translated_to_spanish_before_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalize_calls: list[str] = []
    patch_translate(monkeypatch, "prefijo traducido")
    monkeypatch.setattr(
        reference_engine_module,
        "_normalize",
        lambda prefix: normalize_calls.append(prefix) or "normalized query",
    )
    patch_matcher(monkeypatch, lambda query, sentence: None)

    ReferenceEngine({1: make_record(1)}).search("raw prefix")

    assert normalize_calls == ["prefijo traducido"]


def test_normalized_empty_query_returns_empty_without_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reference_engine_module, "_normalize", lambda prefix: "")
    patch_matcher(monkeypatch, fail_if_called)
    engine = ReferenceEngine({1: make_record(1)})

    assert engine.search("raw prefix") == []


def test_empty_records_returns_empty_without_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, fail_if_called)

    assert ReferenceEngine({}).search("prefix") == []


def test_non_empty_query_matches_every_record_including_discarded_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {
        1: make_record(1, normalized="first normalized"),
        2: make_record(2, normalized="discarded normalized"),
        3: make_record(3, normalized="third normalized"),
    }
    calls: list[tuple[str, str]] = []
    scores = {
        "first normalized": 5,
        "discarded normalized": None,
        "third normalized": 9,
    }
    patch_normalize(monkeypatch, "normalized query")

    def matcher(normalized_query: str, normalized_sentence: str) -> int | None:
        calls.append((normalized_query, normalized_sentence))
        return scores[normalized_sentence]

    patch_matcher(monkeypatch, matcher)

    result = ReferenceEngine(records).search("RAW QUERY")

    assert calls == [
        ("normalized query", "first normalized"),
        ("normalized query", "discarded normalized"),
        ("normalized query", "third normalized"),
    ]
    assert [item.score for item in result] == [9, 5]


def test_match_maps_original_source_line_and_score_without_exposing_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = make_record(
        7,
        original="The Original Sentence!",
        normalized="the normalized sentence",
        source_path="nested/source.txt",
        line_number=42,
    )
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: 83)

    result = ReferenceEngine({7: record}).search("prefix")

    assert result == [
        AutoCompleteData(
            completed_sentence="The Original Sentence!",
            source_text="nested/source.txt",
            offset=42,
            score=83,
        )
    ]
    assert result[0].completed_sentence != record.normalized


@pytest.mark.parametrize("score", [0, -7])
def test_zero_and_negative_scores_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
    score: int,
) -> None:
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: score)

    result = ReferenceEngine({1: make_record(1)}).search("prefix")

    assert len(result) == 1
    assert result[0].score == score


def test_mixed_positive_zero_and_negative_scores_rank_descending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {sentence_id: make_record(sentence_id) for sentence_id in range(1, 5)}
    scores = {
        records[1].normalized: -4,
        records[2].normalized: 12,
        records[3].normalized: 0,
        records[4].normalized: 3,
    }
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: scores[sentence])

    result = ReferenceEngine(records).search("prefix")

    assert [item.score for item in result] == [12, 3, 0, -4]


def test_default_k_scans_all_records_then_returns_best_five(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {sentence_id: make_record(sentence_id) for sentence_id in range(1, 8)}
    scores = {
        record.normalized: score
        for record, score in zip(records.values(), [1, 2, 3, 4, 5, 100, 6], strict=True)
    }
    matched_sentences: list[str] = []
    patch_normalize(monkeypatch)

    def matcher(normalized_query: str, normalized_sentence: str) -> int:
        matched_sentences.append(normalized_sentence)
        return scores[normalized_sentence]

    patch_matcher(monkeypatch, matcher)

    result = ReferenceEngine(records).search("prefix")

    assert matched_sentences == [record.normalized for record in records.values()]
    assert len(result) == 5
    assert [item.score for item in result] == [100, 6, 5, 4, 3]


def test_explicit_custom_k_returns_requested_number_of_best_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {sentence_id: make_record(sentence_id) for sentence_id in range(1, 4)}
    scores = {
        records[1].normalized: 2,
        records[2].normalized: 9,
        records[3].normalized: 5,
    }
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: scores[sentence])

    result = ReferenceEngine(records).search("prefix", k=2)

    assert [item.score for item in result] == [9, 5]


def test_higher_score_ranks_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {
        1: make_record(1, original="lower"),
        2: make_record(2, original="higher"),
    }
    scores = {records[1].normalized: 2, records[2].normalized: 9}
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: scores[sentence])

    result = ReferenceEngine(records).search("prefix")

    assert [item.score for item in result] == [9, 2]


def test_equal_scores_use_sentence_source_and_offset_tie_breakers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {
        1: make_record(1, original="beta", source_path="b.txt", line_number=8),
        2: make_record(2, original="alpha", source_path="z.txt", line_number=9),
        3: make_record(3, original="beta", source_path="a.txt", line_number=7),
        4: make_record(4, original="beta", source_path="a.txt", line_number=2),
    }
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: 10)

    result = ReferenceEngine(records).search("prefix")

    ranked_fields = [
        (item.completed_sentence, item.source_text, item.offset) for item in result
    ]
    assert ranked_fields == [
        ("alpha", "z.txt", 9),
        ("beta", "a.txt", 2),
        ("beta", "a.txt", 7),
        ("beta", "b.txt", 8),
    ]


def test_duplicate_sentences_from_different_records_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {
        1: make_record(1, original="duplicate", source_path="b.txt"),
        2: make_record(2, original="duplicate", source_path="a.txt"),
    }
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: 10)

    result = ReferenceEngine(records).search("prefix")

    assert len(result) == 2
    assert [item.source_text for item in result] == ["a.txt", "b.txt"]


def test_record_insertion_order_does_not_determine_result_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = make_record(1, original="zeta")
    second = make_record(2, original="alpha")
    scores = {first.normalized: 2, second.normalized: 9}
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: scores[sentence])

    forward = ReferenceEngine({1: first, 2: second}).search("prefix")
    reversed_order = ReferenceEngine({2: second, 1: first}).search("prefix")

    assert forward == reversed_order
    assert [item.score for item in forward] == [9, 2]


def test_k_zero_returns_empty_without_search_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reference_engine_module, "_normalize", fail_if_called)
    patch_matcher(monkeypatch, fail_if_called)

    assert ReferenceEngine({1: make_record(1)}).search("prefix", k=0) == []


def test_negative_k_raises_before_search_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reference_engine_module, "_normalize", fail_if_called)
    patch_matcher(monkeypatch, fail_if_called)

    with pytest.raises(ValueError, match="non-negative"):
        ReferenceEngine({1: make_record(1)}).search("prefix", k=-1)


@pytest.mark.parametrize("invalid_k", [1.5, "5", None, True, False])
def test_non_integer_k_raises_before_search_work(
    monkeypatch: pytest.MonkeyPatch,
    invalid_k: object,
) -> None:
    monkeypatch.setattr(reference_engine_module, "_normalize", fail_if_called)
    patch_matcher(monkeypatch, fail_if_called)

    with pytest.raises(TypeError, match="integer"):
        ReferenceEngine({1: make_record(1)}).search(
            "prefix",
            k=invalid_k,  # type: ignore[arg-type]
        )


def test_search_does_not_mutate_input_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {1: make_record(1), 2: make_record(2)}
    original_records = records.copy()
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: 1)

    ReferenceEngine(records).search("prefix")

    assert records == original_records
    assert all(records[key] is record for key, record in original_records.items())
