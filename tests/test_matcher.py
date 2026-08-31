import itertools

import pytest

from autocomplete.matcher import match_and_score
from autocomplete.normalization import normalize


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("To be", 10),
        ("or Not", 12),
        ("be, that", 14),
        ("2o be", 3),
        ("to pe", 6),
        ("or knot", 8),
        ("or nt", 8),
        ("not be", None),
    ],
)
def test_official_permanent_regressions(query: str, expected: int | None) -> None:
    sentence = normalize("To be or not to be, that is the question.")
    assert match_and_score(normalize(query), sentence) == expected


@pytest.mark.parametrize(
    ("query", "sentence"),
    [
        ("alpha", "alpha at the beginning"),
        ("middle", "in the middle here"),
        ("omega", "ending with omega"),
        ("full sentence", "full sentence"),
        ("repeat", "repeat then repeat"),
        ("two words", "spaces make two words match"),
    ],
)
def test_exact_substring_alignments(query: str, sentence: str) -> None:
    assert match_and_score(query, sentence) == 2 * len(query)


@pytest.mark.parametrize(
    ("query", "sentence", "expected"),
    [
        ("xbcdef", "abcdef", 5),
        ("axcdef", "abcdef", 6),
        ("abxdef", "abcdef", 7),
        ("abcxef", "abcdef", 8),
        ("abcdxf", "abcdef", 9),
        ("xbcdef", "abcdef at the beginning", 5),
        ("abxdef", "before abcdef after", 7),
        ("abcdxf", "ending with abcdef", 9),
    ],
)
def test_substitution_positions_and_alignments(
    query: str, sentence: str, expected: int
) -> None:
    assert match_and_score(query, sentence) == expected


def test_substitution_chooses_highest_scoring_alignment() -> None:
    assert match_and_score("abcde", "xbcde and abcdx") == 7


@pytest.mark.parametrize(
    ("query", "sentence", "expected"),
    [
        ("xabcdef", "abcdef", 2),
        ("axbcdef", "abcdef", 4),
        ("abxcdef", "abcdef", 6),
        ("abcxdef", "abcdef", 8),
        ("abcdxef", "abcdef", 10),
        ("abcdefx", "abcdef", 10),
        ("xabcdef", "abcdef starts here", 2),
        ("abxcdef", "before abcdef after", 6),
        ("abcdefx", "ending with abcdef", 10),
    ],
)
def test_extra_query_character_positions_and_alignments(
    query: str, sentence: str, expected: int
) -> None:
    assert match_and_score(query, sentence) == expected


def test_extra_query_character_chooses_highest_scoring_alignment() -> None:
    assert match_and_score("xabcdef", "abcdef and xabcde") == 10


@pytest.mark.parametrize(
    ("query", "sentence", "expected"),
    [
        # Boundary insertions also contain query as an exact substring, so the
        # required highest-alignment rule makes exact score authoritative.
        ("bcdef", "abcdef", 10),
        # Position 2 also admits a higher substitution against the suffix.
        ("acdef", "abcdef", 3),
        ("abdef", "abcdef", 4),
        ("abcef", "abcdef", 6),
        ("abcdf", "abcdef", 8),
        ("abcde", "abcdef", 10),
        ("acdef", "abcdef starts here", 3),
        ("abdef", "before abcdef after", 4),
        ("abcdf", "ending with abcdef", 8),
    ],
)
def test_missing_query_character_positions_and_alignments(
    query: str, sentence: str, expected: int
) -> None:
    assert match_and_score(query, sentence) == expected


def test_missing_query_character_chooses_highest_scoring_alignment() -> None:
    assert match_and_score("abcde", "abxcde and abcdxe") == 8


def test_empty_query_raises() -> None:
    with pytest.raises(ValueError):
        match_and_score("", "sentence")


def test_one_character_query_preserves_negative_legal_match() -> None:
    assert match_and_score("a", "b") == -5
    assert match_and_score("a", "") == -10


def test_query_longer_than_sentence_boundaries() -> None:
    assert match_and_score("abcd", "abc") == 2
    assert match_and_score("abcde", "abc") is None


@pytest.mark.parametrize(
    ("query", "sentence"),
    [
        ("abxxef", "abcdef"),
        ("abxycdef", "abcdef"),
        ("abef", "abcdef"),
        ("abxdef", "abcyef"),
        ("uvwxyz", "abcdef"),
        ("ace", "abcde"),
    ],
)
def test_two_edits_unrelated_and_non_substring_alignments_are_rejected(
    query: str, sentence: str
) -> None:
    assert match_and_score(query, sentence) is None


def test_matcher_does_not_normalize_its_inputs() -> None:
    assert match_and_score("CANT", "cant") is None
    assert match_and_score(normalize("can't"), normalize("can't")) == 8


def _oracle_penalty(position: int, *, substitution: bool) -> int:
    if substitution:
        return (5, 4, 3, 2)[position - 1] if position < 5 else 1
    return (10, 8, 6, 4)[position - 1] if position < 5 else 2


def _oracle_match_and_score(query: str, sentence: str) -> int | None:
    """Exhaustive test-only oracle based on constructing edited strings."""

    scores: list[int] = []
    if query in sentence:
        scores.append(2 * len(query))

    for position in range(len(query)):
        for replacement in "ab ":
            if replacement == query[position]:
                continue
            edited = query[:position] + replacement + query[position + 1 :]
            if edited in sentence:
                penalty = _oracle_penalty(position + 1, substitution=True)
                scores.append(2 * (len(query) - 1) - penalty)

        edited = query[:position] + query[position + 1 :]
        if edited in sentence:
            penalty = _oracle_penalty(position + 1, substitution=False)
            scores.append(2 * (len(query) - 1) - penalty)

    for position in range(len(query) + 1):
        for inserted in "ab ":
            edited = query[:position] + inserted + query[position:]
            if edited in sentence:
                penalty = _oracle_penalty(position + 1, substitution=False)
                scores.append(2 * len(query) - penalty)

    return max(scores) if scores else None


def _strings(alphabet: str, maximum_length: int) -> list[str]:
    return [
        "".join(characters)
        for length in range(maximum_length + 1)
        for characters in itertools.product(alphabet, repeat=length)
    ]


def test_production_matcher_agrees_with_independent_exhaustive_oracle() -> None:
    queries = _strings("ab ", 3)[1:]
    sentences = _strings("ab ", 4)
    for query in queries:
        for sentence in sentences:
            expected = _oracle_match_and_score(query, sentence)
            actual = match_and_score(query, sentence)
            assert actual == expected, (
                f"query={query!r}, sentence={sentence!r}, "
                f"expected={expected!r}, actual={actual!r}"
            )
