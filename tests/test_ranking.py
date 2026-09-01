from itertools import permutations

import pytest

from autocomplete.models import AutoCompleteData
from autocomplete.ranking import TopKSelector, rank_results, rank_top_k


def result(
    completed_sentence: str,
    *,
    source_text: str = "sentences.txt",
    offset: int = 0,
    score: int = 0,
) -> AutoCompleteData:
    return AutoCompleteData(completed_sentence, source_text, offset, score)


def test_higher_score_ranks_first() -> None:
    lower = result("lower", score=4)
    higher = result("higher", score=8)

    assert rank_results([lower, higher]) == [higher, lower]


def test_equal_score_ranks_by_completed_sentence_ascending() -> None:
    zebra = result("zebra", score=8)
    apple = result("apple", score=8)

    assert rank_results([zebra, apple]) == [apple, zebra]


def test_completed_sentence_ordering_uses_original_string() -> None:
    lowercase = result("apple", score=8)
    uppercase = result("Zebra", score=8)

    assert rank_results([lowercase, uppercase]) == [uppercase, lowercase]


def test_equal_score_and_sentence_rank_by_source_text_ascending() -> None:
    later_source = result("same", source_text="z/source.txt", score=8)
    earlier_source = result("same", source_text="a/source.txt", score=8)

    assert rank_results([later_source, earlier_source]) == [
        earlier_source,
        later_source,
    ]


def test_equal_score_sentence_and_source_rank_by_offset_ascending() -> None:
    later_offset = result("same", source_text="source.txt", offset=9, score=8)
    earlier_offset = result("same", source_text="source.txt", offset=2, score=8)

    assert rank_results([later_offset, earlier_offset]) == [
        earlier_offset,
        later_offset,
    ]


def test_input_order_does_not_affect_result() -> None:
    expected = [
        result("alpha", source_text="b.txt", offset=4, score=10),
        result("beta", source_text="a.txt", offset=3, score=10),
        result("beta", source_text="a.txt", offset=8, score=10),
        result("alpha", source_text="a.txt", offset=1, score=5),
    ]

    for input_order in permutations(expected):
        assert rank_results(list(input_order)) == expected


def test_duplicate_completed_sentences_from_different_sources_are_preserved() -> None:
    second_source = result("duplicate", source_text="b.txt", offset=2, score=7)
    first_source = result("duplicate", source_text="a.txt", offset=1, score=7)

    ranked = rank_results([second_source, first_source])

    assert ranked == [first_source, second_source]
    assert len(ranked) == 2


def test_negative_scores_are_preserved_and_ordered_descending() -> None:
    lowest = result("lowest", score=-10)
    highest = result("highest", score=-1)
    middle = result("middle", score=-5)

    ranked = rank_results([lowest, highest, middle])

    assert ranked == [highest, middle, lowest]
    assert [item.score for item in ranked] == [-1, -5, -10]


def test_empty_input_returns_empty_list() -> None:
    assert rank_results([]) == []


def test_single_input_is_returned_unchanged() -> None:
    only_result = result("only", source_text="source.txt", offset=3, score=-2)

    assert rank_results([only_result]) == [only_result]


def test_input_list_and_objects_are_not_modified() -> None:
    first = result("zeta", source_text="z.txt", offset=7, score=1)
    second = result("alpha", source_text="a.txt", offset=2, score=9)
    inputs = [first, second]
    original_values = [vars(item).copy() for item in inputs]

    ranked = rank_results(inputs)

    assert inputs == [first, second]
    assert [vars(item) for item in inputs] == original_values
    assert ranked == [second, first]
    assert ranked[0] is second
    assert ranked[1] is first


@pytest.mark.parametrize("k", [0, 1, 5, 20])
def test_rank_top_k_matches_full_stable_ranking(k: int) -> None:
    results = [
        result("same", source_text="same.txt", offset=7, score=3),
        result("zeta", source_text="z.txt", offset=2, score=-1),
        result("alpha", source_text="b.txt", offset=4, score=9),
        result("alpha", source_text="a.txt", offset=8, score=9),
        result("alpha", source_text="a.txt", offset=1, score=9),
        result("same", source_text="same.txt", offset=7, score=3),
        result("late", source_text="late.txt", offset=99, score=100),
    ]

    expected = rank_results(results)[:k]
    actual = rank_top_k(iter(results), k)

    assert actual == expected
    assert all(
        actual_item is expected_item
        for actual_item, expected_item in zip(actual, expected, strict=True)
    )


def test_rank_top_k_consumes_complete_stream_and_keeps_late_winner() -> None:
    seen: list[int] = []

    def stream():
        for score in [5, 4, 3, 2, 1, 100]:
            seen.append(score)
            yield result(str(score), score=score)

    ranked = rank_top_k(stream(), 5)

    assert seen == [5, 4, 3, 2, 1, 100]
    assert [item.score for item in ranked] == [100, 5, 4, 3, 2]


@pytest.mark.parametrize("k", [1, 5, 20])
def test_equal_score_field_fast_path_matches_frozen_ranking(k: int) -> None:
    fields = [
        ("שלום", "z.txt", 8),
        ("éclair", "unicode.txt", 2),
        ("Zebra", "b.txt", 9),
        ("alpha", "z.txt", 7),
        ("alpha", "a.txt", 9),
        ("alpha", "a.txt", 2),
        ("same", "same.txt", 4),
        ("same", "same.txt", 4),
        ("aardvark", "late.txt", 100),
    ]
    expected = rank_results(
        [
            result(sentence, source_text=source, offset=offset, score=6)
            for sentence, source, offset in fields
        ]
    )[:k]
    selector = TopKSelector(k)

    for sentence, source, offset in fields:
        selector._add_equal_score_fields(
            score=6,
            completed_sentence=sentence,
            source_text=source,
            offset=offset,
        )

    assert selector.results() == expected


@pytest.mark.parametrize(
    ("retained", "maximum_score"),
    [
        ([result("alpha", score=10)], 10),
        ([result(f"item {index}", score=10) for index in range(5)], 10),
        ([result(f"item {index}", score=9) for index in range(5)], 10),
        (
            [
                result("שלום", source_text="z.txt", offset=8, score=10),
                result("éclair", source_text="unicode.txt", offset=2, score=10),
                result("same", source_text="same.txt", offset=4, score=10),
                result("same", source_text="same.txt", offset=4, score=10),
                result("Zebra", source_text="b.txt", offset=9, score=10),
            ],
            10,
        ),
    ],
)
def test_maximum_score_field_bound_matches_previous_predicate(
    retained: list[AutoCompleteData],
    maximum_score: int,
) -> None:
    selector = TopKSelector(5)
    for item in retained:
        selector.add(item)
    candidates = [
        ("aardvark", "late.txt", 100),
        ("item 4", "sentences.txt", 0),
        ("same", "a.txt", 100),
        ("same", "same.txt", 3),
        ("same", "same.txt", 4),
        ("Ωmega", "unicode.txt", 1),
    ]

    for sentence, source, offset in candidates:
        previous = selector.worst_score == maximum_score and not selector.could_accept(
            score=maximum_score,
            completed_sentence=sentence,
            source_text=source,
            offset=offset,
        )
        optimized = selector._cannot_accept_maximum_score_fields(
            maximum_score=maximum_score,
            completed_sentence=sentence,
            source_text=source,
            offset=offset,
        )

        assert optimized is previous
