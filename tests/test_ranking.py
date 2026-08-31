from itertools import permutations

from autocomplete.models import AutoCompleteData
from autocomplete.ranking import rank_results


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
