from collections.abc import Callable

import pytest

import autocomplete.cli as cli_module
from autocomplete.api import EngineNotInitializedError
from autocomplete.models import AutoCompleteData


class StubInput:
    def __init__(self, events: list[str | BaseException]) -> None:
        self._events = iter(events)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        event = next(self._events)
        if isinstance(event, BaseException):
            raise event
        return event


def result(
    completed_sentence: str,
    *,
    source_text: str = "sentences.txt",
    offset: int = 1,
    score: int = 10,
) -> AutoCompleteData:
    return AutoCompleteData(completed_sentence, source_text, offset, score)


def patch_api(
    monkeypatch: pytest.MonkeyPatch,
    function: Callable[[str], list[AutoCompleteData]],
) -> None:
    monkeypatch.setattr(cli_module, "get_best_k_completions", function)


def record_api_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    patch_api(monkeypatch, lambda prefix: calls.append(prefix) or [])
    return calls


def test_first_fragment_searches_exactly_that_fragment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = record_api_calls(monkeypatch)

    cli_module.run_cli(StubInput(["hel", EOFError()]), lambda line: None)

    assert calls == ["hel"]


def test_second_fragment_is_appended_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = record_api_calls(monkeypatch)

    cli_module.run_cli(StubInput(["hel", "lo", EOFError()]), lambda line: None)

    assert calls == ["hel", "hello"]


def test_spaces_are_not_automatically_inserted_between_fragments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = record_api_calls(monkeypatch)

    cli_module.run_cli(StubInput(["hello", "world", EOFError()]), lambda line: None)

    assert calls == ["hello", "helloworld"]


def test_typed_space_is_preserved_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = record_api_calls(monkeypatch)

    cli_module.run_cli(
        StubInput(["hello", " world", EOFError()]),
        lambda line: None,
    )

    assert calls == ["hello", "hello world"]


def test_case_punctuation_and_whitespace_fragments_are_appended_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = record_api_calls(monkeypatch)

    cli_module.run_cli(
        StubInput(["  Hello,", " WORLD!  ", EOFError()]),
        lambda line: None,
    )

    assert calls == ["  Hello,", "  Hello, WORLD!  "]


def test_reset_clears_accumulated_state_without_searching_or_terminating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = record_api_calls(monkeypatch)

    cli_module.run_cli(
        StubInput(["hel", "#", "wor", EOFError()]),
        lambda line: None,
    )

    assert calls == ["hel", "wor"]
    assert "#" not in calls


def test_multiple_resets_each_restore_initial_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = record_api_calls(monkeypatch)

    cli_module.run_cli(
        StubInput(["a", "#", "#", "b", "#", "c", EOFError()]),
        lambda line: None,
    )

    assert calls == ["a", "b", "c"]


def test_only_exact_hash_fragment_resets_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = record_api_calls(monkeypatch)

    cli_module.run_cli(
        StubInput(["abc#", "#suffix", "#", "new", EOFError()]),
        lambda line: None,
    )

    assert calls == ["abc#", "abc##suffix", "new"]


def test_empty_fragment_queries_current_accumulated_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = record_api_calls(monkeypatch)

    cli_module.run_cli(StubInput(["hel", "", EOFError()]), lambda line: None)

    assert calls == ["hel", "hel"]


def test_results_are_displayed_in_returned_order_with_all_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_results = [
        result("First completion", source_text="first.txt", offset=7, score=25),
        result("Second completion", source_text="second.txt", offset=9, score=-4),
    ]
    patch_api(monkeypatch, lambda prefix: api_results)
    output: list[str] = []

    cli_module._process_query("query", output.append)

    assert len(output) == 2
    assert "First completion" in output[0]
    assert "first.txt" in output[0]
    assert "7" in output[0]
    assert "25" in output[0]
    assert "Second completion" in output[1]
    assert "second.txt" in output[1]
    assert "9" in output[1]
    assert "-4" in output[1]


def test_duplicate_completions_are_both_displayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_results = [
        result("duplicate", source_text="a.txt"),
        result("duplicate", source_text="b.txt"),
    ]
    patch_api(monkeypatch, lambda prefix: api_results)
    output: list[str] = []

    cli_module._process_query("query", output.append)

    assert len(output) == 2
    assert "duplicate" in output[0]
    assert "duplicate" in output[1]
    assert "a.txt" in output[0]
    assert "b.txt" in output[1]


def test_empty_api_result_displays_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_api(monkeypatch, lambda prefix: [])
    output: list[str] = []

    cli_module._process_query("query", output.append)

    assert len(output) == 1
    assert "no completions" in output[0].lower()


def test_empty_input_is_delegated_to_official_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = record_api_calls(monkeypatch)

    cli_module.run_cli(StubInput(["", EOFError()]), lambda line: None)

    assert calls == [""]


def test_eof_exits_cleanly_without_api_call(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_api(monkeypatch, lambda prefix: pytest.fail("unexpected API call"))

    cli_module.run_cli(StubInput([EOFError()]), lambda line: None)


def test_keyboard_interrupt_exits_cleanly_without_api_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_api(monkeypatch, lambda prefix: pytest.fail("unexpected API call"))

    cli_module.run_cli(StubInput([KeyboardInterrupt()]), lambda line: None)


def test_engine_not_initialized_error_is_displayed_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def api(prefix: str) -> list[AutoCompleteData]:
        raise EngineNotInitializedError("default SearchEngine is not configured")

    patch_api(monkeypatch, api)
    output: list[str] = []

    cli_module.run_cli(StubInput(["query"]), output.append)

    assert len(output) == 1
    assert "error" in output[0].lower()
    assert "SearchEngine" in output[0]
    assert "not configured" in output[0]


def test_unexpected_api_exception_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def api(prefix: str) -> list[AutoCompleteData]:
        raise RuntimeError("unexpected failure")

    patch_api(monkeypatch, api)

    with pytest.raises(RuntimeError, match="unexpected failure"):
        cli_module.run_cli(StubInput(["query"]), lambda line: None)
