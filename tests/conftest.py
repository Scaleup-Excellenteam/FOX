from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def builder():
    return Path(__file__).with_name("reference_builder.py")
