from __future__ import annotations

import copy
import json
import os
import re
import shutil
import threading
import tempfile
import time
import zipfile
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib import error, parse, request

from backend.worker.gdal_runtime import configure_gdal_runtime

_GDAL: Any | None = None
_OGR: Any | None = None
gdal: Any | None = None
ogr: Any | None = None
_GDAL_OGR_INITIALIZED = False

_PRODUCT_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_TREK_DATA_STREAM_URL = "https://trek.nasa.gov/moon/TrekWS/rest/cat/data/stream"
_VECTOR_ARCHIVE_EXTENSIONS = (".shp", ".geojson", ".json", ".gpkg", ".kml", ".gml")


def _load_gdal_ogr() -> tuple[Any | None, Any | None]:
    global _GDAL
    global _OGR
    global gdal
    global ogr
    global _GDAL_OGR_INITIALIZED

    # Preserve backward-compatible globals used by tests/legacy callers.
    if gdal is not None or ogr is not None:
        return gdal, ogr
    if _GDAL_OGR_INITIALIZED:
        return _GDAL, _OGR
    _GDAL_OGR_INITIALIZED = True

    try:
        configure_gdal_runtime()
        from osgeo import gdal as gdal_module  # type: ignore
        from osgeo import ogr as ogr_module  # type: ignore
    except Exception:
        _GDAL = None
        _OGR = None
        gdal = None
        ogr = None
        return _GDAL, _OGR

    _GDAL = gdal_module
    _OGR = ogr_module
    gdal = gdal_module
    ogr = ogr_module
    try:
        ogr_module.DontUseExceptions()
    except Exception:
        pass
    return _GDAL, _OGR


@dataclass(frozen=True)
class TrekFeatureSnapshot:
    product_label: str
    source_root_url: str
    layer_ids: list[int]
    feature_collection: dict[str, Any]
    fetched_at_utc: str
    cached: bool


class TrekFeatureProxyService:
    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        http_json_fetcher: Callable[[str], Any] | None = None,
        http_bytes_fetcher: Callable[[str], bytes] | None = None,
        zip_feature_parser: Callable[[bytes], dict[str, Any]] | None = None,
    ) -> None:
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._http_json_fetcher = http_json_fetcher or self._fetch_json
        self._http_bytes_fetcher = http_bytes_fetcher or self._fetch_bytes
        self._zip_feature_parser = zip_feature_parser or self._parse_zip_feature_collection
        self._lock = threading.RLock()
        self._feature_cache: dict[tuple[str, tuple[int, ...]], tuple[float, TrekFeatureSnapshot]] = {}
        self._service_cache: dict[str, tuple[float, str, list[int]]] = {}
        self._no_service_cache: dict[str, float] = {}

    def fetch_features(
        self,
        *,
        product_label: str,
        layer_id: int | None = None,
        force_refresh: bool = False,
    ) -> TrekFeatureSnapshot:
        normalized_label = self._normalize_product_label(product_label)
        try:
            source_root_url, available_layer_ids = self._resolve_service_info(
                normalized_label,
                force_refresh=force_refresh,
            )
        except RuntimeError as arcgis_error:
            return self._fetch_download_features(
                product_label=normalized_label,
                layer_id=layer_id,
                force_refresh=force_refresh,
                arcgis_error=arcgis_error,
            )

        selected_layer_ids = [int(layer_id)] if layer_id is not None else list(available_layer_ids)
        if not selected_layer_ids:
            selected_layer_ids = [0]
        cache_key = (normalized_label, tuple(selected_layer_ids))

        with self._lock:
            if not force_refresh:
                cached = self._feature_cache.get(cache_key)
                if cached and (time.time() - cached[0]) < self._ttl_seconds:
                    snap = cached[1]
                    return TrekFeatureSnapshot(
                        product_label=snap.product_label,
                        source_root_url=snap.source_root_url,
                        layer_ids=list(snap.layer_ids),
                        feature_collection=copy.deepcopy(snap.feature_collection),
                        fetched_at_utc=snap.fetched_at_utc,
                        cached=True,
                    )

        merged_features: list[dict[str, Any]] = []
        for next_layer_id in selected_layer_ids:
            layer_payload = self._fetch_layer_geojson(source_root_url, next_layer_id)
            features = layer_payload.get("features")
            if isinstance(features, list):
                merged_features.extend([entry for entry in features if isinstance(entry, dict)])

        feature_collection = {
            "type": "FeatureCollection",
            "features": merged_features,
        }
        fetched_at_utc = _utc_now_iso()
        snapshot = TrekFeatureSnapshot(
            product_label=normalized_label,
            source_root_url=source_root_url,
            layer_ids=list(selected_layer_ids),
            feature_collection=feature_collection,
            fetched_at_utc=fetched_at_utc,
            cached=False,
        )

        with self._lock:
            self._feature_cache[cache_key] = (time.time(), snapshot)

        return snapshot

    def _fetch_download_features(
        self,
        *,
        product_label: str,
        layer_id: int | None,
        force_refresh: bool,
        arcgis_error: RuntimeError,
    ) -> TrekFeatureSnapshot:
        if layer_id is not None and int(layer_id) != 0:
            raise RuntimeError(
                f"Layer-specific fallback is not available for downloadable trek features (requested layer_id={layer_id})."
            ) from arcgis_error
        selected_layer_ids = [0]
        cache_key = (product_label, tuple(selected_layer_ids))
        with self._lock:
            if not force_refresh:
                cached = self._feature_cache.get(cache_key)
                if cached and (time.time() - cached[0]) < self._ttl_seconds:
                    snap = cached[1]
                    return TrekFeatureSnapshot(
                        product_label=snap.product_label,
                        source_root_url=snap.source_root_url,
                        layer_ids=list(snap.layer_ids),
                        feature_collection=copy.deepcopy(snap.feature_collection),
                        fetched_at_utc=snap.fetched_at_utc,
                        cached=True,
                    )

        download_url = self._download_url_for_label(product_label)
        try:
            archive_bytes = self._http_bytes_fetcher(download_url)
            feature_collection = self._zip_feature_parser(archive_bytes)
        except Exception as exc:
            raise RuntimeError(
                f"{arcgis_error} Download fallback failed for {product_label}: {exc}"
            ) from exc

        features = feature_collection.get("features")
        if not isinstance(features, list):
            raise RuntimeError(
                f"{arcgis_error} Download fallback returned invalid GeoJSON for {product_label}."
            )

        snapshot = TrekFeatureSnapshot(
            product_label=product_label,
            source_root_url=download_url,
            layer_ids=selected_layer_ids,
            feature_collection={
                "type": "FeatureCollection",
                "features": [entry for entry in features if isinstance(entry, dict)],
            },
            fetched_at_utc=_utc_now_iso(),
            cached=False,
        )
        with self._lock:
            self._feature_cache[cache_key] = (time.time(), snapshot)
        return snapshot

    def _resolve_service_info(
        self,
        product_label: str,
        *,
        force_refresh: bool = False,
    ) -> tuple[str, list[int]]:
        with self._lock:
            if not force_refresh:
                cached = self._service_cache.get(product_label)
                if cached and (time.time() - cached[0]) < self._ttl_seconds:
                    return cached[1], list(cached[2])
                missing_since = self._no_service_cache.get(product_label)
                if missing_since and (time.time() - missing_since) < self._ttl_seconds:
                    raise RuntimeError(f"No ArcGIS feature service available for {product_label}.")

        encoded = parse.quote(product_label, safe="")
        candidates = [
            f"https://trek.nasa.gov/moon/trekarcgis2/rest/services/{encoded}/MapServer",
            f"https://trek.nasa.gov/moon/trekarcgis2/rest/services/{encoded}/FeatureServer",
        ]
        for root_url in candidates:
            payload = self._http_json_fetcher(f"{root_url}?f=pjson")
            if not isinstance(payload, dict):
                continue
            if "error" in payload:
                continue
            raw_layers = payload.get("layers", [])
            layer_ids: list[int] = []
            if isinstance(raw_layers, list):
                for entry in raw_layers:
                    if not isinstance(entry, dict):
                        continue
                    value = entry.get("id")
                    if isinstance(value, int):
                        layer_ids.append(value)
                    elif isinstance(value, str) and value.isdigit():
                        layer_ids.append(int(value))
            if not layer_ids:
                layer_ids = [0]
            with self._lock:
                self._service_cache[product_label] = (time.time(), root_url, list(layer_ids))
                self._no_service_cache.pop(product_label, None)
            return root_url, layer_ids

        with self._lock:
            self._no_service_cache[product_label] = time.time()
        raise RuntimeError(f"No ArcGIS feature service available for {product_label}.")

    def _fetch_layer_geojson(self, source_root_url: str, layer_id: int) -> dict[str, Any]:
        base = f"{source_root_url}/{int(layer_id)}/query"
        attempts = [
            {
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "true",
                "f": "geojson",
            },
            {
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "true",
                "f": "pgeojson",
            },
        ]
        last_error: str | None = None
        for params in attempts:
            query = parse.urlencode(params)
            payload = self._http_json_fetcher(f"{base}?{query}")
            if not isinstance(payload, dict):
                last_error = "non-object payload"
                continue
            if "error" in payload:
                details = payload.get("error")
                last_error = str(details)
                continue
            payload_type = str(payload.get("type", "")).strip()
            if payload_type == "FeatureCollection" and isinstance(payload.get("features"), list):
                return payload
            last_error = f"unexpected payload type: {payload_type or 'missing'}"
        raise RuntimeError(
            f"Failed to fetch GeoJSON from {source_root_url} layer {layer_id}: {last_error or 'unknown error'}."
        )

    @staticmethod
    def _normalize_product_label(product_label: str) -> str:
        value = str(product_label or "").strip()
        if not value:
            raise ValueError("product_label is required.")
        if not _PRODUCT_LABEL_PATTERN.match(value):
            raise ValueError("product_label contains invalid characters.")
        return value

    @staticmethod
    def _download_url_for_label(product_label: str) -> str:
        query = parse.urlencode({"label": product_label})
        return f"{_TREK_DATA_STREAM_URL}?{query}"

    @staticmethod
    def _fetch_json(url: str) -> Any:
        req = request.Request(
            url=url,
            method="GET",
            headers={
                "Accept": "application/json",
                "User-Agent": "LunarAnalyst/1.0",
            },
        )
        try:
            opener = request.build_opener(request.ProxyHandler({}))
            with opener.open(req, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except error.URLError as exc:
            raise RuntimeError(f"Trek feature request failed: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Trek feature request failed: {exc}") from exc
        try:
            return json.loads(raw)
        except Exception as exc:
            raise RuntimeError(f"Trek feature response JSON parse failed: {exc}") from exc

    @staticmethod
    def _fetch_bytes(url: str) -> bytes:
        req = request.Request(
            url=url,
            method="GET",
            headers={
                "User-Agent": "LunarAnalyst/1.0",
            },
        )
        try:
            opener = request.build_opener(request.ProxyHandler({}))
            with opener.open(req, timeout=90) as response:
                return response.read()
        except error.URLError as exc:
            raise RuntimeError(f"Trek download request failed: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Trek download request failed: {exc}") from exc

    @staticmethod
    def _parse_zip_feature_collection(archive_bytes: bytes) -> dict[str, Any]:
        gdal, ogr = _load_gdal_ogr()
        if ogr is None:
            raise RuntimeError("GDAL/OGR is unavailable; cannot parse downloadable Trek feature archive.")
        if not archive_bytes:
            raise RuntimeError("Download archive was empty.")

        temp_path = ""
        extract_root = ""
        dataset = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_file:
                temp_file.write(archive_bytes)
                temp_path = temp_file.name

            vector_candidates = TrekFeatureProxyService._vector_archive_candidates(temp_path)
            if gdal is not None:
                gdal.PushErrorHandler("CPLQuietErrorHandler")

            # First try direct dataset open for non-archive/flat cases.
            dataset = TrekFeatureProxyService._ogr_open_safe(temp_path)
            if dataset is None and vector_candidates:
                extract_root = tempfile.mkdtemp(prefix="trek_features_")
                extracted = TrekFeatureProxyService._extract_zip_members(
                    archive_path=temp_path,
                    destination_root=extract_root,
                    members=vector_candidates,
                )
                for candidate in extracted:
                    dataset = TrekFeatureProxyService._ogr_open_safe(candidate)
                    if dataset is not None:
                        break
            if dataset is None:
                raise RuntimeError("Failed to open downloadable archive as a vector dataset.")

            features: list[dict[str, Any]] = []
            layer_count = int(dataset.GetLayerCount() or 0)
            for layer_index in range(layer_count):
                layer = dataset.GetLayerByIndex(layer_index)
                if layer is None:
                    continue
                layer.ResetReading()
                for feature in layer:
                    raw = feature.ExportToJson()
                    if not raw:
                        continue
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        features.append(parsed)

            return {
                "type": "FeatureCollection",
                "features": features,
            }
        except Exception as exc:
            raise RuntimeError(f"Failed to parse downloadable trek feature archive: {exc}") from exc
        finally:
            if gdal is not None:
                try:
                    gdal.PopErrorHandler()
                except Exception:
                    pass
            dataset = None
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            if extract_root:
                shutil.rmtree(extract_root, ignore_errors=True)

    @staticmethod
    def _vector_archive_candidates(archive_path: str) -> list[str]:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                names = [name for name in archive.namelist() if name and not name.endswith("/")]
        except Exception:
            return []

        normalized = [name.lstrip("/").replace("\\", "/") for name in names]
        direct = [name for name in normalized if name.lower().endswith(_VECTOR_ARCHIVE_EXTENSIONS)]
        if direct:
            return direct
        return normalized

    @staticmethod
    def _extract_zip_members(
        *,
        archive_path: str,
        destination_root: str,
        members: list[str],
    ) -> list[str]:
        root = Path(destination_root).resolve()
        extracted_by_member: dict[str, str] = {}
        target_members = [member.replace("\\", "/").lstrip("/") for member in members]
        target_member_set = set(target_members)
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                member_name = info.filename.replace("\\", "/").lstrip("/")
                if not member_name or info.is_dir():
                    continue
                candidate = (root / member_name).resolve()
                if root not in candidate.parents and candidate != root:
                    continue
                candidate.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as src, candidate.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                if member_name in target_member_set:
                    extracted_by_member[member_name] = str(candidate)
        return [extracted_by_member[name] for name in target_members if name in extracted_by_member]

    @staticmethod
    def _ogr_open_safe(path: str):
        _gdal, ogr = _load_gdal_ogr()
        if ogr is None:
            return None
        try:
            return ogr.Open(path)
        except Exception:
            return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
