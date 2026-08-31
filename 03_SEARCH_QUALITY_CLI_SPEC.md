# 03_SEARCH_QUALITY_CLI_SPEC.md

# SPEC 3 — Search Integration, Reference Engine, CLI & Quality

## Role

**Search Orchestration, Ranking, Official API, Reference Validation, CLI & End-to-End Quality**

**Architecture baseline:** v1.1

Use this file together with `00_TEAM_BASELINE.md`.

If anything conflicts, `00_TEAM_BASELINE.md` wins.

---

## 1. Branch

```text
feature/search-quality
```

---

## 2. Primary Responsibility

You own:

```text
ranking
SearchEngine orchestration
official get_best_k_completions() facade
Reference Engine
differential testing
official regression tests
integration tests
CLI
startup integration with loaded snapshot
end-to-end quality gates
```

You do **not** own:

```text
canonical normalization semantics
matcher algorithm
scoring penalties
C++ corpus traversal
C++ 1/2/3-Gram construction
Protobuf snapshot writer internals
SearchIndex two-part candidate algorithm
GCS implementation internals
```

---

## 3. Files You Primarily Own

```text
src/autocomplete/ranking.py
src/autocomplete/reference_engine.py
src/autocomplete/search_engine.py
src/autocomplete/api.py
src/autocomplete/cli.py
src/autocomplete/main.py

tests/test_ranking.py
tests/test_reference_engine.py
tests/test_search_engine.py
tests/test_official_examples.py
tests/test_reference_vs_indexed.py
tests/test_cli.py
tests/test_integration.py
```

You will integrate with:

```text
Member 1 normalize()
Member 1 match_and_score()
Member 2 snapshot loader
Member 2 SearchIndex
Member 2 ArtifactStore
```

Do not duplicate those implementations.

---

# 4. Ranking API

Implement:

```python
def rank_results(
    results: list[AutoCompleteData],
) -> list[AutoCompleteData]:
    ...
```

Rules:

```text
1. score descending
2. completed_sentence alphabetical for equal score
```

Reference and optimized engines must use this exact function.

Do not rely on candidate order, sentence ID order, file order, or discovery order for final ranking.

---

# 5. Optimized SearchEngine

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

Required flow:

```text
prefix
 ↓
normalize(prefix)
 ↓
index.get_candidate_ids(normalized_query)
 ↓
record lookup by sentence_id
 ↓
match_and_score(normalized_query, record.normalized)
 ↓
discard None
 ↓
create AutoCompleteData
 ↓
rank_results()
 ↓
first k
```

Do not reimplement matching or scoring.

`SearchEngine` must treat `SearchIndex.get_candidate_ids()` as an opaque recall-safe candidate source. It must not duplicate partitioning or gram logic.

---

# 6. Result Conversion

For every valid match:

```python
AutoCompleteData(
    completed_sentence=record.original,
    source_text=record.source_path,
    offset=record.line_number,
    score=score,
)
```

Required properties:

```text
original sentence preserved
relative POSIX source path preserved
1-based line number preserved
score comes only from match_and_score()
```

---

# 7. Official Google-Facing API

Implement a public facade with the exact required signature:

```python
def get_best_k_completions(
    prefix: str,
) -> list[AutoCompleteData]:
    ...
```

It must return:

```text
up to 5 results
```

Internally it delegates to the initialized default `SearchEngine`.

Do not replace the official facade with only a class method requiring extra arguments.

---

# 8. Engine Initialization

The online process must initialize once before serving queries.

Conceptual startup:

```text
configuration
 ↓
LocalArtifactStore or GCSArtifactStore
 ↓
materialize snapshot locally
 ↓
validate/load snapshot
 ↓
records_by_id + SearchIndex
 ↓
construct SearchEngine
 ↓
install/configure default engine
 ↓
serve queries
```

No per-query:

```text
snapshot reload
GCS download
protobuf shard reread from remote storage
```

unless a later benchmark-driven design explicitly adds a safe local paging strategy.

---

# 9. Reference Engine

Implement:

```python
class ReferenceEngine:
    def __init__(
        self,
        records: list[SentenceRecord],
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
 ↓
normalize()
 ↓
ALL SentenceRecords
 ↓
match_and_score()
 ↓
valid AutoCompleteData
 ↓
rank_results()
 ↓
first k
```

The Reference Engine does not use candidate pruning.

---

# 10. Correct Interpretation of "Oracle"

The Reference Engine is specifically the:

> **candidate-generation / indexing optimization oracle**

Why?

Both engines intentionally reuse:

```text
normalize()
match_and_score()
rank_results()
```

Therefore, if `match_and_score()` has a bug, both engines can share the same bug.

Matcher/scoring correctness is independently protected by:

```text
official Google examples
Member 1 unit tests
golden scoring cases
targeted edge cases
```

Differential tests prove:

```text
optimization did not change the answer
```

---

# 11. Critical Shared-Code Rule

Reference and optimized engines must reuse the same:

```text
normalize()
match_and_score()
rank_results()
AutoCompleteData conversion semantics
```

Do not create copies.

Only candidate source differs:

```text
Reference → all records
Optimized → SearchIndex candidate IDs
```

---

# 12. Differential Testing

Core assertion:

```python
reference = reference_engine.search(query)
optimized = search_engine.search(query)

assert optimized == reference
```

Run across many queries.

Required categories:

```text
exact substring
beginning substring
middle substring
end substring
one substitution
one extra query character
one missing query character
1-character query
2-character query
3-character query
4-character query
5-character query
6+ character query
edit in left partition
edit in right partition
edit exactly at/near partition boundary
same query appearing multiple times
duplicate sentences
same sentence in different files
multiple valid alignments
random/generated queries
```

Candidate-specific assertions should additionally verify:

```text
every result returned by Reference has its sentence_id present
in the optimized candidate set before verification
```

If a differential test fails, report enough data to distinguish:

```text
candidate-recall bug
partition/seed bug
posting intersection/union bug
matcher/scoring issue
ranking issue
metadata conversion issue
```

# 13. Generated Differential Cases

Generate queries from real normalized corpus sentences.

Examples:

```text
select valid substring
→ exact query
```

Then derive:

```text
replace one character
insert one extra character
remove one character
```

Keep random generation reproducible with a fixed seed in CI.

Store failing seeds/cases when possible.


For each generated query of useful length, deliberately place the one edit in:

```text
left partition
right partition
immediately before split
at split
immediately after split
```

Generate length buckets separately so the 1+1, 1+2, 2+2, 2+3, and 3+-gram paths are all exercised.

---

# 14. Official Regression Examples

Base sentence:

```text
To be or not to be, that is the question.
```

Expected:

```text
To be      → 10
or Not     → 12
be, that   → 14
2o be      → 3
to pe      → 6
or knot    → 8
or nt      → 8
not be     → NO MATCH
```

These tests must remain green after every optimization.

---

# 15. SearchEngine Tests

At minimum:

```text
no candidates
one candidate
more than 5 legal matches
mixed scores
tie scores
middle substring
one-edit result
candidate false positive discarded
1-character fallback result verified
short-query indexed candidate verified
original sentence preserved
source path preserved
offset preserved
default Top 5
custom internal k
```

---

# 16. Ranking Tests

Explicitly test:

```text
higher score first
same score → alphabetical completed_sentence
ranking independent of input order
more than 5 then truncate after ranking
```

If identical completed sentences from different records require further tie-breaking, that remains a Team Decision Required case until the baseline defines it.

---

# 17. CLI Behavior

Implement interactive behavior required by the assignment.

The system starts ready for input.

After Enter:

```text
use current accumulated text
show up to 5 suggestions
allow continuation
```

When the user enters:

```text
#
```

reset:

```python
current_input = ""
```

Do not restart the entire application.

---

# 18. CLI State

Maintain:

```text
current_input
```

The exact prompt wording is not architecturally important.

Required semantics:

```text
fragment 1
→ query accumulated text
→ show suggestions

fragment 2
→ append/continue current sentence
→ query updated accumulated text
→ show suggestions

#
→ reset current sentence
```

CLI tests should make the chosen input-append behavior explicit and deterministic.

---

# 19. CLI Output

Each displayed suggestion must include:

```text
completed sentence
source path
offset
score
```

Always display the original corpus sentence.

Do not expose normalized text as the completed sentence.

---

# 20. Startup / Snapshot Integration Test

Use a small deterministic nested corpus.

End-to-end flow:

```text
C++ builder
 ↓
Protobuf snapshot
 ↓
LocalArtifactStore
 ↓
Python snapshot loader
 ↓
SearchIndex + records
 ↓
SearchEngine
 ↓
official API
 ↓
results
```

Verify:

```text
correct sentences
correct source paths
correct line numbers
correct scores
correct ranking
```

A separate optional test may exercise:

```text
GCSArtifactStore
→ local materialization
→ same loaded result
```

Cloud-dependent tests should be isolated from the default local CI path unless credentials/test infrastructure are intentionally provided.

---

# 21. Error / Failure Quality

Startup should fail clearly for:

```text
missing snapshot
corrupt manifest
missing shard
unsupported schema version
unsupported normalization version
invalid protobuf data
GCS materialization failure
```

Do not serve queries with a partially initialized engine.

The public query API should not perform cloud/network recovery logic.

---

# 22. Edge Cases

Explicitly test or mark Team Decision Required:

```text
empty query
spaces-only query
punctuation-only query
very short query
query longer than sentence
empty corpus
empty corpus line
duplicate sentences
same sentence in different files
same score for several sentences
query appears several times in one sentence
typo near removed punctuation
typo near normalized spaces
edit at partition boundary
```

Do not create a branch-only policy for undefined assignment behavior.

---

# 23. Quality Gate

The system is not ready because:

```text
"It works on the demo."
```

Required:

```text
official regression tests pass
Search Core unit tests pass
C++/Python normalization parity passes
snapshot round trip passes
SearchIndex tests pass
partition candidate-generation tests pass
Reference Engine passes
differential tests pass
CLI tests pass
integration tests pass
local no-cloud execution works
```

Competition-ready additionally requires:

```text
benchmark evidence
CI green
documented architecture
clear error handling
team explainability
```

---

# 24. Benchmark Cooperation

Member 2 owns most offline/index metrics.

You should contribute online measurements for:

```text
end-to-end query latency
candidate count per query
candidate count by query-length bucket
safe-fallback rate
verification time
ranking time
```

Do not benchmark GCS download time as if it were per-query latency.

Online benchmark setup must use a fully materialized/loaded snapshot.

---

# 25. Bug Report Template for Differential Failures

Capture at least:

```text
query
normalized query
partition split
seed grams
partition candidate IDs
Reference output
Optimized output
candidate IDs
missing/extra result
source path
line number
random seed if generated
```

This should make it possible to route the bug quickly to:

```text
Member 1 → matcher/scoring
Member 2 → candidate/index/snapshot
Member 3 → orchestration/ranking/metadata
```

---

# 26. Definition of Done

Ready for integration only when:

- [ ] ranking is correct;
- [ ] SearchEngine is correct;
- [ ] official facade has the exact required signature;
- [ ] Reference Engine works;
- [ ] official examples pass;
- [ ] differential tests pass;
- [ ] partition-boundary differential tests pass;
- [ ] optimized candidate recall is proven for generated one-edit cases;
- [ ] optimized output equals reference output for tested query sets;
- [ ] CLI continuation works;
- [ ] `#` reset works;
- [ ] startup from local snapshot works;
- [ ] integration tests cover C++ snapshot → Python search;
- [ ] original sentence/path/offset are preserved;
- [ ] important edge cases are covered or explicitly Team Decision Required;
- [ ] all owned tests pass;
- [ ] frozen interfaces are unchanged;
- [ ] no other teammate's implementation was silently modified.

---

# 27. AI Coding Assistant Instruction

Use:

```text
00_TEAM_BASELINE.md
+
03_SEARCH_QUALITY_CLI_SPEC.md
```

Instruction:

> Implement only Search Integration, Ranking, ReferenceEngine, the official API facade, CLI, and quality/integration tests.
> Reuse Member 1 normalize() and match_and_score().
> Reuse Member 2 SearchIndex and snapshot/artifact APIs.
> Do not duplicate matching, scoring, normalization, corpus traversal, Protobuf serialization, or candidate-generation logic.
> Keep GCS outside the query hot path.
> If another branch is not ready, use mocks/fakes that follow the frozen interfaces.
> If a shared contract appears insufficient, stop and explain the issue instead of changing it automatically.
