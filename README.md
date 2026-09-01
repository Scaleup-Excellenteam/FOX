# FOX Autocomplete

## Overview

FOX is an offline autocomplete/search system. It preprocesses a text corpus into
a deterministic local snapshot, loads that snapshot at application startup, and
returns ranked sentence completions through the official Python API and an
interactive CLI.

Online search uses the local snapshot only. It does not require network or cloud
access: `SearchIndex` retrieves candidate sentence IDs, the matcher verifies and
scores them, and deterministic ranking selects the best results.

## Architecture

```text
Corpus ZIP or directory
        |
        v
C++ autocomplete_builder
        |
        v
records.binpb + index.binpb + manifest.binpb
        |
        v
load_snapshot()
        |
        v
SentenceRecord map + SearchIndex
        |
        v
SearchEngine -> matcher/scoring -> deterministic ranking -> Top-k
        |
        v
Official API / interactive CLI
```

The implementation has three logical areas:

- **Search Core:** normalization, exact/one-edit matching, and scoring.
- **Offline Index / Snapshot:** the C++ builder, Protobuf snapshot, Python
  loader, and candidate index.
- **Search Integration / Quality / CLI:** orchestration, deterministic ranking,
  public API, reference engine, CLI, and benchmark harness.

## Why Protobuf?

The offline index builder is written in C++, while the search runtime is written
in Python. Both use generated code from the same `.proto` schema: the builder
serializes sentence records, n-gram posting lists, and snapshot metadata, and the
Python runtime deserializes and validates that snapshot before serving searches.

This file-based snapshot boundary gives both languages a clear data contract and
separates offline index construction from online search. The runtime loads the
prebuilt records and index instead of rebuilding them from the corpus at every
startup. Protobuf defines the schema and serialized representation; it is not a
transport mechanism.

## Requirements

The validated toolchain is:

| Component | Version |
| --- | --- |
| Python | 3.12.3 (`>=3.12,<3.13`) |
| C++ | C++17 |
| CMake | 3.28.3 or compatible 3.28+ |
| GCC | 13.3.0 |
| `protoc` and C++ `libprotobuf` | 3.21.12 |
| Python `protobuf` | 6.32.0 |
| pytest | 8.4.2 |
| Ruff | 0.16.5 |

On Ubuntu 24.04, install the required system packages with:

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake pkg-config protobuf-compiler \
  libprotobuf-dev libssl-dev python3.12 python3.12-venv
```

The C++ binding generator and library must come from the same Protobuf
installation:

```bash
protoc --version
pkg-config --modversion protobuf
```

Both should report `3.21.12` for the validated configuration.

## Setup

Run these commands from the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The editable installation makes the `autocomplete` and `benchmarks` modules
available without a `PYTHONPATH` override.

## Build the C++ Snapshot Builder

Configure and build from the `cpp` source tree:

```bash
cmake -S cpp -B build/cpp
cmake --build build/cpp
```

The production executable is:

```text
build/cpp/autocomplete_builder
```

Run its C++ tests with:

```bash
ctest --test-dir build/cpp --output-on-failure
```

### Regenerate Protobuf bindings

The schema is [proto/autocomplete_snapshot.proto](proto/autocomplete_snapshot.proto).
Maintainers can regenerate the Python and C++ bindings with:

```bash
scripts/generate_proto.sh
```

The script requires `protoc 3.21.12`. Generated C++ files remain under
`build/generated/proto/`; generated files should not be edited by hand.

## Build a Snapshot

Snapshot construction is an offline operation. The Python orchestration module
accepts either a corpus directory or a ZIP archive, safely extracts supported
ZIP content into a bounded temporary directory when needed, and invokes the C++
builder:

```bash
python -m autocomplete.build_snapshot \
  <builder-path> \
  <corpus.zip-or-directory> \
  <snapshot-output-directory>
```

The output parent directory must exist, and the destination itself must not
already exist. A project-local example is:

```bash
mkdir -p data/snapshots
python -m autocomplete.build_snapshot \
  build/cpp/autocomplete_builder \
  data/raw/Archive.zip \
  data/snapshots/manual
```

`data/raw/Archive.zip` is the corpus path used for the representative build; do
not assume that every clone distributes this archive. A successful build
atomically publishes:

```text
data/snapshots/manual/
|-- manifest.binpb
|-- records.binpb
`-- index.binpb
```

For an already extracted corpus directory, the C++ executable can also be
called directly:

```bash
build/cpp/autocomplete_builder \
  --corpus <extracted-corpus-directory> \
  --output <snapshot-output-directory>
```

## Smart Snapshot Preparation

Use the preparation command when a corpus is expected to change over time:

```bash
python -m autocomplete.prepare_snapshot \
  --builder build/cpp/autocomplete_builder \
  --corpus data/raw/Archive.zip \
  --snapshot-root data/snapshots
```

Preparation inspects the authoritative ZIP or directory using the builder's
existing semantic corpus digest. If the active snapshot is fully valid and has
the same corpus digest, schema/normalization/index versions, and gram sizes, it
is reused without rebuilding the index. Otherwise, preparation builds and fully
validates a new immutable `data/snapshots/<snapshot-id>/` directory before
atomically replacing the relative `data/snapshots/current` pointer file.

ZIP extraction remains temporary. A failed extraction, build, validation, or
activation leaves the previous pointer unchanged. No corpus inspection or
preparation work is added to online query execution. Repeated commands report
either `status=REUSED` or `status=BUILT_AND_ACTIVATED`.

The managed root reserves `current` as a pointer file. If that path is already a
legacy snapshot directory from the one-shot build command, move it or select a
new snapshot root; preparation reports the conflict without modifying it.

## Run the Autocomplete CLI

Start the application with an explicit local snapshot path:

```bash
python -m autocomplete.main --snapshot data/snapshots/current
```

Add `--show-timing` to print `Search time: X.XX ms` after each query. The
measurement covers only the autocomplete lookup; startup, snapshot loading,
input collection, and result formatting are excluded.

The argument may be an immutable snapshot directory or the `current` pointer
created by smart preparation. The selected snapshot is fully loaded and
validated exactly once before the interactive CLI starts.

## CLI Interaction

At each `Enter query:` prompt, the entered fragment is appended exactly to the
current input. The CLI does not insert spaces automatically, so enter a leading
space in a fragment when one is intended.

- Enter exactly `#` to clear the accumulated input without running a search.
- Press Ctrl-D (EOF) to exit cleanly.
- Press Ctrl-C to exit cleanly.
- Each result displays its sentence, score, source path, and line offset.

For example, entering `hel` and then `lo` searches first for `hel` and then for
`hello`.

## Run Tests

Run the Python suite and static checks from the repository root:

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Latest validated implementation state: **522 Python tests passing**.

Run the C++ tests after configuring and building:

```bash
ctest --test-dir build/cpp --output-on-failure
```

## Run Benchmarks

The benchmark harness loads one snapshot, generates a deterministic mix of
real corpus substrings, checks `SearchEngine` against `ReferenceEngine` before
timing, performs warm-up rounds, and reports online latency and stage metrics.
Snapshot loading and initialization are excluded from online query latency.

Run it with its defaults:

```bash
python -m benchmarks.benchmark_search \
  --snapshot data/snapshots/current
```

The defaults can take substantial time on a large snapshot because the harness
also measures the full-scan reference engine. For a smaller development run,
control the workload and write a machine-readable report with:

```bash
python -m benchmarks.benchmark_search \
  --snapshot data/snapshots/current \
  --seed 20260901 \
  --query-count 6 \
  --repeats 3 \
  --warmup-rounds 1 \
  --json-output benchmark-report.json
```

Available controls are `--seed`, `--query-count`, `--repeats`,
`--warmup-rounds`, and `--json-output`. Results depend on hardware, corpus,
Python runtime, and current system load.

## Representative Performance

These are representative development-machine measurements, not latency
guarantees.

Measurement environment:

- Python 3.12.3 on Linux/WSL2, x86-64
- Intel Core i7-1165G7 at 2.80 GHz, 8 logical CPUs
- Approximately 7.6 GiB RAM
- One warm-up and three measured runs per query; median reported
- The same loaded `SearchEngine` reused for all repeated measurements
- Snapshot load excluded from query latency

Snapshot characteristics:

| Item | Value |
| --- | ---: |
| Snapshot ID | `259a885c586d2fab33fedeb51f45ba5728efb4268eae7b28fa09213979a17efa` |
| Records | 2,424,365 |
| Grams | 60,261 |
| Posting IDs | 195,765,640 |
| `records.binpb` | 295,339,652 bytes (~282 MiB) |
| `index.binpb` | 616,023,028 bytes (~588 MiB) |
| `manifest.binpb` | 268 bytes |
| Total snapshot | 911,362,948 bytes (~870 MiB) |
| Snapshot load | 88.537s |

Snapshot load is a startup measurement and is not included below.

| Query | Candidates | Median query latency |
| --- | ---: | ---: |
| `soma` | 465,197 | 2.119s |
| `to` | 2,043,515 | 9.126s |
| `to bv` | 482,683 | 2.689s |
| `to be` | 588,860 | 3.411s |
| `databse` | 121,577 | 0.848s |
| `database` | 104,080 | 0.766s |

### Optimization history

The following `to` measurements were collected during development. Historical
rows were not all rerun in one identical final benchmark process, so this is a
useful progression rather than a controlled scientific comparison.

| Implementation stage | Approx. latency |
| --- | ---: |
| Original integrated implementation | ~51.0s |
| Matcher optimization | ~13.8s |
| Sorted-posting union optimization | ~10.8s |
| Ranking optimization | ~9.8s |
| Final implementation | 9.126s |

The final implementation is approximately **5.59x faster** than the original
integrated implementation for this query on the measured development machine.

## Performance Notes

Snapshot construction is offline work, and snapshot loading is a startup cost.
Online query latency begins only after the snapshot is loaded.

Very short queries can be intentionally expensive under the frozen one-edit
matching semantics. For `to`, all 2,043,515 candidates are legal matches—not
false positives. The SearchEngine contract therefore requires candidate
retrieval, matcher verification and scoring, `AutoCompleteData` materialization
for every legal result, full deterministic ranking, and only then Top-k
truncation.

By comparison, `database` has 104,080 candidates and 10,594 legal matches, with
a final median latency of 0.766s.

Completed performance work includes optimized matcher verification, an
efficient sorted-posting union, stable deterministic ranking with lower
temporary allocation, and removal of avoidable per-candidate SearchEngine
wrapper overhead.

## Project Structure

```text
src/autocomplete/   Python search core, snapshot loader, API, and CLI
cpp/                C++17 snapshot builder and C++ tests
proto/              Frozen Protobuf snapshot schema
benchmarks/         Reproducible online search benchmark harness
tests/              Python unit, contract, integration, and equivalence tests
scripts/            Protobuf generation helper
data/               Local raw-corpus and snapshot locations
```

The frozen specification documents in the repository root remain the detailed
source of truth for behavior and contracts.
