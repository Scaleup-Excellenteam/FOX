# Google Autocomplete

This repository is the shared Phase 0 foundation for the Google Autocomplete
Part A project. Part A has an offline C++ preprocessing stage and an online
Python completion stage. The offline stage will eventually turn an extracted
text corpus into a versioned local Protobuf snapshot; the online stage will
eventually load that snapshot once and answer autocomplete queries. Phase 0
defines their shared models, schema, contracts, packaging, and build plumbing
only. It contains no corpus traversal, indexing, matching, ranking, search
engine, builder executable, or CLI implementation.

## Team boundaries

- Member 1 owns Python normalization, one-edit matching, and scoring.
- Member 2 owns the C++ offline builder, snapshot format implementation,
  Python snapshot loader, and candidate index.
- Member 3 owns ranking, runtime orchestration, the reference engine, public
  API, CLI, integration quality, and online benchmarks.

The five frozen specification files in the repository root are the source of
truth. Official Part A requirements take precedence over team documents.

## Frozen toolchain

The Phase 0 toolchain tested on Ubuntu 24.04 is:

| Component | Version |
| --- | --- |
| Python | 3.12.3 |
| Python environment | repository-local `.venv` |
| C++ standard | C++20 |
| GCC | 13.3.0 |
| CMake | 3.28.3 |
| `protoc` / C++ `libprotobuf` | 3.21.12 |
| Python `protobuf` runtime | 6.32.0 |
| pytest | 8.4.2 |
| Ruff | 0.16.5 |

Install the Ubuntu system dependencies:

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake pkg-config protobuf-compiler \
  libprotobuf-dev python3.12 python3.12-venv unzip
```

The C++ binding generator and `libprotobuf` must resolve to the same system
Protobuf installation. Verify them with:

```bash
protoc --version
pkg-config --modversion protobuf
```

## Python setup and checks

Each checkout or worktree must have its own virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the Python checks from the repository root:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

No `PYTHONPATH` override is needed.

## Protobuf generation

Generate the committed Python binding and the disposable C++ bindings from the
single frozen schema:

```bash
scripts/generate_proto.sh
```

The Python output is
`src/autocomplete/generated/autocomplete_snapshot_pb2.py`. C++ outputs stay
under `build/generated/proto/` and are ignored by Git. Generated files must not
be hand-edited.

## C++ configure, build, and test

```bash
cmake -S cpp -B build/cpp
cmake --build build/cpp
ctest --test-dir build/cpp --output-on-failure
```

The current executable is an infrastructure-only Protobuf smoke test. The
offline builder belongs to a later feature branch.

## Corpus preparation and future builder contract

Preserve the supplied archive and extract it before running the future offline
builder:

```bash
mkdir -p data/corpus
unzip data/raw/Archive.zip -d data/corpus
```

Member 2 will implement this frozen command contract after Phase 0:

```bash
./build/cpp/autocomplete_builder \
  --corpus <extracted-corpus-root> \
  --output <snapshot-directory>
```

Phase 0 uses local files only. GCS, cloud credentials, external services, and
all Part B features are explicitly outside its scope.
