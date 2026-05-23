from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.api.dependencies import ServiceContainer


def resolve_artifact_path(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> Path:
    path, _source_file_id = resolve_artifact_identity(services, arguments)
    return path


def resolve_artifact_identity(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> tuple[Path, str | None]:
    file_id = str(arguments.get("file_id", "")).strip()
    if file_id:
        path, _ = services.product_service.resolve_file_path(file_id)
        return path, file_id

    scenario_id = str(arguments.get("scenario_id", "")).strip()
    relative_path = str(arguments.get("relative_path", "")).strip()
    if scenario_id and relative_path:
        scenario = services.scenario_service.get_scenario(scenario_id)
        scenario_root = Path(scenario.directory).expanduser().resolve()
        path = resolve_relative_path(scenario_root, relative_path)
        return path, find_or_register_file_id_for_path(
            services,
            path,
            scenario_id_hint=scenario_id,
        )

    raw_path = str(arguments.get("path", "")).strip()
    if not raw_path:
        raise ValueError("Either file_id, path, or (scenario_id + relative_path) is required")

    path = Path(raw_path).expanduser().resolve()
    workspace_root = Path(services.stores.workspace_root).expanduser().resolve()
    scenario_roots = [Path(item).expanduser().resolve() for item in services.stores.scenario_roots.values()]
    allowed_roots = [workspace_root, *scenario_roots]
    if not any(is_path_within_root(path, root) for root in allowed_roots):
        raise PermissionError(f"Artifact path must be under workspace/scenario roots: {path}")

    scenario_id_hint = infer_scenario_id_for_path(services, path)
    return path, find_or_register_file_id_for_path(
        services,
        path,
        scenario_id_hint=scenario_id_hint,
    )


def find_or_register_file_id_for_path(
    services: "ServiceContainer",
    path: Path,
    *,
    scenario_id_hint: str | None = None,
) -> str | None:
    file_id = find_file_id_for_path(services, path)
    if file_id is not None or not scenario_id_hint:
        return file_id

    try:
        services.scenario_service.reconcile_scenario_filesystem(scenario_id_hint, force=True)
    except Exception:
        return file_id

    file_id = find_file_id_for_path(services, path)
    if file_id is not None:
        return file_id

    return register_scenario_file_id_for_path(services, path, scenario_id=scenario_id_hint)


def infer_scenario_id_for_path(services: "ServiceContainer", path: Path) -> str | None:
    target = path.expanduser().resolve()
    scenario_roots = getattr(services.stores, "scenario_roots", {})
    if not isinstance(scenario_roots, dict):
        return None

    for scenario_id, root in scenario_roots.items():
        try:
            candidate_root = Path(root).expanduser().resolve()
        except Exception:
            continue
        if is_path_within_root(target, candidate_root):
            return str(scenario_id)
    return None


def register_scenario_file_id_for_path(
    services: "ServiceContainer",
    path: Path,
    *,
    scenario_id: str,
) -> str | None:
    scenario = services.scenario_service.get_scenario(scenario_id)
    scenario_root = Path(scenario.directory).expanduser().resolve()
    target = path.expanduser().resolve()
    if not is_path_within_root(target, scenario_root):
        return None

    relative_path = target.relative_to(scenario_root).as_posix()
    existing_file_id = find_file_id_for_path(services, target)
    if existing_file_id is not None:
        return existing_file_id

    register_product = getattr(services.scenario_service, "_register_discovered_single_file_product", None)
    register_file = getattr(services.scenario_service, "_register_file", None)
    if not callable(register_product) or not callable(register_file):
        return None

    try:
        product = register_product(scenario=scenario, relative_path=relative_path)
        record = register_file(
            product_id=product.product_id,
            scenario_id=scenario_id,
            scenario_root=scenario_root,
            relative_path=relative_path,
            media_type=guess_media_type_for_artifact_path(relative_path),
            role="primary",
        )
    except Exception:
        return None

    return str(getattr(record, "file_id", "") or "") or None


def guess_media_type_for_artifact_path(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower()
    if suffix in {".png"}:
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".svg":
        return "image/svg+xml"
    if suffix == ".csv":
        return "text/csv"
    if suffix in {".tif", ".tiff"}:
        return "image/tiff"
    if suffix == ".geojson":
        return "application/geo+json"
    if suffix == ".json":
        return "application/json"
    return "application/octet-stream"


def find_file_id_for_path(services: "ServiceContainer", path: Path) -> str | None:
    product_files = getattr(services.stores, "product_files", {})
    if not isinstance(product_files, dict):
        return None

    target = path.expanduser().resolve()
    for file_id, record in product_files.items():
        scenario_id = str(getattr(record, "scenario_id", "") or "").strip()
        relative_path = str(getattr(record, "relative_path", "") or "").replace("\\", "/").strip().lstrip("/")
        if not scenario_id or not relative_path:
            continue
        try:
            scenario = services.scenario_service.get_scenario(scenario_id)
        except Exception:
            continue
        candidate = (Path(scenario.directory).expanduser().resolve() / relative_path).resolve()
        if candidate == target:
            return str(file_id)
    return None


def resolve_relative_path(scenario_root: Path, relative_path: str) -> Path:
    rel = relative_path.replace("\\", "/").strip().lstrip("/")
    if not rel:
        raise ValueError("relative_path is required")
    if ".." in [part for part in rel.split("/") if part]:
        raise ValueError("relative_path cannot contain '..'")
    target = (scenario_root / rel).resolve()
    if scenario_root != target and scenario_root not in target.parents:
        raise PermissionError(f"Path escapes scenario root: {target}")
    return target


def is_path_within_root(path: Path, root: Path) -> bool:
    return path == root or root in path.parents
