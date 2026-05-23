from __future__ import annotations

import json
from typing import Any

import httpx

from backend.notebook.client import NotebookClient, NotebookClientConfig


def _build_client(handler: httpx.MockTransport) -> NotebookClient:
    transport = handler
    http_client = httpx.Client(
        base_url="http://127.0.0.1:8000",
        headers={"x-lunar-session-token": "token"},
        transport=transport,
    )
    return NotebookClient(
        config=NotebookClientConfig(base_url="http://127.0.0.1:8000", api_token="token"),
        client=http_client,
    )


def test_zoom_map_to_file_posts_expected_payload() -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"status": "queued", "event": "map_zoom_requested"})

    client = _build_client(httpx.MockTransport(_handler))
    payload = client.zoom_map_to_file(
        scenario_id="scn_1",
        file_id="file_1",
        padding_px=48,
        max_zoom=9.5,
    )

    assert payload == {"status": "queued", "event": "map_zoom_requested"}
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/scenarios/scn_1/map-commands/zoom-to-file"
    assert captured["body"] == {"file_id": "file_1", "padding_px": 48, "max_zoom": 9.5}


def test_import_geotiff_create_layer_and_zoom_runs_roundtrip_calls() -> None:
    requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else None
        requests.append((request.method, request.url.path, body))

        if request.url.path.endswith("/imports/geotiff"):
            return httpx.Response(200, json={"product_id": "prd_1"})
        if request.url.path.endswith("/products/prd_1/files"):
            return httpx.Response(
                200,
                json=[{"file_id": "file_0"}, {"file_id": "file_1"}],
            )
        if request.url.path.endswith("/layers"):
            return httpx.Response(200, json={"layer_id": "lyr_1"})
        if request.url.path.endswith("/map-commands/zoom-to-file"):
            return httpx.Response(200, json={"status": "queued", "event": "map_zoom_requested"})
        raise AssertionError(f"Unexpected request path: {request.url.path}")

    client = _build_client(httpx.MockTransport(_handler))
    result = client.import_geotiff_create_layer_and_zoom(
        scenario_id="scn_1",
        source_path="/d/tmp/out.tif",
        title="Notebook Output",
        padding_px=24,
    )

    assert result["scenario_id"] == "scn_1"
    assert result["product"]["product_id"] == "prd_1"
    assert result["file_id"] == "file_1"
    assert result["layer"]["layer_id"] == "lyr_1"
    assert result["map_zoom"] == {"status": "queued", "event": "map_zoom_requested"}

    assert requests[0][0] == "POST"
    assert requests[0][1] == "/api/v1/scenarios/scn_1/imports/geotiff"
    assert requests[1][0] == "GET"
    assert requests[1][1] == "/api/v1/products/prd_1/files"
    assert requests[2][0] == "POST"
    assert requests[2][1] == "/api/v1/layers"
    assert requests[3][0] == "POST"
    assert requests[3][1] == "/api/v1/scenarios/scn_1/map-commands/zoom-to-file"
