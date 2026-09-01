"""Reproducible warm benchmark for the fully initialized online search path.

Snapshot loading and engine construction happen before all query timing. This harness
does not measure corpus building, serialization, download, startup, or snapshot load
time, and it makes no claim about cold-query performance.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from autocomplete.models import AutoCompleteData, SentenceRecord
from autocomplete.ranking import rank_results
from autocomplete.reference_engine import ReferenceEngine
from autocomplete.search_engine import SearchEngine

DEFAULT_SEED = 20260901
DEFAULT_QUERY_COUNT = 100
DEFAULT_REPEATS = 10
DEFAULT_WARMUP_ROUNDS = 3
BUCKET_NAMES = ("1", "2", "3", "4", "5", "6+")

Timer = Callable[[], int]


def _load_snapshot(snapshot_path: Path) -> tuple[dict[int, SentenceRecord], Any]:
    """Load through Member 2's production loader when that branch is available."""
    from autocomplete.snapshot_loader import load_snapshot

    return load_snapshot(snapshot_path)


def _normalize(text: str) -> str:
    """Delegate normalization to Member 1's production implementation."""
    from autocomplete.normalization import normalize

    return normalize(text)


def _translate(text: str) -> str:
    """Delegate Spanish query translation to the production implementation."""
    from autocomplete.translation import translate_to_spanish

    return translate_to_spanish(text)


def _match_and_score(query: str, sentence: str) -> int | None:
    """Delegate verification and scoring to Member 1's implementation."""
    from autocomplete.matcher import match_and_score

    return match_and_score(query, sentence)


def _timer_ns() -> int:
    return time.perf_counter_ns()


def _stats(samples: Sequence[int | float]) -> dict[str, int | float]:
    """Return summary statistics using the deterministic nearest-rank p95.

    Nearest-rank p95 is sorted_samples[ceil(0.95 * count) - 1].
    """
    if not samples:
        return {"count": 0, "median": 0.0, "p95": 0.0, "mean": 0.0}

    ordered = sorted(samples)
    p95_index = math.ceil(0.95 * len(ordered)) - 1
    return {
        "count": len(ordered),
        "median": statistics.median(ordered),
        "p95": ordered[p95_index],
        "mean": statistics.fmean(ordered),
    }


def _latency_stats(samples_ns: Sequence[int]) -> dict[str, int | float]:
    return _stats([sample / 1_000_000 for sample in samples_ns])


def _query_bucket(normalized_query: str) -> str:
    length = len(normalized_query)
    if length == 0:
        return "empty"
    if length >= 6:
        return "6+"
    return str(length)


def generate_queries(
    records_by_id: dict[int, SentenceRecord],
    *,
    seed: int = DEFAULT_SEED,
    query_count: int = DEFAULT_QUERY_COUNT,
) -> list[str]:
    """Generate an ordered, reproducible set of real normalized substrings.

    Available length buckets are shuffled once per cycle, so every available bucket
    is deliberately represented before any bucket is used again. Corpora too short
    for some buckets use only the buckets they can safely supply.
    """
    if query_count <= 0:
        raise ValueError("query_count must be greater than zero")

    corpus = [
        record.normalized
        for _, record in sorted(records_by_id.items())
        if record.normalized
    ]
    if not corpus:
        raise ValueError("cannot generate queries from an empty normalized corpus")

    eligible: dict[str, list[str]] = {}
    for length in range(1, 6):
        sentences = [sentence for sentence in corpus if len(sentence) >= length]
        if sentences:
            eligible[str(length)] = sentences
    long_sentences = [sentence for sentence in corpus if len(sentence) >= 6]
    if long_sentences:
        eligible["6+"] = long_sentences

    rng = random.Random(seed)
    queries: list[str] = []
    while len(queries) < query_count:
        cycle = list(eligible)
        rng.shuffle(cycle)
        for bucket in cycle:
            sentence = rng.choice(eligible[bucket])
            if bucket == "6+":
                length = rng.randint(6, len(sentence))
            else:
                length = int(bucket)
            start = rng.randrange(len(sentence) - length + 1)
            queries.append(sentence[start : start + length])
            if len(queries) == query_count:
                break

    return queries


def _assert_engine_equality(
    queries: Sequence[str],
    indexed: SearchEngine,
    reference: ReferenceEngine,
) -> None:
    for query in dict.fromkeys(queries):
        indexed_result = indexed.search(query, k=5)
        reference_result = reference.search(query, k=5)
        if indexed_result != reference_result:
            raise AssertionError(
                "indexed/reference correctness mismatch for "
                f"query {query!r}: indexed={indexed_result!r}, "
                f"reference={reference_result!r}"
            )


def _instrument_query(
    query: str,
    normalized_query: str,
    records_by_id: dict[int, SentenceRecord],
    index: Any,
    indexed: SearchEngine,
    timer: Timer,
) -> tuple[int, int, int, int]:
    """Replay production stages for measurement and verify the replayed Top 5."""
    candidate_started = timer()
    candidate_ids = index.get_candidate_ids(normalized_query)
    candidate_elapsed = timer() - candidate_started

    matcher_started = timer()
    legal_results: list[AutoCompleteData] = []
    for sentence_id in candidate_ids:
        record = records_by_id[sentence_id]
        score = _match_and_score(normalized_query, record.normalized)
        if score is None:
            continue
        legal_results.append(
            AutoCompleteData(
                completed_sentence=record.original,
                source_text=record.source_path,
                offset=record.line_number,
                score=score,
            )
        )
    matcher_elapsed = timer() - matcher_started

    ranking_started = timer()
    ranked_results = rank_results(legal_results)
    ranking_elapsed = timer() - ranking_started
    instrumented_result = ranked_results[:5]

    production_result = indexed.search(query, k=5)
    if instrumented_result != production_result:
        raise AssertionError(
            "instrumented/production correctness mismatch for "
            f"query {query!r}: instrumented={instrumented_result!r}, "
            f"production={production_result!r}"
        )

    return (
        len(candidate_ids),
        candidate_elapsed,
        matcher_elapsed,
        ranking_elapsed,
    )


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def run_benchmark(
    snapshot_path: Path,
    *,
    seed: int = DEFAULT_SEED,
    query_count: int = DEFAULT_QUERY_COUNT,
    repeats: int = DEFAULT_REPEATS,
    warmup_rounds: int = DEFAULT_WARMUP_ROUNDS,
    timer: Timer | None = None,
) -> dict[str, Any]:
    """Load once, validate correctness, and run a warm online query benchmark."""
    if query_count <= 0:
        raise ValueError("query_count must be greater than zero")
    if repeats <= 0:
        raise ValueError("repeats must be greater than zero")
    if warmup_rounds < 0:
        raise ValueError("warmup_rounds must be non-negative")
    if timer is None:
        timer = _timer_ns

    # Initialization is intentionally complete before any benchmark timing begins.
    records_by_id, index = _load_snapshot(snapshot_path)
    indexed = SearchEngine(records_by_id, index)
    reference = ReferenceEngine(records_by_id)
    queries = generate_queries(records_by_id, seed=seed, query_count=query_count)

    # Correctness checks are authoritative and explicitly outside measured regions.
    _assert_engine_equality(queries, indexed, reference)

    for _ in range(warmup_rounds):
        for query in queries:
            indexed.search(query, k=5)
            reference.search(query, k=5)

    indexed_ns: list[int] = []
    reference_ns: list[int] = []
    candidate_generation_ns: list[int] = []
    matcher_verification_ns: list[int] = []
    ranking_ns: list[int] = []
    candidate_counts: list[int] = []

    bucket_data: dict[str, dict[str, list[int] | int]] = {
        bucket: {
            "query_count": 0,
            "indexed_ns": [],
            "reference_ns": [],
            "candidate_generation_ns": [],
            "matcher_verification_ns": [],
            "ranking_ns": [],
            "candidate_counts": [],
        }
        for bucket in BUCKET_NAMES
    }

    normalized_queries = [_normalize(_translate(query)) for query in queries]
    if any(not normalized for normalized in normalized_queries):
        bucket_data["empty"] = {
            "query_count": 0,
            "indexed_ns": [],
            "reference_ns": [],
            "candidate_generation_ns": [],
            "matcher_verification_ns": [],
            "ranking_ns": [],
            "candidate_counts": [],
        }

    for query, normalized_query in zip(queries, normalized_queries, strict=True):
        bucket = _query_bucket(normalized_query)
        data = bucket_data[bucket]
        data["query_count"] = int(data["query_count"]) + 1

        query_candidate_count: int | None = None
        for _ in range(repeats):
            indexed_started = timer()
            indexed.search(query, k=5)
            indexed_elapsed = timer() - indexed_started
            indexed_ns.append(indexed_elapsed)
            data["indexed_ns"].append(indexed_elapsed)  # type: ignore[union-attr]

            reference_started = timer()
            reference.search(query, k=5)
            reference_elapsed = timer() - reference_started
            reference_ns.append(reference_elapsed)
            data["reference_ns"].append(reference_elapsed)  # type: ignore[union-attr]

            if normalized_query:
                (
                    measured_candidate_count,
                    candidate_elapsed,
                    matcher_elapsed,
                    ranking_elapsed,
                ) = _instrument_query(
                    query,
                    normalized_query,
                    records_by_id,
                    index,
                    indexed,
                    timer,
                )
                if query_candidate_count is None:
                    query_candidate_count = measured_candidate_count
                elif query_candidate_count != measured_candidate_count:
                    raise AssertionError(
                        f"candidate count changed between repeats for query {query!r}"
                    )

                candidate_generation_ns.append(candidate_elapsed)
                matcher_verification_ns.append(matcher_elapsed)
                ranking_ns.append(ranking_elapsed)
                data["candidate_generation_ns"].append(  # type: ignore[union-attr]
                    candidate_elapsed
                )
                data["matcher_verification_ns"].append(  # type: ignore[union-attr]
                    matcher_elapsed
                )
                data["ranking_ns"].append(ranking_elapsed)  # type: ignore[union-attr]

        if query_candidate_count is None:
            query_candidate_count = 0
        data["candidate_counts"].append(query_candidate_count)  # type: ignore[union-attr]
        if normalized_query:
            candidate_counts.append(query_candidate_count)

    bucket_report: dict[str, dict[str, Any]] = {}
    for bucket, data in bucket_data.items():
        bucket_report[bucket] = {
            "query_count": data["query_count"],
            "indexed_end_to_end_ms": _latency_stats(data["indexed_ns"]),
            "reference_end_to_end_ms": _latency_stats(data["reference_ns"]),
            "candidate_generation_ms": _latency_stats(data["candidate_generation_ns"]),
            "matcher_verification_ms": _latency_stats(data["matcher_verification_ns"]),
            "ranking_ms": _latency_stats(data["ranking_ns"]),
            "candidate_count": _stats(data["candidate_counts"]),
        }

    non_empty_query_count = sum(bool(query) for query in normalized_queries)
    safe_1char_count = sum(len(query) == 1 for query in normalized_queries)
    indexed_stats = _latency_stats(indexed_ns)
    reference_stats = _latency_stats(reference_ns)
    report: dict[str, Any] = {
        "condition": "warm",
        "latency_scope": "online queries after snapshot load and initialization",
        "latency_unit": "milliseconds",
        "p95_definition": "nearest-rank: sorted[ceil(0.95 * count) - 1]",
        "snapshot_path": str(snapshot_path),
        "seed": seed,
        "query_count_requested": query_count,
        "query_count_measured": len(queries),
        "repeats": repeats,
        "warmup_rounds": warmup_rounds,
        "record_count": len(records_by_id),
        "query_length_bucket_counts": {
            bucket: data["query_count"] for bucket, data in bucket_report.items()
        },
        "query_length_buckets": bucket_report,
        "safe_1char_fallback_count": safe_1char_count,
        "safe_1char_fallback_rate": _ratio(safe_1char_count, non_empty_query_count)
        or 0.0,
        "safe_1char_fallback_rate_denominator": "non-empty measured queries",
        "non_empty_query_count": non_empty_query_count,
        "indexed_end_to_end_ms": indexed_stats,
        "reference_end_to_end_ms": reference_stats,
        "candidate_generation_ms": _latency_stats(candidate_generation_ns),
        "matcher_verification_ms": _latency_stats(matcher_verification_ns),
        "ranking_ms": _latency_stats(ranking_ns),
        "candidate_count": _stats(candidate_counts),
        "candidate_count_denominator": "non-empty measured queries",
        "reference_vs_indexed_speedup": {
            "total_elapsed_ratio": _ratio(sum(reference_ns), sum(indexed_ns)),
            "total_elapsed_formula": (
                "reference_total_elapsed / indexed_total_elapsed"
            ),
            "median_latency_ratio": _ratio(
                float(reference_stats["median"]),
                float(indexed_stats["median"]),
            ),
            "median_latency_formula": (
                "reference_median_latency / indexed_median_latency"
            ),
            "interpretation": "a value greater than 1 means Indexed was faster",
        },
    }
    return report


def _format_number(value: int | float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.6f}"


def print_summary(report: dict[str, Any]) -> None:
    indexed = report["indexed_end_to_end_ms"]
    reference = report["reference_end_to_end_ms"]
    candidate = report["candidate_generation_ms"]
    matcher = report["matcher_verification_ms"]
    ranking = report["ranking_ms"]
    counts = report["candidate_count"]
    speedup = report["reference_vs_indexed_speedup"]

    print("Online Search Benchmark")
    print("condition = warm")
    print("latency scope = online queries after snapshot load and initialization")
    print(
        f"queries = {report['query_count_measured']} x {report['repeats']} repeats; "
        f"warm-up rounds = {report['warmup_rounds']} (excluded)"
    )
    print(
        "indexed end-to-end (ms): "
        f"median={_format_number(indexed['median'])} "
        f"p95={_format_number(indexed['p95'])} "
        f"mean={_format_number(indexed['mean'])}"
    )
    print(
        "reference end-to-end (ms): "
        f"median={_format_number(reference['median'])} "
        f"p95={_format_number(reference['p95'])} "
        f"mean={_format_number(reference['mean'])}"
    )
    print(
        "candidate generation (ms): "
        f"median={_format_number(candidate['median'])} "
        f"p95={_format_number(candidate['p95'])}"
    )
    print(
        "matcher verification (ms): "
        f"median={_format_number(matcher['median'])} "
        f"p95={_format_number(matcher['p95'])}"
    )
    print(
        "ranking (ms): "
        f"median={_format_number(ranking['median'])} "
        f"p95={_format_number(ranking['p95'])}"
    )
    print(
        "candidate count (non-empty queries): "
        f"mean={_format_number(counts['mean'])} "
        f"median={_format_number(counts['median'])}"
    )
    print("query-length buckets:")
    for bucket, bucket_stats in report["query_length_buckets"].items():
        bucket_indexed = bucket_stats["indexed_end_to_end_ms"]
        bucket_candidates = bucket_stats["candidate_count"]
        print(
            f"  {bucket}: queries={bucket_stats['query_count']} "
            f"indexed_median_ms={_format_number(bucket_indexed['median'])} "
            f"indexed_p95_ms={_format_number(bucket_indexed['p95'])} "
            f"candidate_mean={_format_number(bucket_candidates['mean'])}"
        )
    print(
        "safe 1-character fallback = "
        f"{report['safe_1char_fallback_count']}/"
        f"{report['non_empty_query_count']} "
        f"({_format_number(report['safe_1char_fallback_rate'])})"
    )
    print(
        "Reference-vs-Indexed speedup "
        "(reference_total_elapsed / indexed_total_elapsed) = "
        f"{_format_number(speedup['total_elapsed_ratio'])}"
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark warm online search latency after one-time snapshot "
            "initialization. Cold-query performance is not measured."
        )
    )
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--query-count", type=_positive_int, default=DEFAULT_QUERY_COUNT
    )
    parser.add_argument("--repeats", type=_positive_int, default=DEFAULT_REPEATS)
    parser.add_argument(
        "--warmup-rounds", type=_nonnegative_int, default=DEFAULT_WARMUP_ROUNDS
    )
    parser.add_argument("--json-output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.snapshot.exists():
        parser.error(f"snapshot path does not exist: {args.snapshot}")

    report = run_benchmark(
        args.snapshot,
        seed=args.seed,
        query_count=args.query_count,
        repeats=args.repeats,
        warmup_rounds=args.warmup_rounds,
    )
    print_summary(report)
    if args.json_output is not None:
        args.json_output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"JSON report written to {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
