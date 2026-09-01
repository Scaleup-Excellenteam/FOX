"""Length-prefixed binary transport for the online Protobuf interface."""

from __future__ import annotations

import struct
from typing import BinaryIO

REQUEST_MAX_BYTES = 64 * 1024
RESPONSE_MAX_BYTES = 1024 * 1024


class FrameError(ValueError):
    """A framed message is malformed or violates its size limit."""


def encode_frame(payload: bytes, *, max_bytes: int) -> bytes:
    if not payload:
        raise FrameError("zero-length payload")
    if len(payload) > max_bytes:
        raise FrameError(f"payload exceeds {max_bytes} byte limit")
    return struct.pack(">I", len(payload)) + payload


def read_frame(stream: BinaryIO, *, max_bytes: int) -> bytes | None:
    header = stream.read(4)
    if header == b"":
        return None
    if len(header) != 4:
        raise FrameError("truncated frame header")
    (length,) = struct.unpack(">I", header)
    if length == 0:
        raise FrameError("zero-length payload")
    if length > max_bytes:
        raise FrameError(f"payload exceeds {max_bytes} byte limit")
    payload = stream.read(length)
    if len(payload) != length:
        raise FrameError("truncated frame body")
    return payload


def read_single_frame(stream: BinaryIO, *, max_bytes: int) -> bytes | None:
    payload = read_frame(stream, max_bytes=max_bytes)
    if payload is not None and stream.read(1) != b"":
        raise FrameError("unexpected bytes after frame")
    return payload
