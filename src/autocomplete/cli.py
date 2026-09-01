import time
from collections.abc import Callable

from autocomplete.api import EngineNotInitializedError, get_best_k_completions
from autocomplete.models import AutoCompleteData


def _format_result(result: AutoCompleteData) -> str:
    return (
        f"{result.completed_sentence} | score: {result.score} | "
        f"source: {result.source_text} | offset: {result.offset}"
    )


def _process_query(
    prefix: str,
    output: Callable[[str], None] = print,
    *,
    show_timing: bool = False,
) -> None:
    started = time.perf_counter() if show_timing else None
    results = get_best_k_completions(prefix)
    elapsed_ms = (
        (time.perf_counter() - started) * 1_000 if started is not None else None
    )

    if not results:
        output("No completions found.")
    else:
        for result in results:
            output(_format_result(result))

    if elapsed_ms is not None:
        output(f"Search time: {elapsed_ms:.2f} ms")


def run_cli(
    input_function: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    *,
    show_timing: bool = False,
) -> None:
    current_input = ""

    while True:
        try:
            fragment = input_function("Enter query: ")
            if fragment == "#":
                current_input = ""
                continue

            current_input += fragment
            _process_query(current_input, output, show_timing=show_timing)
        except (EOFError, KeyboardInterrupt):
            return
        except EngineNotInitializedError as error:
            output(f"Error: {error}")
            return
