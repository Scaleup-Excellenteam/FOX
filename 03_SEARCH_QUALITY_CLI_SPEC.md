# SPEC 3 — Search Integration, Reference Validation, CLI & Quality v2.1

## Role

**Runtime Orchestration, Ranking, Official API, Reference/Differential Validation, CLI, Local Startup & Online Quality**

Use with `00_TEAM_BASELINE.md` and `PHASE0_SHARED_FOUNDATION_SPEC.md`.

## 1. Branch

```text
feature/search-quality
```

It must start from the frozen `PHASE0_COMMIT`.

## 2. Ownership

You own primarily:

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

benchmarks/ online search benchmark harness
```

You also own local runtime startup/configuration from a snapshot path.

You do not own normalization semantics, matcher/scoring, C++ builder, Protobuf writer/framing internals, snapshot loader internals, or SearchIndex candidate logic.

Until Member 2 is ready, use fakes/mocks that implement only the frozen SearchIndex/loader-facing contracts. Do not copy her algorithm.

## 3. Ranking

```python
def rank_results(
    results: list[AutoCompleteData],
) -> list[AutoCompleteData]:
    ...
```

Required ordering:

```text
1. score descending
2. completed_sentence ascending
```

Freeze `completed_sentence ascending` as Python/Unicode lexicographic ordering of the **original** completed sentence. Do not normalize, lowercase, or strip punctuation for ranking.

Deterministic extension only when those are exactly equal:

```text
3. source_text ascending
4. offset ascending
```

Do not rely on candidate order, sentence ID, corpus traversal order, or Python dict order.

Do not deduplicate different source records.

## 4. SearchEngine

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
→ normalize(prefix)
→ if normalized query == "": return []
→ index.get_candidate_ids(normalized_query)
→ lookup record
→ match_and_score(normalized_query, record.normalized)
→ discard None
→ preserve every integer score, including legal negative scores
→ convert to AutoCompleteData
→ rank_results
→ first k
```

For internal `k`, require an integer `k >= 0`; `k == 0` returns `[]`. Negative `k` raises `ValueError`.

If SearchIndex returns an unknown sentence ID at runtime, fail clearly rather than silently ignoring snapshot corruption.

## 5. Result Conversion

For every legal record:

```python
AutoCompleteData(
    completed_sentence=record.original,
    source_text=record.source_path,
    offset=record.line_number,
    score=score,
)
```

Never expose normalized text as the completed sentence.

## 6. Official Google-Facing API

File: `src/autocomplete/api.py`

```python
from typing import List


def configure_default_engine(engine: SearchEngine) -> None:
    ...


def get_best_k_completions(prefix: str) -> List[AutoCompleteData]:
    ...
```

The facade delegates to the configured default engine with `k=5`.

Define a clear error:

```python
class EngineNotInitializedError(RuntimeError):
    ...
```

Calling `get_best_k_completions` before configuration raises that error. Query calls do not perform snapshot/network recovery.

## 7. Local Startup

Part A startup is local-only:

```text
snapshot path
→ load_snapshot(snapshot_path)
→ records_by_id + SearchIndex
→ SearchEngine
→ configure_default_engine
→ CLI loop
```

Recommended invocation contract:

```bash
python -m autocomplete.main --snapshot data/snapshots/current
```

The exact argument parser wording may vary, but startup must accept an explicit local snapshot path and fail clearly for load errors.

No GCS/client/network logic belongs in Part A startup.

## 8. Reference Engine

```python
class ReferenceEngine:
    def __init__(self, records: list[SentenceRecord]):
        ...

    def search(self, prefix: str, k: int = 5) -> list[AutoCompleteData]:
        ...
```

Flow:

```text
normalize
→ empty? []
→ all records
→ same match_and_score
→ same result conversion
→ same rank_results
→ first k
```

It does not use SearchIndex.

## 9. Correct Meaning of the Reference Oracle

ReferenceEngine is the **candidate/index optimization oracle**, not an independent matcher oracle.

Both reference and optimized engines intentionally reuse production:

```text
normalize
match_and_score
rank_results
```

Therefore:

```text
Reference == Indexed
```

proves candidate pruning/integration did not change results, while Member 1's official/unit/generated exhaustive tests independently protect matcher/scoring correctness.

## 10. Differential Testing

Core assertion:

```python
assert search_engine.search(query) == reference_engine.search(query)
```

Required categories:

- exact beginning/middle/end;
- substitution;
- extra query character;
- missing query character;
- normalized-empty query;
- 1-char query;
- lengths 2, 3, 4, 5, 6+;
- edit left partition/right partition;
- edit immediately before/at/after partition split;
- repeated query occurrence;
- duplicate text records;
- same sentence in different files;
- multiple valid alignments;
- generated/randomized corpus-derived queries.

Also verify candidate recall before matching:

```text
Every record that produces a Reference legal result
must have its sentence_id in the optimized candidate set.
```

## 11. Generated Differential Cases

Use real normalized corpus sentences or a deterministic small fixture:

```text
choose substring
→ exact query
→ replace one char
→ insert one extra char
→ remove one char
```

Exercise query-length buckets and boundary edit positions deliberately.

Use fixed seeds in CI. On failure capture the seed and minimal useful case.

## 12. Official Permanent Regression Tests

Sentence:

```text
To be or not to be, that is the question.
```

Expected:

| Query | Expected |
|---|---:|
| `To be` | 10 |
| `or Not` | 12 |
| `be, that` | 14 |
| `2o be` | 3 |
| `to pe` | 6 |
| `or knot` | 8 |
| `or nt` | 8 |
| `not be` | no match |

Integration-level official tests must exercise the public result path, not only Member 1 helpers.

## 13. SearchEngine Tests

At minimum:

- empty normalized query returns `[]` without index call;
- no candidates;
- candidate false positive discarded;
- candidate valid match converted correctly;
- unknown candidate ID errors clearly;
- one legal result;
- more than five legal results;
- mixed scores;
- score ties;
- identical sentence+score different source records;
- exact/middle/one-edit results;
- original/path/offset preservation;
- default `k=5`;
- `k=0`;
- negative `k` rejected.

## 14. Ranking Tests

Explicitly test:

```text
higher score first
same score → original completed_sentence Unicode lexicographic ascending
same score+sentence → source_text ascending
same score+sentence+source → offset ascending
input order does not affect result
truncate only after full ranking
```

## 15. CLI Semantics — Frozen

Maintain:

```text
current_input
```

Each CLI read is a **new fragment**, not the full accumulated string.

Behavior:

```text
fragment != "#"
→ current_input += fragment exactly
→ query current_input
→ display suggestions

fragment == "#"
→ current_input = ""
→ return to initial prompt state
→ do not run a search for "#"
```

Do **not** automatically insert a space between fragments. If the user wants a space, the typed fragment includes one.

EOF/interrupt should exit gracefully without corrupting state or printing a traceback in normal use.

## 16. CLI Output

For each suggestion display:

```text
completed sentence
source path
offset
score
```

Output format wording is team-controlled, but information must be unambiguous and the completed sentence must be original corpus text.

## 17. Full Local Integration Test

Use a deterministic small nested corpus:

```text
`autocomplete_builder --corpus ... --output ...`
→ Protobuf snapshot
→ Python load_snapshot
→ SearchIndex + records
→ SearchEngine
→ configure_default_engine
→ get_best_k_completions
```

Verify:

```text
correct completed sentences
correct relative paths
correct physical line numbers
correct scores
correct ranking
Top 5 behavior
```

This is the primary cross-member Part A E2E gate.

## 18. Failure Quality

Startup must clearly report and stop for:

- snapshot path missing;
- invalid/corrupt manifest;
- missing snapshot data file;
- unsupported version;
- malformed framing/protobuf;
- invalid posting references.

Do not configure the default engine unless the snapshot has fully loaded and validated.

Query-time code should remain network-free and should not reload the snapshot.

## 19. Differential Failure Report

Capture:

```text
query
normalized query
query length/split
left/right seeds
candidate IDs
Reference output
Optimized output
missing/extra record
source path/line
random seed
```

Route likely ownership:

```text
matcher/score mismatch → Member 1
candidate/snapshot/index mismatch → Member 2
ranking/orchestration/metadata/CLI → Member 3
```

## 20. Online Benchmark Harness

Member 3 owns a reproducible online benchmark harness after correctness.

Measure at least:

```text
end-to-end query latency (at least median and p95; mean optional)
candidate count
candidate-generation latency
matcher verification latency
ranking latency
query-length bucket statistics
safe 1-char fallback frequency
Reference-vs-Indexed speedup on the same query set
```

Rules:

- snapshot is fully loaded before timing;
- use a fixed corpus and fixed/reproducible query set;
- report warm and/or cold conditions explicitly;
- do not mix startup/build time with query latency;
- keep correctness assertions enabled in benchmark validation runs.

Member 2 supplies offline/index size/build/load metrics; final quality report combines both.

## 21. Part B Readiness

Do not implement Part B features in this branch during Part A.

The Part A runtime boundary must make later feature modes possible without changing core semantics. A future semantic/generative/translation/speech mode may wrap SearchEngine or add a separate path, but must clearly distinguish its output from Part A matches/scores.

## 22. Definition of Done

- [ ] ranking is fully deterministic and requirement-compatible using the frozen original-string comparator;
- [ ] SearchEngine flow uses only frozen dependencies;
- [ ] normalized-empty query behavior correct;
- [ ] legal negative scores are preserved through result conversion/ranking;
- [ ] official facade exact and default engine initialization explicit;
- [ ] ReferenceEngine works;
- [ ] official E2E regressions pass;
- [ ] differential output equality passes;
- [ ] candidate recall assertions pass on generated sets;
- [ ] CLI continuation/reset/EOF behavior passes;
- [ ] local snapshot startup works;
- [ ] C++→Python→public API integration passes;
- [ ] original sentence/path/offset preserved;
- [ ] benchmark harness exists and is reproducible;
- [ ] Ruff/pytest/integration quality gates pass;
- [ ] no other member's algorithm duplicated or silently changed.

## 23. Codex Instruction

> Implement only Search Integration/Reference/CLI/Quality v2.1. Reuse Member 1 `normalize()` and `match_and_score()` and Member 2 `load_snapshot()`/`SearchIndex`. While Member 2 is unfinished, use fakes that satisfy the frozen interfaces; do not copy her gram/index logic. Keep Part A local and network-free. Do not modify shared contracts without team approval. Never create a new Git root/orphan history; work only on `feature/search-quality` created from PHASE0_COMMIT.
