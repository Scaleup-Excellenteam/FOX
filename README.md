# FOX Offline Index Snapshot

The C++ builder recursively reads UTF-8 `.txt` files in lexicographic relative-POSIX path order. Every complete line—including empty, whitespace-only, punctuation-only, or duplicate lines—is a separate record. Original text and 1-based source lines are preserved. Invalid UTF-8 fails the build. Normalized text uses ASCII normalization v1 and contributes unique character 1/2/3-gram postings; spaces are indexed and repeated grams within one sentence produce one posting ID.

## Prerequisites and build

The builder requires C++17, CMake 3.20+, OpenSSL development headers, and Protobuf 3.21+ development headers/runtime. Python requires 3.10+ and `protobuf>=5.29`.

```bash
cmake -S cpp -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
./build/fox_snapshot_builder path/to/corpus data/snapshots/example
```

An optional third argument selects the target shard size in bytes (minimum 1024; default 4 MiB). The destination must not exist. Failed builds remove their staging directory; successful publication is an atomic rename and snapshots are never patched in place.

Both `cpp/generated/autocomplete_snapshot.pb.*` and `src/autocomplete/autocomplete_snapshot_pb2.*` are generated from `proto/autocomplete_snapshot.proto`. Regenerate them with a compatible `protoc` after an approved schema change.

## Artifacts and identity

A snapshot contains `manifest.binpb`, framed `records-NNNNN.binpb`, and framed `index-NNNNN.binpb` files. A shard starts with `FOXSNAP1`, little-endian framing version and kind. Each frame contains a little-endian payload length, one Protobuf message, and CRC32C of that payload. Frames are limited to 8 MiB; large posting lists are split into ordered chunks that may cross shards. The manifest includes SHA-256 shard checksums, key bounds, counts, format/configuration versions, shard target, corpus digest, content/configuration-derived snapshot ID, and creation timestamp.

The corpus digest is SHA-256 over records in deterministic ID order. Each path and original-text field is encoded as uint64-le byte length followed by UTF-8 bytes; the source line is uint64-le. The index digest similarly encodes gram size, length-delimited UTF-8 gram, posting count, and every sentence ID as uint64-le. The snapshot ID covers both digests and the schema/framing/normalization/index/gram/shard configuration. `created_at_utc` is the Unix epoch so equivalent input and configuration produce byte-identical snapshots.

## Load and retrieve candidates

```python
from pathlib import Path
from autocomplete.snapshot_loader import load_snapshot

records_by_id, index = load_snapshot(Path("data/snapshots/example"))
for sentence_id in index.get_candidate_ids("to be"):
    print(records_by_id[sentence_id].original)
```

The loader validates the complete manifest, identity digests, shard names/kinds/sizes/hashes/key bounds, framing and CRC32C, sequential record IDs, source paths, posting order/chunks, and posting-to-record references before returning runtime state.

For a one-character query, lookup returns every ID. Empty-query behavior is intentionally the same broad fallback while the shared team policy remains unfrozen. Longer queries split at `len(query) // 2`. A half of length one uses its 1-gram; length two uses its 2-gram; longer halves intersect every overlapping 3-gram posting. The halves are unioned. This preserves recall for one edit and permits false positives for the authoritative matcher to reject. The index never scores, ranks, or decides matches.

Build work is linear in corpus bytes plus emitted unique gram/record relationships. The builder keeps records and the inverted postings required to produce the snapshot, but serializes and flushes one shard at a time rather than retaining a second serialized corpus/index. Runtime lookup cost is posting intersection and union; index storage is proportional to unique `(gram, sentence_id)` pairs.

`LocalArtifactStore` and optional `GCSArtifactStore` fully download/copy and validate a snapshot in staging before atomic local publication. GCS is startup-only and never appears in query lookup.

## Tests and benchmarks

```bash
python -m pytest
ctest --test-dir build --output-on-failure
PYTHONPATH=src python benchmarks/benchmark_snapshot.py build/fox_snapshot_builder path/to/corpus /tmp/fox-benchmark-snapshot "a" "to be"
```

The benchmark reports file/sentence counts, build and load times, serialized size, shard counts, peak load and runtime index memory, per-order index contribution and posting distributions, candidate averages and query-length buckets, reduction/fallback rates, and candidate latency. Use the same corpus and query list for comparisons; cloud transfer is excluded from online metrics.
