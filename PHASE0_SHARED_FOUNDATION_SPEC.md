# PHASE0_SHARED_FOUNDATION_SPEC.md

# Phase 0 — Shared Foundation & Clean-Start Contract v2.1

**Purpose:** create one approved repository baseline from which all three feature branches start.  
**Rule:** no feature implementation starts until Phase 0 is merged and its commit hash is frozen.

## 1. Outcome

Phase 0 must produce a clean repository where all shared contracts compile/import and CI is green, but it must **not** implement the feature-owned search algorithms.

Deliverables:

```text
repository skeleton
shared Python models
frozen protobuf schema v1
normalization golden vectors
shared package/test configuration
C++/Python/protobuf toolchain setup
CI skeleton
README bootstrap instructions
Git branch-start contract
```

## 2. Phase 0 Branch

Use one shared branch only:

```text
chore/phase0-foundation
```

It must be created from the clean project `main` history. Do not create an orphan/root branch.

All three teammates review the branch before merge.

After merge:

```bash
git switch main
git pull --ff-only
PHASE0_COMMIT=$(git rev-parse HEAD)
```

Record that hash in the team chat/README. Every feature branch must be created from exactly that commit:

```bash
git switch -c feature/search-core "$PHASE0_COMMIT"
git switch -c feature/offline-index-snapshot "$PHASE0_COMMIT"
git switch -c feature/search-quality "$PHASE0_COMMIT"
```

Each teammate verifies:

```bash
git rev-parse HEAD
git merge-base HEAD "$PHASE0_COMMIT"
```

Both must resolve to the same Phase 0 commit at branch creation time.

## 3. Repository Skeleton

Create exactly the shared structure needed for Part A:

```text
cpp/
  CMakeLists.txt
  include/
  src/
  tests/
proto/
  autocomplete_snapshot.proto
src/autocomplete/
  __init__.py
  models.py
tests/
  contracts/
    normalization_cases.json
benchmarks/
scripts/
data/
  raw/
  snapshots/
.github/workflows/
00_TEAM_BASELINE.md
PHASE0_SHARED_FOUNDATION_SPEC.md
01_SEARCH_CORE_SPEC.md
02_OFFLINE_INDEX_SNAPSHOT_SPEC.md
03_SEARCH_QUALITY_CLI_SPEC.md
pyproject.toml
README.md
.gitignore
```

Do not create empty production modules owned by members unless required for package import. The goal is to prevent fake implementations from becoming accidental contracts.

## 4. Shared Python Models — Frozen

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

No role branch may introduce an alternative record/result class.

## 5. Shared Protobuf Schema v1 — Frozen

File: `proto/autocomplete_snapshot.proto`

The initial schema must logically contain these messages and meanings:

```proto
syntax = "proto3";

package autocomplete.snapshot.v1;

message SentenceRecordProto {
  uint64 sentence_id = 1;
  string original = 2;
  string normalized = 3;
  string source_path = 4;
  uint64 line_number = 5;
}

message GramPostingProto {
  uint32 gram_size = 1;
  string gram = 2;
  repeated uint64 sentence_ids = 3;
}

message SnapshotManifestProto {
  uint32 schema_version = 1;
  uint32 normalization_version = 2;
  uint32 index_strategy_version = 3;
  repeated uint32 gram_sizes = 4;
  string corpus_digest_sha256 = 5;
  string snapshot_id = 6;
  string created_at_utc = 7;
  repeated string record_files = 8;
  repeated string index_files = 9;
  uint64 searchable_record_count = 10;
  uint64 posting_count = 11;
  string index_digest_sha256 = 12;
}
```

Frozen initial values:

```text
schema_version = 1
normalization_version = 1
index_strategy_version = 1
gram_sizes = [1, 2, 3]
```

Rules:

- Never reuse a protobuf field number for a different meaning.
- Do not renumber fields after freeze.
- Generated C++/Python bindings come only from this file.
- Generated bindings are generated artifacts and must never be hand-edited; the Python binding is committed according to Section 12, while C++ bindings remain build outputs.
- Any schema semantic change requires all-team approval and a version decision.

- Searchable sentence IDs start at `1`; `0` is reserved/invalid.
- `GramPostingProto.gram` stores UTF-8 text, but gram length is defined by Unicode code points, not bytes.
- `searchable_record_count`, `posting_count`, and `index_digest_sha256` are validated by the Python loader.

## 6. Snapshot Framing Contract — Frozen

`manifest.binpb` contains one manifest message.

`records.binpb` and `index.binpb` are streams of messages framed as:

```text
4-byte unsigned big-endian payload length
+ exactly that many protobuf payload bytes
```

Initial snapshot:

```text
snapshot/
├── manifest.binpb
├── records.binpb
└── index.binpb
```

If later benchmarks justify sharding:

```text
records-00000.binpb ...
index-00000.binpb ...
```

The same framing applies and the manifest lists files in deterministic order.

## 7. Canonical Normalization Golden Vectors — Frozen

File: `tests/contracts/normalization_cases.json`.

It must cover at least:

```text
mixed ASCII case
all ASCII punctuation characters
punctuation adjacent to letters
multiple/repeated spaces
leading/trailing spaces
punctuation + spaces
empty string
spaces-only string
punctuation-only string
```

Required examples include:

```json
[
  {"input": "Hello,       WORLD!!!", "expected": "hello world"},
  {"input": "  Leading and trailing  ", "expected": "leading and trailing"},
  {"input": "can't-stop", "expected": "cantstop"},
  {"input": "!!!", "expected": ""},
  {"input": "", "expected": ""}
]
```

The same file is consumed by Python and C++ tests. No branch maintains a second normalization truth table.

## 8. Corpus I/O Contract — Frozen

- recursively discover regular `.txt` files, extension case-insensitive;
- store paths relative to corpus root using `/` separators;
- sort relative paths lexicographically before processing;
- process physical lines in order, using 1-based line numbers;
- accept UTF-8 with optional BOM; if present, the BOM is treated as an encoding marker and is not part of the first sentence;
- accept LF/CRLF;
- invalid UTF-8 fails the offline build clearly;
- normalize each line using the shared contract;
- define character positions/gram lengths as Unicode code points after UTF-8 decoding;
- skip lines whose normalized value is empty while retaining their physical line numbers for surrounding records;
- assign searchable sentence IDs starting at 1; sentence ID 0 is invalid/reserved.

## 9. Toolchain Policy

Do not guess package/tool versions independently in each branch.

Phase 0 must record a tested toolchain in README/CI after successful installation on the team environment:

```text
Python: 3.12.x
C++ language standard: C++20
CMake: one team-agreed tested version/minimum
C++ compiler: team-agreed GCC/Clang family + tested version
protoc + protobuf runtime: compatible pinned versions
pytest: pinned in project config
ruff: pinned in project config
```

The exact CMake/compiler/protobuf package versions are frozen from a successful local+CI setup during Phase 0, not invented later by a feature branch.

## 10. Python Packaging / Test Contract

Use a standard `src/` package layout configured by `pyproject.toml`.

Every checkout/worktree uses its **own repository-local virtual environment**:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Do not share one editable-install virtual environment across multiple Git worktrees: an editable package points at one source tree and can silently import code from the wrong worktree.

After setup, a fresh checkout must support:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

`pyproject.toml` must configure package discovery from `src/`. Generated protobuf Python code is excluded from manual formatting/lint-fix ownership; CI validates it by regeneration/diff instead.

Tests must not require ad-hoc `PYTHONPATH=src`, a developer's previous editable install, or another worktree. CI starts from a clean checkout and is the packaging truth check.

## 11. C++ Build/Test Contract

A fresh checkout must support documented commands equivalent to:

```bash
cmake -S cpp -B build/cpp
cmake --build build/cpp
ctest --test-dir build/cpp --output-on-failure
```

The exact generated-protobuf build wiring is established once in Phase 0 and reused by Member 2.

Freeze the future builder CLI contract (implementation belongs to Member 2):

```bash
./build/cpp/autocomplete_builder \
  --corpus <extracted-corpus-root> \
  --output <snapshot-directory>
```

README documents how to extract the provided `Archive.zip` before this command. Direct ZIP parsing is optional, not required.

## 12. Proto Generation Contract

Provide one documented command:

```text
scripts/generate_proto.sh
```

It generates both language bindings from the same frozen `.proto` using the pinned `protoc`.

Frozen locations/policy:

```text
Python committed binding:
src/autocomplete/generated/autocomplete_snapshot_pb2.py

Python package marker:
src/autocomplete/generated/__init__.py

C++ generated build output:
build/generated/proto/...
```

The Python generated binding is committed so a fresh Python checkout can import the runtime package without requiring `protoc` at query time. It is never hand-edited.

C++ bindings are generated by the documented build/generation step and are not committed as source-of-truth files.

CI must:

1. generate both Python and C++ bindings successfully;
2. regenerate the Python binding with the pinned `protoc`;
3. fail if the regenerated Python binding differs from the committed binding;
4. prove the Python generated module imports from a clean checkout.

## 13. CI Skeleton

Phase 0 CI must at least verify:

```text
fresh .venv/project dependency installation succeeds
editable `src/` package import resolves from the current checkout
Python package imports
Ruff check
Ruff format --check
shared model tests
normalization golden JSON is valid
C++ configures/builds
protoc generates both languages
```

Feature-specific tests are added by their owners later.

## 14. Shared Public Contracts

Frozen after Phase 0 approval:

```python
normalize(text: str) -> str
match_and_score(query: str, sentence: str) -> int | None
SearchIndex.get_candidate_ids(normalized_query: str) -> list[int]
load_snapshot(snapshot_path: Path) -> tuple[dict[int, SentenceRecord], SearchIndex]
rank_results(results: list[AutoCompleteData]) -> list[AutoCompleteData]
SearchEngine.search(prefix: str, k: int = 5) -> list[AutoCompleteData]
configure_default_engine(engine: SearchEngine) -> None
get_best_k_completions(prefix: str) -> List[AutoCompleteData]
```

Implementation files may not exist yet; the contracts are documented here and in the baseline.

## 15. Shared Behavioral Decisions

Freeze these before feature coding:

```text
normalized empty query → []
normalized-empty corpus line → skipped
same text at different source/offset → distinct records
required ranking → score desc, completed_sentence asc
full deterministic tie → source_text asc, offset asc
CLI reset fragment → exactly "#"
CLI continuation → append exactly what user types; do not auto-insert a space
sentence_id starts at 1; 0 is invalid/reserved
character/query/gram positions use Unicode code points
legal negative scores are preserved, never clamped
match_and_score("", sentence) → ValueError
ranking alphabetical key → original completed_sentence Unicode lexicographic order
```

## 16. Ownership Boundary During Phase 0

Phase 0 is shared work. After freeze:

- Member 1 owns Search Core files.
- Member 2 owns Offline/Index/Snapshot files.
- Member 3 owns Integration/Quality/CLI files.
- Shared frozen files require all-team approval.

## 17. Phase 0 Definition of Done

Do not create feature branches until all are true:

- [ ] all five SPEC/baseline files approved;
- [ ] repository skeleton exists;
- [ ] no old unrelated/orphan feature history is used as a new base;
- [ ] shared models committed;
- [ ] `.proto` v1 committed; Python generated binding committed and verified; C++ binding generation verified;
- [ ] normalization golden JSON committed and valid;
- [ ] sentence-id, Unicode code-point, negative-score, and empty-matcher contracts are documented consistently;
- [ ] Python packaging works from a clean checkout using a repository-local `.venv` and editable install;
- [ ] Python lint/format baseline green;
- [ ] C++ configure/build baseline green;
- [ ] CI green;
- [ ] README has setup/build/test commands;
- [ ] `PHASE0_COMMIT` recorded;
- [ ] all three feature branches are created from exactly `PHASE0_COMMIT`.

## 18. AI Coding Assistant Rule

When using Codex or another coding assistant during Phase 0:

> Implement only the shared foundation. Do not implement member-owned search algorithms. Do not change frozen interfaces or create alternate models/schemas. If a contract appears ambiguous, stop and report the ambiguity before writing code.
