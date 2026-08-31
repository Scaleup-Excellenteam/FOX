# SPEC 1 — Search Core v2.1

## Role

**Python Canonical Normalization, Exact/One-Edit Matching & Official Scoring**

Use with `00_TEAM_BASELINE.md` and `PHASE0_SHARED_FOUNDATION_SPEC.md`. Official Part A requirements override all team documents.

## 1. Branch

```text
feature/search-core
```

It must be created from the frozen `PHASE0_COMMIT`.

## 2. Ownership

You own:

```text
src/autocomplete/normalization.py
src/autocomplete/matcher.py
src/autocomplete/scoring.py

tests/test_normalization.py
tests/test_matcher.py
tests/test_scoring.py
```

You also contribute official matcher/scoring cases to integration tests through coordination with Member 3.

You do not own corpus traversal, Protobuf, snapshot loading, SearchIndex, ranking, SearchEngine, ReferenceEngine, CLI, startup, or external integrations.

The frozen normalization golden JSON is shared; propose changes rather than silently editing its semantics.

## 3. Normalization API

```python
def normalize(text: str) -> str:
    ...
```

Exact v2.1 semantics:

1. ASCII `A-Z` → `a-z`.
2. Delete exactly the ASCII punctuation set:

```text
!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~
```

3. Collapse runs of ASCII space `U+0020` to one ASCII space.
4. Trim leading/trailing ASCII spaces.
5. Preserve all other valid Unicode code points unchanged; the assignment corpus is expected to be English.

All character positions and lengths in this spec are Unicode code-point positions. Do not count UTF-8 bytes as characters.

Never overwrite original corpus text.

Examples:

```text
Hello,       WORLD!!! → hello world
can't-stop             → cantstop
be, that               → be that
```

## 4. Scoring Module Responsibility

`scoring.py` owns the penalty tables and score arithmetic. `matcher.py` may call scoring helpers; `scoring.py` must not import matcher logic.

Recommended public/internal helpers:

```python
def substitution_penalty(position: int) -> int:
    ...


def extra_or_missing_penalty(position: int) -> int:
    ...


def exact_score(query_length: int) -> int:
    ...


def edited_score(matching_characters: int, penalty: int) -> int:
    ...
```

Penalty positions are 1-based and must reject invalid positions `< 1` in internal helpers.

## 5. Authoritative Matching API

```python
def match_and_score(query: str, sentence: str) -> int | None:
    ...
```

Preconditions:

- both inputs are already normalized;
- `query == ""` is an invalid internal call and must raise `ValueError`;
- production callers prevent this case because SearchEngine returns `[]` for a query that normalizes to empty.

Contract:

- exact substring is valid;
- one substitution is valid;
- one extra character in query is valid;
- one missing character in query is valid;
- 2+ edits are invalid;
- match may begin anywhere in sentence;
- inspect all legal alignments;
- return the highest valid score for this sentence;
- return `None` when no legal alignment exists.

Only this function is authoritative for MATCH/NO_MATCH and the final Part A score.

## 6. Match Cases and Exact Score Rules

Let normalized query length be `m`.

### 6.1 Exact

Target substring length = `m`.

```text
matching_characters = m
score = 2m
```

### 6.2 Substitution

Target substring length = `m`; exactly one aligned character differs.

```text
matching_characters = m - 1
score = 2(m - 1) - substitution_penalty(mismatch_position)
```

### 6.3 Extra character in query

Removing exactly one query character produces a target substring of length `m - 1`.

```text
matching_characters = m - 1
score = 2(m - 1) - extra_or_missing_penalty(extra_query_position)
```

### 6.4 Missing character in query

Inserting exactly one target character into the query produces a target substring of length `m + 1`.

All `m` typed characters match; the inserted character earns no matching points.

```text
matching_characters = m
score = 2m - extra_or_missing_penalty(insertion_position)
```

Insertion position may be `1 .. m + 1`.

### 6.5 Negative scores are valid

A legal one-edit match may have a negative score for a very short query.

Example:

```text
query = "a"
target substring = "b"
one substitution at position 1
matching characters = 0
score = 0 - 5 = -5
```

Return `-5`; do not clamp to zero and do not reinterpret it as `NO_MATCH`.

## 7. Implementation Guidance

Because only one edit is permitted, a full general-purpose Levenshtein DP matrix is not required. Prefer a clear bounded/two-pointer alignment check that can explain:

```text
same-length target → exact/substitution check
length m-1 target → one extra query character check
length m+1 target → one missing query character check
```

The implementation may optimize exact substring discovery first, but must still evaluate all relevant one-edit alignments and choose the best score.

Do not couple the matcher to grams, sentence IDs, partitions, candidate order, or snapshot representation.

## 8. Official Permanent Regression Cases

Normalize the query and sentence before calling `match_and_score`.

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
| `not be` | `None` |

These tests are never weakened to make an implementation pass.

## 9. Required Tests

### Normalization

- mixed case;
- every punctuation character in the frozen set;
- punctuation adjacent to words;
- repeated spaces;
- leading/trailing spaces;
- mixed punctuation/spaces/case;
- empty input;
- spaces-only input;
- punctuation-only input;
- all Phase 0 golden vectors.

### Exact matching

- beginning/middle/end;
- full sentence;
- repeated occurrence in one sentence;
- spaces count as characters.

### Substitution

- edit position 1, 2, 3, 4, 5+;
- beginning/middle/end alignments;
- multiple alignments choose best score.

### Extra character in query

- extra at positions 1, 2, 3, 4, 5+;
- extra at query end;
- multiple alignments choose best score.

### Missing character in query

- missing at insertion positions 1, 2, 3, 4, 5+;
- missing at insertion position `m + 1`;
- multiple alignments choose best score.

### API boundary / score edge

- `match_and_score("", sentence)` raises `ValueError`;
- a legal negative-score edit remains a match and returns the negative integer unchanged.

### Rejection

- two substitutions;
- two extra characters;
- two missing characters;
- mixed two-edit cases;
- unrelated query;
- cases where only a non-substring global alignment would succeed.

### Boundaries

- 1-character normalized query when called directly;
- query longer than sentence by one (possible extra-in-query case);
- query longer by more than one → no match unless another sentence substring alignment exists;
- punctuation already normalized out by caller.

## 10. Property/Generated Core Tests

In addition to fixed unit cases, generate small deterministic strings and compare the optimized matcher implementation against a simple **test-only exhaustive one-edit oracle** for short strings.

This oracle must live only in tests and must not become production code. It is valuable because ReferenceEngine intentionally shares the production matcher and therefore cannot detect matcher bugs.

Use a fixed random seed in CI and retain failing examples.

## 11. Performance

Correctness first. After tests are green:

- avoid unnecessary allocations in inner alignment loops;
- avoid a full edit-distance matrix for a one-edit problem unless benchmark evidence justifies it;
- keep time proportional to examined candidate sentence/alignment size;
- do not add caching/global mutable state without team approval.

Member 3 measures matcher time in end-to-end benchmarks; Member 1 cooperates on hot-path profiling if needed.

## 12. Integration Contract

Member 3 calls:

```python
score = match_and_score(normalized_query, record.normalized)
```

Assumptions:

```text
int  → final score for that record
None → no legal match
```

Member 2 may never duplicate or approximate this final decision inside SearchIndex.

## 13. Definition of Done

- [ ] Python normalization matches every frozen golden vector;
- [ ] exact substring works at beginning/middle/end;
- [ ] all three one-edit types work;
- [ ] 2+ edits are rejected;
- [ ] empty normalized query is rejected at the matcher boundary with `ValueError`;
- [ ] legal negative scores are returned unchanged and never clamped;
- [ ] all penalty positions are correct;
- [ ] missing-at-end insertion position is correct;
- [ ] multiple alignments return highest score;
- [ ] official regression cases pass;
- [ ] generated exhaustive short-string oracle agrees with production matcher for the tested domain;
- [ ] Ruff check/format pass;
- [ ] owned tests pass from a clean project environment;
- [ ] frozen interfaces unchanged.

## 14. Codex Instruction

> Implement only Search Core v2.1. Follow the frozen normalization and scoring contracts exactly. Keep scoring arithmetic in `scoring.py` and alignment/match detection in `matcher.py`. Do not implement corpus loading, Protobuf, indexing, SearchEngine, ranking, ReferenceEngine, CLI, or Part B integrations. If a shared contract appears ambiguous, stop and report it instead of inventing behavior.
