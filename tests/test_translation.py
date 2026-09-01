from autocomplete.translation import translate_to_spanish


def test_translates_known_word() -> None:
    assert translate_to_spanish("hello") == "hola"


def test_unknown_word_passes_through_unchanged() -> None:
    assert translate_to_spanish("xyzzy") == "xyzzy"


def test_case_insensitive_lookup_returns_lowercase_spanish() -> None:
    assert translate_to_spanish("HELLO") == "hola"


def test_translates_each_word_in_multi_word_phrase() -> None:
    assert translate_to_spanish("hello world") == "hola mundo"


def test_preserves_word_order() -> None:
    assert translate_to_spanish("world hello") == "mundo hola"


def test_mixes_translated_and_unknown_words() -> None:
    assert translate_to_spanish("hello xyzzy world") == "hola xyzzy mundo"


def test_empty_string_returns_empty_string() -> None:
    assert translate_to_spanish("") == ""


def test_repeated_whitespace_between_words_is_collapsed() -> None:
    assert translate_to_spanish("hello   world") == "hola mundo"
