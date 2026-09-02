import sys
from types import ModuleType, SimpleNamespace

import pytest

from autocomplete.incident_retrieval.gemini_client import (
    DEFAULT_MODEL,
    GeminiClient,
    GeminiRequestError,
    GeminiResponseError,
)
from autocomplete.incident_retrieval.schema import IncidentRequest, SearchConcepts


@pytest.fixture
def incident_request() -> IncidentRequest:
    return IncidentRequest("SAT-07", "OPTICAL_LINK", "CRITICAL", "weak transfer")


class Models:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def test_mocked_success_returns_parsed_concepts(
    incident_request: IncidentRequest,
) -> None:
    models = Models(
        SimpleNamespace(
            text='{"search_terms":["optical link degradation"],'
            '"incident_classification":"LINK"}'
        )
    )

    result = GeminiClient(SimpleNamespace(models=models)).expand(incident_request)

    assert result == SearchConcepts(["optical link degradation"], "LINK")
    assert models.calls[0]["config"]["response_mime_type"] == "application/json"


def test_mocked_malformed_json_raises_parse_error(
    incident_request: IncidentRequest,
) -> None:
    client = GeminiClient(
        SimpleNamespace(models=Models(SimpleNamespace(text="not-json")))
    )

    with pytest.raises(GeminiResponseError):
        client.expand(incident_request)


def test_mocked_network_error_is_wrapped(incident_request: IncidentRequest) -> None:
    client = GeminiClient(
        SimpleNamespace(models=Models(error=TimeoutError("network timeout")))
    )

    with pytest.raises(GeminiRequestError) as error:
        client.expand(incident_request)

    assert isinstance(error.value.__cause__, TimeoutError)


def test_environment_client_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert GeminiClient.from_environment() is None


@pytest.mark.parametrize(
    ("configured_model", "expected_model"),
    [("gemini-custom", "gemini-custom"), (None, DEFAULT_MODEL)],
)
def test_environment_client_uses_configured_model(
    monkeypatch: pytest.MonkeyPatch,
    configured_model: str | None,
    expected_model: str,
) -> None:
    sdk_client = object()
    google_module = ModuleType("google")
    google_module.genai = SimpleNamespace(Client=lambda *, api_key: sdk_client)
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    if configured_model is None:
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
    else:
        monkeypatch.setenv("GEMINI_MODEL", configured_model)

    client = GeminiClient.from_environment()

    assert client is not None
    assert client._client is sdk_client
    assert client._model == expected_model
