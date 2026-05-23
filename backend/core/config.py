from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

APP_CONFIG_ENV = "LUNAR_ANALYST_CONFIG_TOML"
DEFAULT_CONFIG_RELATIVE_PATH = Path("config") / "lunar_analyst.toml"
_REMOVED_CONFIG_KEYS: set[tuple[str, ...]] = {
    ("backend", "llm", "hybrid_command_router_enabled"),
    ("backend", "llm", "legacy_parser_enabled"),
    ("backend", "llm", "prompt_segmentation_model"),
    ("backend", "llm", "deterministic_agent_substeps_enabled"),
    ("backend", "llm", "create_product_recipe_catalog_enabled"),
    ("backend", "llm", "session_store_backend"),
    ("backend", "llm", "routing", "entity_kind_routing_enabled"),
    ("backend", "llm", "routing", "domain_entity_context_enabled"),
    ("backend", "llm", "routing", "semantic_classifier_fallback_enabled"),
    ("backend", "llm", "segment_intent_classifier", "provider"),
    ("backend", "llm", "segment_intent_classifier", "model"),
    ("backend", "llm", "segment_intent_classifier", "timeout_seconds"),
    ("backend", "llm", "performance", "allow_cross_provider_fallback"),
    ("backend", "llm", "performance", "prewarm_on_startup"),
}
_REMOVED_CONFIG_TABLES: set[tuple[str, ...]] = {
    ("backend", "llm", "segment_intent_classifier"),
}

ESRI_103878_PROJ4 = (
    "+proj=stere +lat_0=-90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs"
)
ESRI_103878_WKT = (
    'PROJCS["ESRI:103878",'
    'GEOGCS["Moon_2000",DATUM["D_Moon_2000",SPHEROID["Moon_2000_IAU_IAG",1737400,0]],'
    'PRIMEM["Reference_Meridian",0],UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Polar_Stereographic"],'
    'PARAMETER["latitude_of_origin",-90],'
    'PARAMETER["central_meridian",0],'
    'PARAMETER["scale_factor",1],'
    'PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",0],'
    'UNIT["Meter",1]]'
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    return (repo_root() / DEFAULT_CONFIG_RELATIVE_PATH).resolve()


def resolve_config_path() -> Path:
    env_override = os.getenv(APP_CONFIG_ENV)
    if env_override and env_override.strip():
        return Path(env_override).expanduser().resolve()
    return default_config_path()


def resolve_config_relative_path(raw_path: str, *, config_path: Path | None = None) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    active_config = config_path or resolve_config_path()
    return (active_config.parent / candidate).resolve()


def _lookup_path(payload: Any, path: tuple[str, ...]) -> tuple[bool, Any]:
    node = payload
    for item in path:
        if not isinstance(node, dict) or item not in node:
            return False, None
        node = node[item]
    return True, node


def _validate_removed_keys(payload: dict[str, Any]) -> None:
    found: list[str] = []
    for path in sorted(_REMOVED_CONFIG_KEYS):
        exists, _value = _lookup_path(payload, path)
        if exists:
            found.append(".".join(path))
    for path in sorted(_REMOVED_CONFIG_TABLES):
        exists, value = _lookup_path(payload, path)
        if exists and isinstance(value, dict):
            found.append(".".join(path))
    if found:
        raise ValueError(
            "Removed/invalid config keys are present: "
            + ", ".join(found)
        )


def load_app_config(*, strict: bool = False) -> dict[str, Any]:
    config_path = resolve_config_path()
    if not config_path.exists():
        return {}
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            _validate_removed_keys(payload)
        return payload
    except ValueError:
        # Removed/invalid key validation errors must not be silently ignored.
        raise
    except Exception:
        if strict:
            raise
        return {}
