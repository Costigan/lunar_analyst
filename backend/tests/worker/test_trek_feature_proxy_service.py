from __future__ import annotations

import io
import zipfile
from urllib.parse import parse_qs, urlparse

import pytest

from backend.services import trek_feature_proxy_service as proxy_module
from backend.services.trek_feature_proxy_service import TrekFeatureProxyService


def _feature_collection(name: str) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": name},
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            }
        ],
    }


def test_fetch_features_resolves_service_and_uses_cache() -> None:
    calls: list[str] = []

    def fetcher(url: str):
        calls.append(url)
        parsed = urlparse(url)
        if parsed.path.endswith("/MapServer"):
            return {"layers": [{"id": 0}, {"id": 1}]}
        if parsed.path.endswith("/MapServer/0/query"):
            return _feature_collection("layer0")
        if parsed.path.endswith("/MapServer/1/query"):
            return _feature_collection("layer1")
        raise AssertionError(f"Unexpected URL: {url}")

    service = TrekFeatureProxyService(ttl_seconds=600, http_json_fetcher=fetcher)
    first = service.fetch_features(product_label="EnduranceA_Path_SouthernTraverse_v2_SP")
    second = service.fetch_features(product_label="EnduranceA_Path_SouthernTraverse_v2_SP")

    assert first.cached is False
    assert second.cached is True
    assert first.layer_ids == [0, 1]
    assert len(first.feature_collection["features"]) == 2
    assert len(calls) == 3


def test_fetch_features_single_layer() -> None:
    def fetcher(url: str):
        parsed = urlparse(url)
        if parsed.path.endswith("/MapServer"):
            return {"layers": [{"id": 0}, {"id": 7}]}
        if parsed.path.endswith("/MapServer/7/query"):
            params = parse_qs(parsed.query)
            assert params.get("f", [""])[0] in {"geojson", "pgeojson"}
            return _feature_collection("layer7")
        return {"type": "FeatureCollection", "features": []}

    service = TrekFeatureProxyService(ttl_seconds=600, http_json_fetcher=fetcher)
    snapshot = service.fetch_features(
        product_label="Endurance_A_Concept_AZ_Filter_Path_Coverage_SP",
        layer_id=7,
    )

    assert snapshot.layer_ids == [7]
    assert len(snapshot.feature_collection["features"]) == 1


def test_fetch_features_rejects_invalid_product_label() -> None:
    service = TrekFeatureProxyService(ttl_seconds=600, http_json_fetcher=lambda _: {})
    with pytest.raises(ValueError):
        service.fetch_features(product_label="../bad")


def test_fetch_features_falls_back_to_downloadable_archive_and_caches() -> None:
    json_calls: list[str] = []
    download_calls: list[str] = []

    def json_fetcher(url: str):
        json_calls.append(url)
        return {"error": {"message": "missing"}}

    def bytes_fetcher(url: str) -> bytes:
        download_calls.append(url)
        return b"fake-archive"

    def zip_parser(archive: bytes) -> dict:
        assert archive == b"fake-archive"
        return _feature_collection("fallback-path")

    service = TrekFeatureProxyService(
        ttl_seconds=600,
        http_json_fetcher=json_fetcher,
        http_bytes_fetcher=bytes_fetcher,
        zip_feature_parser=zip_parser,
    )

    first = service.fetch_features(product_label="Endurance_A_Concept_AZ_Filter_Path_Coverage_SP")
    second = service.fetch_features(product_label="Endurance_A_Concept_AZ_Filter_Path_Coverage_SP")

    assert first.cached is False
    assert second.cached is True
    assert first.layer_ids == [0]
    assert first.source_root_url.endswith(
        "/moon/TrekWS/rest/cat/data/stream?label=Endurance_A_Concept_AZ_Filter_Path_Coverage_SP"
    )
    assert len(first.feature_collection["features"]) == 1
    assert len(download_calls) == 1
    assert len(json_calls) == 2


def test_fetch_features_download_fallback_rejects_nonzero_layer_id() -> None:
    service = TrekFeatureProxyService(
        ttl_seconds=600,
        http_json_fetcher=lambda _: {"error": {"message": "missing"}},
        http_bytes_fetcher=lambda _: b"fake-archive",
        zip_feature_parser=lambda _: _feature_collection("fallback-path"),
    )
    with pytest.raises(RuntimeError):
        service.fetch_features(
            product_label="Endurance_A_Concept_AZ_Filter_Path_Coverage_SP",
            layer_id=3,
        )


def test_parse_zip_feature_collection_supports_nested_shapefile_path(monkeypatch) -> None:
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, mode="w") as archive:
        archive.writestr("nested/path/data.shp", b"placeholder")
    archive_bytes = archive_buffer.getvalue()

    class _FakeFeature:
        def ExportToJson(self) -> str:
            return '{"type":"Feature","properties":{"name":"nested"},"geometry":{"type":"Point","coordinates":[1,2]}}'

    class _FakeLayer:
        def ResetReading(self) -> None:
            return

        def __iter__(self):
            return iter([_FakeFeature()])

    class _FakeDataset:
        def GetLayerCount(self) -> int:
            return 1

        def GetLayerByIndex(self, _: int):
            return _FakeLayer()

    open_calls: list[str] = []

    class _FakeOgr:
        @staticmethod
        def Open(path: str):
            open_calls.append(path)
            normalized = path.replace("\\", "/")
            if normalized.endswith(".zip"):
                raise RuntimeError("zip open unsupported")
            if normalized.endswith("/nested/path/data.shp"):
                return _FakeDataset()
            return None

    monkeypatch.setattr(proxy_module, "ogr", _FakeOgr)
    monkeypatch.setattr(proxy_module, "gdal", None)

    feature_collection = TrekFeatureProxyService._parse_zip_feature_collection(archive_bytes)

    assert feature_collection["type"] == "FeatureCollection"
    assert len(feature_collection["features"]) == 1
    assert feature_collection["features"][0]["properties"]["name"] == "nested"
    assert len(open_calls) >= 2
    assert any(call.replace("\\", "/").endswith("/nested/path/data.shp") for call in open_calls)
