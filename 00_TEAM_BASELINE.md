# 00_TEAM_BASELINE.md

# Google Autocomplete Project — Team Architecture & Integration Baseline v1.1

**Team size:** 3 developers
**Project phase:** Part A — Functionality
**Status:** Final pre-implementation technical baseline — freeze after team approval
**Primary rule:** This file is the single source of truth for shared architecture, contracts, ownership boundaries, and integration behavior.

---

## 1. Purpose

This document defines the shared technical baseline for the entire team.

The three teammate-specific specs extend this file:

- `01_SEARCH_CORE_SPEC.md`
- `02_OFFLINE_INDEX_SNAPSHOT_SPEC.md`
- `03_SEARCH_QUALITY_CLI_SPEC.md`

If a teammate-specific spec conflicts with this file, **this file wins**.

No teammate or AI coding assistant may silently rename, move, redesign, or change a frozen public contract.

---

## 2. Source-of-Truth Hierarchy

We distinguish clearly between:

### 2.1 Official Google Requirements

These come from the assignment specification and are mandatory.

### 2.2 Team Architecture Decisions

These are our engineering choices for implementing the assignment professionally.

We must never present a team design choice as if Google explicitly required it.

### 2.3 Team-Specific Implementation Details

These may evolve inside a branch only when they do not change shared contracts or behavior.

---

# 3. Official Google Requirements — Locked

## 3.1 Two-Stage System

The program operates in two stages.

### Offline

The system reads the text sources from a known location and prepares data for serving.

Google allows the initialization/offline part to be written in any language.

### Online

The system waits for user input and returns autocomplete suggestions.

The completion function itself must be implemented in Python.

---

## 3.2 Corpus Rules

- Input text is in English.
- Files may contain punctuation.
- Text files may exist at arbitrary depths in a directory tree.
- Every complete line in a source file is one sentence.

Therefore:

```text
one source line = one sentence
```

Do not split a source line on `.`, `!`, `?`, or any other punctuation.

---

## 3.3 Required Result Type

The required result model is:

```python
from dataclasses import dataclass


@dataclass
class AutoCompleteData:
    completed_sentence: str
    source_text: str
    offset: int
    score: int
```

Each suggestion must contain:

- the original completed sentence;
- the source path;
- the source offset / line;
- the calculated score.

---

## 3.4 Required Public Completion Function

The external Google-facing API must exist **exactly** as:

```python
def get_best_k_completions(
    prefix: str,
) -> list[AutoCompleteData]:
    ...
```

An internal search engine may support configurable `k`, but the required facade remains fixed to the official signature and returns up to five suggestions.

---

## 3.5 Matching Semantics

A normalized query is a valid match when:

1. it is an exact substring of the normalized sentence; or
2. it can become a substring using at most one character edit.

A valid match may begin:

- at the beginning;
- in the middle;
- at the end.

This is **substring autocomplete**, not prefix-only autocomplete.

---

## 3.6 Allowed Edit Cases

At most one of the following is allowed:

- substitution;
- one extra character in the query;
- one missing character in the query.

More than one edit is invalid.

Team vocabulary:

```text
EXACT
SUBSTITUTION
EXTRA_IN_QUERY
MISSING_IN_QUERY
NO_MATCH
```

We use `EXTRA_IN_QUERY` and `MISSING_IN_QUERY` to avoid ambiguity about insertion/deletion direction.

---

## 3.7 Required Normalization Behavior

For matching and scoring:

- case is ignored;
- punctuation is removed;
- repeated spaces between words collapse to one space;
- spaces that remain after normalization count as characters.

The original corpus sentence must be preserved separately for output.

---

## 3.8 Required Scoring

Formula:

```text
Score = 2 × matching_characters - edit_penalty
```

An incorrect, extra, or missing character earns no matching points.

Positions are **1-based** in the normalized query.

### Substitution Penalty

| Position | 1 | 2 | 3 | 4 | 5+ |
|---|---:|---:|---:|---:|---:|
| Penalty | 5 | 4 | 3 | 2 | 1 |

### Extra / Missing Character Penalty

| Position | 1 | 2 | 3 | 4 | 5+ |
|---|---:|---:|---:|---:|---:|
| Penalty | 10 | 8 | 6 | 4 | 2 |

For a missing character, the position is the position where that character would be inserted.

If several valid alignments exist inside the same sentence, the implementation must use the highest valid score for that sentence.

---

## 3.9 Ranking

Results are ordered by:

```text
1. score descending
2. alphabetical completed_sentence for equal scores
```

The alphabetical rule above is our explicit technical interpretation of the assignment's tie-breaking requirement.

Return the best **up to 5** results.

---

## 3.10 Output Preservation

Search operates on normalized text.

Output uses:

```text
original sentence
original capitalization
original punctuation
source path
source line
score
```

Never return the normalized sentence as `completed_sentence`.

---

## 3.11 Interactive Behavior

The online program must:

1. wait for input;
2. show suggestions after Enter;
3. let the user continue the current sentence;
4. reset the current sentence when `#` is entered.

The exact terminal UX may be implemented by Member 3, but it must preserve this behavior.

---

## 3.12 Evaluation Priorities

The two official evaluation dimensions are:

```text
Correctness
+
Efficiency
```

A fast but incorrect result fails.

A correct solution should still move expensive work out of the online query path where possible.

---

# 4. Team Architecture Decision — v1.1

Our production-oriented architecture is:

```text
                              OFFLINE
                                 │
                                 ▼
                           Archive / Corpus
                                 │
                                 ▼
                    C++ Recursive Corpus Loader
                                 │
                                 ▼
                     C++ Canonical Normalization
                                 │
                                 ▼
             C++ Character 1/2/3-Gram Inverted Index Builder
                                 │
                                 ▼
                       Versioned Protobuf Snapshot
                      /            |             \
                     /             |              \
             record shards    index shards      manifest
                     \             |             /
                      \            |            /
                       └────────── snapshot ─────┘
                                 │
                      ┌──────────┴──────────┐
                      │                     │
                      ▼                     ▼
               Local Artifact Store   Optional Google
                                      Cloud Storage
                                             │
                           STARTUP / MATERIALIZATION
                                             │
                                      download once
                                             │
                                             ▼
                                      local cache/disk
                                             │
                                             ▼
                            Python Snapshot Loader
                                             │
                                             ▼
                        records_by_id + SearchIndex
                                             │
                                            ONLINE
                                             │
                                             ▼
                                         User Query
                                             │
                                             ▼
                                Python Canonical Normalize
                                             │
                                             ▼
                         Two-Part Recall-Safe Query Partition
                                             │
                                             ▼
                      SearchIndex 1/2/3-Gram Candidate Lookup
                                             │
                                             ▼
                              Exact One-Edit Match Verifier
                                             │
                                             ▼
                                     Google Scoring
                                             │
                                             ▼
                                         Ranking
                                             │
                                             ▼
                                          Top 5
```

We call the online strategy:

> **Recall-Safe Candidate Generation → Exact Verification → Scoring → Ranking → Top-K**

The index is an optimization only. It is never allowed to change the correct result.

# 5. Why C++ Offline + Python Online

This is a deliberate team design choice.

### C++ Offline

Used for:

- recursive corpus ingestion;
- deterministic corpus ordering;
- normalization parity implementation;
- 1/2/3-Gram inverted-index construction;
- snapshot serialization;
- heavy preprocessing.

### Python Online

Used for:

- required completion API;
- query normalization;
- candidate retrieval from the loaded index;
- exact matching;
- scoring;
- ranking;
- reference engine;
- CLI.

The online completion path therefore satisfies the requirement that the completion function be written in Python.

---

# 6. Protobuf Boundary

Protocol Buffers are our **cross-language data contract** between C++ and Python.

Important:

> Protobuf is the serialization format, not a live RPC requirement.

For v1.0 we intentionally do **not** require gRPC.

The offline builder creates versioned `.binpb` snapshot artifacts. Python loads those artifacts before serving queries.

This keeps the serving path simple and avoids requiring a live C++ process.

---

# 7. Snapshot Design

## 7.1 Shared Schema

The repository will contain a shared frozen schema:

```text
proto/autocomplete_snapshot.proto
```

After the initial team-approved schema is committed, field-number or semantic changes require team approval.

Both:

```text
C++ generated bindings
Python generated bindings
```

must come from this same schema.

---

## 7.2 Snapshot Layout

Do not serialize the entire corpus/index as one huge protobuf message.

Initial logical layout:

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

Exact shard sizes are an implementation/performance decision.

A shard may contain multiple length-delimited protobuf messages or another clearly documented framed representation.

---

## 7.3 Manifest Metadata

The snapshot manifest must logically provide at least:

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

Recommended principle:

```text
same corpus + same schema + same normalization contract + same build options
→ reproducible logical snapshot
```

`created_at_utc` may differ between builds, but content identity should be independently verifiable.

---

## 7.4 Snapshot Validation

Python must validate a snapshot before serving.

At minimum:

- supported schema version;
- supported normalization version;
- supported index strategy version;
- supported gram sizes;
- required shards are present;
- records referenced by postings exist;
- malformed/corrupt protobuf data fails clearly;
- incompatible snapshots are rejected rather than silently loaded.

Optional checksums/digests may be added and are encouraged.

## 7.5 Snapshot Immutability & Corpus Update Policy

Part A uses immutable/versioned snapshots.

If any source file is added, removed, or changed:

```text
corpus change
→ run a new offline build
→ produce a new snapshot identity
→ validate the new snapshot
→ load the new snapshot at the next startup/materialization
```

Do not mutate an already-published snapshot in place.

The currently running online engine may continue serving the previously validated snapshot until the new snapshot is ready. Hot swapping is optional and is not required for Part A.

Incremental indexing/live index mutation is intentionally deferred until a benchmark or later requirement justifies the additional complexity around stable identities, deletions, consistency, and atomic publication.

---

# 8. Storage Architecture

## 8.1 Local Is the Default Runtime Path

The project must run locally without Google Cloud credentials.

A reviewer must be able to use a local snapshot.

---

## 8.2 Google Cloud Storage Is Optional Durable Storage

GCS may store:

- source corpus archives;
- immutable/versioned snapshot artifacts;
- benchmark artifacts if useful.

Recommended snapshot layout:

```text
gs://<bucket>/autocomplete/snapshots/<snapshot_id>/...
```

---

## 8.3 GCS Is Not in the Query Hot Path

Forbidden design:

```text
every query
→ Cloud Storage request
→ retrieve index
→ search
```

Required design:

```text
startup
→ materialize snapshot locally
→ validate
→ load into local memory/data structures
→ serve many queries without per-query GCS I/O
```

This preserves low online latency.

---

## 8.4 Artifact Store Abstraction

The storage layer should expose a stable abstraction, conceptually:

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

The exact class signature may be refined during repository Phase 0, but Local and GCS behavior must remain interchangeable at the search-engine boundary.

---

# 9. Canonical Cross-Language Normalization Contract

Normalization is a **behavioral contract**, not merely one language-specific function.

Two implementations will exist:

```text
C++ offline normalization
Python online normalization
```

They must produce identical normalized text.

---

## 9.1 Canonical v1 Normalization Semantics

Because the assignment corpus is English, v1 normalization is intentionally ASCII-defined.

Apply these steps in order:

1. convert ASCII `A-Z` to `a-z`;
2. remove ASCII punctuation characters from this exact set:

```text
!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~
```

3. collapse runs of ASCII space (`U+0020`) to one ASCII space;
4. trim leading and trailing ASCII spaces.

Important distinction:

```text
punctuation is deleted
```

not replaced with a space.

Example:

```text
Hello,       WORLD!!!
→
hello world
```

If the mentors provide a more specific punctuation/whitespace rule later, update this contract once for both languages.

---

## 9.2 Golden Cross-Language Test Vectors

Shared frozen behavioral contract:

```text
tests/contracts/normalization_cases.json
```

The exact same cases must be run by:

```text
C++ tests
Python tests
```

For every case:

```text
normalize_cpp(input)
==
normalize_python(input)
==
expected
```

No branch may invent a second normalization interpretation.

---

# 10. Deterministic Corpus Identity

## 10.1 Source Paths

Store source paths as:

```text
POSIX-style paths relative to the corpus root
```

Example:

```text
books/classics/example.txt
```

Do not store machine-specific absolute paths such as:

```text
/home/user/project/Archive/books/example.txt
C:\Users\...\Archive\books\example.txt
```

---

## 10.2 File Traversal Order

For deterministic builds:

1. discover all eligible corpus files recursively;
2. compute normalized relative POSIX paths;
3. sort files lexicographically by relative path;
4. process lines from 1 to N.

---

## 10.3 Sentence IDs

Assign sentence IDs deterministically in that traversal order.

For the same corpus and same filtering rules:

```text
same source line
→ same sentence_id
```

This supports:

- reproducible debugging;
- snapshot comparisons;
- stable benchmarks;
- repeatable index construction.

---

# 11. Shared Python Models

File:

```text
src/autocomplete/models.py
```

Created and frozen during repository Phase 0.

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class SentenceRecord:
    sentence_id: int
    original: str
    normalized: str
    source_path: str
    line_number: int


@dataclass(frozen=True)
class AutoCompleteData:
    completed_sentence: str
    source_text: str
    offset: int
    score: int
```

Mapping:

```text
completed_sentence <- record.original
source_text        <- record.source_path
offset             <- record.line_number
score              <- match_and_score(...)
```

Team convention:

```text
offset = 1-based source line number
```

---

# 12. Shared Python Search Contracts

## 12.1 Python Normalization

Owned by Member 1:

```python
def normalize(text: str) -> str:
    ...
```

Must implement the canonical normalization contract exactly.

---

## 12.2 Matching and Scoring

Owned by Member 1:

```python
def match_and_score(
    query: str,
    sentence: str,
) -> int | None:
    ...
```

Contract:

- both strings are already normalized;
- exact substring is valid;
- at most one edit is valid;
- search all relevant substring alignments;
- return highest valid score for the sentence;
- return `None` for no valid match.

Only this component decides true:

```text
MATCH / NO MATCH
```

The index must never make the final match decision.

---

## 12.3 Loaded SearchIndex

Owned by Member 2.

Python runtime API:

```python
class SearchIndex:
    def get_candidate_ids(
        self,
        normalized_query: str,
    ) -> list[int]:
        ...
```

Contract:

- query is already normalized;
- returns unique sentence IDs only;
- false positives are allowed;
- false negatives are not allowed;
- all official one-edit cases must be recall-safe;
- candidate order is not part of the ranking contract;
- deterministic ascending output is recommended for reproducible tests;
- the final MATCH / NO MATCH decision belongs only to `match_and_score()`.

Frozen v1.1 candidate-generation behavior:

```text
m = len(normalized_query)

m == 1
→ safe broad fallback to all eligible sentence IDs

m >= 2
→ split the query deterministically into two contiguous,
  non-overlapping, near-balanced partitions
→ generate exact-seed candidates for each partition
→ UNION the two partition candidate sets
```

For an exact seed:

```text
seed length 1 → use the 1-gram posting list
seed length 2 → use the 2-gram posting list
seed length 3+ → intersect the posting lists of all overlapping 3-grams
```

Why this is recall-safe for at most one edit:

```text
query is split into 2 non-overlapping parts
+ at most 1 edit is allowed
→ at least one partition remains error-free in a legal alignment
→ every legal match is reachable through at least one exact seed
```

For a query where the team cannot guarantee safe pruning, broaden the candidate set rather than risk a false negative.

The runtime index must not:

- calculate Google score;
- decide true match;
- rank;
- return `AutoCompleteData`.

## 12.4 Internal SearchEngine

Owned by Member 3.

Preferred internal API:

```python
class SearchEngine:
    def __init__(
        self,
        records_by_id: dict[int, SentenceRecord],
        index: SearchIndex,
    ):
        ...

    def search(
        self,
        prefix: str,
        k: int = 5,
    ) -> list[AutoCompleteData]:
        ...
```

Flow:

```text
prefix
→ normalize()
→ index.get_candidate_ids()
→ record lookup
→ match_and_score()
→ AutoCompleteData
→ rank_results()
→ first k
```

---

## 12.5 Required Google Facade

Owned by Member 3.

External public API:

```python
def get_best_k_completions(
    prefix: str,
) -> list[AutoCompleteData]:
    ...
```

It delegates to the initialized default `SearchEngine` with `k=5`.

Do not expose a different required signature in place of this facade.

---

## 12.6 Ranking

Owned by Member 3:

```python
def rank_results(
    results: list[AutoCompleteData],
) -> list[AutoCompleteData]:
    ...
```

Rules:

```text
1. score descending
2. completed_sentence alphabetical
```

Reference and optimized engines must reuse this same ranking function.

---

# 13. Reference Engine

Owned by Member 3.

Concept:

```text
prefix
→ normalize()
→ ALL SentenceRecords
→ same match_and_score()
→ same AutoCompleteData conversion
→ same rank_results()
→ Top K
```

Important terminology:

> The Reference Engine is the **index/candidate-generation optimization oracle**.

It proves that pruning/indexing did not change the result.

It is **not** an independent proof that `match_and_score()` itself is correct, because both engines intentionally reuse the same matcher/scorer.

Matcher/scoring correctness is validated by:

- official Google examples;
- independent unit tests;
- golden scoring tests;
- targeted edge cases.

---

# 14. Candidate Generation Principle

The index may produce:

```text
false positives
```

because the exact matcher filters them.

The index must avoid:

```text
false negatives
```

because missing one legal candidate can change the final Top 5.

Priority:

```text
candidate recall first
candidate reduction second
```

When uncertain:

```text
broaden candidates
```

not:

```text
risk correctness
```

For v1.1, candidate generation is based on the classical partition/pigeonhole idea for edit distance: with at most one edit, splitting the query into two non-overlapping parts guarantees that at least one part is error-free in a legal alignment. The index searches for those exact seeds; `match_and_score()` still performs the authoritative verification.

# 15. Frozen v1.1 Index Strategy

Production candidate index:

```text
Character 1-Gram + 2-Gram + 3-Gram Inverted Index
```

Logical postings:

```text
(gram_size, gram) → sorted unique sentence IDs
```

Examples:

```text
(1, "a")   → [1, 5, 8, ...]
(2, "to")  → [2, 9, 20, ...]
(3, "the") → [3, 7, 11, ...]
```

Grams are built from the normalized sentence, including normalized ASCII spaces. Punctuation has already been removed by canonical normalization.

### Why exactly 1/2/3 grams in v1.1

- `1-gram` is required for recall-safe filtering of two- and three-character queries after two-part partitioning;
- `2-gram` supports exact seeds of length two, especially queries of length 3–5;
- `3-gram` provides stronger pruning for longer exact seeds;
- longer seeds are represented safely by intersecting their overlapping 3-gram posting lists;
- `4-gram`/`5-gram` indexes are not required for recall and would increase offline build cost, snapshot size, and runtime memory before benchmarks justify them.

### Deterministic query partition

For `m >= 2`:

```text
split_at = m // 2
left  = query[:split_at]
right = query[split_at:]
```

This gives:

```text
length 2 → 1 + 1
length 3 → 1 + 2
length 4 → 2 + 2
length 5 → 2 + 3
length 6 → 3 + 3
length 7 → 3 + 4
...
```

### Exact-seed lookup

```text
len(seed) == 1
→ posting(1, seed)

len(seed) == 2
→ posting(2, seed)

len(seed) >= 3
→ INTERSECTION of posting(3, every overlapping trigram in seed)
```

Then:

```text
candidate_ids
=
seed_candidates(left)
UNION
seed_candidates(right)
```

For `m == 1`, candidate generation returns the broad safe set because one substitution can transform the typed character into any target character; a character-specific posting cannot safely guarantee recall.

No optimization is accepted until differential tests prove:

```text
Reference == Indexed
```

for exact, substitution, extra-character, missing-character, short-query, partition-boundary, and generated query sets.

# 16. Team Ownership

## Member 1 — Search Core

Branch:

```text
feature/search-core
```

Owns primarily:

```text
Python canonical normalization implementation
exact substring matching
one-edit matcher
Google scoring
normalization golden cases
unit tests for core correctness
```

---

## Member 2 — Offline Index & Snapshot

Branch:

```text
feature/offline-index-snapshot
```

Owns primarily:

```text
C++ recursive corpus loader
C++ normalization parity implementation
C++ 1/2/3-Gram inverted index builder
recall-safe two-part candidate-generation algorithm
deterministic sentence IDs
Protobuf snapshot writer
snapshot sharding
Python snapshot loader
Python SearchIndex
LocalArtifactStore
optional GCSArtifactStore
performance benchmarks
```

---

## Member 3 — Search Quality & CLI

Branch:

```text
feature/search-quality
```

Owns primarily:

```text
ranking
SearchEngine orchestration
official Google facade
Reference Engine
differential testing
official regression tests
CLI
integration tests
end-to-end quality gates
```

---

# 17. Shared / Frozen Artifacts

Created during Phase 0 and changed only by team agreement:

```text
00_TEAM_BASELINE.md
src/autocomplete/models.py
proto/autocomplete_snapshot.proto
tests/contracts/normalization_cases.json
```

Public signatures in this file are also frozen.

If an AI assistant believes a frozen contract is insufficient:

```text
STOP
→ explain the issue
→ propose a change
→ do not change it automatically
```

---

# 18. Dependency Direction

Allowed:

```text
C++ offline builder
→ canonical normalization contract
→ shared .proto schema

Python snapshot loader
→ generated Python protobuf bindings

Python SearchEngine
→ Member 1 normalize()
→ Member 1 match_and_score()
→ Member 2 SearchIndex
→ Member 3 rank_results()

Python ReferenceEngine
→ Member 1 normalize()
→ Member 1 match_and_score()
→ Member 3 rank_results()
```

Forbidden:

```text
matcher importing SearchEngine
C++ builder calculating final online Top 5
SearchIndex deciding MATCH / NO MATCH
SearchIndex calculating Google score
SearchEngine reading private index internals directly
ReferenceEngine copying a second matcher
per-query GCS reads for index data
```

---

# 19. Suggested Repository Shape

```text
google-autocomplete/
│
├── cpp/
│   ├── CMakeLists.txt
│   ├── include/
│   ├── src/
│   └── tests/
│
├── proto/
│   └── autocomplete_snapshot.proto
│
├── src/
│   └── autocomplete/
│       ├── __init__.py
│       ├── models.py
│       ├── normalization.py
│       ├── matcher.py
│       ├── scoring.py
│       ├── snapshot_loader.py
│       ├── index.py
│       ├── artifact_store.py
│       ├── ranking.py
│       ├── reference_engine.py
│       ├── search_engine.py
│       ├── api.py
│       ├── cli.py
│       └── main.py
│
├── tests/
│   ├── contracts/
│   │   └── normalization_cases.json
│   └── ...
│
├── benchmarks/
│
├── data/
│   └── .gitkeep
│
├── 00_TEAM_BASELINE.md
├── 01_SEARCH_CORE_SPEC.md
├── 02_OFFLINE_INDEX_SNAPSHOT_SPEC.md
├── 03_SEARCH_QUALITY_CLI_SPEC.md
├── README.md
├── pyproject.toml
├── .gitignore
└── ...
```

Exact toolchain files will be added during repository setup.

---

# 20. Quality Engineering Baseline

The final project should be:

```text
Correct
Fast
Deterministic
Reproducible
Tested
Explainable
Portable
Production-oriented
```

Expected engineering support:

- Python unit/integration tests;
- C++ unit tests;
- cross-language normalization contract tests;
- official regression tests;
- differential tests;
- performance benchmarks;
- CI for both languages;
- lint/format checks;
- clear configuration;
- error handling;
- useful logging around offline builds/startup;
- architecture documentation.

Exact Python/C++/Protobuf toolchain versions are pinned during repository setup so all three teammates and CI use compatible versions.

---

# 21. What We Intentionally Do Not Add in v1.1

Do not add technology merely to appear more complex.

Unless a benchmark or new requirement justifies it, v1.0 does not require:

```text
gRPC
microservices
Kubernetes
Redis
SQL/NoSQL database
load balancer
per-query cloud calls
incremental live index mutation
```

Production quality comes from disciplined architecture, reproducibility, correctness, observability, and performance — not the number of technologies.

---

# 22. Required Testing Baseline

## Normalization

- case;
- punctuation;
- repeated spaces;
- leading/trailing spaces under team convention;
- C++ == Python golden vectors.

## Matching

- exact substring;
- beginning/middle/end;
- substitution;
- extra query character;
- missing query character;
- 2+ edits rejected;
- multiple possible alignments use best score.

## Scoring

- exact score;
- all penalty positions;
- spaces counted;
- edited/missing/extra character gets no matching points.

## Corpus / Snapshot

- root-level file;
- nested file;
- deeply nested file;
- deterministic path order;
- deterministic IDs;
- 1-based line number;
- relative POSIX source path;
- snapshot write/read round trip;
- corrupt/incompatible snapshot failure.

## Index

- 1-character safe broad fallback;
- 2-character query using 1+1 partition;
- 3-character query using 1+2 partition;
- 4-character query using 2+2 partition;
- 5-character query using 2+3 partition;
- longer balanced partitions;
- 1/2/3-gram posting correctness;
- sorted/unique postings;
- posting intersection inside an exact seed;
- union between the two query partitions;
- exact query candidate recall;
- substitution in either partition;
- extra character in either partition;
- missing character in either partition;
- edit exactly at/near the partition boundary;
- recall-safe broadening when required;
- zero known false negatives.

## Ranking / Search

- descending score;
- alphabetical tie;
- Top 5;
- original sentence preserved;
- source path/offset correct.

## Differential

```python
reference == optimized
```

for deterministic and generated queries.

## CLI

- initial query;
- continuation;
- repeated continuation;
- `#` reset;
- query after reset.

---

# 23. Official Regression Examples

Sentence:

```text
To be or not to be, that is the question.
```

Permanent expected behavior:

| Query | Expected |
|---|---:|
| `To be` | 10 |
| `or Not` | 12 |
| `be, that` | 14 |
| `2o be` | 3 |
| `to pe` | 6 |
| `or knot` | 8 |
| `or nt` | 8 |
| `not be` | NO MATCH |

A change that breaks these is not acceptable.

---

# 24. Performance Baseline

After correctness is established, measure at least:

```text
corpus file count
corpus sentence count
offline build time
snapshot serialized size
snapshot load/startup time
in-memory index size
average candidate count
candidate count by query-length bucket
candidate reduction ratio
posting-list size by gram order
1/2/3-gram index size contribution
safe-fallback rate
candidate-generation latency
matcher verification latency
end-to-end query latency
```

Performance comparisons must use the same corpus and query set.

---

# 25. Team-Decision-Required Cases

Do not let one branch silently invent behavior for:

```text
empty query
spaces-only query
punctuation-only query
empty corpus line
unsupported/invalid text encoding
duplicate identical output records
identical completed_sentence from different source/offset pairs
```

If one becomes relevant:

```text
TEAM DECISION REQUIRED
→ agree once
→ update this baseline
→ add tests
```

---

# 26. Merge / Integration Policy

Recommended branch integration order:

```text
1. shared Phase 0 baseline/skeleton
2. feature/search-core
3. feature/offline-index-snapshot
4. feature/search-quality
5. integration-only fixes
```

Before merging a branch:

- branch unit tests pass;
- frozen interfaces are unchanged;
- no other teammate's owned implementation is silently modified;
- shared contract changes, if any, were approved first;
- formatter/linter/static checks pass;
- relevant cross-language/differential checks pass when dependencies are available.

---

# 27. Development Phases

## Phase 0 — Shared Foundation

```text
repository skeleton
toolchain pinning
models.py
.proto schema draft + team approval
index_strategy_version + gram_sizes metadata
normalization_cases.json
CI skeleton
shared interfaces
```

## Phase 1 — Search Correctness Core

```text
Python normalization
matcher
scoring
official regression tests
```

## Phase 2 — Offline Builder & Snapshot

```text
C++ corpus loader
C++ normalization parity
1/2/3-Gram inverted index builder
sorted unique posting lists
Protobuf snapshot writer
Python snapshot loader
```

## Phase 3 — Runtime Index & Search Integration

```text
SearchIndex
two-part query partitioning
exact-seed posting lookup
candidate union
SearchEngine
ranking
official facade
```

## Phase 4 — Reference & Differential Validation

```text
ReferenceEngine
generated query tests
Reference == Indexed
```

## Phase 5 — CLI

```text
interactive continuation
#
reset
```

## Phase 6 — Storage Integration

```text
LocalArtifactStore
optional GCSArtifactStore
startup materialization
```

## Phase 7 — Benchmark & Optimize

```text
measure
profile
optimize safely
re-run differential tests
```

## Phase 8 — Final Production Review

```text
full tests
CI green
benchmark report
documentation
architecture explanation
failure handling review
```

---

# 28. Team-Level Definition of Done

Functionally ready only when:

- [ ] corpus traversal is correct;
- [ ] deterministic IDs are correct;
- [ ] C++ and Python normalization are identical;
- [ ] Protobuf snapshot round trip works;
- [ ] snapshot validates schema, normalization, and index-strategy compatibility;
- [ ] 1/2/3-gram postings are deterministic, sorted, and unique;
- [ ] two-part candidate generation is recall-safe for all one-edit cases;
- [ ] exact substring works;
- [ ] all one-edit cases work;
- [ ] 2+ edits are rejected;
- [ ] scoring is correct;
- [ ] ranking is correct;
- [ ] Top 5 is correct;
- [ ] original text/path/offset are correct;
- [ ] official examples pass;
- [ ] Reference Engine works;
- [ ] Indexed Engine matches Reference Engine;
- [ ] CLI continuation and reset work;
- [ ] local execution works without cloud credentials.

Competition-ready only when additionally:

- [ ] benchmark suite exists;
- [ ] one-edit candidate recall is proven by differential tests;
- [ ] candidate pruning materially reduces work on realistic data;
- [ ] no optimization changes correct results;
- [ ] startup/snapshot costs are measured;
- [ ] optional GCS flow works;
- [ ] CI is green;
- [ ] all team members can explain architecture, matching, scoring, indexing, Protobuf boundary, and trade-offs.

---

# 29. Core Engineering Principle

```text
Correctness first.
Cross-language contract second.
Safe indexing third.
Optimization fourth.
```

Every optimization must answer:

> Did we make the system faster without changing the correct result?

If that is not proven, the optimization is not ready.

---

# 30. Technical References

## 30.1 Official Vendor Documentation

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

## 30.2 Algorithmic Rationale

Algorithmic references supporting the team-design choice (not official assignment requirements):

Approximate matching / pigeonhole principle lecture notes, Johns Hopkins University:
https://www.cs.jhu.edu/~langmea/resources/lecture_notes/06_approximate_matching_v2.pdf

Dynamic partitioning of search patterns for approximate pattern matching (open-access research article):
https://pmc.ncbi.nlm.nih.gov/articles/PMC8246400/

Approximate string-matching with q-grams and maximal matches:
https://doi.org/10.1016/0304-3975(92)90143-4
