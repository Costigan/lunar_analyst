from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.app import create_app


def test_validation_error_uses_stage1_envelope() -> None:
    client = TestClient(create_app())
    response = client.post("/api/v1/jobs/add-one", json={})
    assert response.status_code == 422
    payload = response.json()
    assert set(payload.keys()) == {"code", "message", "details", "request_id"}
    assert payload["code"] == "invalid_request"
    assert isinstance(payload["details"], dict)


def test_not_found_error_uses_stage1_envelope() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/jobs/does-not-exist")
    assert response.status_code == 404
    payload = response.json()
    assert set(payload.keys()) == {"code", "message", "details", "request_id"}
    assert payload["code"] == "not_found"

