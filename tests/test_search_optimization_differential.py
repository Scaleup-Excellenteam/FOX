from __future__ import annotations

import random
import re
from collections import defaultdict

import pytest

from autocomplete.index import SearchIndex
from autocomplete.models import SentenceRecord
from autocomplete.normalization import normalize
from autocomplete.observability import reset_for_tests
from autocomplete.reference_engine import ReferenceEngine
from autocomplete.search_engine import SearchEngine


def _engines(
    values: list[tuple[str, str, int]],
) -> tuple[SearchEngine, ReferenceEngine]:
    records: dict[int, SentenceRecord] = {}
    postings: dict[tuple[int, str], set[int]] = defaultdict(set)
    for sentence_id, (original, source, offset) in enumerate(values, start=1):
        normalized = normalize(original)
        record = SentenceRecord(sentence_id, original, normalized, source, offset)
        records[sentence_id] = record
        for size in (1, 2, 3):
            for position in range(len(normalized) - size + 1):
                postings[(size, normalized[position : position + size])].add(
                    sentence_id
                )
    return (
        SearchEngine(records, SearchIndex(postings, records)),
        ReferenceEngine(records),
    )


@pytest.mark.parametrize(
    "query",
    [
        "to be",  # exact
        "to ve",  # one substitution
        "t be",  # one character missing from the query
        "to xbe",  # one extra query character
        "qzxqzx",  # no match
        "a",  # one-character query
        "to",  # two-character query
        "  É-COLE!! ",  # Unicode plus normalized punctuation/case
    ],
)
def test_optimized_search_matches_exhaustive_for_required_cases(query: str) -> None:
    values = [
        ("Alpha to be omega", "z.txt", 8),
        ("To be first", "b.txt", 4),
        ("To be first", "a.txt", 9),
        ("A route to be", "a.txt", 2),
        ("Try to be exact", "c.txt", 7),
        ("École ouverte", "unicode.txt", 3),
        ("unrelated sentence", "none.txt", 1),
    ]
    optimized, exhaustive = _engines(values)

    assert optimized.search(query) == exhaustive.search(query)


def test_equal_score_ties_match_exhaustive_order_exactly() -> None:
    values = [
        ("zeta to be", "z.txt", 9),
        ("alpha to be", "z.txt", 8),
        ("beta to be", "b.txt", 7),
        ("beta to be", "a.txt", 9),
        ("beta to be", "a.txt", 2),
        ("gamma to be", "a.txt", 1),
        ("aardvark to be", "late.txt", 100),
    ]
    optimized, exhaustive = _engines(values)

    assert optimized.search("to be") == exhaustive.search("to be")


@pytest.mark.parametrize("mode", ["OFF", "INFO", "DETAILED"])
def test_randomized_corpora_and_queries_match_exhaustive_order_exactly(
    monkeypatch, tmp_path, mode
) -> None:
    monkeypatch.setenv("LOG_DIRECTORY", str(tmp_path / mode.lower()))
    monkeypatch.setenv("LOG_LEVEL", "OFF" if mode == "OFF" else "INFO")
    monkeypatch.setenv("DETAILED_PROFILING", str(mode == "DETAILED").lower())
    reset_for_tests()
    randomizer = random.Random(0xF05A11)
    alphabet = "abcdeéßשלום🙂     -!?"
    for _ in range(40):
        values = []
        for sentence_id in range(35):
            text = "".join(randomizer.choices(alphabet, k=randomizer.randrange(1, 28)))
            values.append((text, f"source-{randomizer.randrange(4)}.txt", sentence_id))
        optimized, exhaustive = _engines(values)
        queries = [
            "".join(randomizer.choices(alphabet, k=randomizer.randrange(1, 10)))
            for _ in range(12)
        ]
        for query in queries:
            for k in (0, 1, 5, 50):
                assert optimized.search(query, k) == exhaustive.search(query, k)
    reset_for_tests()


@pytest.mark.parametrize("mode", ["OFF", "INFO", "DETAILED"])
def test_all_logging_modes_preserve_k_unicode_duplicates_and_edit_boundaries(
    monkeypatch, tmp_path, mode
) -> None:
    monkeypatch.setenv("LOG_DIRECTORY", str(tmp_path / mode.lower()))
    monkeypatch.setenv("LOG_LEVEL", "OFF" if mode == "OFF" else "INFO")
    monkeypatch.setenv("DETAILED_PROFILING", str(mode == "DETAILED").lower())
    reset_for_tests()
    values = [
        ("abcdef", "same.txt", 1),
        ("abcdef", "same.txt", 1),
        ("xbcdef", "substitution.txt", 2),
        ("bcdef", "missing-start.txt", 3),
        ("abcde", "missing-end.txt", 4),
        ("xabcdef", "extra-start.txt", 5),
        ("abcdefx", "extra-end.txt", 6),
        ("École שלום 🙂", "unicode.txt", 7),
        ("unrelated", "none.txt", 8),
    ]
    optimized, exhaustive = _engines(values)

    for query in (
        "",
        "!!!",
        "a",
        "ab",
        "abc",
        "abcdef",
        "xbcdef",
        "bcdef",
        "abcdefx",
        "  É-COLE!! ",
        "שלום",
        "🙂",
    ):
        for k in (0, 1, 5, 20):
            assert optimized.search(query, k) == exhaustive.search(query, k)
    reset_for_tests()


@pytest.mark.parametrize("mode", ["OFF", "INFO", "DETAILED"])
@pytest.mark.parametrize(
    ("prefix", "k"),
    [
        (None, 5),
        (123, 5),
        ([], 5),
        ("valid", True),
        ("valid", 1.5),
        ("valid", "5"),
        ("valid", -1),
    ],
)
def test_invalid_input_exception_equivalence_across_logging_modes(
    monkeypatch, tmp_path, mode, prefix, k
) -> None:
    monkeypatch.setenv("LOG_DIRECTORY", str(tmp_path / mode.lower()))
    monkeypatch.setenv("LOG_LEVEL", "OFF" if mode == "OFF" else "INFO")
    monkeypatch.setenv("DETAILED_PROFILING", str(mode == "DETAILED").lower())
    reset_for_tests()
    optimized, exhaustive = _engines([("one sentence", "source.txt", 1)])

    with pytest.raises(Exception) as expected:
        exhaustive.search(prefix, k)
    with pytest.raises(type(expected.value), match=re_escape(str(expected.value))):
        optimized.search(prefix, k)
    reset_for_tests()


def re_escape(value: str) -> str:
    return re.escape(value)
