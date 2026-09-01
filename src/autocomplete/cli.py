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
) -> None:
    results = get_best_k_completions(prefix)
    if not results:
        output("No completions found.")
        return

    for result in results:
        output(_format_result(result))


def run_cli(
    input_function: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> None:
    current_input = ""

    while True:
        try:
            fragment = input_function("Enter query: ")
            if fragment == "#":
                current_input = ""
                continue

            current_input += fragment
            _process_query(current_input, output)
        except (EOFError, KeyboardInterrupt):
            return
        except EngineNotInitializedError as error:
            output(f"Error: {error}")
            return
