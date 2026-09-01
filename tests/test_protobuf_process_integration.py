import struct
import subprocess
import sys
from pathlib import Path

import pytest

from autocomplete.generated import autocomplete_search_pb2 as search_pb2
from autocomplete.protobuf_client import SearchClientError, search
from autocomplete.protobuf_transport import REQUEST_MAX_BYTES, encode_frame
from autocomplete.search_engine import SearchEngine
from autocomplete.snapshot_loader import load_snapshot


@pytest.fixture(scope="module")
def protobuf_snapshot(tmp_path_factory, builder: Path) -> Path:
    root = tmp_path_factory.mktemp("protobuf-corpus")
    corpus = root / "corpus"
    corpus.mkdir()
    (corpus / "a.txt").write_text(
        "To be or not to be, that is the question.\n"
        "Database systems are useful.\n"
        "Unicode café lives here.\n"
        "A prefix target suffix.\n",
        encoding="utf-8",
    )
    nested = corpus / "nested"
    nested.mkdir()
    (nested / "b.txt").write_text(
        "Another DATABASE system.\nDatabase tools are useful.\n",
        encoding="utf-8",
    )
    snapshot = root / "snapshot"
    subprocess.run(
        [str(builder), "--corpus", str(corpus), "--output", str(snapshot)],
        check=True,
        capture_output=True,
    )
    return snapshot


def _native(snapshot: Path, query: str):
    records, index = load_snapshot(snapshot)
    by_location = {
        (record.original, record.source_path, record.line_number): sentence_id
        for sentence_id, record in records.items()
    }
    return [
        (
            item.completed_sentence,
            item.score,
            item.source_text,
            item.offset,
            by_location[(item.completed_sentence, item.source_text, item.offset)],
        )
        for item in SearchEngine(records, index).search(query)
    ]


def _protobuf(snapshot: Path, query: str):
    response = search(snapshot, query)
    assert response.WhichOneof("outcome") == "success"
    return [
        (
            item.completed_sentence,
            item.score,
            item.source_text,
            item.offset,
            item.sentence_id,
        )
        for item in response.success.results
    ]


@pytest.mark.parametrize(
    "query",
    [
        "database",
        "databqse",
        "databasee",
        "datbase",
        "target",
        "not-present-anywhere",
        "café",
        "DATA,BASE",
        "a",
    ],
)
def test_real_client_process_matches_native_final_results(
    protobuf_snapshot: Path, query: str
) -> None:
    assert _protobuf(protobuf_snapshot, query) == _native(protobuf_snapshot, query)


def _run_raw_adapter(snapshot: Path, data: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "autocomplete.protobuf_adapter",
            "--snapshot",
            str(snapshot),
        ],
        input=data,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    "data",
    [
        b"\x00",
        struct.pack(">I", 5) + b"ab",
        struct.pack(">I", REQUEST_MAX_BYTES + 1),
        struct.pack(">I", 1) + b"\xff",
        encode_frame(b"\x08\x01", max_bytes=REQUEST_MAX_BYTES) + b"extra",
    ],
)
def test_adapter_returns_framed_structured_error_without_stdout_logs(
    protobuf_snapshot: Path, data: bytes
) -> None:
    process = _run_raw_adapter(protobuf_snapshot, data)
    length = struct.unpack(">I", process.stdout[:4])[0]
    assert len(process.stdout) == length + 4
    response = search_pb2.SearchResponse.FromString(process.stdout[4:])
    assert response.error.code == search_pb2.SEARCH_ERROR_CODE_INVALID_REQUEST


def test_client_reports_unavailable_and_early_adapter(protobuf_snapshot: Path) -> None:
    with pytest.raises(SearchClientError, match="adapter unavailable"):
        search(protobuf_snapshot, "x", adapter_command=["/missing/adapter"])
    with pytest.raises(SearchClientError, match="without a response"):
        search(protobuf_snapshot, "x", adapter_command=[sys.executable, "-c", ""])


@pytest.mark.parametrize(
    ("program", "message"),
    [
        ("import sys;sys.stdout.buffer.write(b'\\x00')", "invalid adapter response"),
        (
            "import struct,sys;sys.stdout.buffer.write(struct.pack('>I',1)+b'\\xff')",
            "malformed Protobuf",
        ),
        (
            "import struct,sys;sys.stdout.buffer.write(struct.pack('>I',1048577))",
            "invalid adapter response",
        ),
    ],
)
def test_client_rejects_malformed_or_oversized_response(
    protobuf_snapshot: Path, program: str, message: str
) -> None:
    with pytest.raises(SearchClientError, match=message):
        search(
            protobuf_snapshot,
            "x",
            adapter_command=[sys.executable, "-c", program],
        )
