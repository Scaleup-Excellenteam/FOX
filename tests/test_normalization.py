import json
import string
from pathlib import Path

import pytest

from autocomplete.normalization import normalize

CONTRACT_PATH = Path(__file__).parent / "contracts" / "normalization_cases.json"


def _contract_cases() -> list[dict[str, str]]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(case["input"], case["expected"]) for case in _contract_cases()],
)
def test_all_frozen_normalization_vectors(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_frozen_punctuation_set_is_deleted_exactly() -> None:
    assert normalize(string.punctuation) == ""
    assert normalize("alpha,beta.gamma") == "alphabetagamma"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ASCII Case", "ascii case"),
        ("one     two", "one two"),
        ("  leading and trailing  ", "leading and trailing"),
        ("  Hello,   WORLD!!!  ", "hello world"),
        ("", ""),
        ("     ", ""),
        ("!!!", ""),
    ],
)
def test_targeted_ascii_normalization(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_non_ascii_code_points_and_non_ascii_whitespace_are_preserved() -> None:
    assert normalize("Ä CAFÉ\t世界\n") == "Ä cafÉ\t世界\n"
