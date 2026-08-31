"""Canonical Python normalization for the frozen v2.1 contract."""

_ASCII_PUNCTUATION = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_TRANSLATION = str.maketrans(
    {
        **{
            ord(upper): lower
            for upper, lower in zip(
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz", strict=True
            )
        },
        **{ord(character): None for character in _ASCII_PUNCTUATION},
    }
)


def normalize(text: str) -> str:
    """Return text normalized according to the cross-language v2.1 rules.

    Only ASCII uppercase letters, the frozen ASCII punctuation set, and ASCII
    spaces receive special treatment. Every other Unicode code point is kept
    unchanged.
    """

    translated = text.translate(_TRANSLATION)
    return " ".join(part for part in translated.split(" ") if part)
