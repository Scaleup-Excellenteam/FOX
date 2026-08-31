# Snapshot benchmark sample

Audit smoke run on a deterministic four-record, two-file UTF-8 corpus with query lengths 1, 2, 5, and 7. Cloud transfer is excluded.

| Metric | Result |
|---|---:|
| Corpus files / sentences | 2 / 4 |
| Offline build | 3.65 ms |
| Snapshot size | 2,329 bytes |
| Record / index shards | 1 / 1 |
| Python load | 20.19 ms |
| Peak traced load memory | 36,866 bytes |
| Approximate runtime index memory | 18,328 bytes |
| 1/2/3-gram posting IDs | 32 / 37 / 36 |
| 1-gram posting min / median / mean / max | 1 / 1 / 1.68 / 3 |
| 2-gram posting min / median / mean / max | 1 / 1 / 1.09 / 2 |
| 3-gram posting min / median / mean / max | 1 / 1 / 1 / 1 |
| Average candidates | 2 |
| Length 1 / 2 / 5 / 7 candidates | 4 / 2 / 1 / 1 |
| Candidate reduction ratio | 50% |
| Safe fallback rate | 25% |
| Candidate latency min / median / mean / max | 3.04 / 4.27 / 4.21 / 5.25 µs |

This is a repeatable functional smoke benchmark, not a production capacity claim. Run `benchmark_snapshot.py` against the same full corpus and query set for meaningful performance comparisons.
