import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="session")
def builder(tmp_path_factory):
    build = tmp_path_factory.mktemp("cpp-build")
    command = ["cmake", "-S", str(ROOT / "cpp"), "-B", str(build)]
    if include := os.environ.get("FOX_PROTOBUF_INCLUDE"):
        command.append(f"-DProtobuf_INCLUDE_DIR={include}")
    if library := os.environ.get("FOX_PROTOBUF_LIBRARY"):
        command.append(f"-DProtobuf_LIBRARY={library}")
    subprocess.run(command, check=True)
    subprocess.run(["cmake", "--build", str(build), "-j2"], check=True)
    return build / "fox_snapshot_builder"
