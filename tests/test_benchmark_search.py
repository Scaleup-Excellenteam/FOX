from __future__ import annotations

import ast
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import autocomplete.reference_engine as reference_engine_module
import autocomplete.search_engine as search_engine_module
import benchmarks.benchmark_search as benchmark
from autocomplete.models import AutoCompleteData, SentenceRecord


class FakeIndex:
    def __init__(
        self,
        candidate_ids: list[int] | Callable[[str], list[int]],
    ) -> None:
        self._candidate_ids = candidate_ids
        self.queries: list[str] = []

    def get_candidate_ids(self, normalized_query: str) -> list[int]:
        self.queries.append(normalized_query)
        if callable(self._candidate_ids):
            return list(self._candidate_ids(normalized_query))
        return list(self._candidate_ids)


class DurationTimer:
    """A deterministic timer that advances by one supplied duration per region."""

    def __init__(self, durations_ns: Iterator[int] | None = None) -> None:
        self._durations_ns = durations_ns or iter(lambda: 1_000_000, None)
        self._now = 0
        self._starting = True
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        if self._starting:
            self._starting = False
            return self._now
        self._now += next(self._durations_ns)
        self._starting = True
        return self._now


def make_record(sentence_id: int, normalized: str) -> SentenceRecord:
    return SentenceRecord(
        sentence_id=sentence_id,
        original=f"Original {sentence_id}",
        normalized=normalized,
        source_path="sentences.txt",
        line_number=sentence_id,
    )


@pytest.fixture
def records() -> dict[int, SentenceRecord]:
    return {
        1: make_record(1, "abcdefghij"),
        2: make_record(2, "klmnopqrst"),
    }


def patch_delegates(
    monkeypatch: pytest.MonkeyPatch,
    *,
    normalize: Callable[[str], str] = lambda text: text,
    translate: Callable[[str], str] = lambda text: text,
    matcher: Callable[[str, str], int | None] = lambda query, sentence: (
        len(query) if query in sentence else None
    ),
) -> None:
    monkeypatch.setattr(benchmark, "_normalize", normalize)
    monkeypatch.setattr(benchmark, "_translate", translate)
    monkeypatch.setattr(benchmark, "_match_and_score", matcher)
    monkeypatch.setattr(search_engine_module, "_normalize", normalize)
    monkeypatch.setattr(search_engine_module, "_translate", translate)
    monkeypatch.setattr(search_engine_module, "_match_and_score", matcher)
    monkeypatch.setattr(reference_engine_module, "_normalize", normalize)
    monkeypatch.setattr(reference_engine_module, "_translate", translate)
    monkeypatch.setattr(reference_engine_module, "_match_and_score", matcher)


def run_with_fakes(
    monkeypatch: pytest.MonkeyPatch,
    records: dict[int, SentenceRecord],
    index: FakeIndex,
    **kwargs: Any,
) -> dict[str, Any]:
    monkeypatch.setattr(benchmark, "_load_snapshot", lambda path: (records, index))
    patch_delegates(monkeypatch)
    return benchmark.run_benchmark(
        Path("snapshot"),
        query_count=kwargs.pop("query_count", 6),
        repeats=kwargs.pop("repeats", 1),
        warmup_rounds=kwargs.pop("warmup_rounds", 0),
        timer=kwargs.pop("timer", DurationTimer()),
        **kwargs,
    )


def test_statistics_helper_calculates_count_median_mean_and_nearest_rank_p95() -> None:
    stats = benchmark._stats(list(range(1, 21)))

    assert stats == {"count": 20, "median": 10.5, "p95": 19, "mean": 10.5}


def test_statistics_helper_handles_odd_median_and_empty_samples() -> None:
    assert benchmark._stats([9, 1, 5])["median"] == 5
    assert benchmark._stats([]) == {
        "count": 0,
        "median": 0.0,
        "p95": 0.0,
        "mean": 0.0,
    }


def test_p95_nearest_rank_behavior_is_deterministic() -> None:
    assert benchmark._stats([100, 1])["p95"] == 100
    assert benchmark._stats(list(range(100)))["p95"] == 94


def test_query_generation_is_deterministic_for_same_seed(
    records: dict[int, SentenceRecord],
) -> None:
    first = benchmark.generate_queries(records, seed=42, query_count=50)
    second = benchmark.generate_queries(records, seed=42, query_count=50)

    assert first == second


def test_different_seed_changes_generated_queries(
    records: dict[int, SentenceRecord],
) -> None:
    first = benchmark.generate_queries(records, seed=1, query_count=50)
    second = benchmark.generate_queries(records, seed=2, query_count=50)

    assert first != second


def test_query_generation_covers_all_frozen_length_buckets(
    records: dict[int, SentenceRecord],
) -> None:
    queries = benchmark.generate_queries(records, seed=7, query_count=6)

    assert {benchmark._query_bucket(query) for query in queries} == {
        "1",
        "2",
        "3",
        "4",
        "5",
        "6+",
    }


def test_query_generation_finishes_and_reduces_buckets_for_short_records() -> None:
    short_records = {1: make_record(1, "xy")}

    queries = benchmark.generate_queries(short_records, seed=5, query_count=25)

    assert len(queries) == 25
    assert all(query in "xy" for query in queries)
    assert {len(query) for query in queries} == {1, 2}


@pytest.mark.parametrize("records", [{}, {1: make_record(1, "")}])
def test_query_generation_rejects_empty_normalized_corpus(
    records: dict[int, SentenceRecord],
) -> None:
    with pytest.raises(ValueError, match="empty normalized corpus"):
        benchmark.generate_queries(records, query_count=1)


def test_snapshot_loads_exactly_once_for_whole_run(
    monkeypatch: pytest.MonkeyPatch,
    records: dict[int, SentenceRecord],
) -> None:
    loads: list[Path] = []
    index = FakeIndex(list(records))

    def load(path: Path) -> tuple[dict[int, SentenceRecord], FakeIndex]:
        loads.append(path)
        return records, index

    monkeypatch.setattr(benchmark, "_load_snapshot", load)
    patch_delegates(monkeypatch)

    benchmark.run_benchmark(
        Path("one-snapshot"),
        query_count=6,
        repeats=3,
        warmup_rounds=2,
        timer=DurationTimer(),
    )

    assert loads == [Path("one-snapshot")]


def test_engines_are_created_from_same_records_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {1: make_record(1, "abcdef")}
    index = FakeIndex([1])
    seen: list[tuple[str, object, object | None]] = []

    class Indexed:
        def __init__(self, received_records: object, received_index: object) -> None:
            seen.append(("indexed", received_records, received_index))

        def search(self, query: str, k: int = 5) -> list[AutoCompleteData]:
            return []

    class Reference:
        def __init__(self, received_records: object) -> None:
            seen.append(("reference", received_records, None))

        def search(self, query: str, k: int = 5) -> list[AutoCompleteData]:
            return []

    monkeypatch.setattr(benchmark, "SearchEngine", Indexed)
    monkeypatch.setattr(benchmark, "ReferenceEngine", Reference)
    monkeypatch.setattr(benchmark, "_load_snapshot", lambda path: (records, index))
    monkeypatch.setattr(benchmark, "_normalize", lambda query: query)
    monkeypatch.setattr(benchmark, "_match_and_score", lambda query, sentence: None)

    benchmark.run_benchmark(
        Path("snapshot"),
        query_count=1,
        repeats=1,
        warmup_rounds=0,
        timer=DurationTimer(),
    )

    assert seen == [("indexed", records, index), ("reference", records, None)]


def test_correctness_is_checked_before_first_timer_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {1: make_record(1, "abcdef")}
    index = FakeIndex([1])
    searches: list[str] = []

    class Engine:
        def __init__(self, *args: object) -> None:
            pass

        def search(self, query: str, k: int = 5) -> list[AutoCompleteData]:
            searches.append(query)
            return []

    def timer() -> int:
        assert len(searches) >= 2
        return clock()

    clock = DurationTimer()

    monkeypatch.setattr(benchmark, "SearchEngine", Engine)
    monkeypatch.setattr(benchmark, "ReferenceEngine", Engine)
    monkeypatch.setattr(benchmark, "_load_snapshot", lambda path: (records, index))
    monkeypatch.setattr(benchmark, "_normalize", lambda query: query)
    monkeypatch.setattr(benchmark, "_match_and_score", lambda query, sentence: None)

    benchmark.run_benchmark(
        Path("snapshot"),
        query_count=1,
        repeats=1,
        warmup_rounds=0,
        timer=timer,
    )


def test_correctness_mismatch_fails_loudly_with_query_before_timing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {1: make_record(1, "abcdef")}
    result = AutoCompleteData("different", "source", 1, 1)

    class Indexed:
        def __init__(self, *args: object) -> None:
            pass

        def search(self, query: str, k: int = 5) -> list[AutoCompleteData]:
            return [result]

    class Reference(Indexed):
        def search(self, query: str, k: int = 5) -> list[AutoCompleteData]:
            return []

    monkeypatch.setattr(benchmark, "SearchEngine", Indexed)
    monkeypatch.setattr(benchmark, "ReferenceEngine", Reference)
    monkeypatch.setattr(
        benchmark, "generate_queries", lambda *args, **kwargs: ["bad-query"]
    )
    monkeypatch.setattr(
        benchmark, "_load_snapshot", lambda path: (records, FakeIndex([1]))
    )

    with pytest.raises(AssertionError, match="bad-query"):
        benchmark.run_benchmark(
            Path("snapshot"),
            query_count=1,
            repeats=1,
            warmup_rounds=0,
            timer=lambda: pytest.fail("timing began before correctness passed"),
        )


def test_candidate_generation_receives_normalized_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {1: make_record(1, "irrelevant")}
    index = FakeIndex([])

    def normalize(text: str) -> str:
        return f"normalized-{text}"

    monkeypatch.setattr(benchmark, "_load_snapshot", lambda path: (records, index))
    monkeypatch.setattr(benchmark, "generate_queries", lambda *args, **kwargs: ["RAW"])
    patch_delegates(monkeypatch, normalize=normalize, matcher=lambda q, s: None)

    benchmark.run_benchmark(
        Path("snapshot"),
        query_count=1,
        repeats=1,
        warmup_rounds=0,
        timer=DurationTimer(),
    )

    assert index.queries
    assert set(index.queries) == {"normalized-RAW"}


def test_normalized_empty_query_never_calls_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {1: make_record(1, "abcdef")}
    index = FakeIndex(lambda query: pytest.fail(f"unexpected index query {query!r}"))
    monkeypatch.setattr(benchmark, "_load_snapshot", lambda path: (records, index))
    monkeypatch.setattr(benchmark, "generate_queries", lambda *args, **kwargs: ["!!!"])
    patch_delegates(monkeypatch, normalize=lambda text: "")

    report = benchmark.run_benchmark(
        Path("snapshot"),
        query_count=1,
        repeats=2,
        warmup_rounds=1,
        timer=DurationTimer(),
    )

    assert index.queries == []
    assert report["query_length_bucket_counts"]["empty"] == 1
    assert report["query_length_buckets"]["empty"]["candidate_count"]["mean"] == 0


def test_candidate_count_and_stage_latencies_are_recorded_separately(
    monkeypatch: pytest.MonkeyPatch,
    records: dict[int, SentenceRecord],
) -> None:
    report = run_with_fakes(
        monkeypatch,
        records,
        FakeIndex(list(records)),
        query_count=1,
        repeats=2,
    )

    assert report["candidate_count"] == {
        "count": 1,
        "median": 2,
        "p95": 2,
        "mean": 2.0,
    }
    assert report["candidate_generation_ms"]["count"] == 2
    assert report["matcher_verification_ms"]["count"] == 2
    assert report["ranking_ms"]["count"] == 2


def test_instrumentation_delegates_matcher_discards_none_and_keeps_legal_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {
        1: make_record(1, "discard"),
        2: make_record(2, "zero"),
        3: make_record(3, "negative"),
    }
    calls: list[tuple[str, str]] = []
    scores = {"discard": None, "zero": 0, "negative": -7}

    def matcher(query: str, sentence: str) -> int | None:
        calls.append((query, sentence))
        return scores[sentence]

    monkeypatch.setattr(benchmark, "_match_and_score", matcher)
    expected = [
        AutoCompleteData("Original 2", "sentences.txt", 2, 0),
        AutoCompleteData("Original 3", "sentences.txt", 3, -7),
    ]

    class Indexed:
        def search(self, query: str, k: int = 5) -> list[AutoCompleteData]:
            return expected

    count, *_ = benchmark._instrument_query(
        "raw",
        "normalized",
        records,
        FakeIndex([1, 2, 3]),
        Indexed(),
        DurationTimer(),
    )

    assert count == 3
    assert calls == [
        ("normalized", "discard"),
        ("normalized", "zero"),
        ("normalized", "negative"),
    ]


def test_instrumentation_reuses_production_rank_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {1: make_record(1, "sentence")}
    ranked: list[list[AutoCompleteData]] = []

    def rank(results: list[AutoCompleteData]) -> list[AutoCompleteData]:
        ranked.append(results)
        return results

    monkeypatch.setattr(benchmark, "_match_and_score", lambda query, sentence: 4)
    monkeypatch.setattr(benchmark, "rank_results", rank)

    class Indexed:
        def search(self, query: str, k: int = 5) -> list[AutoCompleteData]:
            return ranked[0]

    benchmark._instrument_query(
        "raw",
        "query",
        records,
        FakeIndex([1]),
        Indexed(),
        DurationTimer(),
    )

    assert len(ranked) == 1
    assert ranked[0][0].score == 4


def test_instrumented_top_five_mismatch_fails_with_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {1: make_record(1, "sentence")}
    monkeypatch.setattr(benchmark, "_match_and_score", lambda query, sentence: 1)

    class Indexed:
        def search(self, query: str, k: int = 5) -> list[AutoCompleteData]:
            return []

    with pytest.raises(AssertionError, match="drift-query"):
        benchmark._instrument_query(
            "drift-query",
            "drift-query",
            records,
            FakeIndex([1]),
            Indexed(),
            DurationTimer(),
        )


def test_safe_one_character_fallback_count_and_rate(
    monkeypatch: pytest.MonkeyPatch,
    records: dict[int, SentenceRecord],
) -> None:
    monkeypatch.setattr(
        benchmark,
        "generate_queries",
        lambda *args, **kwargs: ["a", "bb", "c", "dddd"],
    )
    report = run_with_fakes(
        monkeypatch,
        records,
        FakeIndex(list(records)),
        query_count=4,
    )

    assert report["safe_1char_fallback_count"] == 2
    assert report["safe_1char_fallback_rate"] == 0.5
    assert (
        report["safe_1char_fallback_rate_denominator"] == "non-empty measured queries"
    )


def test_bucket_level_candidate_count_statistics(
    monkeypatch: pytest.MonkeyPatch,
    records: dict[int, SentenceRecord],
) -> None:
    monkeypatch.setattr(
        benchmark,
        "generate_queries",
        lambda *args, **kwargs: ["a", "bb", "ccc", "dddd", "eeeee", "ffffff"],
    )
    index = FakeIndex(lambda query: list(records)[: 1 if len(query) < 4 else 2])

    report = run_with_fakes(monkeypatch, records, index, query_count=6)

    for bucket in ("1", "2", "3"):
        assert report["query_length_buckets"][bucket]["candidate_count"]["mean"] == 1
    for bucket in ("4", "5", "6+"):
        assert report["query_length_buckets"][bucket]["candidate_count"]["mean"] == 2


def test_warmups_are_excluded_and_repeats_control_measured_sample_count(
    monkeypatch: pytest.MonkeyPatch,
    records: dict[int, SentenceRecord],
) -> None:
    timer = DurationTimer()
    report = run_with_fakes(
        monkeypatch,
        records,
        FakeIndex(list(records)),
        query_count=2,
        repeats=3,
        warmup_rounds=4,
        timer=timer,
    )

    assert report["indexed_end_to_end_ms"]["count"] == 6
    assert report["reference_end_to_end_ms"]["count"] == 6
    assert report["candidate_generation_ms"]["count"] == 6
    assert report["warmup_rounds"] == 4
    assert timer.calls == 6 * 10


def test_same_ordered_query_set_is_used_for_indexed_and_reference_timing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {1: make_record(1, "abcdef")}
    queries = ["first", "second"]
    indexed_queries: list[str] = []
    reference_queries: list[str] = []

    class Indexed:
        def __init__(self, *args: object) -> None:
            pass

        def search(self, query: str, k: int = 5) -> list[AutoCompleteData]:
            indexed_queries.append(query)
            return []

    class Reference:
        def __init__(self, *args: object) -> None:
            pass

        def search(self, query: str, k: int = 5) -> list[AutoCompleteData]:
            reference_queries.append(query)
            return []

    monkeypatch.setattr(benchmark, "SearchEngine", Indexed)
    monkeypatch.setattr(benchmark, "ReferenceEngine", Reference)
    monkeypatch.setattr(benchmark, "generate_queries", lambda *args, **kwargs: queries)
    monkeypatch.setattr(
        benchmark,
        "_load_snapshot",
        lambda path: (records, FakeIndex(lambda query: pytest.fail("index called"))),
    )
    monkeypatch.setattr(benchmark, "_normalize", lambda query: "")

    benchmark.run_benchmark(
        Path("snapshot"),
        query_count=2,
        repeats=2,
        warmup_rounds=1,
        timer=DurationTimer(),
    )

    assert indexed_queries == reference_queries
    assert indexed_queries[-4:] == ["first", "first", "second", "second"]


def test_speedup_uses_reference_total_over_indexed_total(
    monkeypatch: pytest.MonkeyPatch,
    records: dict[int, SentenceRecord],
) -> None:
    # Per repeat: indexed=2ms, reference=6ms, then three 1ms stage regions.
    durations = iter([2_000_000, 6_000_000, 1_000_000, 1_000_000, 1_000_000] * 2)
    report = run_with_fakes(
        monkeypatch,
        records,
        FakeIndex(list(records)),
        query_count=1,
        repeats=2,
        timer=DurationTimer(durations),
    )

    speedup = report["reference_vs_indexed_speedup"]
    assert speedup["total_elapsed_ratio"] == 3.0
    assert speedup["median_latency_ratio"] == 3.0
    assert speedup["total_elapsed_formula"] == (
        "reference_total_elapsed / indexed_total_elapsed"
    )


def test_json_report_contains_reproducibility_metadata(
    monkeypatch: pytest.MonkeyPatch,
    records: dict[int, SentenceRecord],
) -> None:
    report = run_with_fakes(
        monkeypatch,
        records,
        FakeIndex(list(records)),
        query_count=6,
        repeats=2,
        warmup_rounds=3,
        seed=123,
    )

    assert report["condition"] == "warm"
    assert report["snapshot_path"] == "snapshot"
    assert report["seed"] == 123
    assert report["query_count_requested"] == 6
    assert report["query_count_measured"] == 6
    assert report["repeats"] == 2
    assert report["warmup_rounds"] == 3
    assert report["record_count"] == 2
    assert set(report["query_length_bucket_counts"]) == set(benchmark.BUCKET_NAMES)


def test_cli_writes_machine_readable_json_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    output = tmp_path / "report.json"
    report = {"condition": "warm", "seed": 11}
    monkeypatch.setattr(benchmark, "run_benchmark", lambda *args, **kwargs: report)
    monkeypatch.setattr(benchmark, "print_summary", lambda received: None)

    exit_code = benchmark.main(
        [
            "--snapshot",
            str(snapshot),
            "--seed",
            "11",
            "--json-output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8")) == report


@pytest.mark.parametrize(
    ("option", "value"),
    [("--query-count", "0"), ("--repeats", "-2"), ("--warmup-rounds", "-1")],
)
def test_invalid_cli_numeric_options_fail_clearly(
    tmp_path: Path,
    option: str,
    value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    with pytest.raises(SystemExit, match="2"):
        benchmark.main(["--snapshot", str(snapshot), option, value])

    assert "must be" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"query_count": 0}, "query_count"),
        ({"repeats": 0}, "repeats"),
        ({"warmup_rounds": -1}, "warmup_rounds"),
    ],
)
def test_run_benchmark_rejects_invalid_numeric_values_before_loading(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, int],
    message: str,
) -> None:
    monkeypatch.setattr(
        benchmark,
        "_load_snapshot",
        lambda path: pytest.fail("invalid configuration loaded the snapshot"),
    )

    with pytest.raises(ValueError, match=message):
        benchmark.run_benchmark(Path("snapshot"), **kwargs)


def test_benchmark_module_introduces_no_network_or_cloud_imports() -> None:
    source = Path(benchmark.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert imported_roots.isdisjoint(
        {"boto3", "google.cloud", "httpx", "requests", "urllib"}
    )
