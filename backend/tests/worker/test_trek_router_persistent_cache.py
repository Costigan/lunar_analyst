from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routers import trek as trek_router


def _app_with_trek_router() -> FastAPI:
    app = FastAPI()
    app.include_router(trek_router.router)
    return app


def test_trek_layers_endpoint_uses_persistent_catalog_cache_across_restart(tmp_path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(workspace_root))

    sample_layers = [
        {
            "item_UUID": "uuid_artemis_mosaic",
            "productLabel": "Artemis_Mosaic_SP",
            "title": "Artemis Mosaic South Pole",
            "description": "High-res mosaic for Artemis region",
            "serviceTypes": ["Mosaic"],
        }
    ]

    calls = {"count": 0}

    def _fetch_ok() -> list[dict[str, object]]:
        calls["count"] += 1
        return sample_layers

    trek_router._catalog = None
    monkeypatch.setattr(
        trek_router.TrekCatalogService,
        "_fetch_remote_layers",
        staticmethod(_fetch_ok),
    )

    first_client = TestClient(_app_with_trek_router())
    first_response = first_client.get("/api/v1/trek/layers")
    assert first_response.status_code == 200
    first_payload = first_response.json()
    assert first_payload["count"] == 1
    assert first_payload["cached"] is False
    assert calls["count"] == 1

    def _fetch_fails() -> list[dict[str, object]]:
        raise RuntimeError("remote unavailable")

    # Simulate backend restart: drop in-memory singleton and rebuild service from disk cache.
    trek_router._catalog = None
    monkeypatch.setattr(
        trek_router.TrekCatalogService,
        "_fetch_remote_layers",
        staticmethod(_fetch_fails),
    )

    second_client = TestClient(_app_with_trek_router())
    second_response = second_client.get("/api/v1/trek/layers")
    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert second_payload["count"] == 1
    assert second_payload["cached"] is True
    assert second_payload["layers"][0]["productLabel"] == "Artemis_Mosaic_SP"

    trek_router._catalog = None
