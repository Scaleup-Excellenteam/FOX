# FOX Autocomplete Manual Terminal Test Pack

This pack converts the externally meaningful behavior in the repository test suite
into terminal tests against the real FOX builder, snapshot preparation flow,
snapshot loader, search engine, official API, interactive CLI, artifact store, and
benchmark command. It does not use `pytest` or replace production functions with
test doubles.

All test data is created under a fresh `/tmp` directory. Run the tests from WSL or
Linux. Each test is independent unless it explicitly says otherwise.

## Environment setup

Run this once from the repository root in the terminal that will run the tests:

```bash
set -euo pipefail

export FOX_REPO="$(pwd)"
export FOX_PYTHON="$FOX_REPO/.venv/bin/python"
export FOX_BUILDER="$FOX_REPO/build/cpp/autocomplete_builder"

test -x "$FOX_PYTHON"
test -x "$FOX_BUILDER"
"$FOX_PYTHON" --version
"$FOX_BUILDER" --normalize "Environment OK"
printf '\n'
```

Expected final output:

```text
environment ok
```

If either `test -x` fails, prepare the repository using the setup and C++ build
instructions in `README.md`, then repeat this section. The validated project uses
Python 3.12.

Shell blocks below may print builder diagnostics containing a temporary path and
an elapsed time. Those values are expected to vary and are not part of PASS/FAIL.

## MT-01 — Builder normalization contract

**What this verifies:** The production C++ builder lowercases ASCII letters,
deletes the frozen ASCII punctuation set, collapses only ASCII spaces, trims ASCII
spaces, preserves non-ASCII text, and returns normalized-empty output where
appropriate.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt01.XXXXXX)

printf '<%s>\n' "$("$FOX_BUILDER" --normalize 'Hello,       WORLD!!!')"
printf '<%s>\n' "$("$FOX_BUILDER" --normalize 'alpha,beta.gamma')"
printf '<%s>\n' "$("$FOX_BUILDER" --normalize "can't-stop")"
printf '<%s>\n' "$("$FOX_BUILDER" --normalize '  Leading and trailing  ')"
printf '<%s>\n' "$("$FOX_BUILDER" --normalize '!!!')"
printf '<%s>\n' "$("$FOX_BUILDER" --normalize 'Café 世界')"
"$FOX_PYTHON" - "$FOX_BUILDER" <<'PY'
import string
import subprocess
import sys
result = subprocess.run([sys.argv[1], "--normalize", string.punctuation],
                        text=True, capture_output=True, check=True)
print("full_ascii_punctuation", repr(result.stdout))
PY
```

**CLI input:** None; this uses the builder's public `--normalize` mode.

**Expected output:**

```text
<hello world>
<alphabetagamma>
<cantstop>
<leading and trailing>
<>
<café 世界>
full_ascii_punctuation ''
```

**PASS:** All seven lines match exactly, including the empty `<>` result, complete
punctuation deletion, and the preserved accented/CJK characters.

**FAIL:** Any punctuation remains, ASCII case/spacing differs, non-ASCII text is
changed, the command exits nonzero, or any line differs.

## MT-02 — Recursive deterministic directory build and snapshot load

**What this verifies:** Recursive case-insensitive `.txt` discovery, lexicographic
relative-path record order, UTF-8 BOM removal, CRLF handling, ignored non-text
files, normalized-empty record skipping, deterministic snapshot bytes, frozen
manifest values, big-endian framed files, Unicode n-grams, and successful loading.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt02.XXXXXX)
mkdir -p "$T/corpus/deep" "$T/output"

printf '\357\273\277Hello, WORLD!!!\r\n   \r\nUnicode \327\251\327\234\327\225\327\235\r\n' > "$T/corpus/deep/A.TXT"
printf '!!!\r\nLast line\r\n' > "$T/corpus/z.txt"
printf 'not indexed\n' > "$T/corpus/ignore.md"

"$FOX_BUILDER" --inspect-corpus "$T/corpus"
"$FOX_BUILDER" --corpus "$T/corpus" --output "$T/output/one"
"$FOX_BUILDER" --corpus "$T/corpus" --output "$T/output/two"

cmp "$T/output/one/manifest.binpb" "$T/output/two/manifest.binpb"
cmp "$T/output/one/records.binpb" "$T/output/two/records.binpb"
cmp "$T/output/one/index.binpb" "$T/output/two/index.binpb"
find "$T/output/one" -maxdepth 1 -type f -printf '%f\n' | sort

"$FOX_PYTHON" - "$T/output/one" <<'PY'
import struct
import sys
from pathlib import Path

from autocomplete.generated.autocomplete_snapshot_pb2 import SnapshotManifestProto
from autocomplete.snapshot_loader import load_snapshot

snapshot = Path(sys.argv[1])
manifest = SnapshotManifestProto()
manifest.ParseFromString((snapshot / "manifest.binpb").read_bytes())
records, index = load_snapshot(snapshot)

print("versions", manifest.schema_version, manifest.normalization_version,
      manifest.index_strategy_version)
print("grams", list(manifest.gram_sizes))
print("counts", manifest.searchable_record_count, manifest.posting_count)
print("record_files", list(manifest.record_files))
print("index_files", list(manifest.index_files))
print("records", [
    (r.sentence_id, r.original, r.normalized, r.source_path, r.line_number)
    for r in records.values()
])
print("unicode_postings", list(index.postings[(1, "ו")]),
      list(index.postings[(2, "של")]), list(index.postings[(3, "לום")]))

raw = (snapshot / "records.binpb").read_bytes()
first_length = struct.unpack(">I", raw[:4])[0]
print("first_frame", first_length, len(raw) > first_length + 4)
PY
```

**CLI input:** None.

**Expected output or behavior:**

- Inspection reports `files=2 lines=5 accepted=3 skipped=2`.
- Each build reports `sentences=3 files=2` and the same 64-character snapshot ID.
- All three `cmp` commands are silent and exit zero.
- The snapshot contains exactly `index.binpb`, `manifest.binpb`, and
  `records.binpb`.
- Loader output includes:

```text
versions 1 1 1
grams [1, 2, 3]
record_files ['records.binpb']
index_files ['index.binpb']
records [(1, 'Hello, WORLD!!!', 'hello world', 'deep/A.TXT', 1), (2, 'Unicode שלום', 'unicode שלום', 'deep/A.TXT', 3), (3, 'Last line', 'last line', 'z.txt', 2)]
unicode_postings [2] [2] [2]
```

`counts` must start with `3`; its posting count is deterministic but need not be
memorized. `first_frame` must show a positive length and `True`.

**PASS:** Both builds succeed, their three files are byte-identical, all listed
manifest/record/posting values match, and the loader raises no error.

**FAIL:** File discovery/order, accepted/skipped counts, normalized/original text,
source line offsets, manifest versions, Unicode postings, framing, determinism, or
loading differs.

## MT-03 — Builder usage and atomic failure behavior

**What this verifies:** Required builder syntax, missing corpus rejection,
nonexistent output-parent rejection, invalid UTF-8 rejection, destination
no-overwrite behavior, preservation of an existing destination, and cleanup of
partial staging directories.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt03.XXXXXX)
mkdir -p "$T/corpus" "$T/parent"
printf 'valid\n' > "$T/corpus/valid.txt"

set +e
"$FOX_BUILDER" >"$T/usage.out" 2>"$T/usage.err"
echo "usage_status=$?"

"$FOX_BUILDER" --corpus "$T/missing" --output "$T/parent/missing-out" \
  >"$T/missing.out" 2>"$T/missing.err"
echo "missing_status=$?"

"$FOX_BUILDER" --corpus "$T/corpus" --output "$T/no-parent/snapshot" \
  >"$T/parent.out" 2>"$T/parent.err"
echo "parent_status=$?"
set -e

mkdir "$T/existing"
printf 'preserve\n' > "$T/existing/keep.txt"
set +e
"$FOX_BUILDER" --corpus "$T/corpus" --output "$T/existing" \
  >"$T/existing.out" 2>"$T/existing.err"
echo "existing_status=$?"
set -e

mkdir "$T/bad-corpus"
printf 'bad\377\n' > "$T/bad-corpus/bad.txt"
set +e
"$FOX_BUILDER" --corpus "$T/bad-corpus" --output "$T/parent/bad-snapshot" \
  >"$T/utf8.out" 2>"$T/utf8.err"
echo "utf8_status=$?"
set -e

cat "$T/usage.err" "$T/missing.err" "$T/parent.err" \
  "$T/existing.err" "$T/utf8.err"
printf 'marker=%s\n' "$(cat "$T/existing/keep.txt")"
test ! -e "$T/parent/missing-out"
test ! -e "$T/parent/bad-snapshot"
test -z "$(find "$T/parent" -maxdepth 1 -name '.*.incomplete-*' -print -quit)"
```

**CLI input:** None.

**Expected output or behavior:** Statuses are `usage_status=2`, then four
`..._status=1` values. Error text contains, respectively, `usage:`, `corpus root is
not a directory`, `snapshot parent directory does not exist`, `snapshot destination
already exists`, and `invalid UTF-8`. The marker remains `preserve`; no requested
failed snapshot or `.incomplete-*` directory exists.

**PASS:** Every failure has the expected nonzero status/message and leaves no
partial publication or modification to the existing destination.

**FAIL:** A bad build returns zero, publishes a partial snapshot, deletes/changes
the marker, leaves staging debris, or reports the wrong failure.

## MT-04 — ZIP build, filtering, deterministic output, and real search

**What this verifies:** The production Python ZIP entry point safely extracts
supported entries, recursively keeps `.txt` files, skips directories and
unsupported files, invokes the real C++ builder, reports extraction statistics,
produces deterministic snapshots, and drives real search.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt04.XXXXXX)
mkdir -p "$T/out"

"$FOX_PYTHON" - "$T/corpus.zip" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1], "w", compression=zipfile.ZIP_DEFLATED) as z:
    z.writestr("a.txt", "To be or not to be\nbanana banana\nhi\nabc\nabcd\n")
    z.writestr("nested/b.txt", "To be again\nUnicode שלום\n")
    z.writestr("notes.md", "not corpus data\n")
    z.writestr("empty-dir/", b"")
PY

"$FOX_PYTHON" -m autocomplete.build_snapshot \
  "$FOX_BUILDER" "$T/corpus.zip" "$T/out/one" | tee "$T/first.log"
"$FOX_PYTHON" -m autocomplete.build_snapshot \
  "$FOX_BUILDER" "$T/corpus.zip" "$T/out/two" | tee "$T/second.log"

cmp "$T/out/one/manifest.binpb" "$T/out/two/manifest.binpb"
cmp "$T/out/one/records.binpb" "$T/out/two/records.binpb"
cmp "$T/out/one/index.binpb" "$T/out/two/index.binpb"

printf 'to be\n' | "$FOX_PYTHON" -m autocomplete.main --snapshot "$T/out/one"
```

**Text entered inside the CLI:** The pipe enters `to be`, then EOF.

**Expected output or behavior:** Each build log contains:

```text
[PYTHON BUILDER] [ZIP EXTRACTION] -> Completed archive extraction
zip_entries=4 processed_files=2 zip_records=7 skipped_directories=1 skipped_unsupported_files=1
snapshot_id=<64 lowercase hexadecimal characters> sentences=7 files=2
```

The three comparisons are silent. Search returns these two results in this order:

```text
To be again | score: 10 | source: nested/b.txt | offset: 1
To be or not to be | score: 10 | source: a.txt | offset: 1
```

Prompts may appear on the same lines because stdin is piped.

**PASS:** Both ZIP builds succeed with the stated counts, their snapshots are
byte-identical, ignored entries are not indexed, and search produces both results
with the displayed metadata.

**FAIL:** Extraction/build fails, statistics differ, snapshots differ, ignored
content is searchable, or results/metadata/order differ.

## MT-05 — ZIP safety, supported compression, and configurable limits

**What this verifies:** Rejection and cleanup for malformed archives, traversal,
duplicate paths, symbolic-link entries, unsupported compression, compression bombs,
and entry/individual/total size ceilings.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt05.XXXXXX)
mkdir -p "$T/out"

"$FOX_PYTHON" - "$T" <<'PY'
import stat
import sys
import warnings
import zipfile
from pathlib import Path

root = Path(sys.argv[1])
(root / "bad.zip").write_bytes(b"not a ZIP")
with zipfile.ZipFile(root / "unsafe.zip", "w") as z:
    z.writestr("../escape.txt", "must not escape")
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    with zipfile.ZipFile(root / "duplicate.zip", "w") as z:
        z.writestr("same.txt", "one")
        z.writestr("same.txt", "two")
with zipfile.ZipFile(root / "symlink.zip", "w") as z:
    info = zipfile.ZipInfo("link.txt")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    z.writestr(info, "outside")
with zipfile.ZipFile(root / "bzip.zip", "w", compression=zipfile.ZIP_BZIP2) as z:
    z.writestr("a.txt", "text")
with zipfile.ZipFile(root / "ratio.zip", "w", compression=zipfile.ZIP_DEFLATED) as z:
    z.writestr("zeros.txt", b"0" * 100_000)
PY

for case in bad unsafe duplicate symlink bzip ratio; do
  set +e
  "$FOX_PYTHON" -m autocomplete.build_snapshot \
    "$FOX_BUILDER" "$T/$case.zip" "$T/out/$case" \
    >"$T/$case.out" 2>"$T/$case.err"
  status=$?
  set -e
  echo "case=$case status=$status"
  cat "$T/$case.err"
  test ! -e "$T/out/$case"
done

test ! -e "$T/escape.txt"

"$FOX_PYTHON" - "$T" <<'PY'
import sys
import zipfile
from pathlib import Path

from autocomplete.build_snapshot import ZipExtractionLimits, ZipInputError, extract_zip_corpus

root = Path(sys.argv[1])
cases = [
    ("entries", [("one.txt", b"1"), ("two.txt", b"2")],
     ZipExtractionLimits(1, 100, 100, 100.0)),
    ("entry-size", [("large.txt", b"12345678901")],
     ZipExtractionLimits(10, 100, 10, 100.0)),
    ("total-size", [("one.txt", b"123456"), ("two.txt", b"abcdef")],
     ZipExtractionLimits(10, 10, 100, 100.0)),
]
for name, entries, limits in cases:
    archive = root / f"limit-{name}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as z:
        for path, content in entries:
            z.writestr(path, content)
    destination = root / f"extract-{name}"
    try:
        extract_zip_corpus(archive, destination, limits=limits)
    except ZipInputError as error:
        print(name, type(error).__name__, error, "cleaned=", not destination.exists())
    else:
        print(name, "UNEXPECTED SUCCESS")
PY
```

**CLI input:** None.

**Expected output or behavior:** Every build case has `status=1` and no output
directory. Error text identifies `cannot open ZIP`, `unsafe ZIP entry path`,
`duplicate file paths`, `symbolic-link ZIP entry`, `unsupported ZIP compression
method 12`, and `ZIP compression ratio limit exceeded`, respectively. No
`escape.txt` is created. The final three lines contain `entry count limit`,
`per-entry uncompressed size limit`, and `total uncompressed size limit`; each ends
with `cleaned= True`.

**PASS:** Every unsafe/over-limit input is rejected before publication, all partial
extractions are removed, and traversal creates nothing outside its destination.

**FAIL:** Any case succeeds, publishes output, leaves partial extraction, escapes
the destination, or returns an unrelated error.

## MT-06 — Builder timeout and temporary ZIP cleanup

**What this verifies:** The real Python build orchestration kills a builder that
exceeds its configured timeout and removes the extracted temporary corpus. The
timeout is a public Python API option but is not exposed by the command-line parser.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt06.XXXXXX)
mkdir -p "$T/tmp"

"$FOX_PYTHON" - "$T/corpus.zip" <<'PY'
import sys
import zipfile
with zipfile.ZipFile(sys.argv[1], "w", compression=zipfile.ZIP_STORED) as z:
    z.writestr("corpus.txt", "one sentence\n")
PY

printf '%s\n' '#!/usr/bin/env bash' 'sleep 30' > "$T/slow-builder"
chmod +x "$T/slow-builder"

TMPDIR="$T/tmp" "$FOX_PYTHON" - "$T" <<'PY'
import sys
import time
from pathlib import Path
from autocomplete.build_snapshot import BuildError, build_snapshot_from_input

root = Path(sys.argv[1])
started = time.monotonic()
try:
    build_snapshot_from_input(
        root / "slow-builder", root / "corpus.zip", root / "snapshot",
        builder_timeout_seconds=0.05,
    )
except BuildError as error:
    print(type(error).__name__, error)
    print("elapsed_under_5_seconds", time.monotonic() - started < 5)
    print("snapshot_absent", not (root / "snapshot").exists())
    print("temporary_directory_empty", not any((root / "tmp").iterdir()))
else:
    print("UNEXPECTED SUCCESS")
PY
```

**CLI input:** None.

**Expected output:**

```text
BuildError C++ snapshot builder timed out after 0.05 seconds and was killed
elapsed_under_5_seconds True
snapshot_absent True
temporary_directory_empty True
```

**PASS:** The slow process is killed promptly and neither a snapshot nor extracted
temporary directory remains.

**FAIL:** It runs for about 30 seconds, succeeds, reports a different exception, or
leaves temporary/output data.

## MT-07 — CLI startup, normalization, result fields, no results, and empty input

**What this verifies:** A valid snapshot loads before interaction; CLI search uses
the real normalization/search path; original text, score, source, and source-line
offset are displayed; normalized-empty and unrelated input return the clear
no-results message.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt07.XXXXXX)
mkdir -p "$T/corpus/nested"
printf 'Hello world\nHello there\n!!!\n' > "$T/corpus/nested/sentences.txt"
"$FOX_BUILDER" --corpus "$T/corpus" --output "$T/snapshot"

# Input lines: HELLO, WORLD!!! ; # ; zzzzzz ; # ; !!! ; EOF
printf '  HELLO, WORLD!!!  \n#\nzzzzzz\n#\n!!!\n' | \
  "$FOX_PYTHON" -m autocomplete.main --snapshot "$T/snapshot"
```

**Text entered inside the CLI:**

```text
␠␠HELLO, WORLD!!!␠␠
#
zzzzzz
#
!!!
```

Here each `␠` means one typed ASCII space; do not type the marker itself. The first
line deliberately has two leading and two trailing spaces. EOF follows the last
line.

**Expected output or behavior:** The first search displays exactly:

```text
Hello world | score: 22 | source: nested/sentences.txt | offset: 1
```

The searches for `zzzzzz` and `!!!` each display `No completions found.`. Entering
`#` only resets state; it produces no result line. The CLI exits successfully at
EOF. The corpus line containing only `!!!` is not itself indexed.

**PASS:** Output, score, source, offset, no-results messages, reset behavior, and
clean EOF exit all match.

**FAIL:** Startup fails, normalized search misses/changes the original result, any
field differs, reset causes a search, empty-normalized input matches data, or EOF
produces an error.

## MT-08 — Accumulated fragments, literal spaces, empty fragments, and exact reset

**What this verifies:** Fragments append exactly; the CLI inserts no space;
explicit leading spaces are preserved; an empty fragment searches the current
accumulated value; only a fragment equal to `#` resets; reset does not terminate the
session.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt08.XXXXXX)
mkdir -p "$T/corpus"
printf 'foo bar\nfoobar\nfoosuffix\nbar\n' > "$T/corpus/data.txt"
"$FOX_BUILDER" --corpus "$T/corpus" --output "$T/snapshot" >/dev/null

# foo + bar => foobar (no inserted space); blank repeats foobar.
# # resets; foo + leading-space-bar => foo bar.
# # resets; foo + #suffix is not a reset => foo#suffix => foosuffix.
# # resets; bar proves the session continues with fresh state.
printf 'foo\nbar\n\n#\nfoo\n bar\n#\nfoo\n#suffix\n#\nbar\n' | \
  "$FOX_PYTHON" -m autocomplete.main --snapshot "$T/snapshot"
```

**Text entered inside the CLI:**

```text
foo
bar

#
foo
 bar
#
foo
#suffix
#
bar
```

The third input is an empty line; ` bar` begins with one space.

**Expected output or behavior:**

- After `foo` then `bar`, `foobar | score: 12` is first; `foo bar` may also be a
  lower-scored legal one-edit result. No space was inserted into the accumulated
  query.
- The empty line immediately repeats the same result order/scores.
- After reset, `foo` then ` bar` puts `foo bar | score: 14` first.
- `#suffix` is appended, not treated as reset, and puts
  `foosuffix | score: 18` first because punctuation is normalized away by search.
- Exact `#` lines emit no completion or `No completions found.` line.
- The final `bar` runs after reset and includes `bar | score: 6`.

**PASS:** All five state behaviors above are observed in one continuing CLI
session.

**FAIL:** A space is inserted automatically, a typed space is lost, blank input
clears state, `#suffix` resets, exact `#` searches/terminates, or post-reset input
uses old state.

## MT-09 — Exact and one-edit matcher scoring regressions

**What this verifies:** Real end-to-end exact, substitution, extra-character,
missing-character, best-alignment, punctuation normalization, and rejection of a
nonmatching/two-edit query using the frozen scoring rules.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt09.XXXXXX)
mkdir -p "$T/corpus"
printf 'To be or not to be, that is the question.\n' > "$T/corpus/q.txt"
printf 'b\nabc\n' > "$T/corpus/scores.txt"
"$FOX_BUILDER" --corpus "$T/corpus" --output "$T/snapshot" >/dev/null

printf 'To be\n#\nor Not\n#\nbe, that\n#\n2o be\n#\nto pe\n#\nor knot\n#\nor nt\n#\nnot be\n#\naxc\n#\na\n' | \
  "$FOX_PYTHON" -m autocomplete.main --snapshot "$T/snapshot"
```

**Text entered inside the CLI:** `To be`, `or Not`, `be, that`, `2o be`, `to pe`,
`or knot`, `or nt`, `not be`, `axc`, and `a`, with an exact `#` reset between
queries.

**Expected output or behavior:** The sentence is returned with scores, in query
order: `10`, `12`, `14`, `3`, `6`, `8`, and `8`. The `not be` query prints
`No completions found.`. Query `axc` returns `abc` with score `0`. The final
one-character query `a` demonstrates the broad candidate fallback: it includes the
Shakespeare sentence and `abc` with exact score `2`, plus `b` with legal negative
score `-5`.

**PASS:** All seven regression scores, the rejection, the zero score, and the
preserved/ranked negative score match exactly.

**FAIL:** Any legal match is absent, any score differs, or `not be` is accepted.

## MT-10 — Ranking, duplicates, tie-breakers, and top-five limit

**What this verifies:** Default top-five truncation occurs after full deterministic
ranking; equal scores sort by original completed sentence, then source path, then
line offset; duplicate completed sentences from distinct records are preserved.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt10.XXXXXX)
mkdir -p "$T/corpus"
printf 'alpha match\nduplicate match\nduplicate match\n' > "$T/corpus/a.txt"
printf 'duplicate match\n' > "$T/corpus/b.txt"
printf 'Zebra match\nzeta match\n' > "$T/corpus/c.txt"
"$FOX_BUILDER" --corpus "$T/corpus" --output "$T/snapshot" >/dev/null

printf 'match\n' | "$FOX_PYTHON" -m autocomplete.main --snapshot "$T/snapshot"
```

**Text entered inside the CLI:** `match`, then EOF.

**Expected result lines, in exact order:**

```text
Zebra match | score: 10 | source: c.txt | offset: 1
alpha match | score: 10 | source: a.txt | offset: 1
duplicate match | score: 10 | source: a.txt | offset: 2
duplicate match | score: 10 | source: a.txt | offset: 3
duplicate match | score: 10 | source: b.txt | offset: 1
```

`Zebra match` precedes lowercase `alpha match` because completed-sentence ordering
uses the original string. `zeta match` must not appear because only the best five
are returned.

**PASS:** Exactly five results appear in that order, including all three duplicate
records with their own source/offset.

**FAIL:** More/fewer results appear, duplicates are collapsed, excluded rows
appear, or any tie-breaker/order/metadata differs.

## MT-11 — Public API initialization and SearchEngine `k` validation

**What this verifies:** The official facade fails clearly before configuration,
uses the configured real engine with `k=5`, and SearchEngine handles custom, zero,
negative, and non-integer `k` values as implemented.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt11.XXXXXX)
mkdir -p "$T/corpus"
printf 'alpha match\nbeta match\ngamma match\ndelta match\nepsilon match\nzeta match\n' > "$T/corpus/data.txt"
"$FOX_BUILDER" --corpus "$T/corpus" --output "$T/snapshot" >/dev/null

"$FOX_PYTHON" - "$T/snapshot" <<'PY'
import sys
from pathlib import Path

from autocomplete.api import (
    EngineNotInitializedError,
    configure_default_engine,
    get_best_k_completions,
)
from autocomplete.search_engine import SearchEngine
from autocomplete.snapshot_loader import load_snapshot

try:
    get_best_k_completions("match")
except Exception as error:
    print("unconfigured", type(error).__name__, isinstance(error, RuntimeError), error)

records, index = load_snapshot(Path(sys.argv[1]))
engine = SearchEngine(records, index)
configure_default_engine(engine)
print("official_count", len(get_best_k_completions("match")))
print("custom_two", [r.completed_sentence for r in engine.search("match", k=2)])
print("zero", engine.search("match", k=0))
for invalid in (-1, 1.5, "5", None, True):
    try:
        engine.search("match", k=invalid)
    except Exception as error:
        print(repr(invalid), type(error).__name__, error)
PY
```

**CLI input:** None; this exercises the real public Python API from the terminal.

**Expected output or behavior:**

- `unconfigured EngineNotInitializedError True` followed by
  `The default SearchEngine has not been configured.`
- `official_count 5`
- `custom_two ['alpha match', 'beta match']`
- `zero []`
- `-1` produces `ValueError k must be non-negative`.
- `1.5`, `'5'`, `None`, and `True` each produce
  `TypeError k must be an integer`.

**PASS:** Every value and exception type/message matches.

**FAIL:** The facade works while unconfigured, returns other than five, custom `k`
is ignored, `k=0` searches/returns data, or invalid values are accepted/wrongly
classified.

## MT-12 — Empty corpus builds, loads, searches, and fails benchmark generation

**What this verifies:** A completely empty corpus still creates a valid three-file
snapshot with zero records/postings; CLI search returns no results; the benchmark
correctly refuses query generation from an empty normalized corpus.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt12.XXXXXX)
mkdir -p "$T/corpus"
"$FOX_BUILDER" --corpus "$T/corpus" --output "$T/snapshot"

"$FOX_PYTHON" - "$T/snapshot" <<'PY'
import sys
from pathlib import Path
from autocomplete.generated.autocomplete_snapshot_pb2 import SnapshotManifestProto
from autocomplete.snapshot_loader import load_snapshot

root = Path(sys.argv[1])
manifest = SnapshotManifestProto()
manifest.ParseFromString((root / "manifest.binpb").read_bytes())
records, index = load_snapshot(root)
print("files", sorted(p.name for p in root.iterdir()))
print("counts", manifest.searchable_record_count, manifest.posting_count)
print("records", records)
print("postings", dict(index.postings))
PY

printf 'anything\n' | "$FOX_PYTHON" -m autocomplete.main --snapshot "$T/snapshot"

set +e
"$FOX_PYTHON" -m benchmarks.benchmark_search --snapshot "$T/snapshot" \
  --query-count 1 --repeats 1 --warmup-rounds 0 \
  >"$T/benchmark.out" 2>"$T/benchmark.err"
echo "benchmark_status=$?"
set -e
cat "$T/benchmark.err"
```

**Text entered inside the CLI:** `anything`, then EOF.

**Expected output or behavior:** Builder reports `sentences=0 files=0`. The loader
prints the three filenames, `counts 0 0`, `records {}`, and `postings {}`. CLI prints
`No completions found.`. Benchmark exits nonzero with
`cannot generate queries from an empty normalized corpus`.

**PASS:** The empty snapshot is valid and searchable while benchmark query
generation rejects it clearly.

**FAIL:** Build/load fails, nonzero records/postings exist, CLI reports a match, or
the benchmark silently succeeds.

## MT-13 — Snapshot loader validation matrix through application startup

**What this verifies:** Production startup rejects missing shards, corrupt
manifest/framing, unsupported versions/gram sizes, count mismatches, unsafe or
duplicate manifest filenames, bad digests/identity, invalid record IDs, unknown or
unsorted posting IDs, and invalid gram metadata. It also verifies startup never
enters the interactive prompt after a load failure.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt13.XXXXXX)
mkdir -p "$T/corpus"
printf 'to be or not to be\nabcdefghi\n' > "$T/corpus/a.txt"
"$FOX_BUILDER" --corpus "$T/corpus" --output "$T/base" >/dev/null

"$FOX_PYTHON" - "$T" <<'PY'
import shutil
import struct
import sys
from pathlib import Path

from autocomplete.generated.autocomplete_snapshot_pb2 import (
    GramPostingProto,
    SentenceRecordProto,
    SnapshotManifestProto,
)

root = Path(sys.argv[1])
base = root / "base"

def clone(name):
    path = root / name
    shutil.copytree(base, path)
    return path

def manifest(path):
    value = SnapshotManifestProto()
    value.ParseFromString((path / "manifest.binpb").read_bytes())
    return value

def write_manifest(path, value):
    (path / "manifest.binpb").write_bytes(value.SerializeToString(deterministic=True))

def messages(path, message_type):
    data = path.read_bytes()
    result = []
    offset = 0
    while offset < len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        value = message_type()
        value.ParseFromString(data[offset:offset + length])
        result.append(value)
        offset += length
    return result

def write_messages(path, values):
    data = bytearray()
    for value in values:
        payload = value.SerializeToString(deterministic=True)
        data.extend(struct.pack(">I", len(payload)))
        data.extend(payload)
    path.write_bytes(data)

p = clone("missing-records")
(p / "records.binpb").unlink()
p = clone("truncated-index")
(p / "index.binpb").write_bytes((p / "index.binpb").read_bytes()[:-1])
p = clone("short-frame-prefix")
(p / "records.binpb").write_bytes(b"\x00")
p = clone("zero-frame-length")
(p / "records.binpb").write_bytes(struct.pack(">I", 0))
p = clone("large-frame-length")
(p / "records.binpb").write_bytes(struct.pack(">I", 8 * 1024 * 1024 + 1))
p = clone("corrupt-manifest")
(p / "manifest.binpb").write_bytes(b"\xff")
for name, field in [("bad-schema-version", "schema_version"),
                    ("bad-normalization-version", "normalization_version"),
                    ("bad-index-version", "index_strategy_version")]:
    p = clone(name)
    m = manifest(p); setattr(m, field, 99); write_manifest(p, m)
p = clone("bad-grams")
m = manifest(p); m.gram_sizes[:] = [1, 3]; write_manifest(p, m)
p = clone("bad-count")
m = manifest(p); m.posting_count += 1; write_manifest(p, m)
p = clone("unsafe-name")
m = manifest(p); m.record_files[0] = "../outside.binpb"; write_manifest(p, m)
p = clone("duplicate-name")
m = manifest(p); m.index_files[0] = m.record_files[0]; write_manifest(p, m)
p = clone("bad-corpus-digest")
m = manifest(p); m.corpus_digest_sha256 = "0" * 64; write_manifest(p, m)
p = clone("bad-index-digest")
m = manifest(p); m.index_digest_sha256 = "0" * 64; write_manifest(p, m)
p = clone("bad-identity")
m = manifest(p); m.snapshot_id = "0" * 64; write_manifest(p, m)
p = clone("duplicate-record")
f = p / "records.binpb"; v = messages(f, SentenceRecordProto); v[1].sentence_id = v[0].sentence_id; write_messages(f, v)
p = clone("zero-record")
f = p / "records.binpb"; v = messages(f, SentenceRecordProto); v[0].sentence_id = 0; write_messages(f, v)
p = clone("large-record")
f = p / "records.binpb"; v = messages(f, SentenceRecordProto); v[0].sentence_id = 0x1_0000_0000; write_messages(f, v)
p = clone("unknown-posting")
f = p / "index.binpb"; v = messages(f, GramPostingProto); v[0].sentence_ids.append(999); write_messages(f, v)
p = clone("unsorted-posting")
f = p / "index.binpb"; v = messages(f, GramPostingProto); v[0].sentence_ids[:] = [2, 1]; write_messages(f, v)
p = clone("invalid-gram")
f = p / "index.binpb"; v = messages(f, GramPostingProto); v[0].gram_size = 9; write_messages(f, v)
PY

for case in \
  missing-records truncated-index short-frame-prefix zero-frame-length \
  large-frame-length corrupt-manifest bad-schema-version \
  bad-normalization-version bad-index-version bad-grams bad-count unsafe-name \
  duplicate-name bad-corpus-digest bad-index-digest bad-identity \
  duplicate-record zero-record large-record unknown-posting unsorted-posting \
  invalid-gram; do
  set +e
  output=$("$FOX_PYTHON" -m autocomplete.main --snapshot "$T/$case" </dev/null 2>&1)
  status=$?
  set -e
  echo "CASE=$case STATUS=$status"
  echo "$output"
  if printf '%s' "$output" | grep -q 'Enter query:'; then
    echo "UNEXPECTED CLI START"
  fi
done
```

**CLI input:** None; stdin is EOF. Startup must fail before an input prompt.

**Expected error fragment by case:**

| Case | Required error fragment |
| --- | --- |
| `missing-records` | `cannot read snapshot file records.binpb` |
| `truncated-index` | `truncated frame payload` |
| `short-frame-prefix` | `truncated frame length` |
| `zero-frame-length`, `large-frame-length` | `invalid frame length` |
| `corrupt-manifest` | `corrupt manifest protobuf` |
| `bad-schema-version`, `bad-normalization-version`, `bad-index-version` | `unsupported snapshot versions` |
| `bad-grams` | `unsupported gram sizes` |
| `bad-count` | `posting count mismatch` |
| `unsafe-name` | `unsafe snapshot file name` |
| `duplicate-name` | `duplicate snapshot file name` |
| `bad-corpus-digest` | `corpus digest mismatch` |
| `bad-index-digest` | `index digest mismatch` |
| `bad-identity` | `snapshot ID mismatch` |
| `duplicate-record` | `duplicate record identifier` |
| `zero-record`, `large-record` | `record identifier is outside uint32 range` |
| `unknown-posting` | `posting references unknown sentence ID` |
| `unsorted-posting` | `posting IDs are not strictly increasing` |
| `invalid-gram` | `invalid posting list` |

Every case must show `STATUS=1`, begin its application error with
`Snapshot loading failed for`, and omit `UNEXPECTED CLI START`.

**PASS:** All 22 variants produce their mapped loader error and no prompt.

**FAIL:** Any corrupted snapshot loads, reaches the CLI, returns status zero, or
produces an error inconsistent with the mapped validation.

## MT-14 — Required snapshot argument and pointer validation

**What this verifies:** The application requires `--snapshot`; reports a missing
snapshot clearly; rejects malformed current-pointer contents; rejects a validly
formatted pointer that references a missing directory; and resolves a valid
preparation pointer.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt14.XXXXXX)
mkdir -p "$T/bad-pointer-root" "$T/missing-pointer-root" "$T/corpus"
printf 'not-an-id\n' > "$T/bad-pointer-root/current"
printf '%064d\n' 0 > "$T/missing-pointer-root/current"
printf 'pointer search\n' > "$T/corpus/data.txt"

set +e
"$FOX_PYTHON" -m autocomplete.main >"$T/noarg.out" 2>"$T/noarg.err"
echo "noarg_status=$?"
"$FOX_PYTHON" -m autocomplete.main --snapshot "$T/does-not-exist" \
  >"$T/missing.out" 2>"$T/missing.err"
echo "missing_status=$?"
"$FOX_PYTHON" -m autocomplete.main --snapshot "$T/bad-pointer-root/current" \
  >"$T/badpointer.out" 2>"$T/badpointer.err"
echo "badpointer_status=$?"
"$FOX_PYTHON" -m autocomplete.main --snapshot "$T/missing-pointer-root/current" \
  >"$T/missingpointer.out" 2>"$T/missingpointer.err"
echo "missingpointer_status=$?"
set -e
cat "$T/noarg.err" "$T/missing.err" "$T/badpointer.err" "$T/missingpointer.err"

"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$FOX_BUILDER" --corpus "$T/corpus" --snapshot-root "$T/snapshots"
printf 'pointer\n' | "$FOX_PYTHON" -m autocomplete.main \
  --snapshot "$T/snapshots/current"
```

**Text entered inside the valid-pointer CLI:** `pointer`, then EOF.

**Expected output or behavior:** `noarg_status=2`; the other three invalid starts
have status `1`. Error text contains `the following arguments are required:
--snapshot`, `cannot read manifest`, `invalid snapshot ID in pointer`, and
`references missing snapshot`, respectively. The valid preparation reports
`status=BUILT_AND_ACTIVATED`, and search through the `current` file returns:

```text
pointer search | score: 14 | source: data.txt | offset: 1
```

**PASS:** All invalid cases fail before a prompt and the valid relative pointer
resolves to a working snapshot.

**FAIL:** An invalid reference starts the CLI, status/messages differ materially,
or the prepared pointer cannot be searched.

## MT-15 — Preparation first build, reuse, rebuild, path sensitivity, and retention

**What this verifies:** First preparation builds/activates; unchanged corpus reuses
without another immutable directory; changed content builds a new ID and retains
the previous snapshot; source path is part of identity, so renaming a file with
unchanged contents rebuilds.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt15.XXXXXX)
mkdir -p "$T/corpus"
printf 'Hello world\n' > "$T/corpus/a.txt"

prepare() {
  "$FOX_PYTHON" -m autocomplete.prepare_snapshot \
    --builder "$FOX_BUILDER" --corpus "$T/corpus" --snapshot-root "$T/snapshots"
}

prepare | tee "$T/first.log"
FIRST=$(tr -d '\n' < "$T/snapshots/current")
prepare | tee "$T/reuse.log"
SECOND=$(tr -d '\n' < "$T/snapshots/current")
echo "unchanged_same_id=$([ "$FIRST" = "$SECOND" ] && echo yes || echo no)"
echo "directories_after_reuse=$(find "$T/snapshots" -mindepth 1 -maxdepth 1 -type d | wc -l)"

printf 'Changed sentence\n' > "$T/corpus/a.txt"
prepare | tee "$T/changed.log"
THIRD=$(tr -d '\n' < "$T/snapshots/current")
echo "content_changed_id=$([ "$THIRD" != "$SECOND" ] && echo yes || echo no)"
echo "directories_after_change=$(find "$T/snapshots" -mindepth 1 -maxdepth 1 -type d | wc -l)"

mv "$T/corpus/a.txt" "$T/corpus/renamed.txt"
prepare | tee "$T/renamed.log"
FOURTH=$(tr -d '\n' < "$T/snapshots/current")
echo "rename_changed_id=$([ "$FOURTH" != "$THIRD" ] && echo yes || echo no)"
echo "directories_after_rename=$(find "$T/snapshots" -mindepth 1 -maxdepth 1 -type d | wc -l)"
```

**CLI input:** None.

**Expected output or behavior:** Logs report, in order,
`BUILT_AND_ACTIVATED`, `REUSED`, `BUILT_AND_ACTIVATED`, and
`BUILT_AND_ACTIVATED`. Printed checks are:

```text
unchanged_same_id=yes
directories_after_reuse=1
content_changed_id=yes
directories_after_change=2
rename_changed_id=yes
directories_after_rename=3
```

Each `current` file contains the ID reported by that invocation.

**PASS:** Statuses, identity changes, current pointer, and retained-directory counts
all match.

**FAIL:** Unchanged input rebuilds, changed contents/path reuse, an old immutable
snapshot is removed, or `current` points to the wrong ID.

## MT-16 — Semantic ZIP reuse and previously published snapshot fallback

**What this verifies:** Repacking the same ZIP semantics in a different entry order
and compression method reuses the active snapshot. Returning corpus content from B
to previously published A reactivates A without creating a duplicate immutable
directory.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt16.XXXXXX)

"$FOX_PYTHON" - "$T/corpus.zip" deflated <<'PY'
import sys, zipfile
mode = zipfile.ZIP_DEFLATED if sys.argv[2] == "deflated" else zipfile.ZIP_STORED
entries = [("a.txt", "Alpha\n"), ("nested/b.txt", "Beta\n")]
with zipfile.ZipFile(sys.argv[1], "w", compression=mode) as z:
    for name, contents in entries:
        z.writestr(name, contents)
PY
"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$FOX_BUILDER" --corpus "$T/corpus.zip" --snapshot-root "$T/zip-snaps" \
  | tee "$T/zip-first.log"
ZIP_FIRST=$(tr -d '\n' < "$T/zip-snaps/current")

"$FOX_PYTHON" - "$T/corpus.zip" stored <<'PY'
import sys, zipfile
entries = [("nested/b.txt", "Beta\n"), ("a.txt", "Alpha\n")]
with zipfile.ZipFile(sys.argv[1], "w", compression=zipfile.ZIP_STORED) as z:
    for name, contents in entries:
        z.writestr(name, contents)
PY
"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$FOX_BUILDER" --corpus "$T/corpus.zip" --snapshot-root "$T/zip-snaps" \
  | tee "$T/zip-second.log"
ZIP_SECOND=$(tr -d '\n' < "$T/zip-snaps/current")
echo "repacked_same_id=$([ "$ZIP_FIRST" = "$ZIP_SECOND" ] && echo yes || echo no)"

mkdir -p "$T/directory-corpus"
printf 'Corpus A\n' > "$T/directory-corpus/sentences.txt"
"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$FOX_BUILDER" --corpus "$T/directory-corpus" --snapshot-root "$T/fallback-snaps"
A=$(tr -d '\n' < "$T/fallback-snaps/current")
printf 'Corpus B\n' > "$T/directory-corpus/sentences.txt"
"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$FOX_BUILDER" --corpus "$T/directory-corpus" --snapshot-root "$T/fallback-snaps"
B=$(tr -d '\n' < "$T/fallback-snaps/current")
printf 'Corpus A\n' > "$T/directory-corpus/sentences.txt"
"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$FOX_BUILDER" --corpus "$T/directory-corpus" --snapshot-root "$T/fallback-snaps"
RETURNED=$(tr -d '\n' < "$T/fallback-snaps/current")

echo "a_and_b_differ=$([ "$A" != "$B" ] && echo yes || echo no)"
echo "returned_to_a=$([ "$RETURNED" = "$A" ] && echo yes || echo no)"
echo "fallback_directories=$(find "$T/fallback-snaps" -mindepth 1 -maxdepth 1 -type d | wc -l)"
```

**CLI input:** None.

**Expected output or behavior:** ZIP preparation first reports
`BUILT_AND_ACTIVATED`, then `REUSED`; `repacked_same_id=yes`. Directory preparation
creates A then B, and the third call reports A's original ID. Final checks are:

```text
a_and_b_differ=yes
returned_to_a=yes
fallback_directories=2
```

The return-to-A call may report `BUILT_AND_ACTIVATED`: preparation validates a
fresh candidate but publishes/activates the already existing immutable A directory.

**PASS:** Semantic ZIP repacking reuses, A/B IDs differ, returning to A reactivates
the old ID, and only two immutable A/B directories exist.

**FAIL:** Archive bytes/order alone change identity, A and B share an ID, fallback
creates a third directory, or `current` is not returned to A.

## MT-17 — Corrupt/incompatible current snapshot is rebuilt safely

**What this verifies:** Preparation fully validates the active snapshot before
reuse. A truncated active index and an unsupported manifest version each force a
rebuild; a corrupt directory with the deterministic target identity is safely
replaced; no quarantine/workspace debris remains.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt17.XXXXXX)
mkdir -p "$T/corpus"
printf 'Hello world\n' > "$T/corpus/sentences.txt"

prepare() {
  "$FOX_PYTHON" -m autocomplete.prepare_snapshot \
    --builder "$FOX_BUILDER" --corpus "$T/corpus" --snapshot-root "$T/snapshots"
}

prepare
ID=$(tr -d '\n' < "$T/snapshots/current")
truncate -s -1 "$T/snapshots/$ID/index.binpb"
prepare | tee "$T/rebuilt-corrupt.log"
AFTER_CORRUPT=$(tr -d '\n' < "$T/snapshots/current")
echo "corrupt_rebuilt_same_id=$([ "$AFTER_CORRUPT" = "$ID" ] && echo yes || echo no)"

"$FOX_PYTHON" - "$T/snapshots/$ID/manifest.binpb" <<'PY'
import sys
from pathlib import Path
from autocomplete.generated.autocomplete_snapshot_pb2 import SnapshotManifestProto
path = Path(sys.argv[1])
value = SnapshotManifestProto()
value.ParseFromString(path.read_bytes())
value.normalization_version = 99
path.write_bytes(value.SerializeToString(deterministic=True))
PY
prepare | tee "$T/rebuilt-version.log"

"$FOX_PYTHON" - "$T/snapshots/$ID" <<'PY'
import sys
from pathlib import Path
from autocomplete.snapshot_loader import load_snapshot_manifest, load_snapshot
path = Path(sys.argv[1])
load_snapshot(path)
print("normalization_version", load_snapshot_manifest(path).normalization_version)
PY

echo "debris=$(find "$T/snapshots" -mindepth 1 -maxdepth 1 \
  \( -name '.prepare-*' -o -name '.corrupt-*' -o -name '.current-*' \) -print | wc -l)"
```

**CLI input:** None.

**Expected output or behavior:** Both repair calls report
`status=BUILT_AND_ACTIVATED`; the deterministic ID remains the same; loader succeeds
afterward and prints `normalization_version 1`; `debris=0`.

**PASS:** Neither damaged snapshot is reused, both are repaired and activated under
the correct ID, and all temporary/quarantine paths are cleaned.

**FAIL:** Preparation reports `REUSED`, loading still fails, version remains 99,
the pointer changes incorrectly, or debris remains.

## MT-18 — Preparation failures preserve the previous active snapshot

**What this verifies:** Failed ZIP extraction, failed corpus inspection, failed
builder execution, and invalid candidate validation do not replace the previous
pointer. Preparation workspaces are cleaned. A legacy `current` directory is
reported and preserved instead of overwritten.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt18.XXXXXX)
mkdir -p "$T/corpus"
printf 'Corpus A\n' > "$T/corpus/sentences.txt"

"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$FOX_BUILDER" --corpus "$T/corpus" --snapshot-root "$T/snapshots"
ORIGINAL=$(tr -d '\n' < "$T/snapshots/current")
printf 'not a ZIP' > "$T/bad.zip"

set +e
"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$FOX_BUILDER" --corpus "$T/bad.zip" --snapshot-root "$T/snapshots" \
  >"$T/badzip.out" 2>"$T/badzip.err"
echo "badzip_status=$?"
set -e
echo "pointer_after_badzip=$(tr -d '\n' < "$T/snapshots/current")"

printf 'bad\377\n' > "$T/corpus/sentences.txt"
set +e
"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$FOX_BUILDER" --corpus "$T/corpus" --snapshot-root "$T/snapshots" \
  >"$T/inspect.out" 2>"$T/inspect.err"
echo "inspection_status=$?"
set -e
echo "pointer_after_inspection=$([ "$(tr -d '\n' < "$T/snapshots/current")" = "$ORIGINAL" ] && echo preserved || echo changed)"

printf 'Corpus B\n' > "$T/corpus/sentences.txt"
printf '%s\n' '#!/usr/bin/env bash' \
  'if [[ "$1" == "--inspect-corpus" ]]; then exec "$FOX_BUILDER" "$@"; fi' \
  'echo "deliberate build failure" >&2' 'exit 7' > "$T/fail-builder"
chmod +x "$T/fail-builder"
set +e
"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$T/fail-builder" --corpus "$T/corpus" --snapshot-root "$T/snapshots" \
  >"$T/build.out" 2>"$T/build.err"
echo "builder_status=$?"
set -e
echo "pointer_after_builder=$([ "$(tr -d '\n' < "$T/snapshots/current")" = "$ORIGINAL" ] && echo preserved || echo changed)"

printf '%s\n' '#!/usr/bin/env bash' \
  'if [[ "$1" == "--inspect-corpus" ]]; then exec "$FOX_BUILDER" "$@"; fi' \
  'mkdir "$4"' 'printf bad > "$4/manifest.binpb"' 'exit 0' > "$T/invalid-builder"
chmod +x "$T/invalid-builder"
set +e
"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$T/invalid-builder" --corpus "$T/corpus" --snapshot-root "$T/snapshots" \
  >"$T/invalid.out" 2>"$T/invalid.err"
echo "invalid_candidate_status=$?"
set -e
echo "pointer_after_invalid=$([ "$(tr -d '\n' < "$T/snapshots/current")" = "$ORIGINAL" ] && echo preserved || echo changed)"
echo "workspace_debris=$(find "$T/snapshots" -mindepth 1 -maxdepth 1 -name '.prepare-*' -print | wc -l)"

mkdir -p "$T/legacy/current"
printf 'preserve\n' > "$T/legacy/current/keep.txt"
set +e
"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$FOX_BUILDER" --corpus "$T/corpus" --snapshot-root "$T/legacy" \
  >"$T/legacy.out" 2>"$T/legacy.err"
echo "legacy_status=$?"
set -e

cat "$T/badzip.err" "$T/inspect.err" "$T/build.err" \
  "$T/invalid.err" "$T/legacy.err"
echo "legacy_marker=$(cat "$T/legacy/current/keep.txt")"

printf 'corpus a\n' | "$FOX_PYTHON" -m autocomplete.main \
  --snapshot "$T/snapshots/current"
```

**Text entered inside the final CLI:** `corpus a`, then EOF.

**Expected output or behavior:** All four failure statuses and `legacy_status` are
`1`. Errors contain `cannot open ZIP`, `corpus inspection failed` with
`invalid UTF-8`, `snapshot build failed: deliberate build failure`,
`new snapshot validation failed`, and `current`/`existing directory`, respectively.
All `pointer_after_*` checks say `preserved`; `workspace_debris=0`;
`legacy_marker=preserve`. Final CLI search still returns original `Corpus A` from
the active snapshot even though the current corpus file has changed.

**PASS:** Every failure is clear, the original pointer remains usable, temporary
workspaces are gone, and legacy data is untouched.

**FAIL:** Any failure activates B/invalid data, deletes old data, leaves workspace
debris, overwrites the legacy directory, or makes the old pointer unsearchable.

## MT-19 — Concurrent preparation serializes build and reuse

**What this verifies:** The snapshot-root preparation lock allows two concurrent
processes for the same corpus to produce one build and one reuse, with one shared
snapshot ID/directory and a valid pointer.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt19.XXXXXX)
mkdir -p "$T/corpus"
printf 'Concurrent sentence\n' > "$T/corpus/data.txt"

"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$FOX_BUILDER" --corpus "$T/corpus" --snapshot-root "$T/snapshots" \
  >"$T/one.log" 2>"$T/one.err" &
P1=$!
"$FOX_PYTHON" -m autocomplete.prepare_snapshot \
  --builder "$FOX_BUILDER" --corpus "$T/corpus" --snapshot-root "$T/snapshots" \
  >"$T/two.log" 2>"$T/two.err" &
P2=$!
wait "$P1"
wait "$P2"

cat "$T/one.log" "$T/two.log"
echo "built_count=$(grep -h -c 'status=BUILT_AND_ACTIVATED' "$T/one.log" "$T/two.log" | awk '{s+=$1} END {print s}')"
echo "reused_count=$(grep -h -c 'status=REUSED' "$T/one.log" "$T/two.log" | awk '{s+=$1} END {print s}')"
echo "snapshot_directories=$(find "$T/snapshots" -mindepth 1 -maxdepth 1 -type d | wc -l)"
ID=$(tr -d '\n' < "$T/snapshots/current")
test -d "$T/snapshots/$ID"
printf 'concurrent\n' | "$FOX_PYTHON" -m autocomplete.main \
  --snapshot "$T/snapshots/current"
```

**Text entered inside the final CLI:** `concurrent`, then EOF.

**Expected output or behavior:** One log reports `BUILT_AND_ACTIVATED`, the other
`REUSED`; both report the same ID. Counts are `built_count=1`, `reused_count=1`, and
`snapshot_directories=1`. Search returns `Concurrent sentence` with score `20`.

**PASS:** Both processes exit zero, counts/IDs/directory count match, and current is
valid/searchable.

**FAIL:** Both build, either fails/hangs, IDs differ, multiple immutable directories
appear, pointer is invalid, or search fails.

## MT-20 — Local artifact materialization and safety

**What this verifies:** The real local artifact store copies and validates an
immutable snapshot, allows same-source/same-destination validation, rejects missing
sources and overwrite attempts, cleans up corrupt copies, rejects source-tree
symlinks without copying their targets, and leaves outside data unchanged.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt20.XXXXXX)
mkdir -p "$T/corpus"
printf 'to be\nhello world\n' > "$T/corpus/records.txt"
"$FOX_BUILDER" --corpus "$T/corpus" --output "$T/source" >/dev/null

"$FOX_PYTHON" - "$T" <<'PY'
import shutil
import sys
from pathlib import Path

from autocomplete.artifact_store import LocalArtifactStore
from autocomplete.snapshot_loader import load_snapshot

root = Path(sys.argv[1])
store = LocalArtifactStore()
destination = store.materialize_snapshot(str(root / "source"), root / "destination")
records, index = load_snapshot(destination)
print("copied_records", [(i, r.original) for i, r in records.items()])
print("candidate", index.get_candidate_ids("to be"))
print("same_destination", store.materialize_snapshot(str(destination), destination) == destination)

for label, source, target in [
    ("missing", root / "missing", root / "unused-missing"),
    ("overwrite", root / "source", destination),
]:
    try:
        store.materialize_snapshot(str(source), target)
    except Exception as error:
        print(label, type(error).__name__, error)

corrupt = root / "corrupt"
shutil.copytree(root / "source", corrupt)
(corrupt / "manifest.binpb").write_bytes(b"bad")
try:
    store.materialize_snapshot(str(corrupt), root / "unused-corrupt")
except Exception as error:
    print("corrupt", type(error).__name__, error,
          "cleaned=", not (root / "unused-corrupt").exists())

symlink_source = root / "symlink-source"
shutil.copytree(root / "source", symlink_source)
outside = root / "outside-secret.txt"
outside.write_text("must never be copied", encoding="utf-8")
(symlink_source / "leaked.txt").symlink_to(outside)
try:
    store.materialize_snapshot(str(symlink_source), root / "unused-symlink")
except Exception as error:
    print("symlink", type(error).__name__, error,
          "cleaned=", not (root / "unused-symlink").exists(),
          "outside=", outside.read_text(encoding="utf-8"))
PY

test -z "$(find "$T" -maxdepth 1 \
  \( -name '.unused-*' -o -name '.destination-*' \) -print -quit)"
```

**CLI input:** None; this uses the real artifact-store public API from the terminal.

**Expected output or behavior:** Copied records are
`[(1, 'to be'), (2, 'hello world')]`; candidate is `[1]`;
`same_destination True`. Missing yields
`FileNotFoundError`, overwrite yields `FileExistsError`, corrupt yields a snapshot
validation error with `cleaned= True`, and symlink yields `ArtifactError symbolic
link is not allowed` with `cleaned= True outside= must never be copied`. No staging
directory remains.

**PASS:** Valid materialization works, all unsafe/failure paths are rejected and
cleaned, destination is never overwritten, and outside content is unchanged.

**FAIL:** Invalid input publishes, an existing destination changes, a symlink target
is copied/changed, or staging debris remains.

## MT-21 — Benchmark correctness, metrics, JSON, determinism metadata, and errors

**What this verifies:** The real benchmark loads a valid snapshot, generates a
seeded bucket mix, checks indexed results against the reference engine before
reporting, performs warmups/repeats, prints stage/candidate metrics, writes valid
JSON metadata, and rejects invalid numeric options or a missing snapshot.

**Terminal commands:**

```bash
set -euo pipefail
T=$(mktemp -d /tmp/fox-mt21.XXXXXX)
mkdir -p "$T/corpus"
printf 'abcdefghij\nklmnopqrst\nuvwxyzabcd\n' > "$T/corpus/data.txt"
"$FOX_BUILDER" --corpus "$T/corpus" --output "$T/snapshot" >/dev/null

"$FOX_PYTHON" -m benchmarks.benchmark_search \
  --snapshot "$T/snapshot" --seed 123 --query-count 6 --repeats 2 \
  --warmup-rounds 1 --json-output "$T/report.json" | tee "$T/summary.txt"

"$FOX_PYTHON" - "$T/report.json" "$T/snapshot" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print("condition", report["condition"])
print("snapshot_path", report["snapshot_path"] == sys.argv[2])
print("seed", report["seed"])
print("queries", report["query_count_requested"], report["query_count_measured"])
print("repeats_warmups", report["repeats"], report["warmup_rounds"])
print("record_count", report["record_count"])
print("buckets", sorted(report["query_length_bucket_counts"]))
print("indexed_samples", report["indexed_end_to_end_ms"]["count"])
print("reference_samples", report["reference_end_to_end_ms"]["count"])
print("candidate_denominator", report["candidate_count_denominator"])
PY

for args in '--query-count 0' '--repeats -2' '--warmup-rounds -1'; do
  set +e
  "$FOX_PYTHON" -m benchmarks.benchmark_search --snapshot "$T/snapshot" $args \
    >"$T/invalid.out" 2>"$T/invalid.err"
  status=$?
  set -e
  echo "invalid=[$args] status=$status message=$(tail -n 1 "$T/invalid.err")"
done

set +e
"$FOX_PYTHON" -m benchmarks.benchmark_search --snapshot "$T/missing" \
  >"$T/missing.out" 2>"$T/missing.err"
echo "missing_status=$?"
set -e
tail -n 1 "$T/missing.err"
```

**CLI input:** None.

**Expected output or behavior:** Summary begins `Online Search Benchmark` and
includes warm condition/scope, indexed/reference end-to-end metrics, candidate
generation, matcher verification, ranking, candidate counts, query-length buckets,
safe one-character fallback, speedup, and `JSON report written to ...`. Timing
numbers vary by machine.

JSON inspection prints:

```text
condition warm
snapshot_path True
seed 123
queries 6 6
repeats_warmups 2 1
record_count 3
buckets ['1', '2', '3', '4', '5', '6+']
indexed_samples 12
reference_samples 12
candidate_denominator non-empty measured queries
```

All three invalid-option cases exit `2` with `must be greater than zero` or `must
be non-negative`. Missing snapshot exits `2` with `snapshot path does not exist`.

**PASS:** Correctness checks complete without assertion, summary/JSON structure and
sample counts match, and all invalid command lines fail clearly.

**FAIL:** Indexed/reference correctness fails, JSON is absent/invalid, metadata or
sample counts differ, required metrics are missing, or invalid options are accepted.

## Automated-only coverage not suitable for this manual pack

The following repository assertions are intentionally not represented as manual
terminal cases because reproducing them would require monkeypatching, fake clients,
private implementation access, artificial OS faults, or impractical exhaustive
human checking. The manual tests above cover their externally visible outcomes
where one exists.

| Automated-only area | Why it is not a reasonable external manual test | External behavior covered here |
| --- | --- | --- |
| Mocked load/configure/call order and “must not call” assertions | Call counts and private call order are not observable at the terminal without instrumentation | MT-07, MT-11, MT-14, MT-21 exercise successful/failing startup and public behavior |
| Dataclass/object identity, input mutation, mapping-proxy and posting immutability | These are internal Python representation contracts, not an application terminal surface | MT-02, MT-10, MT-11 verify returned fields, ranking, and loaded index behavior |
| 60,000 matcher, 20,000 union, 50,000 ranking, and generated recall equivalence cases | An exhaustive oracle sweep is automated regression/property testing, not meaningful human verification | MT-04, MT-09, and MT-10 cover representative candidate, scoring, ranking, and duplicate cases end to end |
| Fake timers and mocked benchmark stage/call-order mismatches | Exact synthetic timing arithmetic and injected drift require controlled test doubles | MT-21 runs the real correctness comparison, metrics, repeats, warmup, buckets, and JSON |
| A ZIP reader that lies about its declared size | Standard ZIP files/tools cannot create this in a portable honest archive; it requires replacing the ZIP reader | MT-05 verifies all real-creatable archive safety and declared limit failures |
| Forced `os.replace`, `fsync`, directory-descriptor close, and durability failures | Reliably inducing these precise branches requires OS-call fault injection and may risk the host filesystem | MT-15 through MT-19 verify real pointer activation, retention, repair, failure preservation, cleanup, and locking |
| Injected invalid candidate/load/API exceptions and impossible unknown index IDs | These states require replacing production functions or bypassing validated snapshot construction | MT-13 and MT-18 corrupt external snapshot/candidate artifacts and verify real validation/fallback |
| GCS fake-client listings, outside-prefix and duplicate-object simulations | No GCS materialization CLI, credentials, or repository-owned remote fixture is provided; a portable black-box test is impossible | MT-20 covers the real local artifact store and its equivalent validation/publication safety |
| Bounded-read tracing in `_frames` | Read sizes are private I/O instrumentation, not externally visible | MT-02 verifies valid big-endian framing; MT-13 verifies malformed/truncated frame rejection |
| Generated Protobuf descriptors and C++ in-memory unit helpers | Schema descriptor shapes and direct `character_grams` calls are internal compile/runtime contracts | MT-02 inspects real manifest fields, records, framing, and Unicode postings generated across C++/Python |
| Builder timeout value through the CLI | The parser exposes no timeout flag | MT-06 exercises the actual public orchestration API with a real child process and cleanup |
| Ctrl-C generated programmatically | Portable shell piping supplies EOF but not a faithful terminal interrupt | In MT-07 or MT-08, start the same CLI interactively and press Ctrl-C at `Enter query:`; PASS is an immediate clean exit with no traceback |

## Final summary

Record the actual result in the last column while executing the pack.

| Test | Purpose | Expected result | PASS/FAIL |
| --- | --- | --- | --- |
| MT-01 | Builder normalization | Seven frozen normalization results match exactly | ☐ PASS / ☐ FAIL |
| MT-02 | Directory builder/load/determinism | Recursive corpus becomes two identical valid snapshots with expected records/postings | ☐ PASS / ☐ FAIL |
| MT-03 | Builder error atomicity | Usage/input/output/UTF-8 failures publish nothing and preserve existing data | ☐ PASS / ☐ FAIL |
| MT-04 | ZIP-to-search integration | ZIP statistics/counts, deterministic snapshot, and two search results match | ☐ PASS / ☐ FAIL |
| MT-05 | ZIP safety and limits | All unsafe/over-limit archives fail and clean up | ☐ PASS / ☐ FAIL |
| MT-06 | Builder timeout | Child is killed promptly; extraction/output are absent | ☐ PASS / ☐ FAIL |
| MT-07 | Basic CLI behavior | Normalized result metadata plus two clear no-result responses; EOF is clean | ☐ PASS / ☐ FAIL |
| MT-08 | CLI state machine | Exact accumulation, typed space, blank fragment, exact reset, and continuation | ☐ PASS / ☐ FAIL |
| MT-09 | Matcher/scoring | Frozen scores `10,12,14,3,6,8,8`; final query rejected | ☐ PASS / ☐ FAIL |
| MT-10 | Ranking/top five | Exact five-line deterministic order with three duplicates | ☐ PASS / ☐ FAIL |
| MT-11 | API and `k` validation | Initialization error, official five, custom/invalid `k` behavior match | ☐ PASS / ☐ FAIL |
| MT-12 | Empty corpus | Valid zero-count snapshot, no CLI result, benchmark refuses query generation | ☐ PASS / ☐ FAIL |
| MT-13 | Snapshot validation | All 22 corrupt variants fail startup with mapped errors and no prompt | ☐ PASS / ☐ FAIL |
| MT-14 | Startup arguments/pointers | Missing/invalid references fail; prepared pointer searches successfully | ☐ PASS / ☐ FAIL |
| MT-15 | Prepare build/reuse/rebuild | Correct statuses, identity changes, pointer, and retained directory counts | ☐ PASS / ☐ FAIL |
| MT-16 | ZIP reuse/fallback | Repacked ZIP reuses; published A is reactivated without duplication | ☐ PASS / ☐ FAIL |
| MT-17 | Corrupt/version repair | Both active-snapshot defects rebuild to a valid v1 snapshot with no debris | ☐ PASS / ☐ FAIL |
| MT-18 | Preparation failure safety | Previous pointer survives four failure classes; legacy directory is preserved | ☐ PASS / ☐ FAIL |
| MT-19 | Concurrent preparation | Exactly one build, one reuse, one ID/directory, valid pointer | ☐ PASS / ☐ FAIL |
| MT-20 | Local artifact store | Valid copy works; missing/overwrite/corrupt/symlink cases fail safely | ☐ PASS / ☐ FAIL |
| MT-21 | Benchmark CLI | Real correctness run, metrics/JSON metadata, and invalid-option errors match | ☐ PASS / ☐ FAIL |
