import subprocess
import sys

from autocomplete.incident_retrieval.retrieval import retrieve_similar_incidents
from autocomplete.incident_retrieval.schema import IncidentRequest, SearchConcepts
from autocomplete.search_engine import SearchEngine
from autocomplete.snapshot_loader import load_snapshot


class MockGemini:
    def expand(self, request: IncidentRequest) -> SearchConcepts:
        return SearchConcepts(["optical link degradation", "formation drift"])


def test_mocked_gemini_end_to_end_with_real_snapshot(
    tmp_path, reference_builder
) -> None:
    corpus = tmp_path / "corpus"
    source = corpus / "sat_03" / "communication_logs.txt"
    source.parent.mkdir(parents=True)
    source.write_text(
        "Nominal communication state\n"
        "Formation drift caused optical link degradation\n"
        "Battery cycle complete\n"
    )
    snapshot = tmp_path / "snapshot"
    subprocess.run(
        [sys.executable, str(reference_builder), str(corpus), str(snapshot)],
        check=True,
        capture_output=True,
        text=True,
    )
    records, index = load_snapshot(snapshot)
    engine = SearchEngine(records, index)
    request = IncidentRequest(
        "SAT-07",
        "OPTICAL_LINK",
        "CRITICAL",
        "The satellites moved apart and data transfer became weak.",
    )

    plain = retrieve_similar_incidents(request, search=engine.search)
    response = retrieve_similar_incidents(request, MockGemini(), engine.search)

    assert plain.retrieved == []
    assert response.generated == SearchConcepts(
        ["optical link degradation", "formation drift"]
    )
    assert response.used_fallback is False
    assert [
        (item.corpus_sentence, item.source_file, item.line_number)
        for item in response.retrieved
    ] == [
        (
            "Formation drift caused optical link degradation",
            "sat_03/communication_logs.txt",
            2,
        )
    ]
    assert all(
        item.corpus_sentence not in response.generated.search_terms
        for item in response.retrieved
    )
