# 01_SEARCH_CORE_SPEC.md

# SPEC 1 — Search Core

## Role

**Canonical Normalization, Exact/One-Edit Matching & Google Scoring**

**Architecture baseline:** v1.1

Use this file together with `00_TEAM_BASELINE.md`.

If anything conflicts, `00_TEAM_BASELINE.md` wins.

---

## 1. Branch

```text
feature/search-core
```

---

## 2. Primary Responsibility

You own:

```text
Canonical normalization semantics
Python runtime normalization
Normalization golden vectors
Exact substring matching
One-edit matching
Google scoring
Search-core unit tests
```

You do **not** own:

```text
C++ corpus traversal
C++ 1/2/3-Gram inverted index
Protobuf snapshot serialization
Python snapshot loading
SearchIndex internals
two-part candidate-generation algorithm
ranking
SearchEngine orchestration
Reference Engine
CLI
GCS integration
integration test ownership
```

---

## 3. Files You Primarily Own

```text
src/autocomplete/normalization.py
src/autocomplete/matcher.py
src/autocomplete/scoring.py

tests/test_normalization.py
tests/test_matcher.py
tests/test_scoring.py
```

Shared contract artifact whose semantic content you lead:

```text
tests/contracts/normalization_cases.json
```

After the team freezes that file, changes require team agreement because Member 2's C++ implementation also depends on it.

Do not edit another teammate's owned implementation without explicit coordination.

---

# 4. Canonical Normalization Contract

The assignment requires:

```text
case-insensitive matching
punctuation removal
repeated-space collapse
```

The team baseline defines the exact cross-language v1 semantics.

Implement:

```python
def normalize(text: str) -> str:
    ...
```

Apply exactly:

1. ASCII `A-Z` → `a-z`;
2. delete ASCII punctuation from:

```text
!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~
```

3. collapse repeated ASCII spaces (`U+0020`) to one space;
4. trim leading/trailing ASCII spaces.

Example:

```text
Hello,       WORLD!!!
→
hello world
```

Rules:

- input/output are Python `str`;
- the assignment corpus is English;
- original corpus text is never overwritten;
- remaining spaces count as scoring characters;
- punctuation is deleted, not converted to spaces;
- do not implement a second normalization interpretation.

---

# 5. Cross-Language Normalization Parity

Member 2 will implement the same contract in C++.

You are responsible for maintaining high-quality golden vectors in:

```text
tests/contracts/normalization_cases.json
```

The same test cases must be consumed by:

```text
Python normalization tests
C++ normalization tests
```

Required invariant:

```text
normalize_python(input)
==
normalize_cpp(input)
==
expected
```

Include cases for:

```text
mixed case
multiple punctuation characters
punctuation adjacent to words
repeated spaces
leading/trailing spaces
punctuation + repeated spaces together
empty string
punctuation-only input
```

For behaviors still marked Team Decision Required in the baseline, do not silently invent a production behavior outside the agreed contract.

---

# 6. Matching API

Implement exactly:

```python
def match_and_score(
    query: str,
    sentence: str,
) -> int | None:
    ...
```

Inputs are **already normalized**.

The query may match:

```text
beginning
middle
end
```

This is substring matching, not prefix-only matching.

Return:

```text
int  → best valid score for this sentence
None → no valid match
```

---

# 7. Valid Match Types

Support:

```text
EXACT
SUBSTITUTION
EXTRA_IN_QUERY
MISSING_IN_QUERY
NO_MATCH
```

Allowed:

```text
0 edits
or
1 edit
```

Reject:

```text
2+ edits
```

The matcher must inspect all relevant substring alignments.

If multiple legal alignments exist in the same sentence:

```text
return the highest valid score
```

---

# 8. Exact Match

If the normalized query appears exactly as a substring of the normalized sentence:

```text
valid match
penalty = 0
score = 2 × len(normalized_query)
```

Remember that remaining ASCII spaces are characters.

---

# 9. One-Edit Cases

## 9.1 Substitution

One query character differs from the corresponding target character.

The mismatched character earns no matching points.

---

## 9.2 Extra Character in Query

The query contains one extra typed character.

Removing that one query character must allow the remaining query to match a sentence substring.

The extra character earns no matching points.

---

## 9.3 Missing Character in Query

One character is missing from the query.

Inserting exactly one target character at the correct query position must allow the query to match a sentence substring.

The missing character earns no matching points.

---

# 10. Scoring

Formula:

```text
Score = 2 × matching_characters - edit_penalty
```

Positions are 1-based in the normalized query.

## 10.1 Substitution Penalty

```text
Position 1 → 5
Position 2 → 4
Position 3 → 3
Position 4 → 2
Position 5+ → 1
```

## 10.2 Extra / Missing Character Penalty

```text
Position 1 → 10
Position 2 → 8
Position 3 → 6
Position 4 → 4
Position 5+ → 2
```

For a missing character:

```text
use the position where the character would be inserted
```

---

# 11. Internal Design Freedom

You may use internal helper types such as:

```text
MatchType
MatchDetail
AlignmentResult
```

if they improve correctness/readability.

However the frozen public API remains:

```python
match_and_score(query: str, sentence: str) -> int | None
```

Do not expose a branch-specific matcher contract to other teammates without team approval.

---

# 12. Official Regression Examples

Base sentence:

```text
To be or not to be, that is the question.
```

Tests normalize before calling `match_and_score()`.

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

These are permanent regression cases.

---

# 13. Required Unit Tests

At minimum:

## Normalization

```text
ASCII case conversion
each punctuation family
punctuation adjacent to letters
repeated spaces
leading/trailing spaces
mixed normalization cases
golden contract vectors
```

## Exact Matching

```text
beginning substring
middle substring
end substring
full-sentence substring
same query appearing multiple times
```

## Substitution

```text
position 1
position 2
position 3
position 4
position 5+
```

## Extra Character

```text
position 1
position 2
position 3
position 4
position 5+
```

## Missing Character

```text
position 1
position 2
position 3
position 4
position 5+
```

## Rejection

```text
2 substitutions
2 missing characters
2 extra characters
mixed 2-edit cases
unrelated text
```

## Scoring

```text
exact score
all substitution penalties
all extra/missing penalties
edited character receives no match points
space match points
```

## Alignment

```text
multiple valid sentence alignments
best-score alignment wins
```

## Official Examples

All official regression examples must pass.

---

# 14. Performance Responsibility

You are not the owner of corpus-scale indexing performance.

However the matcher is on the online hot path.

Requirements:

- avoid obviously quadratic work when a simpler bounded scan is available;
- keep implementation readable and explainable;
- benchmark only after correctness is proven;
- do not trade correctness for micro-optimizations;
- expose no hidden global state.

If a faster algorithm such as a bit-parallel matcher is considered later, it must preserve the exact same public contract and pass all existing tests.

---

# 15. Cross-Team Integration Contract

Member 3 will call:

```python
score = match_and_score(
    normalized_query,
    record.normalized,
)
```

Member 3 assumes:

```text
score is final for that sentence
None means invalid match
```

Member 2's index must never duplicate this logic.

The matcher must not assume that a candidate came from a particular gram, partition, or index strategy. It must remain fully correct when called directly by the Reference Engine over any normalized sentence.

---

# 16. Definition of Done

Ready for integration only when:

- [ ] `normalize()` exactly follows the shared contract;
- [ ] Python golden normalization vectors pass;
- [ ] C++ parity expectations are documented and consumable;
- [ ] `match_and_score()` handles exact substring;
- [ ] substitution works;
- [ ] extra query character works;
- [ ] missing query character works;
- [ ] 2+ edits are rejected;
- [ ] best alignment is chosen;
- [ ] Google score is exact;
- [ ] official regression examples pass;
- [ ] all owned unit tests pass;
- [ ] frozen public interfaces are unchanged;
- [ ] no other teammate's implementation was silently modified.

---

# 17. AI Coding Assistant Instruction

Use:

```text
00_TEAM_BASELINE.md
+
01_SEARCH_CORE_SPEC.md
```

Instruction:

> Implement only the Search Core specification.
> Follow the canonical normalization contract exactly.
> Do not implement corpus loading, Protobuf snapshots, SearchIndex internals, ranking, SearchEngine, ReferenceEngine, CLI, or GCS.
> Do not rename or redesign frozen interfaces.
> If a shared interface appears insufficient, stop and explain the issue instead of changing it automatically.
