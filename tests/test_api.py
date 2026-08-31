import pytest

import autocomplete.api as api_module
from autocomplete.api import (
    EngineNotInitializedError,
    configure_default_engine,
    get_best_k_completions,
)
from autocomplete.models import AutoCompleteData


class FakeEngine:
    def __init__(self, return_value: list[AutoCompleteData] | None = None) -> None:
        self.return_value = return_value if return_value is not None else []
        self.calls: list[tuple[str, int]] = []

    def search(self, prefix: str, k: int = 5) -> list[AutoCompleteData]:
        self.calls.append((prefix, k))
        return self.return_value


@pytest.fixture(autouse=True)
def isolate_default_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_module, "_default_engine", None)


def test_get_before_configuration_raises_engine_not_initialized_error() -> None:
    with pytest.raises(EngineNotInitializedError):
        get_best_k_completions("hello")


def test_initialization_error_is_runtime_error() -> None:
    assert issubclass(EngineNotInitializedError, RuntimeError)


def test_initialization_error_message_is_clear_and_non_empty() -> None:
    with pytest.raises(EngineNotInitializedError) as error:
        get_best_k_completions("hello")

    message = str(error.value)
    assert message
    assert "SearchEngine" in message
    assert "configured" in message


def test_configure_default_engine_stores_supplied_engine() -> None:
    engine = FakeEngine()

    configure_default_engine(engine)  # type: ignore[arg-type]

    assert api_module._default_engine is engine


def test_facade_delegates_original_prefix_and_k_five() -> None:
    engine = FakeEngine()
    configure_default_engine(engine)  # type: ignore[arg-type]

    get_best_k_completions("  Original Prefix  ")

    assert engine.calls == [("  Original Prefix  ", 5)]


def test_facade_returns_exact_engine_value() -> None:
    engine_result = [AutoCompleteData("result", "source.txt", 4, 10)]
    engine = FakeEngine(engine_result)
    configure_default_engine(engine)  # type: ignore[arg-type]

    result = get_best_k_completions("prefix")

    assert result is engine_result


@pytest.mark.parametrize("prefix", ["", "Hello, WORLD!"])
def test_prefix_is_delegated_unchanged(prefix: str) -> None:
    engine = FakeEngine()
    configure_default_engine(engine)  # type: ignore[arg-type]

    get_best_k_completions(prefix)

    assert engine.calls == [(prefix, 5)]


def test_configuring_second_engine_replaces_first_engine() -> None:
    first_engine = FakeEngine()
    second_result = [AutoCompleteData("second", "second.txt", 2, 8)]
    second_engine = FakeEngine(second_result)
    configure_default_engine(first_engine)  # type: ignore[arg-type]

    configure_default_engine(second_engine)  # type: ignore[arg-type]
    result = get_best_k_completions("later call")

    assert result is second_result
    assert first_engine.calls == []
    assert second_engine.calls == [("later call", 5)]


def test_multiple_calls_delegate_independently() -> None:
    engine = FakeEngine()
    configure_default_engine(engine)  # type: ignore[arg-type]

    get_best_k_completions("first")
    get_best_k_completions("second")
    get_best_k_completions("third")

    assert engine.calls == [("first", 5), ("second", 5), ("third", 5)]


def test_facade_does_not_mutate_returned_values() -> None:
    first = AutoCompleteData("First", "a.txt", 1, 10)
    second = AutoCompleteData("Second", "b.txt", 2, -3)
    engine_result = [first, second]
    original_values = [vars(item).copy() for item in engine_result]
    engine = FakeEngine(engine_result)
    configure_default_engine(engine)  # type: ignore[arg-type]

    result = get_best_k_completions("prefix")

    assert result is engine_result
    assert result[0] is first
    assert result[1] is second
    assert [vars(item) for item in result] == original_values


def test_search_engine_errors_propagate_without_recovery() -> None:
    class FailingEngine:
        def search(self, prefix: str, k: int = 5) -> list[AutoCompleteData]:
            raise LookupError("search failed")

    configure_default_engine(FailingEngine())  # type: ignore[arg-type]

    with pytest.raises(LookupError, match="search failed"):
        get_best_k_completions("prefix")
