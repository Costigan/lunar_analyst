from __future__ import annotations

from pathlib import Path

from backend.services.trek_catalog_service import TrekCatalogService


def _sample_layers():
    return [
        {
            "item_UUID": "uuid_artemis_mosaic",
            "productLabel": "Artemis_Mosaic_SP",
            "title": "Artemis Mosaic South Pole",
            "description": "High-res mosaic for Artemis region",
            "serviceTypes": ["Mosaic"],
        },
        {
            "item_UUID": "uuid_apollo_feature",
            "productLabel": "Apollo_Sites_SP",
            "title": "Apollo Site Features",
            "description": "Feature layer for mission sites",
            "serviceTypes": ["Feature"],
        },
        {
            "item_UUID": "uuid_lola_raster",
            "productLabel": "LOLA_DEM_SP",
            "title": "LOLA DEM South Pole",
            "description": "Raster elevation dataset",
            "serviceTypes": ["Raster"],
        },
    ]


def test_list_layers_uses_cache_until_forced_refresh() -> None:
    calls = {"count": 0}

    def _fetch():
        calls["count"] += 1
        return _sample_layers()

    service = TrekCatalogService(ttl_seconds=600, http_fetcher=_fetch)
    first = service.list_layers()
    second = service.list_layers()
    third = service.list_layers(force_refresh=True)

    assert calls["count"] == 2
    assert first.cached is False
    assert second.cached is True
    assert third.cached is False
    assert len(first.layers) == 3


def test_search_layers_supports_boolean_matching() -> None:
    service = TrekCatalogService(ttl_seconds=600, http_fetcher=_sample_layers)

    and_result = service.search_layers(pattern="Artemis AND Mosaic")
    assert len(and_result.layers) == 1
    assert and_result.layers[0]["item_UUID"] == "uuid_artemis_mosaic"

    or_result = service.search_layers(pattern="Apollo OR LOLA")
    uuids = {item["item_UUID"] for item in or_result.layers}
    assert uuids == {"uuid_apollo_feature", "uuid_lola_raster"}

    not_result = service.search_layers(pattern="Mosaic NOT Artemis")
    assert len(not_result.layers) == 0


def test_list_layers_uses_persistent_catalog_cache_between_service_instances(tmp_path: Path) -> None:
    calls = {"count": 0}

    def _fetch():
        calls["count"] += 1
        return _sample_layers()

    cache_db_path = tmp_path / "scenario_catalog.db"
    service_a = TrekCatalogService(
        ttl_seconds=600,
        http_fetcher=_fetch,
        cache_db_path=cache_db_path,
    )
    first = service_a.list_layers()
    assert first.cached is False
    assert calls["count"] == 1

    service_b = TrekCatalogService(
        ttl_seconds=600,
        http_fetcher=_fetch,
        cache_db_path=cache_db_path,
    )
    second = service_b.list_layers()
    assert second.cached is True
    assert calls["count"] == 1

    third = service_b.list_layers(force_refresh=True)
    assert third.cached is False
    assert calls["count"] == 2
