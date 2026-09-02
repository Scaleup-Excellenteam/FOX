"""Gemini-assisted query expansion over the public FOX search API.

This package is retrieval-only: it has no actuation or recovery-command path.
"""

from autocomplete.incident_retrieval.query_expansion import ExpansionResult, expand
from autocomplete.incident_retrieval.retrieval import retrieve_similar_incidents
from autocomplete.incident_retrieval.schema import (
    IncidentRequest,
    IncidentResponse,
    RetrievedIncident,
    SearchConcepts,
)

__all__ = [
    "ExpansionResult",
    "IncidentRequest",
    "IncidentResponse",
    "RetrievedIncident",
    "SearchConcepts",
    "expand",
    "retrieve_similar_incidents",
]
