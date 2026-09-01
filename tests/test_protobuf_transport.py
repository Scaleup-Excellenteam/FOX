import io
import struct

import pytest

from autocomplete.protobuf_transport import FrameError, encode_frame, read_single_frame


def test_frame_round_trip() -> None:
    framed = encode_frame(b"protobuf", max_bytes=32)
    assert framed[:4] == struct.pack(">I", 8)
    assert read_single_frame(io.BytesIO(framed), max_bytes=32) == b"protobuf"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"\x00", "truncated frame header"),
        (struct.pack(">I", 0), "zero-length payload"),
        (struct.pack(">I", 4) + b"ab", "truncated frame body"),
        (struct.pack(">I", 33), "payload exceeds 32 byte limit"),
        (struct.pack(">I", 1) + b"a!", "unexpected bytes after frame"),
    ],
)
def test_invalid_frames(data: bytes, message: str) -> None:
    with pytest.raises(FrameError, match=message):
        read_single_frame(io.BytesIO(data), max_bytes=32)


def test_clean_eof_is_not_a_malformed_frame() -> None:
    assert read_single_frame(io.BytesIO(), max_bytes=32) is None


def test_encoder_rejects_empty_and_oversized_payloads() -> None:
    with pytest.raises(FrameError, match="zero-length"):
        encode_frame(b"", max_bytes=2)
    with pytest.raises(FrameError, match="exceeds"):
        encode_frame(b"abc", max_bytes=2)
