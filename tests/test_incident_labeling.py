import ast
from pathlib import Path

import pytest

from autocomplete.incident_retrieval.labeling import assemble
from autocomplete.incident_retrieval.schema import (
    IncidentRequest,
    RetrievedIncident,
    SearchConcepts,
)

REQUEST = IncidentRequest("SAT-07", "OPTICAL_LINK", "CRITICAL", "weak transfer")


def test_generated_term_cannot_be_inserted_as_retrieved_evidence() -> None:
    generated = SearchConcepts(["optical link degradation"])
    unverified = RetrievedIncident(
        "optical link degradation",
        "SAT-GEMINI",
        "invented.txt",
        1,
        100,
    )

    with pytest.raises(ValueError, match="FOX API"):
        assemble(REQUEST, generated, [unverified], False)


@pytest.mark.parametrize(
    ("generated", "used_fallback"),
    [(None, True), (SearchConcepts(["formation drift"]), False)],
)
def test_fallback_label_matches_generated_presence(generated, used_fallback) -> None:
    response = assemble(REQUEST, generated, [], used_fallback)

    assert response.used_fallback is used_fallback
    assert (response.generated is None) is used_fallback


@pytest.mark.parametrize(
    ("generated", "used_fallback"),
    [(None, False), (SearchConcepts(["formation drift"]), True)],
)
def test_inconsistent_fallback_label_is_rejected(generated, used_fallback) -> None:
    with pytest.raises(ValueError, match="used_fallback"):
        assemble(REQUEST, generated, [], used_fallback)


def test_package_has_no_actuation_dependency() -> None:
    package = Path(__file__).parents[1] / "src/autocomplete/incident_retrieval"
    imported_modules = set()
    for source in package.glob("*.py"):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

    assert not any(
        keyword in module
        for module in imported_modules
        for keyword in ("actuation", "thruster", "orbit_command", "recovery_action")
    )
