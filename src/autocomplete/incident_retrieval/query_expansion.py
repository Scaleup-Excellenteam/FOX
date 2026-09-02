"""Trusted query-expansion boundary with deterministic FOX fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from autocomplete.incident_retrieval.schema import IncidentRequest, SearchConcepts


class ConceptsClient(Protocol):
    def expand(self, request: IncidentRequest) -> SearchConcepts: ...


@dataclass(frozen=True)
class ExpansionResult:
    terms: list[str]
    generated: SearchConcepts | None
    used_fallback: bool


def expand(
    request: IncidentRequest, client: ConceptsClient | None = None
) -> ExpansionResult:
    if client is None:
        return _fallback(request)
    try:
        concepts = client.expand(request)
        concepts = SearchConcepts(
            search_terms=concepts.search_terms,
            incident_classification=concepts.incident_classification,
        )
    except Exception:
        return _fallback(request)
    return ExpansionResult(list(concepts.search_terms), concepts, False)


def _fallback(request: IncidentRequest) -> ExpansionResult:
    return ExpansionResult([request.description], None, True)
