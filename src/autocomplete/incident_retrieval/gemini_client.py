"""Thin, replaceable wrapper around one Gemini structured-output call."""

from __future__ import annotations

import os
from typing import Any

from autocomplete.incident_retrieval.schema import (
    IncidentRequest,
    SchemaValidationError,
    SearchConcepts,
)

DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiClientError(RuntimeError):
    """Base error exposed by the Gemini boundary."""


class GeminiResponseError(GeminiClientError):
    """Raised when Gemini did not return valid structured output."""


class GeminiRequestError(GeminiClientError):
    """Raised when the Gemini request could not complete."""


def build_prompt(request: IncidentRequest) -> str:
    return (
        "Propose 1 to 6 short lexical search phrases for finding verified historical "
        "incidents in FOX. Return JSON only. Do not diagnose, recommend actions, or "
        "invent an incident. Use only these fields from the current alert:\n"
        f"satellite_id: {request.satellite_id}\n"
        f"subsystem: {request.subsystem}\n"
        f"severity: {request.severity}\n"
        f"description: {request.description}"
    )


class GeminiClient:
    def __init__(self, sdk_client: Any, model: str = DEFAULT_MODEL) -> None:
        self._client = sdk_client
        self._model = model

    @classmethod
    def from_environment(cls) -> GeminiClient | None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None
        try:
            from google import genai
        except ImportError as error:
            raise GeminiRequestError("google-genai is unavailable") from error
        model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        return cls(genai.Client(api_key=api_key), model=model)

    def expand(self, request: IncidentRequest) -> SearchConcepts:
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=build_prompt(request),
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["search_terms"],
                        "properties": {
                            "search_terms": {
                                "type": "array",
                                "minItems": 1,
                                "items": {"type": "string"},
                            },
                            "incident_classification": {"type": ["string", "null"]},
                        },
                    },
                },
            )
        except Exception as error:
            raise GeminiRequestError("Gemini request failed") from error
        text = getattr(response, "text", None)
        if not isinstance(text, str):
            raise GeminiResponseError("Gemini response did not contain text")
        try:
            return SearchConcepts.from_json(text)
        except SchemaValidationError as error:
            raise GeminiResponseError(
                "Gemini returned invalid structured output"
            ) from error
