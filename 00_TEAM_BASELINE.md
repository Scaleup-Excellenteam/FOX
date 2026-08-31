# Google Autocomplete Project — Team Architecture & Integration Baseline v2.1

**Team size:** 3 developers  
**Scope:** Part A first; Part B only after Part A is complete and stable  
**Status:** Final implementation baseline — freeze after all three teammates approve this exact version  

## 1. Purpose

This file is the shared architectural and behavioral contract for the project. The role specs extend it:

- `PHASE0_SHARED_FOUNDATION_SPEC.md`
- `01_SEARCH_CORE_SPEC.md`
- `02_OFFLINE_INDEX_SNAPSHOT_SPEC.md`
- `03_SEARCH_QUALITY_CLI_SPEC.md`

No teammate, branch, or AI coding assistant may silently change a frozen public contract.

## 2. Source-of-Truth Hierarchy

When two documents disagree, use this order:

1. **Official Part A assignment** (`google_project_2026_part_a...`) — mandatory behavior.
2. **Official Part B assignment** for future Part B work only; it must not weaken Part A.
3. **This baseline** — team architecture and explicit decisions for unspecified cases.
4. **Role-specific SPEC**.
5. **Implementation details**.

A team decision must never be presented as an official Google requirement.

## 3. Official Part A Requirements — Locked

### 3.1 Two stages

- **Offline:** read the corpus and prepare data for serving. The offline implementation may use any language.
- **Online:** accept user text and return autocomplete results. The completion function must be implemented in Python.

### 3.2 Corpus semantics

- Corpus text is English and may contain punctuation.
- Text files may occur at arbitrary depths in a directory tree.
- **One physical source line = one sentence.** Never split a line on punctuation.
- The original source line must be preserved for output.

### 3.3 Required public result model

The Google-facing model is kept as close as possible to the assignment:

```python
from dataclasses import dataclass


@dataclass
class AutoCompleteData:
    completed_sentence: str
    source_text: str
    offset: int
    score: int
```

Team mapping:

- `completed_sentence` = original corpus line.
- `source_text` = relative POSIX path of the source file.
- `offset` = 1-based physical line number in that file.
- `score` = official Part A score.

### 3.4 Required completion facade

```python
from typing import List


def get_best_k_completions(prefix: str) -> List[AutoCompleteData]:
    ...
```

It returns the best **up to five** results.

Internal APIs may use modern built-in generics and configurable `k`; the external facade remains assignment-compatible.

### 3.5 Matching

After normalization, a query matches a sentence when:

- it is an exact substring; or
- it can become a substring with **at most one** character edit.

Allowed one-edit cases:

- substitution;
- one extra character in the query;
- one missing character in the query.

The match may begin at the beginning, middle, or end of a sentence. More than one edit is invalid.

### 3.6 Scoring

```text
score = 2 * matching_characters - edit_penalty
```

The edited/extra/missing character earns no matching points.

Substitution penalty:

| Query position | 1 | 2 | 3 | 4 | 5+ |
|---|---:|---:|---:|---:|---:|
| Penalty | 5 | 4 | 3 | 2 | 1 |

Extra/missing penalty:

| Query position | 1 | 2 | 3 | 4 | 5+ |
|---|---:|---:|---:|---:|---:|
| Penalty | 10 | 8 | 6 | 4 | 2 |

Positions are 1-based in the normalized query. For a missing character, use the insertion position; insertion at the end is position `len(query) + 1`.

If a sentence admits multiple legal alignments, use the **highest valid score for that sentence**.

A legal match may have a negative score. **Never clamp scores to zero and never discard a legal match only because its score is negative.**

### 3.7 Ranking

Required keys:

```text
1. score descending
2. completed_sentence ascending
```

The team freezes `completed_sentence ascending` as Python/Unicode lexicographic ordering of the **original output string**. This is our deterministic interpretation of the assignment's alphabetical tie-break; do not normalize, lowercase, or strip punctuation for ranking.

For deterministic behavior only when both required keys are identical, use:

```text
3. source_text ascending
4. offset ascending
```

Do not deduplicate different source records merely because their completed sentences are identical.

### 3.8 Interactive behavior

- Enter submits the accumulated query and shows up to five suggestions.
- The user may continue typing from the current query.
- A fragment equal to `#` resets the current query to empty without restarting the process.

## 4. Team Decisions for Previously Undefined Cases — Frozen v2.1

These are team decisions, not official requirements.

### 4.1 Query normalizes to empty

```text
"" / spaces-only / punctuation-only
→ return []
```

The index and matcher are not invoked for an empty normalized query.

### 4.2 Corpus lines that normalize to empty

Skip them from searchable records and the index. Preserve physical source line numbering for all retained records; skipped lines do not cause line-number renumbering.

### 4.3 Corpus file eligibility

Recursively process regular files whose extension is `.txt`, case-insensitively. Ignore non-regular files and non-`.txt` files.

### 4.4 Encoding and line endings

- Input files are decoded as UTF-8; an optional UTF-8 BOM is accepted.
- Invalid UTF-8 is a clear offline build error naming the source path.
- Both LF and CRLF are accepted as line terminators; line terminators are not part of the original sentence.

### 4.5 Duplicate records

Every retained source line is an independent record. The same text appearing in different files or line numbers remains independently eligible for the Top 5.

### 4.6 Sentence IDs

Searchable `sentence_id` values start at **1** and increase sequentially in deterministic corpus order.

```text
sentence_id = 0
→ reserved / invalid
```

This avoids ambiguity with proto3 scalar default values and gives the loader a simple corruption check.

### 4.7 Character unit

After UTF-8 decoding, matching positions, query length, partitioning, and 1/2/3-gram lengths are defined in **Unicode code points**, not UTF-8 bytes and not grapheme clusters.

Normalization only case-folds ASCII `A-Z`, deletes the frozen ASCII punctuation set, and collapses ASCII spaces; all other valid Unicode code points are preserved unchanged.

The official corpus is English, so this rule mainly removes cross-language ambiguity. C++ and Python must still agree exactly if valid non-ASCII UTF-8 appears.

## 5. Canonical Normalization Contract

Part A requires case-insensitive matching, punctuation removal, and repeated-space collapse. v2.1 freezes the exact cross-language interpretation:

1. ASCII `A-Z` → `a-z`.
2. Delete ASCII punctuation from exactly:

```text
!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~
```

3. Collapse runs of ASCII space `U+0020` to one ASCII space.
4. Trim leading and trailing ASCII spaces.
5. Do not replace punctuation with spaces.

Example:

```text
Hello,       WORLD!!!
→ hello world
```

Python online normalization and C++ offline normalization must pass the same frozen golden vectors.

## 6. Architecture v2.1

```text
OFFLINE
Archive / corpus root
        ↓
C++ recursive .txt loader
        ↓
C++ canonical normalization
        ↓
Sentence records + character 1/2/3-gram postings
        ↓
Versioned Protobuf snapshot on local disk

STARTUP
Local snapshot directory
        ↓
Python snapshot loader + validation
        ↓
records_by_id + SearchIndex
        ↓
SearchEngine initialized once

ONLINE QUERY
User input
        ↓
Python canonical normalize
        ↓
empty? → []
        ↓
SearchIndex recall-safe candidate IDs
        ↓
exact one-edit verification + official score
        ↓
result conversion
        ↓
ranking
        ↓
Top 5
```

Core principle:

> **Recall-safe candidate generation → exact verification → scoring → ranking → Top-K.**

The index is an optimization only. It never decides MATCH/NO_MATCH and never calculates the final score.

## 7. Why C++ Offline + Python Online

This is a team architecture choice:

- C++ handles recursive corpus ingestion, deterministic preprocessing, index construction, and snapshot writing.
- Python handles the required completion API, runtime normalization, candidate retrieval, exact verification, scoring, ranking, reference validation, and CLI.

There is no live C++ service and no gRPC requirement in Part A.

## 8. Protobuf Boundary

`proto/autocomplete_snapshot.proto` is the single cross-language snapshot schema.

Protobuf is used for serialization, not for live transport.

Part A snapshot layout starts simple:

```text
snapshot/
├── manifest.binpb
├── records.binpb
└── index.binpb
```

- `manifest.binpb` is one manifest message.
- `records.binpb` contains multiple framed record messages.
- `index.binpb` contains multiple framed posting messages.
- Framing is fixed as: **4-byte unsigned big-endian payload length + protobuf payload bytes**.

If benchmark evidence shows that a single records/index file is operationally inconvenient, the same framing may be split into numbered shards and listed in the manifest. Sharding is therefore supported by the format but **not mandatory for the first correct implementation**.

### 8.1 Required manifest metadata

At minimum:

```text
schema_version = 1
normalization_version = 1
index_strategy_version = 1
gram_sizes = [1, 2, 3]
corpus_digest_sha256
snapshot_id
created_at_utc
record file list
index file list
searchable_record_count
posting_count
index_digest_sha256
```

### 8.2 Stable identity

Do not compute stable identity from serialized protobuf bytes. Protobuf serialization is not canonical.

`corpus_digest_sha256` is computed from the deterministic retained-record stream in ascending `sentence_id`, using an unambiguous canonical binary encoding of:

```text
sentence_id
source_path
physical line_number
original sentence
normalized sentence
```

`index_digest_sha256` is computed independently from the deterministic logical posting stream sorted by gram size and gram value.

`snapshot_id` is SHA-256 over a canonical configuration/content block containing:

```text
corpus_digest_sha256
index_digest_sha256
schema_version
normalization_version
index_strategy_version
gram_sizes
```

`created_at_utc` is metadata and is excluded from identity. Exact byte-level digest encoding is frozen in `02_OFFLINE_INDEX_SNAPSHOT_SPEC.md`; never use serialized protobuf bytes as the stable identity.

## 9. Shared Models

File: `src/autocomplete/models.py`

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class SentenceRecord:
    sentence_id: int
    original: str
    normalized: str
    source_path: str
    line_number: int


@dataclass
class AutoCompleteData:
    completed_sentence: str
    source_text: str
    offset: int
    score: int
```

`SentenceRecord` is internal and immutable. `AutoCompleteData` follows the assignment's public shape.

## 10. Shared Python Contracts

### Member 1

```python
def normalize(text: str) -> str:
    ...


def match_and_score(query: str, sentence: str) -> int | None:
    ...
```

Inputs to `match_and_score` are already normalized. An empty query is invalid at this internal boundary and raises `ValueError`; production SearchEngine callers return `[]` before calling it. The matcher alone is authoritative for true match and final sentence score.

### Member 2

```python
class SearchIndex:
    def get_candidate_ids(self, normalized_query: str) -> list[int]:
        ...


def load_snapshot(snapshot_path: Path) -> tuple[dict[int, SentenceRecord], SearchIndex]:
    ...
```

`SearchIndex` may return false positives but must not omit a legal match.

### Member 3

```python
def rank_results(results: list[AutoCompleteData]) -> list[AutoCompleteData]:
    ...


class SearchEngine:
    def search(self, prefix: str, k: int = 5) -> list[AutoCompleteData]:
        ...
```

Public API:

```python
def configure_default_engine(engine: SearchEngine) -> None:
    ...


def get_best_k_completions(prefix: str) -> List[AutoCompleteData]:
    ...
```

Calling the facade before configuration raises a clear `EngineNotInitializedError`.

## 11. Frozen Candidate Index Strategy

Initial production index:

```text
character 1-gram + 2-gram + 3-gram inverted index
(gram_size, gram) → sorted unique sentence IDs
```

Normalized ASCII spaces participate in grams. Gram/query lengths and slicing use Unicode code points.

For normalized query length `m`:

```text
m == 0 → [] before SearchIndex
m == 1 → broad safe set of all searchable sentence IDs
m >= 2 → split at m // 2 into left and right partitions
```

Exact seed lookup:

```text
seed length 1 → 1-gram posting
seed length 2 → 2-gram posting
seed length >= 3 → intersection of all overlapping 3-gram postings
```

Final candidate set:

```text
candidates(left) UNION candidates(right)
```

This is a recall-first optimization. Any future pruning/index strategy must pass differential tests against the reference engine before replacing it.

4/5-gram indexes are not part of v2.1. They may be benchmarked later but are added only if measured latency/candidate reduction justifies their memory and snapshot cost.

## 12. Reference Engine

The Reference Engine scans all searchable records and uses the same:

```text
normalize
match_and_score
result conversion
rank_results
```

Only candidate sourcing differs:

```text
Reference → all records
Optimized → SearchIndex candidates
```

Therefore `Reference == Optimized` proves the index did not change results; it does **not** independently prove matcher/scoring correctness.

## 13. Part B Extension Boundary

Part A must remain fully usable and testable without Google Cloud credentials or external network services.

Later Part B features must be added outside the Part A core. They may call the stable search engine, but must not silently change:

```text
normalize()
match_and_score()
Part A scoring
Part A ranking contract
get_best_k_completions()
```

Generated-model text must not be presented as a corpus match, and semantic similarity must not be presented as Part A score.

No generic plugin framework is required in Part A. Create `features/` or `integrations/` only when a selected Part B feature requires them.

## 14. Team Ownership — Final v2.1

### Member 1 — Search Core

Branch: `feature/search-core`

Owns:

```text
Python canonical normalization
exact/one-edit matcher
scoring helpers and penalty tables
core unit tests
official scoring/matcher regression cases
```

### Member 2 — Offline Index & Snapshot

Branch: `feature/offline-index-snapshot`

Owns:

```text
C++ corpus loader
C++ normalization parity
SentenceRecord identity/order
1/2/3-gram index builder
.proto implementation support
snapshot writer/framing/manifest
Python snapshot loader
Python SearchIndex
C++↔Python snapshot round-trip tests
offline/index measurements
```

**Removed from Part A:** GCS implementation and cloud credential handling.

### Member 3 — Search Integration, Quality & CLI

Branch: `feature/search-quality`

Owns:

```text
ranking
SearchEngine orchestration
default-engine configuration + official facade
ReferenceEngine
differential/generated tests
CLI
startup from a local snapshot
integration/end-to-end tests
online benchmark harness and quality report
```

This rebalancing keeps Member 2 focused on the cross-language/index problem while Member 3 owns runtime integration and quality.

## 15. Dependency Direction

Allowed:

```text
C++ builder → shared normalization contract + shared .proto
Python snapshot loader → generated protobuf bindings
SearchEngine → normalize + SearchIndex + match_and_score + rank_results
ReferenceEngine → normalize + match_and_score + rank_results
CLI/main → load_snapshot + SearchEngine/API
```

Forbidden:

```text
matcher → SearchIndex/SearchEngine
SearchIndex → matcher/scoring/ranking
C++ builder → final Top 5
per-query snapshot reload
Part A core → external cloud/LLM API
ReferenceEngine → duplicate matcher implementation
```

## 16. Repository Shape

```text
google-autocomplete/
├── cpp/
│   ├── CMakeLists.txt
│   ├── include/
│   ├── src/
│   └── tests/
├── proto/
│   └── autocomplete_snapshot.proto
├── src/autocomplete/
│   ├── __init__.py
│   ├── models.py
│   ├── generated/
│   │   ├── __init__.py
│   │   └── autocomplete_snapshot_pb2.py
│   ├── normalization.py
│   ├── matcher.py
│   ├── scoring.py
│   ├── snapshot_loader.py
│   ├── index.py
│   ├── ranking.py
│   ├── reference_engine.py
│   ├── search_engine.py
│   ├── api.py
│   ├── cli.py
│   └── main.py
├── tests/
│   ├── contracts/normalization_cases.json
│   └── ...
├── benchmarks/
├── scripts/
├── data/
│   ├── raw/
│   └── snapshots/
├── 00_TEAM_BASELINE.md
├── PHASE0_SHARED_FOUNDATION_SPEC.md
├── 01_SEARCH_CORE_SPEC.md
├── 02_OFFLINE_INDEX_SNAPSHOT_SPEC.md
├── 03_SEARCH_QUALITY_CLI_SPEC.md
├── pyproject.toml
├── README.md
└── .gitignore
```


## 17. Offline Builder Invocation Contract

Member 2 implements one documented C++ executable interface:

```bash
./build/cpp/autocomplete_builder \
  --corpus <extracted-corpus-root> \
  --output <snapshot-directory>
```

For the provided assignment archive, README documents extraction of `Archive.zip` to a directory before invoking the builder. Direct ZIP parsing is optional and is **not** required for Part A.

On success the command produces a complete validated snapshot directory. On failure it exits non-zero and reports a clear error. The builder never partially publishes a snapshot as ready.

## 18. Git / Integration Rules

Phase 0 is completed and merged before feature branches begin.

After the team approves the Phase 0 commit, record its hash as `PHASE0_COMMIT` and create every feature branch directly from that commit.

Required checks:

```bash
git merge-base <feature-branch> <PHASE0_COMMIT>  # must resolve to PHASE0_COMMIT
git status                                      # clean before integration
```

Forbidden:

```text
git init inside the existing repository
orphan/root feature branches
--allow-unrelated-histories
silent force-push of shared branches
changing another member's frozen contract without approval
```

Before integration, branch tests/lint/format must pass. Integration fixes belong to the owner of the broken contract unless the team explicitly agrees otherwise.

Each checkout/worktree uses its own repository-local `.venv`; do not reuse one editable-install virtual environment across worktrees.

## 19. Quality Gates

Functionally ready:

- official examples pass;
- normalization parity passes C++ and Python;
- exact and all one-edit cases pass;
- 2+ edits are rejected;
- snapshot write/load validates versions and references;
- candidate generation has no known false negatives;
- Reference and Indexed engines agree on deterministic/generated sets;
- ranking and Top 5 are deterministic;
- original sentence/path/offset are preserved;
- CLI continuation/reset works;
- local startup works without network/cloud credentials.

Competition-ready additionally:

- CI green;
- benchmark report on one fixed corpus/query set;
- candidate reduction and end-to-end latency measured;
- startup/snapshot costs measured;
- no optimization accepted without differential regression;
- README explains architecture, run steps, failure behavior, and trade-offs;
- all three teammates can explain the core algorithms and boundaries.

## 20. Engineering Principle

```text
Correctness first.
Shared contracts second.
Recall-safe indexing third.
Measured optimization fourth.
Part B value after Part A is stable.
```

## 21. Technical References

- Protocol Buffers overview: https://protobuf.dev/overview/
- Protocol Buffers techniques / streaming multiple messages / large data sets: https://protobuf.dev/programming-guides/techniques/
- Protobuf serialization is not canonical: https://protobuf.dev/programming-guides/serialization-not-canonical/
- Approximate matching / pigeonhole principle lecture notes: https://www.cs.jhu.edu/~langmea/resources/lecture_notes/06_approximate_matching_v2.pdf
- Dynamic partitioning for approximate pattern matching: https://pmc.ncbi.nlm.nih.gov/articles/PMC8246400/
