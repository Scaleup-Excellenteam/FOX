"""Query correction for text typed with the Hebrew keyboard layout."""

_HEBREW_TO_ENGLISH_KEYS = str.maketrans(
    {
        "א": "t",
        "ב": "c",
        "ג": "d",
        "ד": "s",
        "ה": "v",
        "ו": "u",
        "ז": "z",
        "ח": "j",
        "ט": "y",
        "י": "h",
        "כ": "f",
        "ך": "l",
        "ל": "k",
        "מ": "n",
        "ם": "o",
        "נ": "b",
        "ן": "i",
        "ס": "x",
        "ע": "g",
        "פ": "p",
        "ף": ";",
        "צ": "m",
        "ץ": ".",
        "ק": "e",
        "ר": "r",
        "ש": "a",
        "ת": ",",
    }
)


def convert_hebrew_keyboard_to_english(text: str) -> str:
    """Convert Hebrew-layout keystrokes to their English-key equivalents.

    Characters outside the standard Hebrew letter keys are preserved. This
    makes English-only and mixed-layout queries safe to process with the same
    function.
    """

    return text.translate(_HEBREW_TO_ENGLISH_KEYS)
