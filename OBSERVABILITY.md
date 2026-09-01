# Observability

FOX writes UTF-8 JSON Lines to two independently rotated files:
`offline.log` for corpus/snapshot construction and `runtime.log` for snapshot
loading and searches. Each line is one JSON object. `timestamp_utc` is an ISO
8601 UTC timestamp, `level` is the Python logging level name, and `event` is the
event name. JSON escaping makes field boundaries unambiguous and encodes control
characters such as tabs, newlines, NUL, and terminal escapes.

## Configuration

Configuration is parsed once on first use. Processes should set the environment
before importing/using the application; `reset_for_tests()` is the test-only way
to invalidate the cache and close handlers.

| Variable | Default | Meaning |
| --- | --- | --- |
| `LOG_DIRECTORY` | `logs` | Directory created on the first enabled event |
| `LOG_LEVEL` | `INFO` | Python level name; unknown names use `INFO`; `OFF` enables the fast path |
| `LOG_MAX_BYTES` | `10485760` | Positive per-active-file rotation threshold; invalid/non-positive values use the default |
| `LOG_BACKUP_COUNT` | `5` | Positive backups retained per log; invalid/non-positive values use the default |
| `LOG_QUERY_TEXT` | `false` | Development-only raw query opt-in |
| `DETAILED_PROFILING` | `false` | Enables granular search timings and at most 1,024 matcher samples |

With `LOG_LEVEL=OFF`, search uses the uninstrumented search path and snapshot
build/load skip observability IDs, dictionaries, clocks, handler construction,
directory creation, and file creation. OFF still performs one cached configuration
lookup per public call. It does not mean that the production operation itself has
zero cost.

Normal INFO search logging uses two call-level clock reads plus counters. It does
not time individual candidates. Detailed profiling adds clocks around candidate
stages and times only the first 1,024 matcher calls used for its distribution.
Detailed mode is diagnostic and should not be treated as production performance.

## Privacy and failure isolation

Query, candidate, returned sentence, and source text are absent by default.
`LOG_QUERY_TEXT=true` opts raw queries into the runtime log and carries explicit
privacy and retention risk. Exceptions are represented by `error_category` and a
basename/path-free `reason_code`; raw exception messages are not logged.

Directory creation, file open/write/flush, rotation, serialization, and optional
snapshot metric enrichment failures are swallowed. They cannot change a search
result or convert a successful snapshot build/load into failure. Invalid production
inputs and actual snapshot corruption still raise their original exceptions.

## Runtime events

### `search.completed`

Always-present fields:

| Field | Meaning | Unit |
| --- | --- | --- |
| `request_id` | 128-bit UUID4 correlation value, 32 lowercase hex characters | identifier |
| `raw_query_length`, `normalized_query_length` | Query lengths before/after normalization | characters |
| `query_text` | Raw query; present only with explicit opt-in | text |
| `candidates` | IDs produced by exact and fuzzy candidate sources | count |
| `candidate_id_payload_bytes` | `candidates * 4`, logical uint32 payload rather than Python heap | bytes |
| `candidates_examined` | Candidate records successfully looked up | count |
| `exact_checks` | Explicit substring predicates in the fuzzy stream | count |
| `exact_matches_accepted` | Index-proven and explicitly proven exact matches | count |
| `top_k_bound_checks`, `pruned_candidates` | Ranking admission checks and candidates skipped by the safe maximum-score bound | count |
| `matcher_calls`, `matcher_valid`, `matcher_rejected` | Non-exact matcher invocations and outcomes | count |
| `candidates_checked` | Exact matches plus matcher calls | count |
| `valid_matches`, `rejected_candidates` | Accepted exact/matcher-valid and matcher-rejected candidates | count |
| `results_returned` | Final result length | count |
| `search_compute_ms` | Enabled search work from the call-level start through result extraction, captured before event construction and synchronous output | ms |
| `detailed_profiling` | Whether detailed fields are present | boolean |
| `status` | `success` | text |

Detailed-only fields:

| Field | Meaning | Unit |
| --- | --- | --- |
| `normalization_ms` | Query normalization | ms |
| `candidate_iterator_creation_ms` | Exact/fuzzy candidate source and iterator construction | ms |
| `candidate_iteration_ms` | Time advancing candidate iterators | ms |
| `candidate_retrieval_ms` | Iterator creation plus advancement | ms |
| `candidate_lookup_ms` | Record dictionary lookups | ms |
| `exact_match_check_ms` | Explicit exact substring predicates | ms |
| `top_k_bound_check_ms` | Maximum-score admission checks | ms |
| `result_construction_ms` | Result construction and selector insertion | ms |
| `ranking_ms` | Final selector extraction | ms |
| `matcher_sample_count` | Timed matcher calls, bounded to 1,024 | count |
| `matcher_sample_total_ms` | Sum of sampled matcher durations, not all calls when calls exceed the bound | ms |
| `matcher_sample_{min,mean,p50,p95,p99,max}_us` | Sample distribution | µs |
| `profile_unaccounted_ms` | `search_compute_ms` minus isolated detailed stages; includes control flow, unsampled matcher work, field-independent work, and timer overhead | ms |

`search_compute_ms` is deliberately not named `total_ms`: it excludes construction,
serialization, rotation, flush, and output of its own event. Use an external timer,
as the benchmark does, for user-visible end-to-end call latency.

### `search.failed`

Includes `request_id`, query lengths/optional text, `failed_stage`,
`error_category`, path-free `reason_code`, current `candidate_count`, successfully
looked-up `candidates_examined`, pre-event `search_compute_ms`, and `status=failed`.
Stages distinguish validation, normalization, candidate retrieval/iteration,
lookup, ranking-bound checks, exact checks, matching, construction, and ranking.

### Snapshot loading

`snapshot.load_started` contains the basename-only `snapshot_location`.

`snapshot.ready` contains snapshot/version IDs; expected and loaded record/posting
counts; `total_posting_ids`; `manifest_load_and_validation_ms`,
`records_load_and_validation_ms`, `postings_load_and_validation_ms`, and
`search_index_publication_ms`; `load_compute_ms` captured before the ready event;
and `load_unaccounted_ms`, the non-negative residual outside those isolated phases.
Size enrichment uses every `manifest.record_files` and `manifest.index_files` entry
and reports `record_file_count`, `index_file_count`, aggregate record/index/manifest
and total bytes, a display-only human total, and `size_metrics_available`. If a
best-effort stat fails, only `size_metrics_available=false` is emitted.

`snapshot.load_failed` contains `failed_stage`, `error_category`, path-free
`reason_code`, pre-event `load_compute_ms`, `search_index_published=false`, and
`status=failed`.

## Offline events

| Event | Principal fields and boundaries |
| --- | --- |
| `build.started` | Build UUID, input type/basename, best-effort compressed ZIP bytes/human value, destination basename, format versions, gram sizes, and UTC start field |
| `zip.processed` | Entry/text/ignored/directory counts, compressed/extracted bytes, and one combined `validation_and_extraction_ms`; no false split between validation and extraction |
| `builder.completed` | Builder basename, exit code, file/line/retained/skipped counts, and native `cpp_builder_ms` parsed from the builder summary |
| `snapshot.published` | Snapshot/digest/count fields, destination basename, `snapshot_published_by_invocation`, and `previous_known_good_snapshot_remains_available` |
| `build.completed` | Aggregate build/count fields, manifest-derived shard counts/sizes when available, native builder duration, pre-event `offline_compute_ms`, publication/reuse booleans, snapshot ID, cleanup status, and `status=success` |
| `build.failed` | Real failed stage/category/reason code, native exit code when available, pre-event duration, cleanup status, `snapshot_published_by_invocation`, `previous_known_good_snapshot_remains_available`, and `status=failed` |

`snapshot_published_by_invocation` is true only if this call created the output.
On a failed call, `previous_known_good_snapshot_remains_available` is true only
when the destination existed before the call, passed a complete snapshot load,
and still exists afterward. Merely finding an old manifest is not called a partial
publication.

Raw numeric byte and duration fields are authoritative. `_human` fields are only
display aids. Native summary fields can be zero when an external compatible builder
does not provide the optional FOX summary format.

## Benchmark

Run:

```bash
python -m benchmarks.benchmark_logging
```

The benchmark reports external-call medians and overhead ratios for an explicit
uninstrumented baseline, OFF, normal, and detailed modes across exact, non-exact,
and zero-candidate traffic. Seven trials are deterministically interleaved rather
than run in one fixed mode order. It verifies OFF result equivalence/no directory
creation and verifies a nonzero detailed matcher sample.

Results are diagnostic, not stable CI thresholds. Scheduling, filesystem and page
cache, rotation I/O, allocator/GC state, interpreter build, hardware, and other
processes can materially change values. Compare repeated runs on fixed hardware and
never claim zero overhead from these measurements.
