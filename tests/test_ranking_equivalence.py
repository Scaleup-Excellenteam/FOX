import random

from autocomplete.models import AutoCompleteData
from autocomplete.ranking import rank_results


def reference_rank_results(
    results: list[AutoCompleteData],
) -> list[AutoCompleteData]:
    """Frozen implementation from 70f428b, used only as a test oracle."""
    return sorted(
        results,
        key=lambda result: (
            -result.score,
            result.completed_sentence,
            result.source_text,
            result.offset,
        ),
    )


def test_fully_identical_keys_preserve_distinct_objects_and_input() -> None:
    first = AutoCompleteData("same", "same.txt", 7, -3)
    second = AutoCompleteData("same", "same.txt", 7, -3)
    third = AutoCompleteData("same", "same.txt", 7, -3)
    results = [second, first, third]
    results_before = list(results)

    ranked = rank_results(results)

    assert all(
        actual is expected
        for actual, expected in zip(results, results_before, strict=True)
    )
    assert all(
        actual is expected
        for actual, expected in zip(ranked, results_before, strict=True)
    )
    assert len({id(item) for item in ranked}) == 3


def test_already_sorted_and_reverse_sorted_match_reference() -> None:
    sorted_results = [
        AutoCompleteData("alpha", "a.txt", 1, 10),
        AutoCompleteData("alpha", "a.txt", 3, 10),
        AutoCompleteData("alpha", "b.txt", 1, 10),
        AutoCompleteData("beta", "a.txt", 1, 10),
        AutoCompleteData("alpha", "a.txt", 1, 0),
        AutoCompleteData("alpha", "a.txt", 1, -10),
    ]

    for results in (sorted_results, list(reversed(sorted_results))):
        results_before = list(results)
        expected = reference_rank_results(results)
        actual = rank_results(results)

        assert all(
            actual_item is expected_item
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
        assert all(
            actual_item is expected_item
            for actual_item, expected_item in zip(results, results_before, strict=True)
        )


def test_deterministic_collision_heavy_equivalence() -> None:
    random_generator = random.Random(20260901)
    sentences = ["alpha", "beta", "delta", "same", "Zebra"]
    sources = ["a.txt", "b.txt", "repeat/path.txt"]
    scores = [-10, -1, 0, 0, 5, 5, 5, 10]
    offsets = [0, 0, 1, 1, 7, 7, 100]
    results = [
        AutoCompleteData(
            random_generator.choice(sentences),
            random_generator.choice(sources),
            random_generator.choice(offsets),
            random_generator.choice(scores),
        )
        for _ in range(50_000)
    ]
    results_before = list(results)

    expected = reference_rank_results(results)
    actual = rank_results(results)

    assert len(actual) == len(expected) == 50_000
    assert all(
        actual_item is expected_item
        for actual_item, expected_item in zip(actual, expected, strict=True)
    )
    assert all(
        actual_item is original_item
        for actual_item, original_item in zip(results, results_before, strict=True)
    )
