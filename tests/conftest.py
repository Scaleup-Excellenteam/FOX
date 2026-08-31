import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def builder(tmp_path_factory):
    root = Path(__file__).parents[1]
    build = tmp_path_factory.mktemp("cpp-production-build")
    command = ["cmake", "-S", str(root / "cpp"), "-B", str(build)]
    if include := os.environ.get("FOX_PROTOBUF_INCLUDE"):
        command.append(f"-DProtobuf_INCLUDE_DIR={include}")
    if library := os.environ.get("FOX_PROTOBUF_LIBRARY"):
        command.append(f"-DProtobuf_LIBRARY={library}")
    subprocess.run(command, check=True)
    subprocess.run(["cmake", "--build", str(build), "-j2"], check=True)
    return build / "autocomplete_builder"


@pytest.fixture(scope="session")
def reference_builder():
    return Path(__file__).with_name("reference_builder.py")
