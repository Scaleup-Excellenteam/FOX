from autocomplete.snapshot_sync.chunker import restore_snapshot, split_snapshot


def test_chunks_reassemble_snapshot_byte_identically(tmp_path) -> None:
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "manifest.binpb").write_bytes(b"manifest\x00bytes")
    (source / "nested" / "records.binpb").write_bytes(bytes(range(256)) * 3)
    (source / "empty.binpb").write_bytes(b"")

    chunks = split_snapshot(source, chunk_size=73)
    restored = restore_snapshot(chunks, tmp_path / "restored")

    source_files = sorted(
        path.relative_to(source) for path in source.rglob("*") if path.is_file()
    )
    restored_files = sorted(
        path.relative_to(restored) for path in restored.rglob("*") if path.is_file()
    )
    assert restored_files == source_files
    assert all(
        (restored / relative).read_bytes() == (source / relative).read_bytes()
        for relative in source_files
    )
