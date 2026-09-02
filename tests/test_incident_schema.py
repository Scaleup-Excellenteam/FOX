import json

import pytest

from autocomplete.incident_retrieval.schema import (
    IncidentRequest,
    SchemaValidationError,
)


def test_well_formed_request_round_trips_through_json() -> None:
    request = IncidentRequest(
        "SAT-07",
        "OPTICAL_LINK",
        "CRITICAL",
        "throughput dropped after relative position drift",
    )

    assert IncidentRequest.from_json(request.to_json()) == request


def test_missing_request_field_is_rejected() -> None:
    payload = json.dumps(
        {"satellite_id": "SAT-07", "subsystem": "ORBIT", "severity": "WARNING"}
    )

    with pytest.raises(SchemaValidationError):
        IncidentRequest.from_json(payload)


def test_extra_request_field_is_rejected() -> None:
    payload = json.dumps(
        {
            "satellite_id": "SAT-07",
            "subsystem": "ORBIT",
            "severity": "WARNING",
            "description": "drift",
            "command": "fire thrusters",
        }
    )

    with pytest.raises(SchemaValidationError):
        IncidentRequest.from_json(payload)
