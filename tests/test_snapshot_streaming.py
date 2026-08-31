import struct
from pathlib import Path

import pytest

from autocomplete.snapshot_loader import MAX_PAYLOAD, SnapshotError, _frames


class TrackingReader:
    def __init__(self, wrapped, sizes):
        self.wrapped = wrapped
        self.sizes = sizes

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.wrapped.__exit__(*args)

    def read(self, size=-1):
        self.sizes.append(size)
        return self.wrapped.read(size)


def test_frozen_frames_use_big_endian_lengths_and_bounded_reads(monkeypatch, tmp_path):
    payloads = [b"x" * 4096, b"second"]
    path = tmp_path / "records.binpb"
    path.write_bytes(
        b"".join(struct.pack(">I", len(payload)) + payload for payload in payloads)
    )
    sizes = []
    real_open = Path.open

    def tracking_open(value, *args, **kwargs):
        opened = real_open(value, *args, **kwargs)
        return TrackingReader(opened, sizes) if value == path else opened

    monkeypatch.setattr(Path, "open", tracking_open)
    assert list(_frames(path)) == payloads
    assert -1 not in sizes


@pytest.mark.parametrize(
    "data",
    [b"\x00", struct.pack(">I", 5) + b"x", struct.pack(">I", MAX_PAYLOAD + 1)],
)
def test_malformed_frozen_frames_are_rejected(tmp_path, data):
    path = tmp_path / "records.binpb"
    path.write_bytes(data)
    with pytest.raises(SnapshotError):
        list(_frames(path))
