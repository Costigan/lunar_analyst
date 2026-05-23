from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from rasterio.warp import transform_geom

from backend.core.crs_semantics import crs_semantically_equivalent

LUNAR_GEOGRAPHIC_CRS = "+proj=longlat +R=1737400 +no_defs"
DEFAULT_GEOJSON_DATA_CRS = LUNAR_GEOGRAPHIC_CRS
EARTH_4326_ALIASES = {
    "EPSG:4326",
    "CRS84",
    "OGC:CRS84",
    "URN:OGC:DEF:CRS:OGC:1.3:CRS84",
}


def load_map_display_geojson(
    *,
    source_path: Path,
    target_crs: str,
    target_crs_name: str,
) -> dict[str, Any]:
    """Load and normalize GeoJSON geometries into the map delivery CRS."""
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"GeoJSON parse failed: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("GeoJSON root must be an object.")
    return normalize_geojson_for_map(
        payload=raw,
        target_crs=target_crs,
        target_crs_name=target_crs_name,
    )


def normalize_geojson_for_map(
    *,
    payload: dict[str, Any],
    target_crs: str,
    target_crs_name: str,
) -> dict[str, Any]:
    normalized = copy.deepcopy(payload)
    default_source_crs = _extract_crs_token(normalized.get("crs")) or DEFAULT_GEOJSON_DATA_CRS
    _normalize_geojson_object(
        node=normalized,
        default_source_crs=default_source_crs,
        target_crs=target_crs,
    )
    normalized.pop("bbox", None)
    normalized["crs"] = {"type": "name", "properties": {"name": target_crs_name}}
    return normalized


def _normalize_geojson_object(
    *,
    node: dict[str, Any],
    default_source_crs: str,
    target_crs: str,
) -> None:
    geojson_type = str(node.get("type", "")).strip()
    if geojson_type == "FeatureCollection":
        features = node.get("features")
        if not isinstance(features, list):
            raise ValueError("GeoJSON FeatureCollection must contain a features list.")
        for item in features:
            if isinstance(item, dict):
                _normalize_feature(
                    feature=item,
                    default_source_crs=default_source_crs,
                    target_crs=target_crs,
                )
        node.pop("bbox", None)
        return

    if geojson_type == "Feature":
        _normalize_feature(
            feature=node,
            default_source_crs=default_source_crs,
            target_crs=target_crs,
        )
        return

    if "coordinates" in node or geojson_type == "GeometryCollection":
        node.update(
            _transform_geometry(
                geometry=node,
                source_crs=default_source_crs,
                target_crs=target_crs,
            )
        )
        node.pop("bbox", None)


def _normalize_feature(
    *,
    feature: dict[str, Any],
    default_source_crs: str,
    target_crs: str,
) -> None:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        feature.pop("bbox", None)
        return

    source_crs = default_source_crs
    properties = feature.get("properties")
    if isinstance(properties, dict):
        feature_crs = _extract_crs_token(properties.get("crs"))
        if feature_crs:
            source_crs = feature_crs

    feature["geometry"] = _transform_geometry(
        geometry=geometry,
        source_crs=source_crs,
        target_crs=target_crs,
    )
    feature.pop("bbox", None)


def _transform_geometry(
    *,
    geometry: dict[str, Any],
    source_crs: str,
    target_crs: str,
) -> dict[str, Any]:
    if _crs_equivalent(source_crs, target_crs):
        return geometry
    try:
        transformed = transform_geom(
            src_crs=source_crs,
            dst_crs=target_crs,
            geom=geometry,
        )
    except Exception as exc:
        raise ValueError(
            "GeoJSON transform failed: "
            f"source_crs={source_crs!r} target_crs={target_crs!r} error={exc}"
        ) from exc
    if not isinstance(transformed, dict):
        raise ValueError("GeoJSON transform failed: transformed geometry is not an object.")
    transformed.pop("bbox", None)
    return transformed


def _extract_crs_token(value: Any) -> str | None:
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return None
        upper = token.upper()
        if upper in EARTH_4326_ALIASES:
            return LUNAR_GEOGRAPHIC_CRS
        return token
    if isinstance(value, dict):
        kind = str(value.get("type", "")).strip().lower()
        props = value.get("properties")
        if kind == "name" and isinstance(props, dict):
            name = props.get("name")
            if isinstance(name, str) and name.strip():
                token = name.strip()
                upper = token.upper()
                if upper in EARTH_4326_ALIASES:
                    return LUNAR_GEOGRAPHIC_CRS
                return token
        if kind == "epsg" and isinstance(props, dict):
            code = props.get("code")
            if isinstance(code, int):
                if code == 4326:
                    return LUNAR_GEOGRAPHIC_CRS
                return f"EPSG:{code}"
            if isinstance(code, str) and code.strip():
                normalized = code.strip()
                if normalized == "4326":
                    return LUNAR_GEOGRAPHIC_CRS
                return f"EPSG:{normalized}"
    return None


def _crs_equivalent(left: str, right: str) -> bool:
    return crs_semantically_equivalent(left, right)
