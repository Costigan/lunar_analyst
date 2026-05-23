from __future__ import annotations

from backend.api.app import create_app
from backend.contracts.events import STAGE1_WS_EVENT_NAMES, WsEnvelope
from backend.contracts.models import JobEventName


def test_openapi_includes_generated_job_routes() -> None:
    app = create_app()
    schema = app.openapi()
    paths = schema.get("paths", {})
    assert "/api/v1/jobs/generate-horizons" in paths
    assert "/api/v1/jobs/add-one" in paths
    assert "/api/v1/jobs/multiply" in paths
    assert "/api/v1/jobs/ping" in paths
    assert "/api/v1/jobs/echo-upper" in paths
    assert "/api/v1/jobs/raster-transform" in paths


def test_openapi_add_one_signature() -> None:
    app = create_app()
    schema = app.openapi()
    op = schema["paths"]["/api/v1/jobs/add-one"]["post"]
    body_schema = op["requestBody"]["content"]["application/json"]["schema"]
    assert body_schema["type"] == "number"


def test_openapi_multiply_signature() -> None:
    app = create_app()
    schema = app.openapi()
    op = schema["paths"]["/api/v1/jobs/multiply"]["post"]
    body_schema = op["requestBody"]["content"]["application/json"]["schema"]
    ref = body_schema["$ref"]
    ref_name = ref.split("/")[-1]
    model_schema = schema["components"]["schemas"][ref_name]
    assert model_schema["type"] == "object"
    assert set(model_schema["required"]) == {"a", "b"}
    assert model_schema["properties"]["a"]["type"] == "number"
    assert model_schema["properties"]["b"]["type"] == "number"


def test_openapi_generate_horizons_signature() -> None:
    app = create_app()
    schema = app.openapi()
    op = schema["paths"]["/api/v1/jobs/generate-horizons"]["post"]
    body_schema = op["requestBody"]["content"]["application/json"]["schema"]
    ref = body_schema["$ref"]
    ref_name = ref.split("/")[-1]
    model_schema = schema["components"]["schemas"][ref_name]
    assert model_schema["type"] == "object"
    assert set(model_schema["required"]) == {
        "scenario_id",
        "scenario_root_dir",
        "dem_path",
        "horizons_dir",
    }


def test_stage1_ws_event_names_frozen() -> None:
    expected = {e.value for e in JobEventName}
    assert set(STAGE1_WS_EVENT_NAMES) == expected
    assert len(STAGE1_WS_EVENT_NAMES) == 10


def test_ws_job_progress_envelope_shape() -> None:
    payload = WsEnvelope(
        event=JobEventName.JOB_PROGRESS,
        scenario_id="scn_contract",
        timestamp_utc="2026-05-13T20-00-00",
        data={
            "job_id": "job-1",
            "stage": "process_patches",
            "message": "Generated 1/2 horizon patches.",
            "percent": 50.0,
            "processed": 1,
            "total": 2,
        },
    ).model_dump(mode="json")

    assert payload == {
        "schema_version": "1.0",
        "event": "job_progress",
        "scenario_id": "scn_contract",
        "timestamp_utc": "2026-05-13T20-00-00",
        "data": {
            "job_id": "job-1",
            "stage": "process_patches",
            "message": "Generated 1/2 horizon patches.",
            "percent": 50.0,
            "processed": 1,
            "total": 2,
        },
    }
