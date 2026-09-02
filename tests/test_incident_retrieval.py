from autocomplete.incident_retrieval.retrieval import retrieve_similar_incidents
from autocomplete.incident_retrieval.schema import IncidentRequest, SearchConcepts
from autocomplete.models import AutoCompleteData


class Client:
    def __init__(self, terms: list[str]) -> None:
        self.concepts = SearchConcepts(terms)

    def expand(self, request: IncidentRequest) -> SearchConcepts:
        return self.concepts


def test_identical_incident_is_found_without_gemini() -> None:
    request = IncidentRequest(
        "SAT-07", "OPTICAL_LINK", "CRITICAL", "optical link degradation"
    )

    response = retrieve_similar_incidents(
        request,
        search=lambda term: (
            [
                AutoCompleteData(
                    "Formation drift caused optical link degradation",
                    "sat_03/communication_logs.txt",
                    8,
                    48,
                )
            ]
            if term == request.description
            else []
        ),
    )

    assert [item.corpus_sentence for item in response.retrieved] == [
        "Formation drift caused optical link degradation"
    ]
    assert response.used_fallback is True


def test_expansion_surfaces_differently_worded_incident() -> None:
    request = IncidentRequest(
        "SAT-07",
        "OPTICAL_LINK",
        "CRITICAL",
        "The satellites moved apart and data transfer became weak.",
    )

    def search(term: str) -> list[AutoCompleteData]:
        if term == "optical link degradation":
            return [
                AutoCompleteData(
                    "Formation drift caused optical link degradation",
                    "sat_03/communication_logs.txt",
                    8,
                    48,
                )
            ]
        return []

    plain = retrieve_similar_incidents(request, search=search)
    expanded = retrieve_similar_incidents(
        request, Client(["optical link degradation"]), search
    )

    assert plain.retrieved == []
    assert len(expanded.retrieved) == 1
    assert expanded.generated == SearchConcepts(["optical link degradation"])


def test_no_match_returns_empty_evidence_not_fabrication() -> None:
    request = IncidentRequest("SAT-07", "TPU", "WARNING", "unknown anomaly")

    response = retrieve_similar_incidents(
        request, Client(["fabricated phrase"]), lambda _: []
    )

    assert response.retrieved == []
    assert response.evidence_message == "No verified similar incident found."


def test_results_always_have_real_source_and_line() -> None:
    request = IncidentRequest("SAT-07", "TPU", "WARNING", "temperature exceeded")
    source_result = AutoCompleteData(
        "TPU temperature exceeded the safe operating threshold",
        "sat_11/thermal.txt",
        4,
        40,
    )

    response = retrieve_similar_incidents(request, search=lambda _: [source_result])

    assert response.retrieved
    assert all(item.source_file for item in response.retrieved)
    assert all(item.line_number > 0 for item in response.retrieved)


def test_unattributed_search_results_are_dropped() -> None:
    request = IncidentRequest("SAT-07", "TPU", "WARNING", "temperature exceeded")
    invalid = [
        AutoCompleteData("sentence", "", 1, 10),
        AutoCompleteData("sentence", "source.txt", 0, 10),
        AutoCompleteData("sentence", "source.txt", 1, 10),
    ]

    response = retrieve_similar_incidents(request, search=lambda _: invalid)

    assert response.retrieved == []


def test_duplicate_corpus_hit_is_merged_using_best_real_score() -> None:
    request = IncidentRequest("SAT-07", "ORBIT", "WARNING", "drift")
    calls = 0

    def search(_: str) -> list[AutoCompleteData]:
        nonlocal calls
        calls += 1
        return [
            AutoCompleteData(
                "Formation drift caused optical link degradation",
                "sat_03/communication_logs.txt",
                8,
                calls,
            )
        ]

    response = retrieve_similar_incidents(
        request, Client(["drift", "formation"]), search
    )

    assert len(response.retrieved) == 1
    assert response.retrieved[0].fox_score == 2
