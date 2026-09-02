from types import SimpleNamespace

from autocomplete.incident_retrieval.query_expansion import expand
from autocomplete.incident_retrieval.schema import IncidentRequest, SearchConcepts

REQUEST = IncidentRequest("SAT-07", "OPTICAL_LINK", "CRITICAL", "weak transfer")


class Client:
    def __init__(self, value=None, error: Exception | None = None) -> None:
        self.value = value
        self.error = error
        self.calls = 0

    def expand(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        return self.value


def test_valid_output_terms_are_used_as_is() -> None:
    concepts = SearchConcepts(["optical degradation", "formation drift"])

    result = expand(REQUEST, Client(concepts))

    assert result.terms == concepts.search_terms
    assert result.generated == concepts
    assert result.used_fallback is False


def test_client_error_falls_back_to_description() -> None:
    result = expand(REQUEST, Client(error=TimeoutError()))

    assert result.terms == [REQUEST.description]
    assert result.generated is None
    assert result.used_fallback is True


def test_invalid_client_schema_uses_same_fallback() -> None:
    result = expand(REQUEST, Client(SimpleNamespace(search_terms="not-a-list")))

    assert result.terms == [REQUEST.description]
    assert result.generated is None
    assert result.used_fallback is True


def test_unconfigured_client_falls_back_without_call() -> None:
    result = expand(REQUEST)

    assert result.terms == [REQUEST.description]
    assert result.used_fallback is True
