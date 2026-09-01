import pytest

from autocomplete.scoring import (
    edited_score,
    exact_score,
    extra_or_missing_penalty,
    substitution_penalty,
)


@pytest.mark.parametrize(
    ("position", "expected"),
    [(1, 5), (2, 4), (3, 3), (4, 2), (5, 1), (20, 1)],
)
def test_substitution_penalties(position: int, expected: int) -> None:
    assert substitution_penalty(position) == expected


@pytest.mark.parametrize(
    ("position", "expected"),
    [(1, 10), (2, 8), (3, 6), (4, 4), (5, 2), (20, 2)],
)
def test_extra_or_missing_penalties(position: int, expected: int) -> None:
    assert extra_or_missing_penalty(position) == expected


@pytest.mark.parametrize("position", [0, -1, -20])
def test_invalid_penalty_positions_raise(position: int) -> None:
    with pytest.raises(ValueError):
        substitution_penalty(position)
    with pytest.raises(ValueError):
        extra_or_missing_penalty(position)


def test_score_arithmetic_and_negative_scores() -> None:
    assert exact_score(6) == 12
    assert edited_score(5, 4) == 6
    assert edited_score(0, 5) == -5


@pytest.mark.parametrize("query_length", range(1, 65))
def test_exact_score_is_strict_upper_bound_for_every_fuzzy_score_form(
    query_length: int,
) -> None:
    upper_bound = exact_score(query_length)

    for position in range(1, query_length + 1):
        substitution_score = edited_score(
            query_length - 1,
            substitution_penalty(position),
        )
        extra_query_character_score = edited_score(
            query_length - 1,
            extra_or_missing_penalty(position),
        )
        assert substitution_score < upper_bound
        assert extra_query_character_score < upper_bound

    for position in range(1, query_length + 2):
        extra_sentence_character_score = edited_score(
            query_length,
            extra_or_missing_penalty(position),
        )
        assert extra_sentence_character_score < upper_bound
