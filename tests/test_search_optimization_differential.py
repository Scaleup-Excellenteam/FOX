from __future__ import annotations

import random
from collections import defaultdict

import pytest

from autocomplete.index import SearchIndex
from autocomplete.models import SentenceRecord
from autocomplete.normalization import normalize
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


def test_randomized_corpora_and_queries_match_exhaustive_order_exactly() -> None:
    randomizer = random.Random(0xF05A11)
    alphabet = "abcde     -!?"
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
            assert optimized.search(query) == exhaustive.search(query)
