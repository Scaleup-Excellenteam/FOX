import re

import pytest

from autocomplete.index import SearchIndex
from autocomplete.models import SentenceRecord
from autocomplete.observability import event, reset_for_tests
from autocomplete.search_engine import SearchEngine


@pytest.fixture(autouse=True)
def isolated_logging(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DIRECTORY", str(tmp_path / "logs"))
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.delenv("LOG_QUERY_TEXT", raising=False)
    monkeypatch.delenv("DETAILED_PROFILING", raising=False)
    reset_for_tests()
    yield tmp_path / "logs"
    reset_for_tests()


def _engine() -> SearchEngine:
    record = SentenceRecord(1, "Hello world", "hello world", "tiny.txt", 1)
    return SearchEngine({1: record}, SearchIndex({(3, "hel"): [1]}, [1]))


def test_log_families_are_isolated_and_directory_is_created(isolated_logging):
    event("offline", "build.started", build_id="abc")
    event("runtime", "search.completed", request_id="def")
    offline = (isolated_logging / "offline.log").read_text()
    runtime = (isolated_logging / "runtime.log").read_text()
    assert "build.started" in offline and "search.completed" not in offline
    assert "search.completed" in runtime and "build.started" not in runtime
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z \| INFO \|", offline)


def test_level_and_independent_bounded_rotation(monkeypatch, isolated_logging):
    monkeypatch.setenv("LOG_MAX_BYTES", "120")
    monkeypatch.setenv("LOG_BACKUP_COUNT", "2")
    reset_for_tests()
    for number in range(20):
        event("offline", "build.progress", number=number, payload="x" * 50)
    event("runtime", "search.completed", request_id="still-active")
    assert (isolated_logging / "offline.log.1").exists()
    assert len(list(isolated_logging.glob("offline.log.*"))) <= 2
    assert not list(isolated_logging.glob("runtime.log.*"))
    event("offline", "build.after_rotation")
    assert "build.after_rotation" in (isolated_logging / "offline.log").read_text()
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    reset_for_tests()
    event("runtime", "ignored.info")
    assert "ignored.info" not in (isolated_logging / "runtime.log").read_text()


def test_query_is_private_and_metrics_are_consistent(isolated_logging):
    results = _engine().search("hel")
    line = (isolated_logging / "runtime.log").read_text()
    assert len(results) == 1
    assert "query_text=" not in line
    assert "matcher_sample_count=" not in line
    assert "candidates=1" in line
    checked = int(re.search(r"candidates_checked=(\d+)", line).group(1))
    valid = int(re.search(r"valid_matches=(\d+)", line).group(1))
    rejected = int(re.search(r"rejected_candidates=(\d+)", line).group(1))
    assert valid + rejected == checked


def test_query_opt_in_and_bounded_detailed_profile(monkeypatch, isolated_logging):
    monkeypatch.setenv("LOG_QUERY_TEXT", "true")
    monkeypatch.setenv("DETAILED_PROFILING", "true")
    reset_for_tests()
    _engine().search("x")
    line = (isolated_logging / "runtime.log").read_text()
    assert "query_text=x" in line
    assert "matcher_sample_count=1" in line


def test_zero_candidate_metrics(isolated_logging):
    SearchEngine({}, SearchIndex({}, [])).search("missing")
    line = (isolated_logging / "runtime.log").read_text()
    assert "candidates=0" in line
    assert "average_candidate_us=0.000" in line


def test_success_event_has_complete_disjoint_runtime_metrics(isolated_logging):
    _engine().search("hel")
    line = (isolated_logging / "runtime.log").read_text()
    required = (
        "candidate_iterator_creation_ms",
        "candidate_iteration_ms",
        "candidate_lookup_ms",
        "exact_match_check_ms",
        "exact_checks",
        "exact_matches_accepted",
        "matcher_calls",
        "matcher_ms",
        "matcher_valid",
        "matcher_rejected",
        "top_k_bound_checks",
        "pruned_candidates",
        "result_construction_ms",
        "ranking_ms",
        "unaccounted_ms",
        "total_ms",
    )
    assert all(f"{field}=" in line for field in required)

    def value(field):
        return int(re.search(rf"{field}=(\d+)", line).group(1))

    assert value("candidates_checked") == (
        value("exact_matches_accepted") + value("matcher_calls")
    )
    assert value("valid_matches") == (
        value("exact_matches_accepted") + value("matcher_valid")
    )
    assert value("matcher_calls") == (
        value("matcher_valid") + value("matcher_rejected")
    )
