from autocomplete.models import AutoCompleteData


def rank_results(results: list[AutoCompleteData]) -> list[AutoCompleteData]:
    return sorted(
        results,
        key=lambda result: (
            -result.score,
            result.completed_sentence,
            result.source_text,
            result.offset,
        ),
    )
