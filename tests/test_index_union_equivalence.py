"""Equivalence checks for the optimized sorted posting union."""

import random
from collections.abc import Sequence

import pytest

from autocomplete.index import FrozenPosting, PostingArray, _union_sorted


def _reference_union_sorted(
    left: Sequence[int],
    right: Sequence[int],
) -> list[int]:
    """Exact test-only copy of the union frozen at commit 05a5b55."""

    result: list[int] = []
    left_index = right_index = 0
    while left_index < len(left) or right_index < len(right):
        if right_index >= len(right) or (
            left_index < len(left) and left[left_index] < right[right_index]
        ):
            value = left[left_index]
            left_index += 1
        elif left_index >= len(left) or right[right_index] < left[left_index]:
            value = right[right_index]
            right_index += 1
        else:
            value = left[left_index]
            left_index += 1
            right_index += 1
        result.append(value)
    return result


def _sequence(values: list[int], kind: int) -> Sequence[int]:
    constructors = (list, tuple, PostingArray, FrozenPosting)
    return constructors[kind % len(constructors)](values)


@pytest.mark.parametrize(
    ("left_values", "right_values"),
    [
        ([], []),
        ([], [1, 2, 3]),
        ([1, 2, 3], []),
        ([1, 2, 3], [1, 2, 3]),
        ([1, 3, 5], [2, 4, 6]),
        ([1, 2, 3, 4], [1, 2, 3, 4]),
        ([1, 3, 5, 7], [3, 4, 5, 8]),
        ([1], [2]),
        ([1], [1]),
        ([1], list(range(2, 1_000))),
        (list(range(1, 1_000)), [2_000]),
        (list(range(0, 100, 2)), list(range(1, 100, 2))),
        ([0, 1, 2**32 - 1], [0, 2, 2**32 - 1]),
    ],
)
@pytest.mark.parametrize("left_kind", range(4))
@pytest.mark.parametrize("right_kind", range(4))
def test_crafted_unions_match_frozen_reference(
    left_values: list[int],
    right_values: list[int],
    left_kind: int,
    right_kind: int,
) -> None:
    left = _sequence(left_values, left_kind)
    right = _sequence(right_values, right_kind)

    assert _union_sorted(left, right) == _reference_union_sorted(left, right)


def _random_input_pairs() -> list[tuple[list[int], list[int]]]:
    randomizer = random.Random(0x05A5B55)
    overlap_ratios = (0.0, 0.25, 0.5, 0.75, 0.9, 1.0)
    pairs = []

    for case_index in range(20_000):
        if case_index % 20 == 0:
            left_size = randomizer.randrange(0, 4)
            right_size = randomizer.randrange(64, 129)
        elif case_index % 20 == 1:
            left_size = randomizer.randrange(64, 129)
            right_size = randomizer.randrange(0, 4)
        else:
            left_size = randomizer.randrange(0, 65)
            right_size = randomizer.randrange(0, 65)

        overlap_ratio = overlap_ratios[case_index % len(overlap_ratios)]
        shared_size = int(min(left_size, right_size) * overlap_ratio)
        distinct_size = left_size + right_size - shared_size
        values = randomizer.sample(range(0, 10_000), distinct_size)
        shared = values[:shared_size]
        left_only_end = shared_size + left_size - shared_size
        left_only = values[shared_size:left_only_end]
        right_only = values[left_only_end:]
        pairs.append((sorted(shared + left_only), sorted(shared + right_only)))

    return pairs


def test_twenty_thousand_random_unions_match_frozen_reference() -> None:
    pairs = _random_input_pairs()
    assert len(pairs) == 20_000

    mismatches = []
    for case_index, (left_values, right_values) in enumerate(pairs):
        left = _sequence(left_values, case_index)
        right = _sequence(right_values, case_index // 4)
        expected = _reference_union_sorted(left, right)
        actual = _union_sorted(left, right)
        if actual != expected:
            mismatches.append((left_values, right_values, expected, actual))

    assert mismatches == []
