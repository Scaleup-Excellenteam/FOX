#!/usr/bin/env python3
"""Measure required offline build, startup, index-size, and candidate metrics."""

import argparse
import json
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections import defaultdict
from pathlib import Path

from autocomplete.snapshot_loader import load_snapshot


def posting_memory_bytes(index):
    """Approximate owned posting/index storage without double-counting objects."""
    seen = set()

    def size(value):
        identifier = id(value)
        if identifier in seen:
            return 0
        seen.add(identifier)
        total = sys.getsizeof(value)
        if isinstance(value, dict):
            total += sum(size(key) + size(item) for key, item in value.items())
        elif isinstance(value, (tuple, list, set)):
            total += sum(size(item) for item in value)
        return total

    return size(index.postings) + size(index.all_sentence_ids)


def distribution(values):
    return {
        "min": min(values, default=0),
        "median": statistics.median(values) if values else 0,
        "mean": statistics.mean(values) if values else 0,
        "max": max(values, default=0),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("builder", type=Path)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("queries", nargs="*")
    args = parser.parse_args()
    started = time.perf_counter()
    result = subprocess.run(
        [str(args.builder), str(args.corpus), str(args.snapshot)],
        check=True,
        text=True,
        capture_output=True,
    )
    build_seconds = time.perf_counter() - started
    tracemalloc.start()
    started = time.perf_counter()
    records, index = load_snapshot(args.snapshot)
    load_seconds = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    queries = args.queries or ["a", "to", "to be", "network protocol"]
    counts = []
    latencies = []
    buckets = defaultdict(list)
    for query in queries:
        start = time.perf_counter_ns()
        candidates = index.get_candidate_ids(query)
        latency = time.perf_counter_ns() - start
        counts.append(len(candidates))
        latencies.append(latency)
        buckets[str(len(query))].append((len(candidates), latency))

    by_size = {}
    for size in (1, 2, 3):
        entries = [(gram, ids) for (order, gram), ids in index.postings.items() if order == size]
        lengths = [len(ids) for _, ids in entries]
        by_size[str(size)] = {
            "grams": len(entries),
            "posting_ids": sum(lengths),
            "serialized_estimate_bytes": sum(
                len(gram.encode()) + 8 * len(ids) for gram, ids in entries
            ),
            "posting_size_distribution": distribution(lengths),
        }
    by_query_length = {
        length: {
            "queries": len(values),
            "average_candidates": statistics.mean(count for count, _ in values),
            "average_latency_ns": statistics.mean(latency for _, latency in values),
        }
        for length, values in sorted(buckets.items(), key=lambda item: int(item[0]))
    }
    payload = {
        "builder_output": result.stdout.strip(),
        "corpus_file_count": sum(1 for path in args.corpus.rglob("*.txt") if path.is_file()),
        "corpus_sentence_count": len(records),
        "offline_build_seconds": build_seconds,
        "snapshot_bytes": sum(path.stat().st_size for path in args.snapshot.iterdir()),
        "record_shards": len(list(args.snapshot.glob("records-*.binpb"))),
        "index_shards": len(list(args.snapshot.glob("index-*.binpb"))),
        "load_seconds": load_seconds,
        "peak_python_load_bytes": peak,
        "runtime_index_bytes": posting_memory_bytes(index),
        "index_by_gram_size": by_size,
        "queries": len(queries),
        "average_candidates": statistics.mean(counts),
        "candidate_count_by_query_length": by_query_length,
        "candidate_reduction_ratio": 1 - statistics.mean(counts) / max(1, len(records)),
        "safe_fallback_rate": sum(len(query) == 1 for query in queries) / len(queries),
        "candidate_latency_ns": distribution(latencies),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
