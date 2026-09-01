# Observability

FOX uses two logical, independently rotated logs: `offline.log` for corpus and
snapshot construction, and `runtime.log` for snapshot loading and searches.
Every UTF-8 line is a complete event in `UTC timestamp | LEVEL | event |
key=value` form. Raw numeric fields are authoritative; `_human` fields are only
display aids.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `LOG_DIRECTORY` | `logs` | Directory created on first event |
| `LOG_LEVEL` | `INFO` | Python level name; `OFF` disables writes |
| `LOG_MAX_BYTES` | `10485760` | Per-active-file rotation threshold |
| `LOG_BACKUP_COUNT` | `5` | Backups retained per logical log |
| `LOG_QUERY_TEXT` | `false` | Development-only raw query opt-in |
| `DETAILED_PROFILING` | `false` | Bounded matcher timing sample (max 1,024) |

Logging failures are swallowed and cannot fail a build or query. Query text,
candidate/returned sentence text, source content, and random temporary paths are
absent by default. `LOG_QUERY_TEXT=true` has a privacy and data-retention risk.
Detailed profiling adds two clock reads per sampled matcher invocation and is
not suitable as the final production benchmark.

Files rotate independently as `offline.log.1` and `runtime.log.1`. Generated
logs are ignored by Git. Events include `build.started`, `zip.validated`,
`zip.extracted`, `builder.completed`, `snapshot.published`, `build.completed`,
`build.failed`, `snapshot.load_started`, `snapshot.ready`,
`snapshot.load_failed`, `search.completed`, and `search.failed`.

## Metric boundaries

| Metric | Meaning | Component/stage | Unit |
| --- | --- | --- | --- |
| `candidates` | Candidate IDs produced by exact and fuzzy candidate sources | SearchIndex | count |
| `candidates_examined` | Produced candidates whose records were looked up | SearchEngine | count |
| `candidate_iterator_creation_ms` | Candidate iterator/seed construction | SearchIndex | ms |
| `candidate_iteration_ms` | Time advancing the streamed candidate iterator | SearchIndex | ms |
| `candidate_retrieval_ms` | Creation plus iteration time; retained for compatibility | SearchIndex | ms |
| `candidate_id_payload_bytes` | Candidate count times four; logical uint32 payload, not Python heap | SearchIndex | bytes |
| `candidate_lookup_ms` | Dictionary record lookup time | SearchEngine | ms |
| `exact_match_check_ms` | Explicit exact-substring predicates in the fuzzy stream | SearchEngine | ms |
| `exact_checks` / `exact_matches_accepted` | Explicit checks and all accepted exacts, including index-proven short-query exacts | SearchEngine | count |
| `matcher_calls` / `matcher_ms` | Non-exact matcher calls and their total time | Matcher | count / ms |
| `matcher_valid` / `matcher_rejected` | Non-exact matcher outcomes | Matcher | count |
| `candidates_checked` | Exact matches accepted plus non-exact matcher calls | SearchEngine | count |
| `valid_matches` | Exact matches accepted plus matcher-valid results | SearchEngine | count |
| `top_k_bound_checks` / `top_k_bound_check_ms` | Maximum-score admission checks and their time | Ranking | count / ms |
| `pruned_candidates` | Candidates rejected by the maximum-score and full tie-key bound | Ranking | count |
| `average_candidate_us` | Matcher ns / matcher calls / 1000 | Matcher | µs |
| `result_construction_ms` | Result object creation and selector insertion | SearchEngine | ms |
| `ranking_ms` | Final selector result extraction | Ranking | ms |
| `unaccounted_ms` | Total before logging minus measured stages; includes loop/control and timer overhead | SearchEngine | ms |
| `total_ms` | Entire search call from entry through results | SearchEngine | ms |
| `*_size_bytes` | Filesystem size without rescanning content | Snapshot/build | bytes |
| `total_posting_ids` | IDs already counted while writing/loading postings | Builder/loader | count |

All durations use `time.perf_counter_ns()`. Candidate retrieval is accumulated
around iterator advancement because the existing architecture deliberately
streams IDs; it is not materialized merely for logging. Result construction
includes online selector insertion, while `ranking_ms` covers final extraction.

## Reading and comparing runs

Filter an event and turn separators into a quick block without changing stored
logs:

```bash
grep 'search.completed' logs/runtime.log | tail -1 | sed 's/ | /\n  /g'
grep 'build.completed' logs/offline.log | tail -1 | sed 's/ | /\n  /g'
```

For optimization comparisons, keep corpus, snapshot ID, queries, warm-up count,
hardware, and configuration fixed; compare repeated-run medians, candidate
counts, matcher time, and total time. Run `python -m
benchmarks.benchmark_logging` to compare disabled, normal, and detailed modes.
Scheduling, filesystem cache, rotation I/O, and Python allocator noise remain
known limitations; do not claim zero overhead or compare a detailed run to a
production baseline.
