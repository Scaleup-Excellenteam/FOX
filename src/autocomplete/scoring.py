"""Official Part A scoring tables and arithmetic."""


def substitution_penalty(position: int) -> int:
    """Return the substitution penalty for a 1-based query position."""

    if position < 1:
        raise ValueError("position must be at least 1")
    return max(1, 6 - position)


def extra_or_missing_penalty(position: int) -> int:
    """Return the extra/missing-character penalty for a 1-based position."""

    if position < 1:
        raise ValueError("position must be at least 1")
    return max(2, 12 - 2 * position)


def exact_score(query_length: int) -> int:
    """Return the score of an exact match of the supplied query length."""

    return 2 * query_length


def edited_score(matching_characters: int, penalty: int) -> int:
    """Apply the official edit formula without clamping negative scores."""

    return 2 * matching_characters - penalty
