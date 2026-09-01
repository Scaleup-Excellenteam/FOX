"""Authoritative exact/one-edit substring matcher."""

from autocomplete.scoring import (
    edited_score,
    exact_score,
    extra_or_missing_penalty,
    substitution_penalty,
)


def _best_gap_position(
    query: str,
    sentence: str,
    *,
    substitution: bool,
) -> int | None:
    """Return the latest position matching two query pieces around one character."""

    query_length = len(query)
    sentence_length = len(sentence)
    window_length = query_length if substitution else query_length + 1
    if window_length > sentence_length:
        return None

    # An inserted sentence character at either boundary would leave ``query``
    # as an exact substring. The caller has already ruled exact matches out.
    lowest_index = 0 if substitution else 1
    highest_index = query_length - 1
    last_start = sentence_length - window_length
    find = sentence.find
    startswith = sentence.startswith

    for index in range(highest_index, lowest_index - 1, -1):
        if substitution:
            left = query[:index]
            right = query[index + 1 :]
        else:
            left = query[:index]
            right = query[index:]

        left_length = index
        right_length = query_length - index - (1 if substitution else 0)

        if left_length == 0 and right_length == 0:
            return index + 1

        # Anchor on the longer fixed piece so repeated-character inputs produce
        # as few Python-level occurrence checks as possible. ``find`` and
        # ``startswith`` compare the actual characters in optimized C loops.
        if left_length >= right_length:
            start = find(left, 0, last_start + left_length)
            while start != -1:
                if startswith(right, start + left_length + 1):
                    return index + 1
                start = find(left, start + 1, last_start + left_length)
        else:
            right_offset = left_length + 1
            right_start = find(
                right,
                right_offset,
                last_start + right_offset + right_length,
            )
            while right_start != -1:
                start = right_start - right_offset
                if startswith(left, start):
                    return index + 1
                right_start = find(
                    right,
                    right_start + 1,
                    last_start + right_offset + right_length,
                )

    return None


def _best_extra_query_position(query: str, sentence: str) -> int | None:
    """Return the latest position whose removal makes a substring match."""

    for index in range(len(query) - 1, -1, -1):
        if query[:index] + query[index + 1 :] in sentence:
            return index + 1
    return None


def match_and_score(query: str, sentence: str) -> int | None:
    """Return the highest legal exact/one-edit substring score.

    Both inputs must already be normalized. An empty query is invalid at this
    internal boundary; production callers handle it before invoking Search Core.
    """

    if query == "":
        raise ValueError("query must not be empty")

    query_length = len(query)

    # Exact score is strictly higher than every edited score for this query.
    if query in sentence:
        return exact_score(query_length)

    best_score: int | None = None

    position = _best_gap_position(query, sentence, substitution=True)
    if position is not None:
        best_score = edited_score(
            query_length - 1,
            substitution_penalty(position),
        )

    position = _best_extra_query_position(query, sentence)
    if position is not None:
        score = edited_score(
            query_length - 1,
            extra_or_missing_penalty(position),
        )
        if best_score is None or score > best_score:
            best_score = score

    position = _best_gap_position(query, sentence, substitution=False)
    if position is not None:
        score = edited_score(
            query_length,
            extra_or_missing_penalty(position),
        )
        if best_score is None or score > best_score:
            best_score = score

    return best_score
