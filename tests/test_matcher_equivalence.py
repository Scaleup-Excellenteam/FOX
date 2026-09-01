"""Deterministic equivalence checks against the matcher frozen at a79147d."""

import random

import pytest

from autocomplete.matcher import match_and_score
from autocomplete.scoring import (
    edited_score,
    exact_score,
    extra_or_missing_penalty,
    substitution_penalty,
)


def _reference_substitution_position(
    query: str,
    sentence: str,
    start: int,
) -> int | None:
    mismatch_position: int | None = None
    for query_index, query_character in enumerate(query):
        if query_character == sentence[start + query_index]:
            continue
        if mismatch_position is not None:
            return None
        mismatch_position = query_index + 1
    return mismatch_position


def _reference_extra_query_position(
    query: str,
    sentence: str,
    start: int,
    target_length: int,
) -> int | None:
    index = 0
    while index < target_length and query[index] == sentence[start + index]:
        index += 1

    if index == target_length:
        return len(query)

    deleted_index = index
    for target_index in range(deleted_index, target_length):
        if query[target_index + 1] != sentence[start + target_index]:
            return None
    return deleted_index + 1


def _reference_missing_query_position(
    query: str,
    sentence: str,
    start: int,
) -> int | None:
    index = 0
    while index < len(query) and query[index] == sentence[start + index]:
        index += 1

    if index == len(query):
        return len(query) + 1

    inserted_index = index
    for query_index in range(inserted_index, len(query)):
        if query[query_index] != sentence[start + query_index + 1]:
            return None
    return inserted_index + 1


def _reference_match_and_score(query: str, sentence: str) -> int | None:
    """Exact test-only copy of the production matcher frozen at a79147d."""

    if query == "":
        raise ValueError("query must not be empty")

    query_length = len(query)
    sentence_length = len(sentence)

    if query in sentence:
        return exact_score(query_length)

    best_score: int | None = None

    if query_length <= sentence_length:
        for start in range(sentence_length - query_length + 1):
            position = _reference_substitution_position(query, sentence, start)
            if position is None:
                continue
            score = edited_score(query_length - 1, substitution_penalty(position))
            if best_score is None or score > best_score:
                best_score = score

    shorter_length = query_length - 1
    if shorter_length <= sentence_length:
        for start in range(sentence_length - shorter_length + 1):
            position = _reference_extra_query_position(
                query,
                sentence,
                start,
                shorter_length,
            )
            if position is None:
                continue
            score = edited_score(
                query_length - 1,
                extra_or_missing_penalty(position),
            )
            if best_score is None or score > best_score:
                best_score = score

    longer_length = query_length + 1
    if longer_length <= sentence_length:
        for start in range(sentence_length - longer_length + 1):
            position = _reference_missing_query_position(query, sentence, start)
            if position is None:
                continue
            score = edited_score(query_length, extra_or_missing_penalty(position))
            if best_score is None or score > best_score:
                best_score = score

    return best_score


@pytest.mark.parametrize(
    ("query", "sentence"),
    [
        ("alpha", "alpha at the beginning"),
        ("omega", "ending with omega"),
        ("abcde", "xbcde"),
        ("abcde", "abxde"),
        ("abcde", "abcdx"),
        ("xabcde", "abcde"),
        ("abxcde", "abcde"),
        ("abcdex", "abcde"),
        ("abcde", "xabcde"),
        ("abcde", "abxcde"),
        ("abcde", "abcdex"),
        ("a", ""),
        ("a", "b"),
        ("ab", "ba"),
        ("aaaa", "baaa aaab"),
        ("abcde", "xbcde and abcdx"),
        ("abcde", "xbcde then abcde"),
        ("abcdefgh", "abcxefgh"),
        ("abcdefgh", "unrelated"),
        ("a", "a"),
        ("ab", "ab"),
        ("abcd", "abc"),
        ("abc", "abcd"),
        ("longquerytext", "prefix longquerytexx suffix"),
        ("cant", "cant stop wont stop"),
    ],
)
def test_crafted_cases_match_frozen_reference(query: str, sentence: str) -> None:
    assert match_and_score(query, sentence) == _reference_match_and_score(
        query,
        sentence,
    )


def _random_string(randomizer: random.Random, length: int, alphabet: str) -> str:
    return "".join(randomizer.choices(alphabet, k=length))


def _equivalence_cases() -> list[tuple[str, str]]:
    randomizer = random.Random(0xA79147D)
    cases: list[tuple[str, str]] = []
    alphabets = ("ab", "abc", "abcd ")

    for query_length in range(1, 13):
        for case_index in range(5_000):
            alphabet = alphabets[case_index % len(alphabets)]
            query = _random_string(randomizer, query_length, alphabet)
            mode = case_index % 6
            padding_length = randomizer.randrange(0, 25)
            prefix_length = randomizer.randrange(padding_length + 1)
            prefix = _random_string(randomizer, prefix_length, alphabet)
            suffix = _random_string(
                randomizer,
                padding_length - prefix_length,
                alphabet,
            )

            if mode == 0:
                target = query
            elif mode == 1:
                position = randomizer.randrange(query_length)
                alternatives = alphabet.replace(query[position], "") or "z"
                target = (
                    query[:position]
                    + randomizer.choice(alternatives)
                    + query[position + 1 :]
                )
            elif mode == 2:
                position = randomizer.randrange(query_length)
                target = query[:position] + query[position + 1 :]
            elif mode == 3:
                position = randomizer.randrange(query_length + 1)
                target = (
                    query[:position] + randomizer.choice(alphabet) + query[position:]
                )
            elif mode == 4:
                target = _random_string(
                    randomizer,
                    randomizer.randrange(0, query_length + 13),
                    alphabet,
                )
            else:
                target = _random_string(
                    randomizer,
                    randomizer.randrange(0, max(1, query_length - 1)),
                    alphabet,
                )

            cases.append((query, prefix + target + suffix))

    return cases


def test_sixty_thousand_generated_cases_match_frozen_reference() -> None:
    cases = _equivalence_cases()
    assert len(cases) == 60_000

    mismatches = [
        (query, sentence, _reference_match_and_score(query, sentence), actual)
        for query, sentence in cases
        if (actual := match_and_score(query, sentence))
        != _reference_match_and_score(query, sentence)
    ]

    assert mismatches == []
