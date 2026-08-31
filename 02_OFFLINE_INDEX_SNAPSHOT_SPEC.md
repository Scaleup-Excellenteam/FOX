# 02_OFFLINE_INDEX_SNAPSHOT_SPEC.md

# SPEC 2 — Offline Index, Protobuf Snapshot & Performance

## Role

**C++ Offline Builder, Cross-Language Snapshot, Python Runtime Index & Storage**

**Architecture baseline:** v1.1

Use this file together with `00_TEAM_BASELINE.md`.

If anything conflicts, `00_TEAM_BASELINE.md` wins.

---

## 1. Branch

```text
feature/offline-index-snapshot
```

---

## 2. Primary Responsibility

You own:

```text
C++ recursive corpus loading
deterministic corpus traversal
C++ canonical normalization parity
deterministic SentenceRecord IDs
C++ Character 1/2/3-Gram inverted-index construction
Protobuf snapshot writing
snapshot sharding
snapshot validation metadata
Python snapshot loading
Python SearchIndex candidate retrieval
LocalArtifactStore
optional GCSArtifactStore
offline/startup/index performance benchmarks
```

You do **not** own:

```text
Python canonical normalization semantics
exact matcher
Google scoring
ranking
SearchEngine orchestration
Reference Engine
official Google facade
CLI behavior
```

---

## 3. Files You Primarily Own

Suggested C++ area:

```text
cpp/include/...
cpp/src/...
cpp/tests/...
cpp/CMakeLists.txt
```

Suggested Python runtime area:

```text
src/autocomplete/snapshot_loader.py
src/autocomplete/index.py
src/autocomplete/artifact_store.py

tests/test_snapshot_loader.py
tests/test_index.py
tests/test_artifact_store.py
benchmarks/
```

Shared schema:

```text
proto/autocomplete_snapshot.proto
```

You will be the primary implementation contributor to the schema, but after the initial team-approved version is frozen, semantic/field-number changes require all-team approval.

Shared normalization contract:

```text
tests/contracts/normalization_cases.json
```

You consume it; Member 1 leads its behavioral semantics.

---

# 4. C++ Recursive Corpus Loader

The offline builder receives a corpus root.

Requirements:

- recursively discover eligible text files;
- files may be deeply nested;
- each complete line is one sentence;
- never split a source line on punctuation;
- preserve exact original line text;
- preserve 1-based source line number;
- convert source paths to relative POSIX-style paths;
- normalize using the exact shared v1 normalization contract;
- assign deterministic sentence IDs.

---

# 5. Deterministic Traversal

Required deterministic build order:

```text
discover files recursively
→ convert to relative POSIX paths
→ lexicographically sort paths
→ process files in sorted order
→ process each file line 1..N
→ assign sequential sentence IDs
```

For identical corpus content and filtering rules:

```text
same source line
→ same sentence_id
```

Do not depend on filesystem enumeration order.

---

# 6. C++ Normalization Parity

Implement the same canonical semantics defined in `00_TEAM_BASELINE.md`.

Do not invent a C++-specific normalization rule.

Run the shared golden contract:

```text
tests/contracts/normalization_cases.json
```

Required invariant:

```text
normalize_cpp(input)
==
expected
```

Member 1's Python tests independently verify:

```text
normalize_python(input)
==
expected
```

Together they prove cross-language parity.

---

# 7. Offline Sentence Representation

The C++ builder should logically maintain:

```text
sentence_id
original
normalized
source_path
line_number
```

This data is serialized into the shared Protobuf snapshot so Python can reconstruct the frozen `SentenceRecord` model.

---

# 8. Character 1/2/3-Gram Inverted Index

Frozen v1.1 strategy:

```text
Character 1-Gram + 2-Gram + 3-Gram Inverted Index
```

Logical concept:

```text
(gram_size, gram) → sorted unique sentence IDs
```

Build grams from every eligible normalized sentence.

Requirements:

- include normalized ASCII spaces as characters;
- punctuation has already been removed by canonical normalization;
- do not add the same `sentence_id` twice to the same posting list even if a gram repeats in one sentence;
- sort posting lists by ascending `sentence_id` before snapshot serialization;
- produce deterministic postings for the same corpus/build configuration.

Why v1.1 stores 1/2/3 grams:

- 1-grams support recall-safe exact seeds of length one for short queries;
- 2-grams support exact seeds of length two;
- 3-grams provide stronger pruning for longer seeds;
- longer exact seeds are represented by intersecting all of their overlapping 3-gram posting lists;
- 4/5-gram indexes are intentionally not stored in v1.1 because they are not necessary for recall and must be justified by benchmark evidence before increasing snapshot/runtime size.

The index is a candidate-generation structure only. It does not decide whether a sentence actually matches the query.

# 9. Most Important Index Rule — Recall First

Candidate generation may return:

```text
false positives
```

because Member 1's exact matcher filters them.

Candidate generation must avoid:

```text
false negatives
```

because a missing legal candidate may change the Top 5.

Priority:

```text
100% recall for legal matches
before aggressive reduction
```

If a pruning rule cannot guarantee safety:

```text
fallback → broader candidate set
```

and when necessary:

```text
fallback → all eligible sentence IDs
```

Correctness comes before index cleverness.

## 9.1 Frozen v1.1 Candidate-Generation Algorithm

Input:

```text
normalized_query
```

Let:

```text
m = len(normalized_query)
```

Behavior:

```text
m == 1
→ return the broad safe candidate set

m >= 2
→ split query into two contiguous, non-overlapping,
  near-balanced partitions
```

Deterministic split:

```text
split_at = m // 2
left  = query[:split_at]
right = query[split_at:]
```

Examples:

```text
2 chars → 1 + 1
3 chars → 1 + 2
4 chars → 2 + 2
5 chars → 2 + 3
6 chars → 3 + 3
7 chars → 3 + 4
```

For each partition, compute exact-seed candidates:

```text
seed length 1
→ 1-gram posting

seed length 2
→ 2-gram posting

seed length >= 3
→ all overlapping 3-grams
→ INTERSECTION of their posting lists
```

Final candidate set:

```text
seed_candidates(left)
UNION
seed_candidates(right)
```

Return unique sentence IDs.

## 9.2 Why the Partition Is Recall-Safe

The assignment permits at most one edit.

For a legal one-edit alignment, dividing the query into two non-overlapping partitions means that a single substitution/insertion/deletion can disrupt at most one partition; therefore at least one partition remains an exact seed in the target alignment. Searching both seeds and taking their union preserves that legal candidate.

This is a filtering guarantee only. Sentence-level gram postings may still create false positives because all grams can occur in a sentence at different positions. That is acceptable: `match_and_score()` performs exact one-edit verification afterward.

## 9.3 Short Query Policy

```text
length 1
→ broad safe fallback
```

This case cannot be safely pruned by the typed character alone because one allowed substitution can turn that character into any target character.

Queries of length 2 and 3 use the 1-gram index as part of the normal two-part strategy instead of falling back to the whole corpus.

Empty/normalization-to-empty behavior remains governed by the shared Team-Decision-Required policy until explicitly frozen in `00_TEAM_BASELINE.md`.

# 10. Protobuf Is the Cross-Language Contract

Use:

```text
proto/autocomplete_snapshot.proto
```

Generate bindings for:

```text
C++
Python
```

The C++ offline builder serializes.

The Python runtime loader deserializes.

Do not create a separate ad-hoc JSON schema for production snapshot data.

JSON may still be used for human-readable tests/configuration where appropriate.

---

# 11. Snapshot Contents

The logical snapshot must contain enough information to reconstruct:

```text
all SentenceRecords
all candidate index postings/data
manifest metadata
```

Manifest must logically expose:

```text
schema_version
snapshot_id
normalization_version
index_strategy_version
gram_sizes
corpus_digest
created_at_utc
record shard list
index shard list
```

Exact protobuf field names/numbers are frozen after team review.

Index snapshot data must preserve enough information to distinguish gram order, for example logically:

```text
gram_size
gram bytes/text
sorted repeated sentence IDs
```

The exact field names and field numbers are finalized in Phase 0 before generated C++/Python bindings are committed.

---

# 12. Snapshot Sharding

Do not place the entire corpus/index in one enormous protobuf message.

Initial artifact layout:

```text
snapshot/
├── manifest.binpb
├── records-00000.binpb
├── records-00001.binpb
├── ...
├── index-00000.binpb
├── index-00001.binpb
└── ...
```

Shard-size tuning is a benchmark decision.

If multiple protobuf messages share one shard file, use an explicit framed/length-delimited representation because raw protobuf wire messages are not self-delimiting.

Document the framing format.

---

# 13. Snapshot Identity & Reproducibility

Define:

```text
schema_version
normalization_version
index_strategy_version
gram_sizes
corpus_digest
snapshot_id
```

so an identical corpus/configuration can be identified reliably.

Recommended principle:

```text
snapshot_id
=
content/configuration identity
```

not merely a timestamp.

`created_at_utc` is metadata and may vary.

The exact content-digest algorithm must be deterministic and documented.

---

# 14. Python Snapshot Loader

Provide Python code that materializes runtime structures from a snapshot.

Conceptual API:

```python
def load_snapshot(
    snapshot_path: Path,
) -> tuple[dict[int, SentenceRecord], SearchIndex]:
    ...
```

Exact return wrapper may be refined during Phase 0, but Member 3 must receive:

```text
records_by_id
SearchIndex
```

without needing to understand protobuf shard internals.

Responsibilities:

- parse manifest;
- validate schema, normalization, and index-strategy versions;
- validate the expected gram sizes;
- load all required record/index shards;
- reject corrupt or incompatible snapshots clearly;
- construct `SentenceRecord` objects;
- construct runtime `SearchIndex`.

---

# 15. Runtime SearchIndex API

Implement:

```python
class SearchIndex:
    def get_candidate_ids(
        self,
        normalized_query: str,
    ) -> list[int]:
        ...
```

Contract:

- input is already normalized;
- output contains unique sentence IDs only;
- candidate order must not be relied on for final ranking;
- deterministic ascending output is recommended for repeatable tests;
- false positives are allowed;
- false negatives are not allowed;
- use the frozen two-part partition algorithm from Section 9;
- use 1/2/3-gram postings according to exact-seed length;
- one-character query uses the safe broad fallback;
- final verification belongs only to `match_and_score()`.

The index must not:

```text
calculate score
decide true match
rank
return AutoCompleteData
```

# 16. Candidate Generation Placement

For v1.0, candidate retrieval belongs behind:

```python
SearchIndex.get_candidate_ids(...)
```

Do not create an additional public `candidate_generator.py` abstraction unless complexity later proves it useful.

This removes unnecessary duplication between:

```text
index
candidate generator
```

The index may use private helper classes internally.


## 16.1 Corpus Update / Rebuild Policy

Part A snapshots are immutable.

If a corpus file is added, removed, or changed:

```text
run a new offline build
→ create a new snapshot/version
→ validate it
→ load it for serving
```

Do not patch an already-published snapshot in place.

Incremental indexing is explicitly a future optimization, not a Part A dependency. It may be considered later only if corpus-update frequency and benchmark evidence justify stable IDs, deletion handling, partial posting updates, and atomic publication complexity.

---

# 17. Artifact Store

The system must support local operation first.

Conceptual storage interface:

```python
class ArtifactStore:
    def materialize_snapshot(
        self,
        snapshot_ref: str,
        destination: Path,
    ) -> Path:
        ...
```

Implementations:

```text
LocalArtifactStore
GCSArtifactStore
```

Exact details may be refined during Phase 0 with Member 3, but storage is not allowed to leak into query matching/ranking code.

---

# 18. Local Artifact Store

Required for all environments.

A reviewer without cloud credentials must be able to:

```text
build/load local snapshot
→ start Python online engine
→ run queries
```

Local mode is the baseline development and evaluation path.

---

# 19. Optional Google Cloud Storage

GCS is optional durable storage for versioned artifacts.

Typical use:

```text
offline build
→ upload snapshot
→ gs://bucket/autocomplete/snapshots/<snapshot_id>/
```

Startup:

```text
GCSArtifactStore
→ download/materialize snapshot to local cache
→ validate
→ load
```

Forbidden:

```text
per-query GCS index reads
```

Online queries must operate on local/in-memory runtime data.

---

# 20. Cloud Failure Behavior

Cloud failures must fail clearly and early during materialization/startup.

Examples:

```text
missing credentials
missing object
permission denied
incomplete snapshot
network failure
unsupported manifest
```

Do not start serving queries with a partially materialized snapshot.

Local mode must remain usable independently of GCS.

---

# 21. Required Tests — C++

At minimum:

```text
root-level file
nested file
deeply nested file
multiple files
multiple lines
relative POSIX source paths
1-based line number
deterministic file ordering
deterministic sentence IDs
normalization golden vectors
1-gram posting correctness
2-gram posting correctness
3-gram posting correctness
posting de-duplication per sentence
sorted deterministic posting lists
snapshot serialization
manifest generation
```

---

# 22. Required Tests — Python Runtime

At minimum:

```text
snapshot read round trip
record reconstruction
source path preservation
line number preservation
sentence ID preservation
SearchIndex reconstruction
manifest index-strategy validation
1-character broad safe fallback
2-character 1+1 partition
3-character 1+2 partition
4-character 2+2 partition
5-character 2+3 partition
long balanced partition
posting intersection inside a seed
union between partition candidate sets
exact-query candidate recall
substitution in left partition
substitution in right partition
extra character in left partition
extra character in right partition
missing character in left partition
missing character in right partition
edit at/near partition boundary
no false negatives against known legal matches
corrupt snapshot rejection
unsupported schema rejection
unsupported normalization version rejection
unsupported index strategy rejection
missing shard rejection
```

# 23. Cross-Language Round-Trip Test

Required integration check:

```text
C++ builds snapshot
        ↓
Python loads snapshot
        ↓
Python records/index reflect expected corpus
```

Validate at least:

```text
record count
selected original strings
selected normalized strings
source paths
line numbers
sentence IDs
selected 1/2/3-gram postings
selected partition/candidate behavior
```

---

# 24. Performance Metrics

Measure at least:

```text
corpus file count
corpus sentence count
offline build time
snapshot serialized size
record shard count
index shard count
Python snapshot load time
runtime memory/index size
1/2/3-gram index size contribution
posting-list size distribution by gram order
average candidate count
candidate count by query-length bucket
candidate reduction ratio
safe-fallback rate
candidate-generation latency
```

If GCS is enabled, optionally report separately:

```text
snapshot upload time
snapshot download/materialization time
```

Do not mix cloud-transfer time with online query latency.

---

# 25. Differential Correctness Requirement

Member 3 owns the differential test harness.

Your optimization is accepted only when:

```text
Reference Engine result
==
Indexed SearchEngine result
```

across:

```text
exact substrings
beginning/middle/end
substitution
extra query character
missing query character
1-character queries
2-character queries
3/4/5-character queries
long queries
edits in either partition
edits at/near partition boundary
duplicates
generated/random cases
```

When a differential test fails, first determine whether the valid record was absent from the candidate set.

---

# 26. Definition of Done

Ready for integration only when:

- [ ] corpus traversal is recursive;
- [ ] source paths are relative POSIX paths;
- [ ] file order is deterministic;
- [ ] sentence IDs are deterministic;
- [ ] C++ normalization passes shared golden vectors;
- [ ] 1/2/3-gram index builds successfully;
- [ ] 1/2/3-gram postings are deterministic, sorted, and unique;
- [ ] short-query behavior is safe, including 1-character broad fallback;
- [ ] two-part candidate generation is implemented exactly as frozen;
- [ ] no known candidate false negatives exist;
- [ ] Protobuf schema is used for snapshot data;
- [ ] snapshot is sharded/framed safely;
- [ ] manifest/version metadata exists;
- [ ] C++ → Python snapshot round trip works;
- [ ] Python SearchIndex API is stable;
- [ ] local snapshot flow works;
- [ ] optional GCS flow, if enabled, works outside the query hot path;
- [ ] benchmark report exists;
- [ ] all owned tests pass;
- [ ] frozen public interfaces are unchanged;
- [ ] no other teammate's implementation was silently modified.

---

# 27. AI Coding Assistant Instruction

Use:

```text
00_TEAM_BASELINE.md
+
02_OFFLINE_INDEX_SNAPSHOT_SPEC.md
```

Instruction:

> Implement only the Offline Index/Snapshot specification.
> Use C++ for offline corpus/index construction and Protobuf for the C++→Python snapshot boundary.
> Build the frozen 1/2/3-gram postings and implement the frozen two-part recall-safe candidate algorithm.
> Implement the exact shared normalization semantics in C++ and validate them using the shared golden vectors.
> Preserve candidate recall before optimizing reduction.
> Keep Google Cloud Storage optional and outside the per-query hot path.
> Do not implement Python matching, scoring, ranking, ReferenceEngine, SearchEngine orchestration, or CLI.
> Do not change frozen interfaces or the shared .proto schema silently.
> If a shared contract appears insufficient, stop and explain the issue instead of changing it automatically.

---

# 28. Technical References

## 28.1 Official Vendor Documentation

Protocol Buffers overview:
https://protobuf.dev/overview/

Protocol Buffers techniques / large datasets / framing:
https://protobuf.dev/programming-guides/techniques/

Protocol Buffers reference guides:
https://protobuf.dev/reference/

Google Cloud Storage overview:
https://cloud.google.com/storage/docs/introduction

Google Cloud Storage consistency:
https://cloud.google.com/storage/docs/consistency

## 28.2 Algorithmic Rationale

Algorithmic rationale references (team design, not official assignment requirements):

Approximate matching / pigeonhole principle lecture notes, Johns Hopkins University:
https://www.cs.jhu.edu/~langmea/resources/lecture_notes/06_approximate_matching_v2.pdf

Dynamic partitioning of search patterns for approximate pattern matching:
https://pmc.ncbi.nlm.nih.gov/articles/PMC8246400/

Approximate string-matching with q-grams and maximal matches:
https://doi.org/10.1016/0304-3975(92)90143-4
