import itertools

import pytest

from autocomplete.index import SearchIndex


def build_index(texts):
    postings = {}
    for identifier, text in texts.items():
        for size in (1, 2, 3):
            for position in range(len(text) - size + 1):
                postings.setdefault(
                    (size, text[position : position + size]), set()
                ).add(identifier)
    return SearchIndex(postings, texts)


def index():
    return build_index(
        {
            1: "to be or not to be",
            2: "toast",
            3: "unicode שלום",
            4: "banana",
            5: "abcdefghi",
            6: "to be or not to be",
        }
    )


def test_one_character_and_empty_broad_fallback():
    assert index().get_candidate_ids("t") == [1, 2, 3, 4, 5, 6]
    assert index().get_candidate_ids("") == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize(
    "query",
    ["", "t", "to", "to ", "to b", "to be", "abcdef", "abcdefg"],
)
def test_streamed_candidates_equal_materialized_candidates(query):
    idx = index()

    assert list(idx.iter_candidate_ids(query)) == idx.get_candidate_ids(query)


def test_streamed_candidate_type_validation_is_eager():
    with pytest.raises(TypeError, match="string"):
        index().iter_candidate_ids(None)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("t", [1, 2, 6]),
        ("to", [1, 2, 6]),
        ("to ", [1, 6]),
        ("zzz", []),
    ],
)
def test_exact_candidates_use_complete_direct_posting(query, expected):
    assert index().get_exact_candidate_ids(query) == expected


@pytest.mark.parametrize("query", ["", "abcd"])
def test_exact_candidates_reject_queries_outside_direct_gram_sizes(query):
    with pytest.raises(ValueError, match="between 1 and 3"):
        index().get_exact_candidate_ids(query)


def test_exact_candidates_validate_type():
    with pytest.raises(TypeError, match="string"):
        index().get_exact_candidate_ids(None)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("to", [1, 2, 3, 6]),  # 1 + 1
        ("to ", [1, 2, 6]),  # 1 + 2
        ("to b", [1, 2, 6]),  # 2 + 2
        ("to be", [1, 2, 6]),  # 2 + 3
        ("abcdef", [5]),  # 3 + 3
        ("abcdefg", [5]),  # 3 + 4
    ],
)
def test_exact_frozen_even_odd_partitions(query, expected):
    assert index().get_candidate_ids(query) == expected


def test_repeated_grams_and_duplicate_sentences_have_unique_ids():
    idx = index()
    assert idx.postings[(1, "a")] == (2, 4, 5)
    assert idx.get_candidate_ids("to be or not") == [1, 6]


def test_long_seed_intersects_every_overlapping_trigram():
    assert index().get_candidate_ids("abcxyz") == [5]


def test_single_trigram_seed_reuses_immutable_posting_without_copying():
    idx = index()

    assert idx._seed_candidates("to ") is idx.postings[(3, "to ")]


def test_partition_union_keeps_candidates_from_both_sides():
    idx = build_index({1: "xxabyy", 2: "xxcdyy", 3: "unrelated"})
    assert idx.get_candidate_ids("abcd") == [1, 2]


def legal_one_edit_queries(target):
    alphabet = "xz"
    yield target
    for position in range(len(target)):
        for replacement in alphabet:
            if replacement != target[position]:
                yield target[:position] + replacement + target[position + 1 :]
        yield target[:position] + target[position + 1 :]  # missing from query
    for position in range(len(target) + 1):
        yield target[:position] + "x" + target[position:]  # extra in query


@pytest.mark.parametrize("target", ["to", "to ", "to b", "to be", "abcdef", "abcdefg"])
def test_no_false_negatives_for_edits_in_both_partitions_and_boundaries(target):
    idx = build_index({1: f"prefix {target} suffix", 2: "noise"})
    for query in legal_one_edit_queries(target):
        if query:
            assert 1 in idx.get_candidate_ids(query), (target, query)


def test_generated_substrings_have_no_candidate_false_negatives():
    sentence = "to be or not to be that is the question"
    idx = build_index({7: sentence, 9: "irrelevant"})
    for start, length in itertools.product(range(0, 12, 3), range(2, 11)):
        target = sentence[start : start + length]
        for query in legal_one_edit_queries(target):
            if query:
                assert 7 in idx.get_candidate_ids(query), (target, query)


def test_type_validation():
    with pytest.raises(TypeError):
        index().get_candidate_ids(None)


def test_public_index_collections_are_immutable():
    idx = build_index({1: "abc", 2: "abd"})
    expected = idx.get_candidate_ids("ab")

    with pytest.raises(TypeError):
        idx.postings[(1, "a")] = (99,)
    with pytest.raises(TypeError):
        del idx.postings[(1, "a")]
    with pytest.raises(TypeError):
        idx.postings[(1, "a")][0] = 99
    with pytest.raises(AttributeError):
        idx.postings[(1, "a")].clear()
    with pytest.raises(TypeError):
        idx.all_sentence_ids[0] = 99
    with pytest.raises(AttributeError):
        idx.all_sentence_ids.clear()

    assert idx.get_candidate_ids("ab") == expected


def test_mutating_constructor_inputs_cannot_change_index():
    posting_ids = [1, 2]
    sentence_ids = [1, 2]
    postings = {(1, "a"): posting_ids, (1, "b"): posting_ids}
    idx = SearchIndex(postings, sentence_ids)

    posting_ids.clear()
    sentence_ids.clear()
    postings.clear()

    assert idx.postings[(1, "a")] == (1, 2)
    assert idx.all_sentence_ids == (1, 2)
    assert idx.get_candidate_ids("ab") == [1, 2]


def test_public_constructor_cannot_bypass_posting_normalization():
    idx = SearchIndex(
        {(1, "a"): [3, 1, 3, 2], (1, "b"): [2, 1, 2]},
        [3, 1, 3, 2],
    )

    assert idx.postings[(1, "a")] == (1, 2, 3)
    assert idx.postings[(1, "b")] == (1, 2)
    assert idx.all_sentence_ids == (1, 2, 3)
    assert idx.get_candidate_ids("ab") == [1, 2, 3]

    with pytest.raises(TypeError, match="assume_sorted_unique"):
        SearchIndex({}, [], assume_sorted_unique=True)
