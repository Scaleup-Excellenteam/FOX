from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from autocomplete.autocomplete_snapshot_pb2 import ShardMetadata
from autocomplete.snapshot_loader import (
    FRAMING_VERSION,
    MAGIC,
    SHARD_READ_CHUNK_BYTES,
    _crc32c,
    _frames,
)


class _TrackingReader:
    def __init__(self, wrapped, read_sizes):
        self._wrapped = wrapped
        self._read_sizes = read_sizes

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self._wrapped.__exit__(*args)

    def read(self, size=-1):
        self._read_sizes.append(size)
        return self._wrapped.read(size)

    def seek(self, *args):
        return self._wrapped.seek(*args)


def test_shard_validation_uses_only_bounded_reads(monkeypatch, tmp_path):
    payload = b"x" * 4096
    frame = (
        struct.pack("<I", len(payload))
        + payload
        + struct.pack("<I", _crc32c(payload))
    )
    frame_count = 600
    data = (
        MAGIC
        + struct.pack("<II", FRAMING_VERSION, 1)
        + frame * frame_count
    )
    shard_path = tmp_path / "large-record-shard.binpb"
    shard_path.write_bytes(data)
    metadata = ShardMetadata(
        framed_size_bytes=len(data),
        frame_count=frame_count,
        sha256=hashlib.sha256(data).digest(),
    )

    read_sizes = []
    real_open = Path.open

    def tracking_open(path, *args, **kwargs):
        opened = real_open(path, *args, **kwargs)
        if path == shard_path:
            return _TrackingReader(opened, read_sizes)
        return opened

    monkeypatch.setattr(Path, "open", tracking_open)

    assert sum(1 for _ in _frames(shard_path, 1, metadata)) == frame_count
    assert -1 not in read_sizes
