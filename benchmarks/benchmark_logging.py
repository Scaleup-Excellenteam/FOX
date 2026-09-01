"""Diagnostic, interleaved logging-overhead comparison.

Run with: python -m benchmarks.benchmark_logging

Values are external end-to-end call durations. They are not stable CI assertions:
scheduler activity, filesystem cache, allocator state, and hardware all affect them.
"""

from __future__ import annotations

import json
import os
import random
import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from autocomplete.index import SearchIndex
from autocomplete.models import SentenceRecord
from autocomplete.observability import reset_for_tests
from autocomplete.search_engine import SearchEngine

_TRIALS = 7
_CALLS_PER_BLOCK = 40


def _engine() -> SearchEngine:
    records = {
        number: SentenceRecord(
            number,
            f"hello {number}",
            f"hello {number}",
            "bench.txt",
            number,
        )
        for number in range(1, 1001)
    }
    return SearchEngine(records, SearchIndex({}, records))


def _measure(call: Callable[[str], object], query: str, runs: int) -> list[float]:
    values = []
    for _ in range(runs):
        started = time.perf_counter_ns()
        call(query)
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    return values


def _configure(mode: str, directory: Path) -> None:
    os.environ["LOG_DIRECTORY"] = str(directory)
    os.environ["LOG_LEVEL"] = "OFF" if mode == "off" else "INFO"
    os.environ["DETAILED_PROFILING"] = str(mode == "detailed").lower()
    reset_for_tests()


def _confirm_detailed_samples(directory: Path) -> int:
    events = [
        json.loads(line)
        for line in (directory / "runtime.log").read_text().splitlines()
    ]
    samples = [
        value.get("matcher_sample_count", 0)
        for value in events
        if value["event"] == "search.completed"
    ]
    return max(samples, default=0)


def main() -> None:
    engine = _engine()
    workloads = {"exact": "h", "non_exact": "j", "zero_candidate": "zz"}
    modes = ("baseline", "off", "normal", "detailed")
    samples = {(mode, workload): [] for mode in modes for workload in workloads}
    blocks = [
        (trial, mode, workload)
        for trial in range(_TRIALS)
        for mode in modes
        for workload in workloads
    ]
    random.Random(0xF0_05).shuffle(blocks)

    with tempfile.TemporaryDirectory(prefix="fox-log-benchmark-") as temporary:
        root = Path(temporary)
        for trial, mode, workload in blocks:
            query = workloads[workload]
            directory = root / f"trial-{trial}" / mode
            if mode == "baseline":
                call = engine._search_fast
            else:
                _configure(mode, directory)
                call = engine.search
            for _ in range(5):
                call(query)
            samples[(mode, workload)].extend(_measure(call, query, _CALLS_PER_BLOCK))

        off_probe = root / "off-probe"
        _configure("off", off_probe)
        for query in workloads.values():
            assert engine.search(query) == engine._search_fast(query)
        if off_probe.exists():
            raise RuntimeError("LOG_LEVEL=OFF created a logging directory")

        detailed_probe = root / "detailed-probe"
        _configure("detailed", detailed_probe)
        engine.search(workloads["non_exact"])
        detailed_samples = _confirm_detailed_samples(detailed_probe)
        if detailed_samples <= 0:
            raise RuntimeError("detailed workload did not sample matcher calls")

        print(
            f"trials={_TRIALS} calls_per_block={_CALLS_PER_BLOCK} "
            f"detailed_matcher_sample_count={detailed_samples}"
        )
        for workload in workloads:
            baseline = statistics.median(samples[("baseline", workload)])
            for mode in modes:
                median = statistics.median(samples[(mode, workload)])
                ratio = median / baseline if baseline else 0.0
                print(
                    f"workload={workload} mode={mode} "
                    f"median_ms={median:.3f} overhead_ratio={ratio:.3f}"
                )
    reset_for_tests()


if __name__ == "__main__":
    main()
