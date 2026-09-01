from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

import pytest

import autocomplete.search_engine as search_engine_module
from autocomplete.index import SearchIndex
from autocomplete.models import SentenceRecord
from autocomplete.normalization import normalize
from autocomplete.reference_engine import ReferenceEngine
from autocomplete.scoring import exact_score
from autocomplete.search_engine import SearchEngine


class TrackingIndex:
    def __init__(self, index: SearchIndex, *, reject_fuzzy: bool = False) -> None:
        self._index = index
        self.reject_fuzzy = reject_fuzzy
        self.exact_queries: list[str] = []
        self.fuzzy_queries: list[str] = []

    def get_exact_candidate_ids(self, query: str) -> Sequence[int]:
        self.exact_queries.append(query)
        return self._index.get_exact_candidate_ids(query)

    def get_candidate_ids(self, query: str) -> list[int]:
        self.fuzzy_queries.append(query)
        if self.reject_fuzzy:
            pytest.fail(f"fuzzy candidate generation was not expected for {query!r}")
        return self._index.get_candidate_ids(query)

    def iter_candidate_ids(self, query: str):
        self.fuzzy_queries.append(query)
        if self.reject_fuzzy:
            pytest.fail(f"fuzzy candidate generation was not expected for {query!r}")
        return self._index.iter_candidate_ids(query)


def make_record(
    sentence_id: int,
    original: str,
    *,
    normalized: str | None = None,
    source_path: str = "sentences.txt",
    line_number: int | None = None,
) -> SentenceRecord:
    return SentenceRecord(
        sentence_id=sentence_id,
        original=original,
        normalized=normalize(original) if normalized is None else normalized,
        source_path=source_path,
        line_number=sentence_id if line_number is None else line_number,
    )


def build_index(records: Iterable[SentenceRecord]) -> SearchIndex:
    postings: dict[tuple[int, str], set[int]] = defaultdict(set)
    sentence_ids = []
    for record in records:
        sentence_ids.append(record.sentence_id)
        for size in (1, 2, 3):
            for position in range(len(record.normalized) - size + 1):
                postings[(size, record.normalized[position : position + size])].add(
                    record.sentence_id
                )
    return SearchIndex(postings, sentence_ids)


def engines(
    records: list[SentenceRecord],
    *,
    reject_fuzzy: bool = False,
) -> tuple[SearchEngine, ReferenceEngine, TrackingIndex]:
    records_by_id = {record.sentence_id: record for record in records}
    tracking_index = TrackingIndex(
        build_index(records),
        reject_fuzzy=reject_fuzzy,
    )
    return (
        SearchEngine(records_by_id, tracking_index),
        ReferenceEngine(records_by_id),
        tracking_index,
    )


@pytest.mark.parametrize("exact_count", range(5))
def test_fewer_than_five_exact_matches_fall_back_and_fill_with_fuzzy_results(
    monkeypatch: pytest.MonkeyPatch,
    exact_count: int,
) -> None:
    records = [
        make_record(identifier, f"z exact ab {identifier}")
        for identifier in range(1, exact_count + 1)
    ]
    records.extend(
        make_record(
            exact_count + offset,
            f"fuzzy a{character}",
            normalized=f"a{character}",
        )
        for offset, character in enumerate("cdefg", start=1)
    )
    indexed, reference, index = engines(records)
    matcher_calls: list[str] = []
    real_matcher = search_engine_module._match_and_score

    def matcher(query: str, sentence: str) -> int | None:
        matcher_calls.append(sentence)
        return real_matcher(query, sentence)

    monkeypatch.setattr(search_engine_module, "_match_and_score", matcher)

    actual = indexed.search("ab")
    expected = reference.search("ab")

    assert actual == expected
    assert index.exact_queries == ["ab"]
    assert index.fuzzy_queries == ["ab"]
    assert len([item for item in actual if item.score == exact_score(2)]) == exact_count
    assert all("exact" not in sentence for sentence in matcher_calls)


def test_exactly_five_exact_matches_skip_fuzzy_generation_and_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        make_record(identifier, f"exact ab {identifier}") for identifier in range(1, 6)
    ]
    records.append(make_record(6, "fuzzy ac", normalized="ac"))
    indexed, reference, index = engines(records, reject_fuzzy=True)
    monkeypatch.setattr(
        search_engine_module,
        "_match_and_score",
        lambda *args: pytest.fail("matcher must not run after five exact matches"),
    )

    actual = indexed.search("ab")

    assert actual == reference.search("ab")
    assert index.exact_queries == ["ab"]
    assert index.fuzzy_queries == []
    assert [item.score for item in actual] == [4] * 5


@pytest.mark.parametrize("query", ["a", "ab", "abc"])
def test_complete_exact_posting_is_scanned_and_late_best_result_wins(
    query: str,
) -> None:
    records = [
        make_record(identifier, f"z{identifier} {query}") for identifier in range(1, 7)
    ]
    records.append(make_record(7, f"alpha {query}"))
    indexed, reference, index = engines(records, reject_fuzzy=True)

    actual = indexed.search(query)

    assert actual == reference.search(query)
    assert actual[0].completed_sentence == f"alpha {query}"
    assert index.exact_queries == [query]
    assert index.fuzzy_queries == []


def test_exact_first_uses_all_tie_break_fields() -> None:
    records = [
        make_record(1, "beta ab", source_path="b.txt", line_number=8),
        make_record(2, "alpha ab", source_path="z.txt", line_number=9),
        make_record(3, "beta ab", source_path="a.txt", line_number=7),
        make_record(4, "beta ab", source_path="a.txt", line_number=2),
        make_record(5, "gamma ab", source_path="a.txt", line_number=1),
        make_record(6, "delta ab", source_path="a.txt", line_number=1),
    ]
    indexed, reference, _ = engines(records, reject_fuzzy=True)

    actual = indexed.search("ab")

    assert actual == reference.search("ab")
    assert [
        (item.completed_sentence, item.source_text, item.offset) for item in actual
    ] == [
        ("alpha ab", "z.txt", 9),
        ("beta ab", "a.txt", 2),
        ("beta ab", "a.txt", 7),
        ("beta ab", "b.txt", 8),
        ("delta ab", "a.txt", 1),
    ]


def test_duplicate_exact_records_from_different_sources_are_preserved() -> None:
    records = [
        make_record(1, "duplicate a", source_path="b.txt"),
        make_record(2, "duplicate a", source_path="a.txt"),
        make_record(3, "other a 3"),
        make_record(4, "other a 4"),
        make_record(5, "other a 5"),
    ]
    indexed, reference, _ = engines(records, reject_fuzzy=True)

    actual = indexed.search("a")

    assert actual == reference.search("a")
    duplicates = [item for item in actual if item.completed_sentence == "duplicate a"]
    assert [item.source_text for item in duplicates] == ["a.txt", "b.txt"]


def test_raw_query_is_normalized_before_exact_posting_lookup() -> None:
    records = [
        make_record(identifier, f"exact ab {identifier}") for identifier in range(1, 6)
    ]
    indexed, reference, index = engines(records, reject_fuzzy=True)

    actual = indexed.search("  A-B!! ")

    assert actual == reference.search("  A-B!! ")
    assert index.exact_queries == ["ab"]


@pytest.mark.parametrize("k", [0, 1, 5])
def test_exact_first_preserves_k_behavior(k: int) -> None:
    records = [
        make_record(identifier, f"exact abc {identifier}") for identifier in range(1, 7)
    ]
    indexed, reference, index = engines(records, reject_fuzzy=True)

    actual = indexed.search("abc", k=k)

    assert actual == reference.search("abc", k=k)
    assert len(actual) == k
    assert index.exact_queries == ([] if k == 0 else ["abc"])


@pytest.mark.parametrize("query", ["function", "configuration", "to be"])
def test_queries_longer_than_three_keep_existing_fuzzy_safe_path(query: str) -> None:
    records = [
        make_record(1, f"exact {query}"),
        make_record(2, f"another exact {query}"),
        make_record(3, "unrelated text"),
    ]
    indexed, reference, index = engines(records)

    actual = indexed.search(query)

    assert actual == reference.search(query)
    assert index.exact_queries == []
    assert index.fuzzy_queries == [query]
