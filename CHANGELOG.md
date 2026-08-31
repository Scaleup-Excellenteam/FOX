# Changelog

- Validated GCS blob prefix membership and rejected duplicate staged paths to prevent cross-prefix reads and overwrites.
- Rejected local snapshot symlinks before and after copying so materialization cannot read outside the source tree.
- Streamed shard digest verification and frame parsing in bounded reads to avoid whole-shard memory spikes.
- Added a configurable C++ builder timeout with domain-specific failure reporting and automatic extraction cleanup.
- Added declared-size and streamed-byte ZIP resource limits to prevent archive bombs and unbounded extraction.
- Hid the validated-postings fast path behind a private loader API so public index construction always normalizes IDs.
- Made `SearchIndex` postings and sentence IDs immutable after construction to prevent caller-induced query corruption.
