"""Median warm-query logging overhead comparison.

Run with: python -m benchmarks.benchmark_logging
"""

import os
import statistics
import tempfile
import time
from pathlib import Path

from autocomplete.index import SearchIndex
from autocomplete.models import SentenceRecord
from autocomplete.observability import reset_for_tests
from autocomplete.search_engine import SearchEngine


def _median(mode: str, detailed: bool, runs: int = 200) -> float:
    os.environ["LOG_LEVEL"] = mode
    os.environ["DETAILED_PROFILING"] = str(detailed).lower()
    reset_for_tests()
    records = {
        number: SentenceRecord(
            number, f"hello {number}", f"hello {number}", "bench.txt", number
        )
        for number in range(1, 1001)
    }
    index = SearchIndex({}, records)
    engine = SearchEngine(records, index)
    for _ in range(20):
        engine.search("h")
    values = []
    for _ in range(runs):
        started = time.perf_counter_ns()
        engine.search("h")
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    return statistics.median(values)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="fox-log-benchmark-") as temporary:
        os.environ["LOG_DIRECTORY"] = str(Path(temporary) / "logs")
        for label, level, detailed in (
            ("disabled", "OFF", False),
            ("normal", "INFO", False),
            ("detailed", "INFO", True),
        ):
            print(f"{label}_median_ms={_median(level, detailed):.3f}")


if __name__ == "__main__":
    main()
