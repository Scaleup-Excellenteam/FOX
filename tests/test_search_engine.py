from collections.abc import Callable

import pytest

import autocomplete.search_engine as search_engine_module
from autocomplete.models import AutoCompleteData, SentenceRecord
from autocomplete.search_engine import SearchEngine


class FakeIndex:
    def __init__(self, candidate_ids: list[int]) -> None:
        self.candidate_ids = candidate_ids
        self.queries: list[str] = []

    def get_candidate_ids(self, normalized_query: str) -> list[int]:
        self.queries.append(normalized_query)
        return list(self.candidate_ids)


def make_record(
    sentence_id: int,
    *,
    original: str | None = None,
    normalized: str | None = None,
    source_path: str = "sentences.txt",
    line_number: int = 1,
) -> SentenceRecord:
    original = original or f"Original {sentence_id}"
    normalized = normalized or f"normalized {sentence_id}"
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
        search_engine_module,
        "_normalize",
        lambda prefix: normalized_query,
    )


def patch_matcher(
    monkeypatch: pytest.MonkeyPatch,
    matcher: Callable[[str, str], int | None],
) -> None:
    monkeypatch.setattr(search_engine_module, "_match_and_score", matcher)


def fail_if_called(*args: object) -> None:
    pytest.fail(f"unexpected call with arguments {args!r}")


def test_normalized_empty_query_returns_empty_without_index_or_matcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = FakeIndex([1])
    monkeypatch.setattr(search_engine_module, "_normalize", lambda prefix: "")
    patch_matcher(monkeypatch, fail_if_called)
    engine = SearchEngine({1: make_record(1)}, index)

    assert engine.search("raw prefix") == []
    assert index.queries == []


def test_no_candidates_returns_empty_without_calling_matcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = FakeIndex([])
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, fail_if_called)
    engine = SearchEngine({}, index)

    assert engine.search("raw prefix") == []
    assert index.queries == ["normalized query"]


def test_hebrew_keyboard_query_is_converted_before_index_lookup() -> None:
    index = FakeIndex([])

    assert SearchEngine({}, index).search("יקךךם") == []
    assert index.queries == ["hello"]


def test_matcher_receives_normalized_query_and_record_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    record = make_record(1, normalized="normalized sentence")
    index = FakeIndex([1])
    monkeypatch.setattr(
        search_engine_module,
        "_normalize",
        lambda prefix: "normalized query",
    )

    def matcher(normalized_query: str, normalized_sentence: str) -> int:
        calls.append((normalized_query, normalized_sentence))
        return 10

    patch_matcher(monkeypatch, matcher)
    engine = SearchEngine({1: record}, index)

    engine.search("RAW PREFIX")

    assert calls == [("normalized query", "normalized sentence")]
    assert index.queries == ["normalized query"]


def test_matcher_none_discards_false_positive_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = make_record(1)
    index = FakeIndex([1])
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: None)

    assert SearchEngine({1: record}, index).search("prefix") == []


def test_match_converts_original_source_line_and_score_without_exposing_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = make_record(
        7,
        original="The Original Sentence!",
        normalized="the normalized sentence",
        source_path="nested/source.txt",
        line_number=42,
    )
    index = FakeIndex([7])
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: 83)

    result = SearchEngine({7: record}, index).search("prefix")

    assert result == [
        AutoCompleteData(
            completed_sentence="The Original Sentence!",
            source_text="nested/source.txt",
            offset=42,
            score=83,
        )
    ]
    assert result[0].completed_sentence != record.normalized


def test_unknown_candidate_id_raises_key_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, fail_if_called)
    engine = SearchEngine({}, FakeIndex([999]))

    with pytest.raises(KeyError) as error:
        engine.search("prefix")

    assert error.value.args == (999,)


@pytest.mark.parametrize("score", [-7, 0])
def test_non_none_integer_scores_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
    score: int,
) -> None:
    record = make_record(1)
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: score)

    result = SearchEngine({1: record}, FakeIndex([1])).search("prefix")

    assert len(result) == 1
    assert result[0].score == score


@pytest.mark.parametrize(
    ("normalized_sentence", "stub_score"),
    [("exact", 100), ("middle match", 80), ("one edit", 60)],
)
def test_matching_cases_use_stubbed_matcher_responses(
    monkeypatch: pytest.MonkeyPatch,
    normalized_sentence: str,
    stub_score: int,
) -> None:
    record = make_record(1, normalized=normalized_sentence)
    patch_normalize(monkeypatch, "query")
    patch_matcher(monkeypatch, lambda query, sentence: stub_score)

    result = SearchEngine({1: record}, FakeIndex([1])).search("prefix")

    assert result[0].score == stub_score


def test_default_k_returns_best_five_after_full_ranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {sentence_id: make_record(sentence_id) for sentence_id in range(1, 8)}
    scores = {
        record.normalized: score
        for record, score in zip(records.values(), [1, 2, 3, 4, 5, 100, 6], strict=True)
    }
    index = FakeIndex([1, 2, 3, 4, 5, 6, 7])
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: scores[sentence])

    result = SearchEngine(records, index).search("prefix")

    assert len(result) == 5
    assert [item.score for item in result] == [100, 6, 5, 4, 3]


def test_mixed_scores_are_ranked_descending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {sentence_id: make_record(sentence_id) for sentence_id in range(1, 5)}
    scores = {
        records[1].normalized: -2,
        records[2].normalized: 30,
        records[3].normalized: 0,
        records[4].normalized: 7,
    }
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: scores[sentence])

    result = SearchEngine(records, FakeIndex([1, 2, 3, 4])).search("prefix", k=4)

    assert [item.score for item in result] == [30, 7, 0, -2]


def test_score_ties_use_rank_results_tie_breakers(
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

    result = SearchEngine(records, FakeIndex([1, 2, 3, 4])).search("prefix", k=4)

    ranked_fields = [
        (item.completed_sentence, item.source_text, item.offset) for item in result
    ]
    assert ranked_fields == [
        ("alpha", "z.txt", 9),
        ("beta", "a.txt", 2),
        ("beta", "a.txt", 7),
        ("beta", "b.txt", 8),
    ]


def test_duplicate_sentences_from_different_sources_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {
        1: make_record(1, original="duplicate", source_path="b.txt"),
        2: make_record(2, original="duplicate", source_path="a.txt"),
    }
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: 10)

    result = SearchEngine(records, FakeIndex([1, 2])).search("prefix")

    assert len(result) == 2
    assert [item.source_text for item in result] == ["a.txt", "b.txt"]


def test_candidate_input_order_does_not_determine_result_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {
        1: make_record(1, original="lower"),
        2: make_record(2, original="higher"),
    }
    scores = {records[1].normalized: 2, records[2].normalized: 9}
    patch_normalize(monkeypatch)
    patch_matcher(monkeypatch, lambda query, sentence: scores[sentence])

    forward = SearchEngine(records, FakeIndex([1, 2])).search("prefix")
    reversed_order = SearchEngine(records, FakeIndex([2, 1])).search("prefix")

    assert forward == reversed_order
    assert [item.score for item in forward] == [9, 2]


def test_k_zero_returns_empty_without_search_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = FakeIndex([1])
    monkeypatch.setattr(search_engine_module, "_normalize", fail_if_called)
    patch_matcher(monkeypatch, fail_if_called)

    assert SearchEngine({1: make_record(1)}, index).search("prefix", k=0) == []
    assert index.queries == []


def test_negative_k_raises_before_search_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = FakeIndex([1])
    monkeypatch.setattr(search_engine_module, "_normalize", fail_if_called)
    patch_matcher(monkeypatch, fail_if_called)

    with pytest.raises(ValueError, match="non-negative"):
        SearchEngine({1: make_record(1)}, index).search("prefix", k=-1)

    assert index.queries == []


@pytest.mark.parametrize("invalid_k", [1.5, "5", None, True])
def test_non_integer_k_raises_before_search_work(
    monkeypatch: pytest.MonkeyPatch,
    invalid_k: object,
) -> None:
    index = FakeIndex([1])
    monkeypatch.setattr(search_engine_module, "_normalize", fail_if_called)
    patch_matcher(monkeypatch, fail_if_called)

    with pytest.raises(TypeError, match="integer"):
        SearchEngine({1: make_record(1)}, index).search(
            "prefix",
            k=invalid_k,  # type: ignore[arg-type]
        )

    assert index.queries == []
