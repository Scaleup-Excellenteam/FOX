"""Run expanded terms through the ordinary public FOX API."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from autocomplete.api import get_best_k_completions
from autocomplete.incident_retrieval.labeling import assemble
from autocomplete.incident_retrieval.query_expansion import ConceptsClient, expand
from autocomplete.incident_retrieval.schema import (
    IncidentRequest,
    IncidentResponse,
    RetrievedIncident,
)
from autocomplete.models import AutoCompleteData

SearchFunction = Callable[[str], list[AutoCompleteData]]


def search_all(
    terms: Iterable[str],
    search: SearchFunction = get_best_k_completions,
) -> list[RetrievedIncident]:
    best_by_source: dict[tuple[str, int, str], RetrievedIncident] = {}
    for term in terms:
        for result in search(term):
            incident = _from_fox(result)
            if incident is None:
                continue
            key = (incident.source_file, incident.line_number, incident.corpus_sentence)
            previous = best_by_source.get(key)
            if previous is None or incident.fox_score > previous.fox_score:
                best_by_source[key] = incident
    return sorted(
        best_by_source.values(),
        key=lambda item: (
            -item.fox_score,
            item.corpus_sentence,
            item.source_file,
            item.line_number,
        ),
    )


def retrieve_similar_incidents(
    request: IncidentRequest,
    client: ConceptsClient | None = None,
    search: SearchFunction = get_best_k_completions,
) -> IncidentResponse:
    expansion = expand(request, client)
    retrieved = search_all(expansion.terms, search)
    return assemble(
        request,
        expansion.generated,
        retrieved,
        expansion.used_fallback,
    )


def _from_fox(result: AutoCompleteData) -> RetrievedIncident | None:
    if not result.source_text or result.offset < 1:
        return None
    satellite_id = result.source_text.split("/", 1)[0].strip()
    if not satellite_id or satellite_id == result.source_text:
        return None
    return RetrievedIncident.from_fox_result(
        corpus_sentence=result.completed_sentence,
        satellite_id=satellite_id,
        source_file=result.source_text,
        line_number=result.offset,
        fox_score=result.score,
    )
