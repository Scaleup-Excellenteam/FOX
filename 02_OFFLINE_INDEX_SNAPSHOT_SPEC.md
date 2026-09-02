# SPEC 2 — Offline Index, Protobuf Snapshot & Runtime SearchIndex v2.1

## Role

**C++ Offline Builder, Cross-Language Snapshot, Python Snapshot Loader & Recall-Safe Runtime Index**

Use with `00_TEAM_BASELINE.md` and `PHASE0_SHARED_FOUNDATION_SPEC.md`.

## 1. Branch

```text
feature/offline-index-snapshot
```

It must start from the frozen `PHASE0_COMMIT`.

## 2. Ownership

You own primarily:

```text
cpp/include/...
cpp/src/...
cpp/tests/...
cpp/CMakeLists.txt

src/autocomplete/snapshot_loader.py
src/autocomplete/index.py

tests/test_snapshot_loader.py
tests/test_index.py
cross-language snapshot fixtures/tests
offline/index benchmark measurements
```

You are the main implementer consuming the frozen `.proto`, but you may not silently change the frozen schema.

You do not own Python matcher/scoring, ranking, SearchEngine, ReferenceEngine, official API facade, CLI, or Part B cloud services.

## 3. C++ Corpus Loader

Input: a corpus root directory.

Required behavior:

1. recursively discover regular `.txt` files, extension case-insensitive;
2. convert each path to a relative POSIX-style path;
3. sort those paths lexicographically;
4. decode each file as UTF-8, accepting optional UTF-8 BOM;
5. accept LF/CRLF line endings;
6. process physical lines in order with 1-based line numbers;
7. preserve the exact decoded source line excluding line terminator/BOM; a UTF-8 BOM is an encoding marker, not sentence content;
8. normalize with the frozen C++ normalization implementation;
9. skip lines whose normalized text is empty;
10. assign deterministic sequential `sentence_id` values only to retained searchable records, starting at `1`; `0` is reserved/invalid.

Invalid UTF-8 is a build error that names the source path.

Frozen builder invocation:

```bash
./build/cpp/autocomplete_builder \
  --corpus <extracted-corpus-root> \
  --output <snapshot-directory>
```

The provided `Archive.zip` is extracted before the builder is invoked. Direct ZIP parsing is optional and not required for Part A. The builder writes to a temporary/incomplete location and only makes the output snapshot available as complete after all files and the manifest are successfully written.

## 4. C++ Normalization Parity

Implement the exact Phase 0 normalization semantics in C++ without locale-dependent lowercasing.

The same `tests/contracts/normalization_cases.json` must be consumed by C++ tests.

Required invariant:

```text
normalize_cpp(input) == expected
```

Together with Member 1's tests:

```text
normalize_cpp(input) == normalize_python(input)
```

## 5. Offline Record

Each retained record logically contains:

```text
sentence_id
original
normalized
source_path
line_number
```

The snapshot must allow Python to reconstruct `SentenceRecord` exactly.

## 6. Character 1/2/3-Gram Inverted Index

Frozen v2.1 initial strategy:

```text
(gram_size, gram) → sorted unique sentence IDs
```

Build `gram_size` 1, 2, and 3 from normalized sentences.

Rules:

- after UTF-8 decoding, gram length/slicing uses Unicode code points, not UTF-8 bytes;
- `GramPostingProto.gram` stores the resulting gram as UTF-8 text;
- normalized ASCII spaces are characters and participate in grams;
- punctuation is already removed;
- a repeated gram in one sentence contributes that sentence ID only once to that posting;
- posting IDs are sorted ascending before serialization;
- index output is deterministic for the same logical corpus/configuration.

## 7. Runtime Candidate Algorithm

Python API:

```python
class SearchIndex:
    def get_candidate_ids(self, normalized_query: str) -> list[int]:
        ...
```

The production SearchEngine does not call it for an empty normalized query.

For `m = len(normalized_query)` in Unicode code points:

```text
m == 1 → all searchable sentence IDs
m >= 2 → split_at = m // 2
         left = query[:split_at]
         right = query[split_at:]
```

Exact seed lookup:

```text
len(seed) == 1 → 1-gram posting
len(seed) == 2 → 2-gram posting
len(seed) >= 3 → intersection of all overlapping 3-gram postings
```

Final result:

```text
seed_candidates(left) UNION seed_candidates(right)
```

Return unique IDs in ascending order for deterministic tests.

### Correctness rule

False positives are acceptable. False negatives for legal Part A matches are not.

The two-part design is based on the one-edit partition/pigeonhole observation: one edit can disrupt at most one of two non-overlapping query partitions in a legal alignment, leaving at least one exact seed. Exact verification still belongs only to Member 1's matcher.

If a proposed pruning optimization cannot prove recall safety, broaden candidates rather than risk correctness.

## 8. Why 1/2/3 Grams Only

- 1-gram is needed for one-character seeds in short queries.
- 2-gram directly represents two-character seeds.
- 3-gram provides stronger filtering for longer seeds.
- longer exact seeds are represented by intersecting all overlapping trigrams.
- 4/5-gram indexes are not needed for recall and are added only after benchmark evidence shows net value.

## 9. Protobuf Snapshot

Use the frozen Phase 0 schema:

```text
SentenceRecordProto
GramPostingProto
SnapshotManifestProto
```

Do not create a parallel production JSON snapshot schema.

### Initial files

```text
snapshot/
├── manifest.binpb
├── records.binpb
└── index.binpb
```

`manifest.binpb` = one protobuf message.

Records/postings are framed:

```text
4-byte unsigned big-endian payload length
+ protobuf payload
```

A malformed/truncated frame is a hard load error.

### Optional future sharding

If file sizes/build/load benchmarks justify it, split the framed streams into numbered files and list them in the manifest. Do not implement sharding merely for appearance.

## 10. Manifest Validation

Required manifest values:

```text
schema_version = 1
normalization_version = 1
index_strategy_version = 1
gram_sizes exactly [1, 2, 3]
corpus_digest_sha256
snapshot_id
created_at_utc
record file list
index file list
searchable_record_count
posting_count
index_digest_sha256
```

Python loader rejects:

- unsupported version;
- unexpected gram sizes;
- missing listed file;
- malformed manifest/message/frame;
- duplicate sentence IDs;
- posting referencing unknown sentence ID;
- sentence ID `0`;
- invalid gram size;
- non-sorted/duplicate posting IDs if the writer contract is violated;
- record/posting count mismatch;
- corpus digest mismatch;
- index digest mismatch.

Do not serve a partially loaded snapshot.

### Snapshot V2 exact Top-K extension

Index strategy version 2 adds `gram_topk.binpb`, a framed stream of
`PrecomputedGramTopKProto` messages. The manifest discovers and protects this
artifact through `topk_files`, `topk_entry_count`, and
`topk_digest_sha256`; an implicit filename is never used. Every 1/2/3-gram
posting has exactly one entry containing its exact occurrence count and up to
five sentence IDs ordered by completed sentence, source path, line offset, and
stable sentence-ID encounter order.

Compatibility is explicit:

- A current loader accepts an index-strategy-V1 manifest only when all Top-K
  fields declare the artifact absent, then uses the legacy exact/fuzzy path.
- A current loader requires the complete Top-K declaration for V2 and rejects
  a missing, duplicate, unsafe, truncated, corrupt, misordered, or
  integrity-mismatched artifact before returning an index.
- A V1 loader rejects V2 through the index strategy version check.

The V2 snapshot identity adds `topk_digest_sha256` to the canonical identity
block and sets `index_strategy_version=2`. Schema and normalization versions
remain 1; V1 record and posting artifacts retain their existing encoding.

## 11. Stable Corpus Digest, Index Digest and Snapshot ID

Do **not** hash serialized protobuf bytes as the long-lived identity. Protobuf serialization is not canonical.

### 11.1 Corpus digest

Build `corpus_digest_sha256` from the deterministic retained-record stream in ascending `sentence_id`.

For every record, append these canonical bytes to SHA-256:

```text
u64_be(sentence_id)
u64_be(len_utf8(source_path)) + UTF-8(source_path)
u64_be(line_number)
u64_be(len_utf8(original)) + UTF-8(original)
u64_be(len_utf8(normalized)) + UTF-8(normalized)
```

`u64_be` means unsigned 64-bit big-endian. This removes delimiter ambiguity and is identical across C++ and Python.

### 11.2 Index digest

Build `index_digest_sha256` from postings sorted by:

```text
gram_size ascending
then UTF-8 gram bytes lexicographically
```

For each posting, hash:

```text
u32_be(gram_size)
u64_be(len_utf8(gram)) + UTF-8(gram)
u64_be(number_of_sentence_ids)
u64_be(sentence_id_1)
u64_be(sentence_id_2)
...
```

Posting sentence IDs are already sorted unique ascending.

The Python loader recomputes both logical digests after parsing and rejects a mismatch.

### 11.3 Snapshot identity

Compute `snapshot_id` from this exact canonical UTF-8 block:

```text
corpus_digest_sha256=<hex>\n
index_digest_sha256=<hex>\n
schema_version=1\n
normalization_version=1\n
index_strategy_version=1\n
gram_sizes=1,2,3\n
```

Hash the exact bytes with SHA-256 and store lowercase hexadecimal output.

`created_at_utc` is excluded from identity. Including the logical index digest ensures two snapshots with different posting content cannot silently share the same snapshot identity.

## 12. Python Snapshot Loader

Frozen API:

```python
from pathlib import Path


def load_snapshot(
    snapshot_path: Path,
) -> tuple[dict[int, SentenceRecord], SearchIndex]:
    ...
```

Responsibilities:

```text
read/validate manifest
read framed record messages
reconstruct SentenceRecord objects
read framed posting messages
validate postings/record references
validate sentence_id > 0
validate manifest record/posting counts
recompute/validate corpus and index digests
construct SearchIndex
return records_by_id + SearchIndex
```

Member 3 must not need protobuf/framing knowledge.

## 13. Snapshot Update Policy

Part A snapshots are immutable.

Corpus change:

```text
rebuild completely
→ create new snapshot identity
→ validate
→ use new snapshot at next startup
```

No live mutation, incremental deletes, or hot swap is required in Part A.

## 14. Local-Only Part A

Part A has **no GCS implementation requirement** in v2.1.

Snapshot input/output is local disk. This keeps the project runnable without credentials and keeps network I/O out of the query path.

If the team later chooses GCS as a Part B feature or deployment enhancement, add a separate integration layer without modifying SearchIndex/matcher semantics.

## 15. Required C++ Tests

- root-level `.txt`;
- nested/deeply nested `.txt`;
- non-`.txt` ignored;
- case-insensitive `.TXT` policy;
- deterministic path sorting;
- multiple physical lines;
- 1-based line numbers;
- LF and CRLF parity;
- UTF-8 BOM handling;
- invalid UTF-8 failure;
- normalized-empty line skipped while line numbers remain physical;
- deterministic sentence IDs starting at 1, with 0 never emitted;
- Unicode code-point gram behavior on at least one valid non-ASCII UTF-8 fixture;
- normalization golden vectors;
- 1/2/3-gram postings;
- repeated gram deduplicated per record;
- sorted postings;
- manifest creation;
- framed record/posting writing;
- stable corpus/index digests and snapshot ID for repeated logical builds.

## 16. Required Python Tests

### Loader

- record reconstruction;
- original/normalized/path/line/ID preservation;
- valid manifest load;
- missing file;
- truncated frame;
- corrupt protobuf;
- unsupported schema/normalization/index version;
- wrong gram sizes;
- duplicate record ID;
- record ID 0 rejected;
- posting references missing record;
- invalid posting order/duplicates;
- record/posting count mismatch;
- corpus digest mismatch;
- index digest mismatch.

### SearchIndex

- 1-char broad fallback;
- 2-char 1+1;
- 3-char 1+2;
- 4-char 2+2;
- 5-char 2+3;
- 6+ balanced partitions;
- 3+-seed trigram intersection;
- left/right union;
- sorted unique results;
- exact candidate recall;
- substitution in either partition;
- extra query character in either partition;
- missing query character in either partition;
- edit at/before/after split boundary;
- no false negatives on deterministic generated legal queries.

## 17. Cross-Language Round Trip

Required integration fixture:

```text
small nested corpus
→ C++ build
→ snapshot
→ Python load
```

Validate:

```text
record count/content
source paths/physical line numbers
sentence IDs
selected normalized values
selected postings
candidate behavior
manifest versions/counts/corpus digest/index digest
```

Member 3 owns the full SearchEngine E2E harness; Member 2 provides a stable builder/loader fixture.

## 18. Performance Responsibility

Measure after correctness:

```text
file count
searchable record count
offline build time
records/index serialized size
Python load time
runtime posting/index memory estimate
posting size distribution by gram size
candidate count by query-length bucket
candidate-generation latency
candidate reduction ratio
1-char fallback rate
```

Use one fixed corpus/query set for comparisons. Do not add an index structure merely because it seems theoretically faster.

## 19. Definition of Done

- [ ] corpus traversal/I/O contract correct;
- [ ] C++ normalization golden parity passes;
- [ ] deterministic IDs/paths/line numbers correct; sentence IDs start at 1 and 0 is invalid;
- [ ] 1/2/3 postings deterministic, sorted, unique, and use Unicode code-point gram boundaries;
- [ ] candidate algorithm matches frozen partition strategy;
- [ ] no known candidate false negatives;
- [ ] Protobuf framing works cross-language;
- [ ] builder command follows the frozen `--corpus/--output` invocation and publishes manifest last/only after successful data writing;
- [ ] manifest counts, corpus digest, index digest, snapshot identity, and version validation work;
- [ ] C++→Python round trip passes;
- [ ] loader returns frozen API types;
- [ ] local snapshot workflow works without cloud credentials;
- [ ] offline/index metrics captured;
- [ ] C++ and Python owned tests/lint/build pass;
- [ ] shared `.proto` and contracts unchanged unless team-approved.

## 20. Codex Instruction

> Implement only Offline Index/Snapshot v2.1. Use C++ for corpus preprocessing/index construction and the frozen Protobuf schema/framing for C++→Python snapshot data. Implement Python loader and SearchIndex only. Preserve candidate recall before candidate reduction. Do not implement matcher/scoring, ranking, SearchEngine, ReferenceEngine, CLI, GCS, or Part B features. Never create a new Git root/orphan history; work only on the assigned branch created from PHASE0_COMMIT.

## 21. Technical References

- Protocol Buffers overview: https://protobuf.dev/overview/
- Streaming multiple messages and large data sets: https://protobuf.dev/programming-guides/techniques/
- Protobuf serialization is not canonical: https://protobuf.dev/programming-guides/serialization-not-canonical/
- Approximate matching lecture notes: https://www.cs.jhu.edu/~langmea/resources/lecture_notes/06_approximate_matching_v2.pdf
