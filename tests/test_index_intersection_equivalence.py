"""Equivalence checks for iterator-based sorted posting intersection."""

import random
from collections.abc import Sequence

import pytest

from autocomplete.index import FrozenPosting, PostingArray, _intersect_sorted


def reference_intersection(left: Sequence[int], right: Sequence[int]) -> list[int]:
    result = []
    left_index = right_index = 0
    while left_index < len(left) and right_index < len(right):
        left_id, right_id = left[left_index], right[right_index]
        if left_id == right_id:
            result.append(left_id)
            left_index += 1
            right_index += 1
        elif left_id < right_id:
            left_index += 1
        else:
            right_index += 1
    return result


def sequence(values: list[int], kind: int) -> Sequence[int]:
    constructors = (list, tuple, PostingArray, FrozenPosting)
    return constructors[kind % len(constructors)](values)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ([], []),
        ([], [1, 2, 3]),
        ([1, 2, 3], []),
        ([1], [1]),
        ([1], [2]),
        ([1, 3, 5], [2, 4, 6]),
        ([1, 3, 5, 7], [3, 4, 5, 8]),
        ([0, 1, 2**32 - 1], [0, 2, 2**32 - 1]),
    ],
)
@pytest.mark.parametrize("left_kind", range(4))
@pytest.mark.parametrize("right_kind", range(4))
def test_crafted_intersections_match_previous_algorithm(
    left: list[int],
    right: list[int],
    left_kind: int,
    right_kind: int,
) -> None:
    left_sequence = sequence(left, left_kind)
    right_sequence = sequence(right, right_kind)

    assert _intersect_sorted(left_sequence, right_sequence) == (
        reference_intersection(left_sequence, right_sequence)
    )


def test_twenty_thousand_random_intersections_match_previous_algorithm() -> None:
    randomizer = random.Random(0x1A7E25EC7)
    for case_index in range(20_000):
        left_size = randomizer.randrange(130)
        right_size = randomizer.randrange(130)
        left = sorted(randomizer.sample(range(10_000), left_size))
        right = sorted(randomizer.sample(range(10_000), right_size))
        left_sequence = sequence(left, case_index)
        right_sequence = sequence(right, case_index // 4)

        assert _intersect_sorted(left_sequence, right_sequence) == (
            reference_intersection(left_sequence, right_sequence)
        )
