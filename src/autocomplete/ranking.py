from operator import attrgetter

from autocomplete.models import AutoCompleteData


def rank_results(results: list[AutoCompleteData]) -> list[AutoCompleteData]:
    ranked = sorted(results, key=attrgetter("offset"))
    ranked.sort(key=attrgetter("source_text"))
    ranked.sort(key=attrgetter("completed_sentence"))
    ranked.sort(key=attrgetter("score"), reverse=True)
    return ranked
