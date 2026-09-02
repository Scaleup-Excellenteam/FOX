"""Enforce the generated-versus-retrieved response boundary."""

from autocomplete.incident_retrieval.schema import (
    IncidentRequest,
    IncidentResponse,
    RetrievedIncident,
    SearchConcepts,
)


def assemble(
    request: IncidentRequest,
    generated: SearchConcepts | None,
    retrieved: list[RetrievedIncident],
    used_fallback: bool,
) -> IncidentResponse:
    if used_fallback != (generated is None):
        raise ValueError(
            "used_fallback must exactly reflect whether concepts were generated"
        )
    if any(not item._verified_fox_result for item in retrieved):
        raise ValueError("retrieved incidents must originate from a FOX API result")
    return IncidentResponse(request, generated, list(retrieved), used_fallback)
