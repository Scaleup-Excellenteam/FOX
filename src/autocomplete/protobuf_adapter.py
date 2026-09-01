"""One-request stdin/stdout process adapter for Protobuf autocomplete."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from google.protobuf.message import DecodeError

from autocomplete.generated import autocomplete_search_pb2 as search_pb2
from autocomplete.protobuf_search_adapter import SearchProtobufAdapter, error_response
from autocomplete.protobuf_transport import (
    REQUEST_MAX_BYTES,
    RESPONSE_MAX_BYTES,
    FrameError,
    encode_frame,
    read_single_frame,
)
from autocomplete.search_engine import SearchEngine
from autocomplete.snapshot_loader import load_snapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve one framed Protobuf search.")
    parser.add_argument("--snapshot", type=Path, required=True)
    return parser


def _write_response(response: search_pb2.SearchResponse) -> None:
    payload = response.SerializeToString(deterministic=True)
    try:
        framed = encode_frame(payload, max_bytes=RESPONSE_MAX_BYTES)
    except FrameError:
        fallback = error_response(
            search_pb2.SEARCH_ERROR_CODE_INTERNAL,
            "response exceeds configured size limit",
        ).SerializeToString(deterministic=True)
        framed = encode_frame(fallback, max_bytes=RESPONSE_MAX_BYTES)
    sys.stdout.buffer.write(framed)
    sys.stdout.buffer.flush()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        payload = read_single_frame(sys.stdin.buffer, max_bytes=REQUEST_MAX_BYTES)
        if payload is None:
            return 0
    except FrameError as error:
        _write_response(
            error_response(
                search_pb2.SEARCH_ERROR_CODE_INVALID_REQUEST,
                str(error),
            )
        )
        return 2

    request = search_pb2.SearchRequest()
    try:
        request.ParseFromString(payload)
    except DecodeError:
        _write_response(
            error_response(
                search_pb2.SEARCH_ERROR_CODE_INVALID_REQUEST,
                "malformed Protobuf request",
            )
        )
        return 2

    try:
        records_by_id, index = load_snapshot(arguments.snapshot)
        adapter = SearchProtobufAdapter(
            SearchEngine(records_by_id, index), records_by_id
        )
        response = adapter.handle(request)
    except Exception as error:
        print(f"adapter processing failed: {error}", file=sys.stderr)
        response = error_response(
            search_pb2.SEARCH_ERROR_CODE_INTERNAL,
            "search processing failed",
        )
        _write_response(response)
        return 1

    try:
        _write_response(response)
    except BrokenPipeError:
        print("adapter response pipe closed", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
