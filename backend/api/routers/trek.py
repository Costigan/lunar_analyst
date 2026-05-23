from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.core.config import load_app_config as core_load_app_config
from backend.core.config import resolve_config_path as core_resolve_config_path
from backend.core.config import resolve_config_relative_path as core_resolve_config_relative_path
from backend.services.trek_catalog_service import TrekCatalogService
from backend.services.trek_feature_proxy_service import TrekFeatureProxyService


_DEFAULT_TREK_CACHE_TTL_SECONDS = 14 * 24 * 60 * 60
_MIN_TREK_CACHE_TTL_SECONDS = 60
_WORKSPACE_ROOT_ENV = "LUNAR_ANALYST_WORKSPACE_ROOT"
_DEFAULT_WORKSPACE_REL = "scenarios"


def _load_trek_cache_ttl_seconds() -> tuple[int, int]:
    payload = core_load_app_config()
    backend_cfg = payload.get("backend", {})
    if not isinstance(backend_cfg, dict):
        return _DEFAULT_TREK_CACHE_TTL_SECONDS, _DEFAULT_TREK_CACHE_TTL_SECONDS
    trek_cfg = backend_cfg.get("trek", {})
    if not isinstance(trek_cfg, dict):
        return _DEFAULT_TREK_CACHE_TTL_SECONDS, _DEFAULT_TREK_CACHE_TTL_SECONDS

    catalog_raw = trek_cfg.get("catalog_cache_ttl_seconds", _DEFAULT_TREK_CACHE_TTL_SECONDS)
    feature_raw = trek_cfg.get("feature_cache_ttl_seconds", _DEFAULT_TREK_CACHE_TTL_SECONDS)

    def _coerce(value: Any) -> int:
        if isinstance(value, bool):
            return _DEFAULT_TREK_CACHE_TTL_SECONDS
        if isinstance(value, int):
            return max(_MIN_TREK_CACHE_TTL_SECONDS, value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                return max(_MIN_TREK_CACHE_TTL_SECONDS, int(stripped))
        return _DEFAULT_TREK_CACHE_TTL_SECONDS

    return _coerce(catalog_raw), _coerce(feature_raw)


def _resolve_workspace_root() -> Path:
    env_override = os.getenv(_WORKSPACE_ROOT_ENV)
    if env_override and env_override.strip():
        return Path(env_override).expanduser().resolve()

    payload = core_load_app_config()
    backend_cfg = payload.get("backend", {})
    if isinstance(backend_cfg, dict):
        cfg_root = backend_cfg.get("workspace_root")
        if isinstance(cfg_root, str) and cfg_root.strip():
            config_path = core_resolve_config_path()
            return core_resolve_config_relative_path(cfg_root, config_path=config_path)
    return Path(_DEFAULT_WORKSPACE_REL).expanduser().resolve()


def _resolve_catalog_db_path() -> Path:
    return _resolve_workspace_root() / "scenario_catalog.db"


CACHE_TTL_CATALOG_SECONDS, CACHE_TTL_FEATURE_SECONDS = _load_trek_cache_ttl_seconds()

router = APIRouter(prefix="/api/v1/trek", tags=["trek"])
_catalog: TrekCatalogService | None = None
_feature_proxy = TrekFeatureProxyService(ttl_seconds=CACHE_TTL_FEATURE_SECONDS)


def _get_catalog() -> TrekCatalogService:
    global _catalog
    if _catalog is None:
        _catalog = TrekCatalogService(
            ttl_seconds=CACHE_TTL_CATALOG_SECONDS,
            cache_db_path=_resolve_catalog_db_path(),
        )
    return _catalog


@router.get("/layers")
def list_trek_layers(
    force_refresh: bool = Query(default=False),
) -> dict[str, Any]:
    snapshot = _get_catalog().list_layers(force_refresh=force_refresh)
    return {
        "layers": snapshot.layers,
        "count": len(snapshot.layers),
        "cached": snapshot.cached,
        "fetched_at_utc": snapshot.fetched_at_utc,
    }


@router.get("/layers:search")
def search_trek_layers(
    pattern: str = Query(default=""),
    force_refresh: bool = Query(default=False),
) -> dict[str, Any]:
    snapshot = _get_catalog().search_layers(pattern=pattern, force_refresh=force_refresh)
    return {
        "pattern": pattern,
        "layers": snapshot.layers,
        "count": len(snapshot.layers),
        "cached": snapshot.cached,
        "fetched_at_utc": snapshot.fetched_at_utc,
    }


@router.get("/layers/{product_label}/features")
def fetch_trek_layer_features(
    product_label: str,
    layer_id: int | None = Query(default=None),
    force_refresh: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        snapshot = _feature_proxy.fetch_features(
            product_label=product_label,
            layer_id=layer_id,
            force_refresh=force_refresh,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "product_label": snapshot.product_label,
        "source_root_url": snapshot.source_root_url,
        "layer_ids": snapshot.layer_ids,
        "feature_collection": snapshot.feature_collection,
        "feature_count": len(snapshot.feature_collection.get("features", [])),
        "cached": snapshot.cached,
        "fetched_at_utc": snapshot.fetched_at_utc,
    }
