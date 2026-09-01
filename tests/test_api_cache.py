"""Integration tests for caching normalized completion queries."""

import pytest

import autocomplete.api as api_module
from autocomplete.api import (
    configure_default_engine,
    get_best_k_completions,
    get_query_cache_info,
)
from autocomplete.models import AutoCompleteData


class FakeEngine:
    def __init__(self, result: list[AutoCompleteData] | None = None) -> None:
        self.result = result if result is not None else []
        self.calls: list[tuple[str, int]] = []

    def search(self, prefix: str, k: int = 5) -> list[AutoCompleteData]:
        self.calls.append((prefix, k))
        return self.result


@pytest.fixture(autouse=True)
def isolate_default_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_module, "_default_engine", None)
    monkeypatch.setattr(api_module, "_query_cache", api_module.QueryResultCache())


def test_normalized_equivalent_queries_share_cache_entry() -> None:
    engine = FakeEngine([AutoCompleteData("result", "source.txt", 1, 5)])
    configure_default_engine(engine, query_cache_capacity=2)  # type: ignore[arg-type]

    first = get_best_k_completions(" Hello, WORLD! ")
    second = get_best_k_completions("hello world")

    assert first == second
    assert engine.calls == [(" Hello, WORLD! ", 5)]
    assert get_query_cache_info().size == 1
    assert get_query_cache_info().hits == 1
    assert get_query_cache_info().misses == 1


def test_cached_empty_results_are_cache_hits() -> None:
    engine = FakeEngine([])
    configure_default_engine(engine)  # type: ignore[arg-type]

    assert get_best_k_completions("missing") == []
    assert get_best_k_completions("MISSING!") == []

    assert engine.calls == [("missing", 5)]
    assert get_query_cache_info().hits == 1
    assert get_query_cache_info().misses == 1


def test_reconfiguring_engine_clears_cached_values_and_statistics() -> None:
    first_engine = FakeEngine([AutoCompleteData("first", "one.txt", 1, 1)])
    configure_default_engine(first_engine)  # type: ignore[arg-type]
    get_best_k_completions("query")
    get_best_k_completions("QUERY")

    second_engine = FakeEngine([AutoCompleteData("second", "two.txt", 2, 2)])
    configure_default_engine(second_engine)  # type: ignore[arg-type]

    assert get_query_cache_info().size == 0
    assert get_query_cache_info().hits == 0
    assert get_query_cache_info().misses == 0
    assert get_best_k_completions("query") == second_engine.result
    assert second_engine.calls == [("query", 5)]
    assert get_query_cache_info().misses == 1
