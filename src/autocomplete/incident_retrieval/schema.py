"""Strict data contracts for similar-incident retrieval."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


class SchemaValidationError(ValueError):
    """Raised when data does not match an incident-retrieval contract."""


def _require_exact_fields(
    value: object, required: set[str], optional: set[str] | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaValidationError("expected a JSON object")
    optional = optional or set()
    keys = set(value)
    missing = required - keys
    extra = keys - required - optional
    if missing or extra:
        raise SchemaValidationError(
            f"invalid fields: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return value


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class IncidentRequest:
    satellite_id: str
    subsystem: str
    severity: str
    description: str

    def __post_init__(self) -> None:
        for name in ("satellite_id", "subsystem", "severity", "description"):
            _required_string(getattr(self, name), name)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, payload: str) -> IncidentRequest:
        try:
            value = json.loads(payload)
        except (json.JSONDecodeError, TypeError) as error:
            raise SchemaValidationError("invalid incident request JSON") from error
        data = _require_exact_fields(
            value, {"satellite_id", "subsystem", "severity", "description"}
        )
        return cls(**data)


@dataclass(frozen=True)
class SearchConcepts:
    search_terms: list[str]
    incident_classification: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.search_terms, list) or not self.search_terms:
            raise SchemaValidationError("search_terms must be a non-empty list")
        if len(self.search_terms) > 6:
            raise SchemaValidationError("search_terms must contain at most 6 phrases")
        for term in self.search_terms:
            if not isinstance(term, str) or not term.strip():
                raise SchemaValidationError("search terms must be non-empty strings")
            if len(term) > 120:
                raise SchemaValidationError(
                    "search terms must be at most 120 characters"
                )
            if "\n" in term or term.count(" ") > 15:
                raise SchemaValidationError("search terms must be short phrases")
        if self.incident_classification is not None and not isinstance(
            self.incident_classification, str
        ):
            raise SchemaValidationError(
                "incident_classification must be a string or null"
            )

    @classmethod
    def from_json(cls, payload: str) -> SearchConcepts:
        try:
            value = json.loads(payload)
        except (json.JSONDecodeError, TypeError) as error:
            raise SchemaValidationError("invalid search concepts JSON") from error
        data = _require_exact_fields(
            value, {"search_terms"}, {"incident_classification"}
        )
        return cls(**data)


@dataclass(frozen=True)
class RetrievedIncident:
    corpus_sentence: str
    satellite_id: str
    source_file: str
    line_number: int
    fox_score: int
    _verified_fox_result: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _required_string(self.corpus_sentence, "corpus_sentence")
        _required_string(self.satellite_id, "satellite_id")
        _required_string(self.source_file, "source_file")
        if isinstance(self.line_number, bool) or not isinstance(self.line_number, int):
            raise SchemaValidationError("line_number must be an integer")
        if self.line_number < 1:
            raise SchemaValidationError("line_number must be positive")
        if isinstance(self.fox_score, bool) or not isinstance(self.fox_score, int):
            raise SchemaValidationError("fox_score must be an integer")

    @classmethod
    def from_fox_result(
        cls,
        *,
        corpus_sentence: str,
        satellite_id: str,
        source_file: str,
        line_number: int,
        fox_score: int,
    ) -> RetrievedIncident:
        return cls(
            corpus_sentence=corpus_sentence,
            satellite_id=satellite_id,
            source_file=source_file,
            line_number=line_number,
            fox_score=fox_score,
            _verified_fox_result=True,
        )


@dataclass(frozen=True)
class IncidentResponse:
    current_incident: IncidentRequest
    generated: SearchConcepts | None
    retrieved: list[RetrievedIncident]
    used_fallback: bool

    def __post_init__(self) -> None:
        if self.used_fallback != (self.generated is None):
            raise SchemaValidationError(
                "used_fallback must exactly reflect whether concepts were generated"
            )
        if any(not item._verified_fox_result for item in self.retrieved):
            raise SchemaValidationError(
                "retrieved incidents must originate from a FOX API result"
            )

    @property
    def evidence_message(self) -> str | None:
        if self.retrieved:
            return None
        return "No verified similar incident found."
