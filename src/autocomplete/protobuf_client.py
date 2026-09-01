"""Separate command-line client for the Protobuf autocomplete interface."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from google.protobuf.message import DecodeError

from autocomplete.generated import autocomplete_search_pb2 as search_pb2
from autocomplete.protobuf_search_adapter import PROTOCOL_VERSION
from autocomplete.protobuf_transport import (
    REQUEST_MAX_BYTES,
    RESPONSE_MAX_BYTES,
    FrameError,
    encode_frame,
    read_single_frame,
)


class SearchClientError(RuntimeError):
    pass


def search(
    snapshot: Path,
    query: str,
    *,
    max_results: int = 5,
    timeout_seconds: float = 120.0,
    adapter_command: Sequence[str] | None = None,
) -> search_pb2.SearchResponse:
    request = search_pb2.SearchRequest(
        protocol_version=PROTOCOL_VERSION,
        query=query,
        max_results=max_results,
    )
    framed_request = encode_frame(
        request.SerializeToString(deterministic=True), max_bytes=REQUEST_MAX_BYTES
    )
    command = (
        list(adapter_command)
        if adapter_command
        else [
            sys.executable,
            "-m",
            "autocomplete.protobuf_adapter",
            "--snapshot",
            str(snapshot),
        ]
    )
    try:
        process = subprocess.run(
            command,
            input=framed_request,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as error:
        raise SearchClientError(f"adapter unavailable: {command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise SearchClientError("adapter timed out") from error

    try:
        payload = read_single_frame(
            stream=_BytesReader(process.stdout), max_bytes=RESPONSE_MAX_BYTES
        )
    except FrameError as error:
        raise SearchClientError(f"invalid adapter response: {error}") from error
    if payload is None:
        diagnostic = process.stderr.decode("utf-8", errors="replace").strip()
        detail = f": {diagnostic}" if diagnostic else ""
        raise SearchClientError(f"adapter exited without a response{detail}")

    response = search_pb2.SearchResponse()
    try:
        response.ParseFromString(payload)
    except DecodeError as error:
        raise SearchClientError("adapter returned malformed Protobuf") from error
    if response.protocol_version != PROTOCOL_VERSION:
        raise SearchClientError(
            f"unsupported response protocol version: {response.protocol_version}"
        )
    if response.WhichOneof("outcome") is None:
        raise SearchClientError("adapter response has no outcome")
    return response


class _BytesReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._data) - self._offset
        result = self._data[self._offset : self._offset + size]
        self._offset += len(result)
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query the Protobuf adapter.")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("query")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        response = search(
            arguments.snapshot,
            arguments.query,
            max_results=arguments.max_results,
            timeout_seconds=arguments.timeout,
        )
    except (FrameError, SearchClientError, ValueError) as error:
        print(f"Search failed: {error}", file=sys.stderr)
        return 1

    if response.WhichOneof("outcome") == "error":
        print(
            f"Search failed ({response.error.code}): {response.error.message}",
            file=sys.stderr,
        )
        return 2
    if not response.success.results:
        print("No completions found.")
        return 0
    for result in response.success.results:
        print(
            f"{result.completed_sentence} | score: {result.score} | "
            f"source: {result.source_text} | offset: {result.offset} | "
            f"sentence_id: {result.sentence_id}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
