import pytest

from autocomplete.keyboard_layout import convert_hebrew_keyboard_to_english


@pytest.mark.parametrize(
    ("hebrew_key", "english_key"),
    [
        ("א", "t"),
        ("ב", "c"),
        ("ג", "d"),
        ("ד", "s"),
        ("ה", "v"),
        ("ו", "u"),
        ("ז", "z"),
        ("ח", "j"),
        ("ט", "y"),
        ("י", "h"),
        ("כ", "f"),
        ("ך", "l"),
        ("ל", "k"),
        ("מ", "n"),
        ("ם", "o"),
        ("נ", "b"),
        ("ן", "i"),
        ("ס", "x"),
        ("ע", "g"),
        ("פ", "p"),
        ("ף", ";"),
        ("צ", "m"),
        ("ץ", "."),
        ("ק", "e"),
        ("ר", "r"),
        ("ש", "a"),
        ("ת", ","),
    ],
)
def test_maps_every_hebrew_letter_key(
    hebrew_key: str,
    english_key: str,
) -> None:
    assert convert_hebrew_keyboard_to_english(hebrew_key) == english_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("יקךךם", "hello"),
        ("hello world", "hello world"),
        ("יקךךם world", "hello world"),
        ("", ""),
        ("123 !?", "123 !?"),
    ],
)
def test_converts_queries_without_changing_unmapped_characters(
    raw: str,
    expected: str,
) -> None:
    assert convert_hebrew_keyboard_to_english(raw) == expected
