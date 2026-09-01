from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import pytest

import autocomplete.observability as observability
import autocomplete.search_engine as search_engine_module
from autocomplete.index import SearchIndex
from autocomplete.models import SentenceRecord
from autocomplete.observability import (
    event,
    get_config,
    reset_for_tests,
    safe_reason,
    short_id,
)
from autocomplete.ranking import TopKSelector
from autocomplete.search_engine import SearchEngine


@pytest.fixture(autouse=True)
def isolated_logging(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DIRECTORY", str(tmp_path / "logs"))
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.delenv("LOG_QUERY_TEXT", raising=False)
    monkeypatch.delenv("DETAILED_PROFILING", raising=False)
    monkeypatch.delenv("LOG_MAX_BYTES", raising=False)
    monkeypatch.delenv("LOG_BACKUP_COUNT", raising=False)
    reset_for_tests()
    yield tmp_path / "logs"
    reset_for_tests()


def _engine() -> SearchEngine:
    record = SentenceRecord(1, "Hello world", "hello world", "tiny.txt", 1)
    return SearchEngine({1: record}, SearchIndex({(3, "hel"): [1]}, [1]))


def _events(directory, kind="runtime"):
    return [
        json.loads(line)
        for line in (directory / f"{kind}.log").read_text().splitlines()
    ]


def test_log_families_are_isolated_utc_json_and_directory_is_created(
    isolated_logging,
):
    event("offline", "build.started", build_id="abc")
    event("runtime", "search.completed", request_id="def")

    offline = _events(isolated_logging, "offline")
    runtime = _events(isolated_logging)
    assert [value["event"] for value in offline] == ["build.started"]
    assert [value["event"] for value in runtime] == ["search.completed"]
    timestamp = datetime.fromisoformat(offline[0]["timestamp_utc"])
    assert timestamp.utcoffset().total_seconds() == 0
    assert offline[0]["level"] == "INFO"


def test_independent_rotation_is_bounded_for_both_logs(monkeypatch, isolated_logging):
    monkeypatch.setenv("LOG_MAX_BYTES", "500")
    monkeypatch.setenv("LOG_BACKUP_COUNT", "2")
    reset_for_tests()
    for number in range(40):
        event("offline", "build.progress", number=number, payload="x" * 80)
        event("runtime", "search.progress", number=number, payload="x" * 80)

    for kind in ("offline", "runtime"):
        assert (isolated_logging / f"{kind}.log").exists()
        backups = list(isolated_logging.glob(f"{kind}.log.*"))
        assert 0 < len(backups) <= 2
        assert len([isolated_logging / f"{kind}.log", *backups]) <= 3
        assert (isolated_logging / f"{kind}.log").stat().st_size <= 500


@pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
def test_non_positive_or_invalid_backup_count_uses_bounded_default(
    monkeypatch, isolated_logging, value
):
    monkeypatch.setenv("LOG_MAX_BYTES", "300")
    monkeypatch.setenv("LOG_BACKUP_COUNT", value)
    reset_for_tests()
    assert get_config().backup_count == 5
    for number in range(30):
        event("runtime", "large", number=number, payload="x" * 80)
    assert 0 < len(list(isolated_logging.glob("runtime.log.*"))) <= 5


def test_query_is_private_and_normal_metrics_are_consistent(isolated_logging):
    results = _engine().search("hel")
    value = _events(isolated_logging)[-1]

    assert len(results) == 1
    assert "query_text" not in value
    assert value["candidates"] == 1
    assert value["candidates_checked"] == (
        value["exact_matches_accepted"] + value["matcher_calls"]
    )
    assert (
        value["valid_matches"] + value["rejected_candidates"]
        == value["candidates_checked"]
    )
    assert value["detailed_profiling"] is False
    assert "candidate_lookup_ms" not in value
    assert "matcher_sample_count" not in value
    assert "total_ms" not in value
    assert value["search_compute_ms"] >= 0


def test_query_opt_in_and_bounded_detailed_profile(monkeypatch, isolated_logging):
    monkeypatch.setenv("LOG_QUERY_TEXT", "true")
    monkeypatch.setenv("DETAILED_PROFILING", "true")
    reset_for_tests()
    _engine().search("x")
    value = _events(isolated_logging)[-1]

    assert value["query_text"] == "x"
    assert value["detailed_profiling"] is True
    assert value["matcher_sample_count"] == 1
    assert value["matcher_sample_count"] <= 1024
    assert value["candidate_lookup_ms"] >= 0
    assert value["matcher_sample_total_ms"] >= 0
    assert value["profile_unaccounted_ms"] >= 0


def test_detailed_matcher_sample_is_capped_at_1024(monkeypatch, isolated_logging):
    monkeypatch.setenv("DETAILED_PROFILING", "true")
    reset_for_tests()
    records = {
        number: SentenceRecord(number, "hello", "hello", "tiny.txt", number)
        for number in range(1, 1501)
    }
    SearchEngine(records, SearchIndex({}, records)).search("j", k=2000)
    value = _events(isolated_logging)[-1]
    assert value["matcher_calls"] == 1500
    assert value["matcher_sample_count"] == 1024


def test_zero_candidate_metrics(isolated_logging):
    SearchEngine({}, SearchIndex({}, [])).search("missing")
    value = _events(isolated_logging)[-1]
    assert value["candidates"] == 0
    assert value["matcher_calls"] == 0
    assert value["results_returned"] == 0


def test_off_path_has_no_observability_side_effects_or_timers(
    monkeypatch, isolated_logging
):
    monkeypatch.setenv("LOG_LEVEL", "OFF")
    reset_for_tests()

    def forbidden(*args, **kwargs):
        raise AssertionError("OFF executed observability work")

    monkeypatch.setattr(observability.uuid, "uuid4", forbidden)
    monkeypatch.setattr(search_engine_module.time, "perf_counter_ns", forbidden)
    assert [value.completed_sentence for value in _engine().search("hel")] == [
        "Hello world"
    ]
    assert not isolated_logging.exists()


def test_normal_uses_call_level_clocks_and_detailed_enables_granular_clocks(
    monkeypatch, isolated_logging
):
    class Clock:
        def __init__(self):
            self.calls = 0

        def perf_counter_ns(self):
            self.calls += 1
            return self.calls * 1000

    normal_clock = Clock()
    monkeypatch.setattr(search_engine_module, "time", normal_clock)
    _engine().search("x")
    assert normal_clock.calls == 2

    monkeypatch.setenv("DETAILED_PROFILING", "true")
    reset_for_tests()
    detailed_clock = Clock()
    monkeypatch.setattr(search_engine_module, "time", detailed_clock)
    _engine().search("x")
    assert detailed_clock.calls > 2


def test_reset_invalidates_cached_configuration(monkeypatch, isolated_logging):
    monkeypatch.setenv("LOG_LEVEL", "OFF")
    reset_for_tests()
    assert get_config().enables(logging.CRITICAL) is False
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    assert get_config().enables(logging.INFO) is False
    reset_for_tests()
    assert get_config().enables(logging.INFO) is True
    event("runtime", "after.reset")
    assert (isolated_logging / "runtime.log").exists()


def test_json_values_cannot_inject_fields_or_terminal_controls(isolated_logging):
    raw_value = 'value | forged=true\r\n\t\0\x1b[31m "quote" \\ slash שלום'
    event("runtime", "safe.value", payload=raw_value)
    raw_line = (isolated_logging / "runtime.log").read_text()
    parsed = json.loads(raw_line)

    assert parsed["payload"] == raw_value
    assert parsed["event"] == "safe.value"
    assert "\x1b" not in raw_line
    assert "\0" not in raw_line
    assert "\t" not in raw_line
    assert "\r" not in raw_line
    assert raw_line.count("\n") == 1
    assert "\\u0000" in raw_line and "\\u001b" in raw_line


def test_path_bearing_exception_is_reduced_to_reason_code():
    error = FileNotFoundError(2, "missing", "/secret/customer/input.txt")
    assert safe_reason(error) == "FileNotFoundError"


def test_correlation_identifier_uses_full_uuid_hex():
    assert re.fullmatch(r"[0-9a-f]{32}", short_id())


def test_event_emission_failure_is_non_fatal(monkeypatch):
    class BrokenLogger:
        def log(self, *args, **kwargs):
            raise OSError("injected write failure")

    monkeypatch.setattr(observability, "_logger", lambda *args: BrokenLogger())
    assert _engine().search("hel")[0].completed_sentence == "Hello world"


def test_directory_creation_and_rotation_failures_are_non_fatal(monkeypatch, tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked")
    monkeypatch.setenv("LOG_DIRECTORY", str(blocked / "logs"))
    reset_for_tests()
    assert _engine().search("hel")[0].completed_sentence == "Hello world"

    monkeypatch.setenv("LOG_DIRECTORY", str(tmp_path / "rotation"))
    monkeypatch.setenv("LOG_MAX_BYTES", "100")
    monkeypatch.setenv("LOG_BACKUP_COUNT", "1")
    reset_for_tests()
    monkeypatch.setattr(
        observability._SafeRotatingHandler,
        "doRollover",
        lambda self: (_ for _ in ()).throw(OSError("rotation")),
    )
    assert _engine().search("hel")[0].completed_sentence == "Hello world"


def test_invalid_level_and_max_bytes_configuration_fall_back(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "not-a-level")
    monkeypatch.setenv("LOG_MAX_BYTES", "0")
    reset_for_tests()
    config = get_config()
    assert config.level == logging.INFO
    assert config.max_bytes == 10 * 1024 * 1024


@pytest.mark.parametrize(
    ("case", "expected_stage", "expected_candidates", "expected_examined"),
    [
        ("validation", "input_validation", 0, 0),
        ("normalization", "normalization", 0, 0),
        ("iteration", "candidate_iteration", 1, 1),
        ("lookup", "candidate_lookup", 1, 0),
        ("matcher", "matcher", 1, 1),
        ("ranking", "ranking", 1, 1),
    ],
)
def test_search_failure_reports_real_stage_and_progress(
    monkeypatch,
    isolated_logging,
    case,
    expected_stage,
    expected_candidates,
    expected_examined,
):
    record = SentenceRecord(1, "hxllo world", "hxllo world", "tiny.txt", 1)

    class Index:
        def get_exact_candidate_ids(self, query):
            return ()

        def iter_candidate_ids(self, query):
            if case == "iteration":

                def values():
                    yield 1
                    raise RuntimeError("injected iteration failure")

                return values()
            return iter([99] if case == "lookup" else [1])

    engine = SearchEngine({1: record}, Index())
    expected_error = RuntimeError
    search_k = 5
    if case == "validation":
        search_k = -1
        expected_error = ValueError
    elif case == "lookup":
        expected_error = KeyError
    elif case == "normalization":
        monkeypatch.setattr(
            search_engine_module,
            "_normalize",
            lambda value: (_ for _ in ()).throw(RuntimeError("normalization")),
        )
    elif case == "matcher":
        monkeypatch.setattr(
            search_engine_module,
            "_match_and_score",
            lambda *args: (_ for _ in ()).throw(RuntimeError("matcher")),
        )
    elif case == "ranking":
        monkeypatch.setattr(
            TopKSelector,
            "results",
            lambda self: (_ for _ in ()).throw(RuntimeError("ranking")),
        )

    with pytest.raises(expected_error):
        engine.search("hello", search_k)
    value = _events(isolated_logging)[-1]
    assert value["event"] == "search.failed"
    assert value["failed_stage"] == expected_stage
    assert value["candidate_count"] == expected_candidates
    assert value["candidates_examined"] == expected_examined
    assert value["reason_code"] == expected_error.__name__
