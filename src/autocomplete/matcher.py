"""Authoritative exact/one-edit substring matcher."""

from autocomplete.scoring import (
    edited_score,
    exact_score,
    extra_or_missing_penalty,
    substitution_penalty,
)


def _substitution_position(query: str, sentence: str, start: int) -> int | None:
    mismatch_position: int | None = None
    for query_index, query_character in enumerate(query):
        if query_character == sentence[start + query_index]:
            continue
        if mismatch_position is not None:
            return None
        mismatch_position = query_index + 1
    return mismatch_position


def _extra_query_position(
    query: str, sentence: str, start: int, target_length: int
) -> int | None:
    """Return the best query position removable for this shorter target."""

    index = 0
    while index < target_length and query[index] == sentence[start + index]:
        index += 1

    if index == target_length:
        # Consuming equal characters first chooses the latest legal deletion.
        # Penalties never increase with position, so that alignment scores best.
        return len(query)

    deleted_index = index
    for target_index in range(deleted_index, target_length):
        if query[target_index + 1] != sentence[start + target_index]:
            return None
    return deleted_index + 1


def _missing_query_position(query: str, sentence: str, start: int) -> int | None:
    """Return the best position of one target character missing from query."""

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


def match_and_score(query: str, sentence: str) -> int | None:
    """Return the highest legal exact/one-edit substring score.

    Both inputs must already be normalized. An empty query is invalid at this
    internal boundary; production callers handle it before invoking Search Core.
    """

    if query == "":
        raise ValueError("query must not be empty")

    query_length = len(query)
    sentence_length = len(sentence)

    # Exact score is strictly higher than every edited score for this query.
    if query in sentence:
        return exact_score(query_length)

    best_score: int | None = None

    if query_length <= sentence_length:
        for start in range(sentence_length - query_length + 1):
            position = _substitution_position(query, sentence, start)
            if position is None:
                continue
            score = edited_score(query_length - 1, substitution_penalty(position))
            if best_score is None or score > best_score:
                best_score = score

    shorter_length = query_length - 1
    if shorter_length <= sentence_length:
        for start in range(sentence_length - shorter_length + 1):
            position = _extra_query_position(query, sentence, start, shorter_length)
            if position is None:
                continue
            score = edited_score(query_length - 1, extra_or_missing_penalty(position))
            if best_score is None or score > best_score:
                best_score = score

    longer_length = query_length + 1
    if longer_length <= sentence_length:
        for start in range(sentence_length - longer_length + 1):
            position = _missing_query_position(query, sentence, start)
            if position is None:
                continue
            score = edited_score(query_length, extra_or_missing_penalty(position))
            if best_score is None or score > best_score:
                best_score = score

    return best_score
