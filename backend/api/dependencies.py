from __future__ import annotations

import json
import hashlib
import inspect
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import tomllib
from collections import deque
from queue import Empty, Queue
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel

from backend.api.errors import ApiError
from backend.api.marimo_service import (
    MarimoLaunchConflictError,
    MarimoService,
    _default_marimo_notebook_template,
    _default_python_script_template,
)
from backend.api.notebook_session_service import NotebookSessionService
from backend.api.runtime_state import (
    BoundedEventBuffer,
    BoundedRunInfoMap,
    connect_sqlite as _connect_sqlite,
)
from backend.api.store_models import (
    DiscoveryRunRecord,
    InMemoryStores,
    ProductFileRecord,
    ScenarioTomlConfig,
)
from backend.api.dependencies_constants import (
    WORKSPACE_ROOT_ENV,
    DEFAULT_WORKSPACE_REL,
)
from backend.api.dependency_helpers import (
    append_workspace_message as _append_workspace_message,
    build_notebook_runner_env as _build_notebook_runner_env,
    clear_workspace_messages as _clear_workspace_messages,
    create_unique_scenario_python_file as _create_unique_scenario_python_file,
    default_polygon as _default_polygon,
    dir_size_bytes as _dir_size_bytes,
    dir_stats as _dir_stats,
    dir_tree_stats as _dir_tree_stats,
    ensure_within_root as _ensure_within_root,
    guess_media_type_from_path as _guess_media_type_from_path,
    is_hidden_default_path as _is_hidden_default_path,
    is_renderable_relative_path as _is_renderable_relative_path,
    load_app_config as _load_app_config,
    load_llm_config as _load_llm_config,
    messages_log_dir as _messages_log_dir,
    messages_log_path as _messages_log_path,
    new_job_event as _new_job_event,
    normalize_primary_dem_path as _normalize_primary_dem_path,
    normalize_relative_path as _normalize_relative_path,
    parent_relative_path as _parent_relative_path,
    preferred_repo_python as _preferred_repo_python,
    read_workspace_messages as _read_workspace_messages,
    repo_root as _repo_root,
    resolve_action_router_spec_path as _resolve_action_router_spec_path,
    resolve_assistant_legacy_json_path as _resolve_assistant_legacy_json_path,
    resolve_assistant_store_path as _resolve_assistant_store_path,
    resolve_config_path as _resolve_config_path,
    resolve_workspace_root as _resolve_workspace_root,
    serialize_result as _serialize_result,
    unique_file_path as _unique_file_path,
    utc_from_timestamp as _utc_from_timestamp,
    utc_now as _utc_now,
    workspace_message_from_job_event as _workspace_message_from_job_event,
)
from backend.mcp.server import McpServer
from backend.contracts.events import WsEnvelope
from backend.contracts.models import (
    CreateLayerStateRequest,
    ExplorerNode,
    ExplorerNodeType,
    DiscoverScenariosRequest,
    DiscoverScenariosResponse,
    DiscoveryStatusResponse,
    CreateScenarioRequest,
    ImportGeoTiffRequest,
    JobDefinition,
    JobDefinitionParam,
    JobDefinitionType,
    Job,
    JobDefinitionsResponse,
    JobEvent,
    JobEventName,
    JobMode,
    JobStatus,
    LayerState,
    MoveScenarioPathRequest,
    MoveScenarioPathResponse,
    Producer,
    ProductFile,
    Product,
    RenderMode,
    ForgetScenarioResponse,
    HorizonSetDetachResponse,
    HorizonSetStatusResponse,
    RegisterProductRequest,
    ResolveHorizonSetRequest,
    ResolveHorizonSetResponse,
    ReingestScenarioRequest,
    ReingestScenarioResponse,
    ScenarioDiscoveryResult,
    Scenario,
    UpdateLayerStateRequest,
)
from backend.jobs.handlers import ToolImplementations
from backend.jobs.runtime_context import (
    PublishedLayerOutput,
    RegisteredRasterOutput,
    set_generated_raster_registrar,
    set_generated_raster_layer_publisher,
    set_job_cancel_checker,
    set_job_progress_emitter,
    ScenarioPaths,
    set_notebook_job_executor,
    set_scenario_paths_resolver,
)
from backend.jobs.worker_protocol import (
    build_worker_protocol_paths,
    read_progress_events_since_line as _read_worker_progress_events_since_line,
    request_cancel as _request_worker_cancel,
    worker_context_payload,
    write_json_file as _write_worker_json_file,
)
from backend.core.config import load_app_config as core_load_app_config
from backend.core.config import resolve_config_path as core_resolve_config_path
from backend.services.assistant.assistant_service import AssistantService, ToolExecutionServices
from backend.services.assistant.policy_service import AssistantPolicyService
from backend.services.assistant.provider_registry import AssistantProviderRegistry
from backend.services.assistant.session_store import AssistantSessionStore
from backend.notebook.job_catalog import (
    DiscoveredNotebookJob,
    NotebookJobMetadata,
    discover_notebook_jobs,
    resolve_notebook_job_roots,
)
from backend.services.cog import convert_geotiff_to_cog
from backend.services.migrations import ensure_schema
from backend.services.repositories.layer_state_repository import LayerStateRepository
from backend.worker.gdal_runtime import import_rasterio


logger = logging.getLogger(__name__)
HORIZON_DIMENSION_ERROR_RE = re.compile(
    r"DEM (width|height) \((\d+)\) must be an even multiple of (\d+)\.",
    re.IGNORECASE,
)
CURRENT_JOB_ID: ContextVar[str | None] = ContextVar("current_job_id", default=None)


def _copy_file_bytes(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("wb") as dst:
        shutil.copyfileobj(src, dst)


def _native_worker_env() -> dict[str, str]:
    env = os.environ.copy()
    try:
        from backend.worker.native_bootstrap import load_native_bootstrap_config, resolve_moonlib_dll

        cfg = load_native_bootstrap_config()
        moonlib_dir = resolve_moonlib_dll(cfg).parent
        native_dirs = [moonlib_dir, *cfg.dll_resolver_search_dirs]
    except Exception:
        repo_root = _repo_root()
        native_dirs = [
            repo_root / "native" / "new_horizon" / "moonlib" / "bin" / "Debug" / "net9.0" / "linux-x64",
            repo_root / "native" / "third_party" / "cspice" / "linux-x64",
        ]

    existing = env.get("LD_LIBRARY_PATH", "")
    parts = [part for part in existing.split(os.pathsep) if part]
    for directory in reversed([path.resolve() for path in native_dirs if path.exists()]):
        text = str(directory)
        if text not in parts:
            parts.insert(0, text)
    if parts:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(parts)
    return env


class ScenarioService:
    def __init__(self, stores: InMemoryStores, *, reconcile_on_startup: bool = True) -> None:
        self._stores = stores
        self._ensure_catalog_schema()
        self._load_catalog()
        if reconcile_on_startup:
            for scenario_id in list(self._stores.scenarios.keys()):
                try:
                    self.reconcile_scenario_filesystem(scenario_id)
                except Exception as exc:  # pragma: no cover - defensive bootstrap logging
                    logger.warning("scenario reconcile skipped scenario_id=%s reason=%s", scenario_id, exc)

    def create_scenario(self, request: CreateScenarioRequest) -> Scenario:
        scenario_id = f"scn_{request.scenario_root}"
        if scenario_id in self._stores.scenarios:
            return self._stores.scenarios[scenario_id]

        scenario_root_dir = (self._stores.workspace_root / request.scenario_root).resolve()
        _ensure_within_root(self._stores.workspace_root, scenario_root_dir)
        scenario_root_dir.mkdir(parents=True, exist_ok=True)

        scenario_db = scenario_root_dir / "scenario.db"
        ensure_schema(scenario_db)

        now = _utc_now()
        scenario = Scenario(
            scenario_id=scenario_id,
            scenario_root=request.scenario_root,
            name=request.name,
            owner=request.owner,
            directory=str(scenario_root_dir),
            primary_dem_path="dem.tif",
            primary_dem_crs="ESRI:103878",
            primary_dem_footprint=_default_polygon(),
            size_bytes=_dir_size_bytes(scenario_root_dir),
            last_touched_utc=now,
            created_at_utc=now,
            updated_at_utc=now,
        )
        self._stores.scenarios[scenario_id] = scenario
        self._stores.scenario_roots[scenario_id] = scenario_root_dir
        self._upsert_catalog_scenario(scenario)
        self._persist_scenario_to_local_db(scenario)
        return scenario

    def get_scenario(self, scenario_id: str) -> Scenario:
        if scenario_id not in self._stores.scenarios:
            raise KeyError(f"Scenario not found: {scenario_id}")
        return self._stores.scenarios[scenario_id]

    def list_scenarios(self) -> list[Scenario]:
        return list(self._stores.scenarios.values())

    def ensure_product_catalog_hydrated(self, scenario_id: str) -> None:
        if scenario_id in self._stores.product_catalog_hydrated_scenarios:
            return
        self._hydrate_products_and_files_from_local_db(scenario_id)

    def reconcile_scenario_filesystem(self, scenario_id: str, *, force: bool = False) -> bool:
        scenario = self.get_scenario(scenario_id)
        scenario_dir = Path(scenario.directory).resolve()
        _ensure_within_root(self._stores.workspace_root, scenario_dir)
        if not scenario_dir.exists() or not scenario_dir.is_dir():
            logger.warning(f"Scenario directory missing or not a directory: {scenario_dir}")
            return False

        try:
            file_count, size_bytes, last_touched_utc = _dir_tree_stats(scenario_dir)
        except (OSError, FileNotFoundError) as exc:
            logger.error(f"Failed to calculate stats for scenario directory {scenario_dir}: {exc}")
            return False

        ensure_schema(scenario_dir / "scenario.db")
        self._hydrate_products_and_files_from_local_db(scenario_id)

        should_reconcile = force or self._scenario_needs_reconcile(
            scenario_id=scenario_id,
            scenario_dir=scenario_dir,
            file_count=file_count,
            size_bytes=size_bytes,
            last_touched_utc=last_touched_utc,
        )
        if not should_reconcile:
            return False

        self._validate_registered_path_collisions(scenario_id)
        self._remove_stale_file_registrations(scenario_id=scenario_id, scenario_dir=scenario_dir)
        dem_rel = _normalize_primary_dem_path(scenario.primary_dem_path)
        dem_path = (scenario_dir / dem_rel).resolve()
        if dem_path.exists() and dem_path.is_file():
            self._register_or_update_primary_dem_product(
                scenario=scenario,
                canonical_dem_path=dem_path,
                canonical_dem_rel=dem_rel,
            )
        self._register_or_update_canonical_hillshade_product(scenario=scenario)
        self._register_discovered_renderable_products(scenario=scenario)
        refreshed = scenario.model_copy(
            update={
                "size_bytes": size_bytes,
                "last_touched_utc": last_touched_utc,
                "updated_at_utc": _utc_now(),
            }
        )
        self._stores.scenarios[scenario_id] = refreshed
        self._stores.scenario_roots[scenario_id] = scenario_dir
        self._upsert_catalog_scenario(refreshed)
        self._persist_scenario_to_local_db(refreshed)
        return True

    def _hydrate_products_and_files_from_local_db(self, scenario_id: str) -> None:
        scenario = self.get_scenario(scenario_id)
        scenario_root = Path(scenario.directory).resolve()
        _ensure_within_root(self._stores.workspace_root, scenario_root)
        db_path = scenario_root / "scenario.db"
        ensure_schema(db_path)
        with _connect_sqlite(db_path) as conn:
            product_rows = conn.execute(
                """
                SELECT
                    product_id, scenario_id, kind, subkind, producer, crs, footprint,
                    created_at_utc, lineage
                FROM products
                WHERE scenario_id = ?
                """,
                (scenario_id,),
            ).fetchall()
            file_rows = conn.execute(
                """
                SELECT
                    file_id, product_id, scenario_id, relative_path, media_type, role, created_at_utc
                FROM product_files
                WHERE scenario_id = ?
                """,
                (scenario_id,),
            ).fetchall()

        hydrated_products: dict[str, Product] = {}
        for row in product_rows:
            try:
                producer = Producer(str(row[4]))
                footprint = json.loads(str(row[6]))
                lineage = json.loads(str(row[8]))
                if not isinstance(lineage, dict):
                    lineage = {}
                product = Product(
                    product_id=str(row[0]),
                    scenario_id=str(row[1]),
                    kind=str(row[2]),
                    subkind=str(row[3]),
                    producer=producer,
                    crs=str(row[5]),
                    footprint=footprint,
                    created_at_utc=str(row[7]),
                    lineage=lineage,
                )
            except Exception as exc:
                logger.warning(
                    "Skipping invalid product row during hydrate scenario_id=%s product_id=%s reason=%s",
                    scenario_id,
                    row[0] if row else None,
                    exc,
                )
                continue
            hydrated_products[product.product_id] = product

        hydrated_product_ids = set(hydrated_products.keys())
        hydrated_files: dict[str, ProductFileRecord] = {}
        for row in file_rows:
            product_id = str(row[1])
            if product_id not in hydrated_product_ids:
                continue
            relative_path = _normalize_relative_path(str(row[3]))
            try:
                candidate = (scenario_root / relative_path).resolve()
                _ensure_within_root(scenario_root, candidate)
            except Exception:
                continue
            record = ProductFileRecord(
                file_id=str(row[0]),
                product_id=product_id,
                scenario_id=str(row[2]),
                scenario_root=scenario_root,
                relative_path=relative_path,
                media_type=str(row[4]),
                role=str(row[5]),
                created_at_utc=str(row[6]),
            )
            hydrated_files[record.file_id] = record

        # Update in two phases to avoid a transient "empty catalog" window for this scenario.
        for product_id, product in hydrated_products.items():
            self._stores.products[product_id] = product
        for file_id, record in hydrated_files.items():
            self._stores.product_files[file_id] = record

        hydrated_product_ids_lower = set(hydrated_products.keys())
        hydrated_file_ids_lower = set(hydrated_files.keys())
        for file_id, record in list(self._stores.product_files.items()):
            if record.scenario_id != scenario_id:
                continue
            if file_id not in hydrated_file_ids_lower:
                self._stores.product_files.pop(file_id, None)
        for product_id, product in list(self._stores.products.items()):
            if product.scenario_id != scenario_id:
                continue
            if product_id not in hydrated_product_ids_lower:
                self._stores.products.pop(product_id, None)
        self._stores.product_catalog_hydrated_scenarios.add(scenario_id)

    def _remove_stale_file_registrations(self, *, scenario_id: str, scenario_dir: Path) -> None:
        stale_file_ids: list[str] = []
        # Reconcile can run while other requests mutate in-memory stores.
        # Iterate over a snapshot to avoid "dictionary changed size during iteration".
        for file_id, record in list(self._stores.product_files.items()):
            if record.scenario_id != scenario_id:
                continue
            candidate = (scenario_dir / record.relative_path).resolve()
            _ensure_within_root(scenario_dir, candidate)
            if not candidate.exists() or not candidate.is_file():
                stale_file_ids.append(file_id)
        for file_id in stale_file_ids:
            record = self._stores.product_files.pop(file_id, None)
            if record is None:
                continue
            self._delete_product_file_from_local_db(record.scenario_id, file_id)
            self._delete_orphan_product(record.product_id)

    def _delete_orphan_product(self, product_id: str) -> None:
        if any(record.product_id == product_id for record in self._stores.product_files.values()):
            return
        product = self._stores.products.pop(product_id, None)
        if product is None:
            return
        self._delete_product_from_local_db(product.scenario_id, product_id)

    def _validate_registered_path_collisions(self, scenario_id: str) -> None:
        by_normalized: dict[str, str] = {}
        for record in self._stores.product_files.values():
            if record.scenario_id != scenario_id:
                continue
            normalized = _normalize_relative_path(record.relative_path).lower()
            existing = by_normalized.get(normalized)
            if existing is not None and existing != record.relative_path:
                raise ValueError(
                    f"Path collision detected for scenario {scenario_id}: "
                    f"{existing!r} conflicts with {record.relative_path!r}"
                )
            by_normalized[normalized] = record.relative_path

    def _register_discovered_renderable_products(self, *, scenario: Scenario) -> None:
        scenario_root = Path(scenario.directory).resolve()
        registered = {
            _normalize_relative_path(record.relative_path).lower()
            for record in self._stores.product_files.values()
            if record.scenario_id == scenario.scenario_id
        }
        discovered: list[str] = []
        for node in sorted(scenario_root.rglob("*")):
            if not node.is_file():
                continue
            rel = _normalize_relative_path(node.relative_to(scenario_root).as_posix())
            if rel.lower() in {"scenario.db", "scenario.toml"}:
                continue
            if not _is_renderable_relative_path(rel):
                continue
            normalized = rel.lower()
            if normalized in registered:
                continue
            discovered.append(rel)

        if not discovered:
            return

        grouped: dict[str, list[str]] = {}
        for rel in discovered:
            parent = Path(rel).parent.as_posix()
            key = "" if parent == "." else parent
            grouped.setdefault(key, []).append(rel)

        for parent_rel, paths in grouped.items():
            if parent_rel and len(paths) > 1:
                product = self._register_collection_product(
                    scenario=scenario,
                    collection_rel_path=parent_rel,
                )
                for rel in sorted(paths):
                    self._register_file(
                        product_id=product.product_id,
                        scenario_id=scenario.scenario_id,
                        scenario_root=scenario_root,
                        relative_path=rel,
                        media_type=_guess_media_type_from_path(rel),
                        role="member",
                    )
                continue

            for rel in sorted(paths):
                product = self._register_discovered_single_file_product(
                    scenario=scenario,
                    relative_path=rel,
                )
                self._register_file(
                    product_id=product.product_id,
                    scenario_id=scenario.scenario_id,
                    scenario_root=scenario_root,
                    relative_path=rel,
                    media_type=_guess_media_type_from_path(rel),
                    role="primary",
                )

    def _register_discovered_single_file_product(
        self,
        *,
        scenario: Scenario,
        relative_path: str,
    ) -> Product:
        suffix = Path(relative_path).suffix.lower()
        if suffix in {".tif", ".tiff"}:
            kind = "raster"
            subkind = "tif"
        elif suffix in {".geojson", ".json"}:
            kind = "vector"
            subkind = suffix.lstrip(".")
        else:
            kind = "file"
            subkind = suffix.lstrip(".") or "data"
        return self._register_product_internal(
            RegisterProductRequest(
                scenario_id=scenario.scenario_id,
                kind=kind,
                subkind=subkind,
                producer=Producer.MANUAL,
                crs=scenario.primary_dem_crs,
                footprint=scenario.primary_dem_footprint,
                lineage={"source": "scenario_filesystem_reconcile", "relative_path": relative_path},
            )
        )

    def _register_collection_product(
        self,
        *,
        scenario: Scenario,
        collection_rel_path: str,
    ) -> Product:
        for product in self._stores.products.values():
            if product.scenario_id != scenario.scenario_id:
                continue
            if product.lineage.get("collection_path") == collection_rel_path:
                return product
        return self._register_product_internal(
            RegisterProductRequest(
                scenario_id=scenario.scenario_id,
                kind="collection",
                subkind="directory",
                producer=Producer.MANUAL,
                crs=scenario.primary_dem_crs,
                footprint=scenario.primary_dem_footprint,
                lineage={
                    "source": "scenario_filesystem_reconcile",
                    "collection_path": collection_rel_path,
                },
            )
        )

    def discover_scenarios(
        self,
        request: DiscoverScenariosRequest,
    ) -> DiscoverScenariosResponse:
        candidate_dirs: list[Path] = []
        if request.scenario_roots:
            for root_name in request.scenario_roots:
                candidate = (self._stores.workspace_root / root_name).resolve()
                _ensure_within_root(self._stores.workspace_root, candidate)
                if candidate.exists() and candidate.is_dir():
                    candidate_dirs.append(candidate)
        else:
            for node in sorted(self._stores.workspace_root.iterdir()):
                if node.is_dir() and not node.name.startswith("."):
                    candidate_dirs.append(node.resolve())

        results: list[ScenarioDiscoveryResult] = []
        counters = {
            "discovered_count": 0,
            "ingested_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "error_count": 0,
        }

        for scenario_dir in candidate_dirs:
            counters["discovered_count"] += 1
            config_path = scenario_dir / "scenario.toml"
            if not config_path.exists() or not config_path.is_file():
                results.append(
                    ScenarioDiscoveryResult(
                        scenario_root=scenario_dir.name,
                        scenario_id=None,
                        status="skipped",
                        reason="scenario.toml not found",
                        warnings=[],
                    )
                )
                counters["skipped_count"] += 1
                continue
            result = self._ingest_from_scenario_toml_dir(
                scenario_dir=scenario_dir,
                dry_run=bool(request.dry_run),
                include_existing=bool(request.include_existing),
            )
            results.append(result)
            if result.status == "ingested":
                counters["ingested_count"] += 1
            elif result.status == "updated":
                counters["updated_count"] += 1
            elif result.status == "skipped":
                counters["skipped_count"] += 1
            elif result.status == "forgotten":
                counters["updated_count"] += 1
            elif result.status == "error":
                counters["error_count"] += 1

        if request.reconcile_missing:
            known_ids = sorted(self._stores.scenarios.keys())
            for known_id in known_ids:
                known = self._stores.scenarios.get(known_id)
                if known is None:
                    continue
                known_dir = Path(known.directory).resolve()
                if known_dir.exists() and known_dir.is_dir():
                    continue
                forgot = self.forget_scenario(known_id)
                results.append(
                    ScenarioDiscoveryResult(
                        scenario_root=known.scenario_root,
                        scenario_id=known_id,
                        status=forgot.status,
                        reason="scenario directory missing on disk",
                        warnings=[],
                    )
                )
                counters["updated_count"] += 1

        now = _utc_now()
        self._stores.discovery = DiscoveryRunRecord(
            last_run_utc=now,
            discovered_count=counters["discovered_count"],
            ingested_count=counters["ingested_count"],
            updated_count=counters["updated_count"],
            skipped_count=counters["skipped_count"],
            error_count=counters["error_count"],
            results=[item.model_dump() for item in results],
        )
        return DiscoverScenariosResponse(
            workspace_root=str(self._stores.workspace_root),
            last_run_utc=now,
            discovered_count=counters["discovered_count"],
            ingested_count=counters["ingested_count"],
            updated_count=counters["updated_count"],
            skipped_count=counters["skipped_count"],
            error_count=counters["error_count"],
            results=results,
        )

    def get_discovery_status(self) -> DiscoveryStatusResponse:
        record = self._stores.discovery
        return DiscoveryStatusResponse(
            workspace_root=str(self._stores.workspace_root),
            last_run_utc=record.last_run_utc,
            discovered_count=record.discovered_count,
            ingested_count=record.ingested_count,
            updated_count=record.updated_count,
            skipped_count=record.skipped_count,
            error_count=record.error_count,
            results=[ScenarioDiscoveryResult.model_validate(item) for item in record.results],
        )

    def reingest_scenario(
        self,
        scenario_id: str,
        request: ReingestScenarioRequest,
    ) -> ReingestScenarioResponse:
        scenario = self.get_scenario(scenario_id)
        scenario_dir = Path(scenario.directory).resolve()
        result = self._ingest_from_scenario_toml_dir(
            scenario_dir=scenario_dir,
            dry_run=bool(request.dry_run),
            include_existing=True,
        )
        status = "updated" if result.status in {"ingested", "updated"} else result.status
        return ReingestScenarioResponse(
            scenario_id=scenario_id,
            status=status,
            reason=result.reason,
            warnings=result.warnings,
        )

    def forget_scenario(self, scenario_id: str) -> ForgetScenarioResponse:
        scenario = self.get_scenario(scenario_id)

        self._stores.scenarios.pop(scenario_id, None)
        self._stores.scenario_roots.pop(scenario_id, None)
        self._stores.product_catalog_hydrated_scenarios.discard(scenario_id)

        product_ids = [
            product_id
            for product_id, product in self._stores.products.items()
            if product.scenario_id == scenario_id
        ]
        for product_id in product_ids:
            self._stores.products.pop(product_id, None)
            file_ids = [
                file_id
                for file_id, record in self._stores.product_files.items()
                if record.product_id == product_id
            ]
            for file_id in file_ids:
                self._stores.product_files.pop(file_id, None)

        layer_ids = [
            layer_id
            for layer_id, layer in self._stores.layers.items()
            if layer.scenario_id == scenario_id
        ]
        for layer_id in layer_ids:
            self._stores.layers.pop(layer_id, None)

        job_ids = [
            job_id
            for job_id, job in self._stores.jobs.items()
            if job.scenario_id == scenario_id
        ]
        for job_id in job_ids:
            self._stores.jobs.pop(job_id, None)
            self._stores.job_events.pop(job_id, None)

        with _connect_sqlite(self._stores.catalog_db_path) as conn:
            conn.execute("DELETE FROM scenario_catalog WHERE scenario_id = ?", (scenario_id,))
            conn.execute("DELETE FROM horizon_set_refs WHERE scenario_id = ?", (scenario_id,))
            conn.commit()

        logger.info("scenario forgotten scenario_id=%s directory=%s", scenario_id, scenario.directory)
        return ForgetScenarioResponse(scenario_id=scenario_id, status="forgotten")

    def resolve_scenario_root(self, scenario_id: str) -> Path:
        scenario = self.get_scenario(scenario_id)
        scenario_root = Path(scenario.directory).expanduser().resolve()
        _ensure_within_root(self._stores.workspace_root, scenario_root)
        return scenario_root

    def create_scenario_python_file(self, scenario_id: str, *, kind: str) -> Path:
        scenario_root = self.resolve_scenario_root(scenario_id)
        normalized_kind = str(kind or "").strip().lower()
        if normalized_kind == "notebook":
            return _create_unique_scenario_python_file(
                scenario_root,
                stem_prefix="notebook",
                suffix=".mo.py",
                initial_content=_default_marimo_notebook_template(),
            )
        if normalized_kind == "script":
            return _create_unique_scenario_python_file(
                scenario_root,
                stem_prefix="script",
                suffix=".py",
                initial_content=_default_python_script_template(),
            )
        raise ValueError(f"Unsupported python file kind: {kind}")

    def resolve_scenario_text_file(self, scenario_id: str, relative_path: str) -> Path:
        scenario_root = self.resolve_scenario_root(scenario_id)
        normalized_relative_path = _normalize_relative_path(relative_path)
        if not normalized_relative_path:
            raise ValueError("relative_path is required")
        target_path = (scenario_root / normalized_relative_path).resolve()
        _ensure_within_root(scenario_root, target_path)
        if not target_path.exists() or not target_path.is_file():
            raise KeyError(f"Scenario file not found: {normalized_relative_path}")
        if target_path.suffix.lower() != ".py":
            raise ValueError(f"Scenario file must be a Python file: {normalized_relative_path}")
        return target_path

    def read_scenario_text_file(self, scenario_id: str, relative_path: str) -> tuple[Path, str]:
        target_path = self.resolve_scenario_text_file(scenario_id, relative_path)
        return target_path, target_path.read_text(encoding="utf-8")

    def write_scenario_text_file(self, scenario_id: str, relative_path: str, content: str) -> Path:
        target_path = self.resolve_scenario_text_file(scenario_id, relative_path)
        target_path.write_text(content, encoding="utf-8")
        return target_path

    def resolve_scenario_file(self, scenario_id: str, relative_path: str) -> Path:
        scenario_root = self.resolve_scenario_root(scenario_id)
        normalized_relative_path = _normalize_relative_path(relative_path)
        if not normalized_relative_path:
            raise ValueError("relative_path is required")
        target_path = (scenario_root / normalized_relative_path).resolve()
        _ensure_within_root(scenario_root, target_path)
        if not target_path.exists() or not target_path.is_file():
            raise KeyError(f"Scenario file not found: {normalized_relative_path}")
        return target_path

    def resolve_scenario_editable_file(self, scenario_id: str, relative_path: str) -> Path:
        target_path = self.resolve_scenario_file(scenario_id, relative_path)
        if target_path.suffix.lower() not in {".txt", ".csv"}:
            raise ValueError(f"Scenario file must be editable text or csv: {relative_path}")
        return target_path

    def read_scenario_editable_file(self, scenario_id: str, relative_path: str) -> tuple[Path, str]:
        target_path = self.resolve_scenario_editable_file(scenario_id, relative_path)
        return target_path, target_path.read_text(encoding="utf-8")

    def write_scenario_editable_file(self, scenario_id: str, relative_path: str, content: str) -> Path:
        target_path = self.resolve_scenario_editable_file(scenario_id, relative_path)
        target_path.write_text(content, encoding="utf-8")
        return target_path

    def move_scenario_path(
        self,
        scenario_id: str,
        request: MoveScenarioPathRequest,
    ) -> MoveScenarioPathResponse:
        scenario = self.get_scenario(scenario_id)
        scenario_root = Path(scenario.directory).resolve()
        source_rel = _normalize_relative_path(request.source_relative_path)
        target_rel = _normalize_relative_path(request.target_relative_path)
        source_abs = (scenario_root / source_rel).resolve()
        target_abs = (scenario_root / target_rel).resolve()
        _ensure_within_root(scenario_root, source_abs)
        _ensure_within_root(scenario_root, target_abs)
        if not source_abs.exists():
            raise FileNotFoundError(f"Path not found: {source_rel}")
        if target_abs.exists():
            raise FileExistsError(f"Target already exists: {target_rel}")
        if source_rel.lower() in {"scenario.db", "scenario.toml"}:
            raise ValueError("Reserved scenario files cannot be moved.")
        if target_rel.lower() in {"scenario.db", "scenario.toml"}:
            raise ValueError("Reserved scenario files cannot be targeted.")

        moved_file_ids: list[str] = []
        old_by_file_id: dict[str, ProductFileRecord] = {}
        prefix = f"{source_rel}/"
        for file_id, record in self._stores.product_files.items():
            if record.scenario_id != scenario_id:
                continue
            rec_rel = _normalize_relative_path(record.relative_path)
            if rec_rel == source_rel:
                moved_file_ids.append(file_id)
                old_by_file_id[file_id] = record
            elif rec_rel.startswith(prefix):
                moved_file_ids.append(file_id)
                old_by_file_id[file_id] = record

        if source_abs.is_file() and not moved_file_ids:
            raise KeyError(f"No registered product file for source path: {source_rel}")
        if source_abs.is_dir() and not moved_file_ids:
            raise KeyError(f"No registered product files found under directory: {source_rel}")

        target_abs.parent.mkdir(parents=True, exist_ok=True)
        moved_on_disk = False
        try:
            source_abs.replace(target_abs)
            moved_on_disk = True

            for file_id in moved_file_ids:
                old = old_by_file_id[file_id]
                old_rel = _normalize_relative_path(old.relative_path)
                if old_rel == source_rel:
                    next_rel = target_rel
                else:
                    suffix = old_rel[len(prefix) :]
                    next_rel = _normalize_relative_path(f"{target_rel}/{suffix}")
                updated = ProductFileRecord(
                    file_id=old.file_id,
                    product_id=old.product_id,
                    scenario_id=old.scenario_id,
                    scenario_root=old.scenario_root,
                    relative_path=next_rel,
                    media_type=old.media_type,
                    role=old.role,
                    created_at_utc=old.created_at_utc,
                )
                self._stores.product_files[file_id] = updated
                self._persist_product_file(updated)

            self._validate_registered_path_collisions(scenario_id)

            affected_layers = [
                layer
                for layer in self._stores.layers.values()
                if layer.scenario_id == scenario_id and layer.source_file_id in set(moved_file_ids)
            ]
            for layer in affected_layers:
                self._emit_ws_event(
                    JobEventName.LAYER_UPDATED,
                    scenario_id,
                    {"layer_id": layer.layer_id, "reason": "source_path_moved"},
                )

            self.reconcile_scenario_filesystem(scenario_id, force=True)
            return MoveScenarioPathResponse(
                scenario_id=scenario_id,
                status="moved",
                source_relative_path=source_rel,
                target_relative_path=target_rel,
                moved_file_count=len(moved_file_ids),
                updated_layer_count=len(affected_layers),
            )
        except Exception:
            for file_id, record in old_by_file_id.items():
                self._stores.product_files[file_id] = record
                try:
                    self._persist_product_file(record)
                except Exception:
                    # Best-effort rollback for local db persistence; in-memory state is authoritative.
                    pass
            if moved_on_disk and target_abs.exists() and not source_abs.exists():
                target_abs.replace(source_abs)
            raise

    def import_geotiff(self, scenario_id: str, request: ImportGeoTiffRequest) -> Product:
        scenario = self.get_scenario(scenario_id)
        scenario_root = self._stores.scenario_roots[scenario_id]
        source_path = Path(request.source_path).expanduser().resolve()
        if not source_path.exists() or not source_path.is_file():
            raise FileNotFoundError(f"GeoTIFF not found: {source_path}")

        imports_dir = scenario_root
        if request.bypass_cog:
            output_name = f"{source_path.stem}.native.tif"
            output_path = _unique_file_path(imports_dir / output_name)
            _copy_file_bytes(source_path, output_path)
        else:
            output_name = f"{source_path.stem}.cog.tif"
            output_path = _unique_file_path(imports_dir / output_name)
            convert_geotiff_to_cog(source_path, output_path)

        rasterio = import_rasterio()
        with rasterio.open(output_path) as ds:
            crs = ds.crs.to_string() if ds.crs is not None else "unknown"
            left, bottom, right, top = ds.bounds
            footprint = {
                "type": "Polygon",
                "coordinates": [
                    [
                        [left, bottom],
                        [right, bottom],
                        [right, top],
                        [left, top],
                        [left, bottom],
                    ]
                ],
            }

        register = RegisterProductRequest(
            scenario_id=scenario_id,
            kind=request.kind,
            subkind=request.subkind,
            producer=request.producer,
            crs=crs,
            footprint=footprint,
            lineage={
                **request.lineage,
                "import_source_path": str(source_path),
                "import_bypass_cog": request.bypass_cog,
            },
        )
        product = self._register_product_internal(register)
        rel_path = output_path.relative_to(scenario_root).as_posix()
        self._register_file(
            product_id=product.product_id,
            scenario_id=scenario_id,
            scenario_root=scenario_root,
            relative_path=rel_path,
            media_type="image/tiff",
            role="primary",
        )
        self._record_import(
            scenario_root=scenario_root,
            scenario_id=scenario_id,
            source_path=source_path,
            output_file_id=self._latest_file_id_for_product(product.product_id),
        )
        return product

    def _emit_ws_event(self, event_name: JobEventName, scenario_id: str, data: dict[str, Any]) -> None:
        payload = WsEnvelope(
            event=event_name,
            scenario_id=scenario_id,
            timestamp_utc=_utc_now(),
            data=data,
        )
        self._stores.ws_events.append(payload.model_dump(mode="json"))
        _workspace_message_from_job_event(
            event_name,
            workspace_root=self._stores.workspace_root,
            scenario_id=scenario_id,
            data=data,
        )

    def _ingest_from_scenario_toml_dir(
        self,
        *,
        scenario_dir: Path,
        dry_run: bool,
        include_existing: bool,
    ) -> ScenarioDiscoveryResult:
        scenario_root = scenario_dir.name.strip().lower()
        if not re.fullmatch(r"^[a-z0-9][a-z0-9_-]{2,31}$", scenario_root):
            return ScenarioDiscoveryResult(
                scenario_root=scenario_dir.name,
                scenario_id=None,
                status="error",
                reason=(
                    "Directory name must match scenario_root pattern: "
                    "^[a-z0-9][a-z0-9_-]{2,31}$"
                ),
                warnings=[],
            )

        scenario_id = f"scn_{scenario_root}"
        config_path = scenario_dir / "scenario.toml"
        try:
            config = self._load_scenario_toml_config(config_path)
        except Exception as exc:
            return ScenarioDiscoveryResult(
                scenario_root=scenario_root,
                scenario_id=scenario_id,
                status="error",
                reason=str(exc),
                warnings=[],
            )

        if not include_existing and self._is_same_ingest_hash(
            scenario_root=scenario_root,
            scenario_id=scenario_id,
            config_sha256=config.raw_sha256,
        ):
            if scenario_id in self._stores.scenarios:
                changed = self.reconcile_scenario_filesystem(scenario_id)
                if not changed:
                    return ScenarioDiscoveryResult(
                        scenario_root=scenario_root,
                        scenario_id=scenario_id,
                        status="skipped",
                        reason="scenario.toml unchanged and scenario files unchanged",
                        warnings=[],
                    )

        if dry_run:
            status = "updated" if scenario_id in self._stores.scenarios else "ingested"
            return ScenarioDiscoveryResult(
                scenario_root=scenario_root,
                scenario_id=scenario_id,
                status=status,
                reason="dry_run",
                warnings=[],
            )

        scenario_existed = scenario_id in self._stores.scenarios
        scenario = self.create_scenario(
            CreateScenarioRequest(
                scenario_root=scenario_root,
                name=scenario_root,
                owner=str(config.metadata.get("owner", "scenario_toml")),
            )
        )
        scenario_dir = Path(scenario.directory).resolve()
        canonical_dem_rel = "dem.tif"
        canonical_dem_path = (scenario_dir / canonical_dem_rel).resolve()
        canonical_primary_path = self._canonicalize_primary_dem(
            source_path=config.primary_path,
            canonical_path=canonical_dem_path,
            scenario_dir=scenario_dir,
        )
        self._register_or_update_primary_dem_product(
            scenario=scenario,
            canonical_dem_path=canonical_primary_path,
            canonical_dem_rel=canonical_dem_rel,
        )
        self._upsert_bootstrap_metadata(
            scenario=scenario,
            config=config,
            canonical_dem_rel=canonical_dem_rel,
        )
        self.reconcile_scenario_filesystem(scenario.scenario_id, force=True)

        status = "updated" if scenario_existed else "ingested"
        return ScenarioDiscoveryResult(
            scenario_root=scenario_root,
            scenario_id=scenario.scenario_id,
            status=status,
            reason=None,
            warnings=[],
        )

    def _load_scenario_toml_config(self, config_path: Path) -> ScenarioTomlConfig:
        if not config_path.exists() or not config_path.is_file():
            raise FileNotFoundError(f"scenario.toml not found: {config_path}")
        raw_text = config_path.read_text(encoding="utf-8")
        payload = tomllib.loads(raw_text)
        raw_sha256 = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

        required_top = {"schema_version", "dem", "time_interval"}
        allowed_top = required_top | {"metadata"}
        unknown_top = set(payload.keys()) - allowed_top
        missing_top = required_top - set(payload.keys())
        if missing_top:
            raise ValueError(f"Missing required top-level keys: {sorted(missing_top)}")
        if unknown_top:
            raise ValueError(f"Unknown top-level keys: {sorted(unknown_top)}")

        schema_version = payload.get("schema_version")
        if not isinstance(schema_version, int) or schema_version != 1:
            raise ValueError("schema_version must be integer 1")

        dem_cfg = payload.get("dem")
        if not isinstance(dem_cfg, dict):
            raise ValueError("[dem] table is required")
        dem_allowed = {"primary_path", "surrounding_paths"}
        dem_unknown = set(dem_cfg.keys()) - dem_allowed
        if dem_unknown:
            raise ValueError(f"Unknown keys in [dem]: {sorted(dem_unknown)}")
        primary_path_raw = dem_cfg.get("primary_path")
        if not isinstance(primary_path_raw, str) or not primary_path_raw.strip():
            raise ValueError("[dem].primary_path must be a non-empty string")
        primary_path = self._resolve_config_path(config_path, primary_path_raw)
        if not primary_path.exists() or not primary_path.is_file():
            raise FileNotFoundError(f"[dem].primary_path does not exist: {primary_path}")

        surrounding_raw = dem_cfg.get("surrounding_paths", [])
        if not isinstance(surrounding_raw, list) or any(
            not isinstance(item, str) or not item.strip() for item in surrounding_raw
        ):
            raise ValueError("[dem].surrounding_paths must be an array of path strings")
        surrounding_paths = [
            self._resolve_config_path(config_path, item)
            for item in surrounding_raw
        ]
        for surrounding in surrounding_paths:
            if not surrounding.exists() or not surrounding.is_file():
                raise FileNotFoundError(f"surrounding DEM path does not exist: {surrounding}")

        interval_cfg = payload.get("time_interval")
        if not isinstance(interval_cfg, dict):
            raise ValueError("[time_interval] table is required")
        interval_allowed = {"start_utc", "stop_utc", "time_step_hours"}
        interval_unknown = set(interval_cfg.keys()) - interval_allowed
        if interval_unknown:
            raise ValueError(f"Unknown keys in [time_interval]: {sorted(interval_unknown)}")
        start_utc = interval_cfg.get("start_utc")
        stop_utc = interval_cfg.get("stop_utc")
        time_step_hours = interval_cfg.get("time_step_hours")
        if not isinstance(start_utc, str) or not isinstance(stop_utc, str):
            raise ValueError("[time_interval].start_utc and stop_utc must be strings")
        if not isinstance(time_step_hours, (int, float)):
            raise ValueError("[time_interval].time_step_hours must be numeric")
        if float(time_step_hours) <= 0.0:
            raise ValueError("[time_interval].time_step_hours must be > 0")
        start_dt = self._parse_iso_utc(start_utc, field="time_interval.start_utc")
        stop_dt = self._parse_iso_utc(stop_utc, field="time_interval.stop_utc")
        if start_dt >= stop_dt:
            raise ValueError("[time_interval] start_utc must be earlier than stop_utc")

        metadata_cfg = payload.get("metadata", {})
        if not isinstance(metadata_cfg, dict):
            raise ValueError("[metadata] must be a table when present")
        metadata_allowed = {"owner", "notes", "tags"}
        metadata_unknown = set(metadata_cfg.keys()) - metadata_allowed
        if metadata_unknown:
            raise ValueError(f"Unknown keys in [metadata]: {sorted(metadata_unknown)}")
        owner = metadata_cfg.get("owner")
        notes = metadata_cfg.get("notes")
        tags = metadata_cfg.get("tags", [])
        if owner is not None and (not isinstance(owner, str) or not owner.strip()):
            raise ValueError("[metadata].owner must be a non-empty string when present")
        if notes is not None and not isinstance(notes, str):
            raise ValueError("[metadata].notes must be a string when present")
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError("[metadata].tags must be an array of strings")

        return ScenarioTomlConfig(
            schema_version=1,
            primary_path=primary_path,
            surrounding_paths=surrounding_paths,
            time_start_utc=start_utc,
            time_stop_utc=stop_utc,
            time_step_hours=float(time_step_hours),
            metadata={
                "owner": owner.strip() if isinstance(owner, str) else None,
                "notes": notes,
                "tags": tags,
            },
            raw_sha256=raw_sha256,
            config_path=config_path.resolve(),
        )

    def _parse_iso_utc(self, value: str, *, field: str) -> datetime:
        candidate = value.strip()
        has_z = candidate.endswith("Z")
        if has_z:
            parse_candidate = candidate.replace("Z", "+00:00")
        else:
            parse_candidate = candidate
        try:
            parsed = datetime.fromisoformat(parse_candidate)
        except ValueError as exc:
            raise ValueError(
                f"{field} must be ISO 8601 UTC timestamp (optional trailing 'Z')"
            ) from exc
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError(
                f"{field} timezone must be UTC; if timezone symbol is present it must be 'Z'"
            )
        if not has_z and ("+" in candidate[10:] or "-" in candidate[10:]):
            raise ValueError(
                f"{field} timezone must be UTC; if timezone symbol is present it must be 'Z'"
            )
        return parsed.astimezone(timezone.utc)

    def _resolve_config_path(self, config_path: Path, value: str) -> Path:
        raw = Path(value.strip()).expanduser()
        if raw.is_absolute():
            return raw.resolve()
        return (config_path.parent / raw).resolve()

    def _is_same_ingest_hash(
        self,
        *,
        scenario_root: str,
        scenario_id: str,
        config_sha256: str,
    ) -> bool:
        scenario_dir = (self._stores.workspace_root / scenario_root).resolve()
        db_path = scenario_dir / "scenario.db"
        if not db_path.exists():
            return False
        ensure_schema(db_path)
        with _connect_sqlite(db_path) as conn:
            row = conn.execute(
                """
                SELECT config_sha256
                FROM scenario_bootstrap_metadata
                WHERE scenario_id = ?
                """,
                (scenario_id,),
            ).fetchone()
        if row is None:
            return False
        return str(row[0]) == config_sha256

    def _canonicalize_primary_dem(
        self,
        *,
        source_path: Path,
        canonical_path: Path,
        scenario_dir: Path,
    ) -> Path:
        source = source_path.resolve()
        canonical = canonical_path.resolve()
        _ensure_within_root(scenario_dir, canonical)
        if source == canonical:
            return canonical
        source_inside_scenario = source == scenario_dir or scenario_dir in source.parents
        canonical.parent.mkdir(parents=True, exist_ok=True)
        if source_inside_scenario:
            if source.exists():
                if canonical.exists():
                    canonical.unlink()
                source.rename(canonical)
            elif canonical.exists():
                return canonical
            return canonical
        _copy_file_bytes(source, canonical)
        return canonical

    def _register_or_update_primary_dem_product(
        self,
        *,
        scenario: Scenario,
        canonical_dem_path: Path,
        canonical_dem_rel: str,
    ) -> None:
        existing_product: Product | None = None
        for product in self._stores.products.values():
            if (
                product.scenario_id == scenario.scenario_id
                and product.kind == "dem"
                and product.subkind == "primary"
            ):
                existing_product = product
                break

        if existing_product is None:
            rasterio = import_rasterio()
            with rasterio.open(canonical_dem_path) as ds:
                crs = ds.crs.to_string() if ds.crs is not None else scenario.primary_dem_crs
                left, bottom, right, top = ds.bounds
            footprint = {
                "type": "Polygon",
                "coordinates": [
                    [
                        [left, bottom],
                        [right, bottom],
                        [right, top],
                        [left, top],
                        [left, bottom],
                    ]
                ],
            }
            existing_product = self._register_product_internal(
                RegisterProductRequest(
                    scenario_id=scenario.scenario_id,
                    kind="dem",
                    subkind="primary",
                    producer=Producer.IMPORT,
                    crs=crs,
                    footprint=footprint,
                    lineage={"source": "scenario_toml"},
                )
            )

        existing_file_id: str | None = None
        for file_id, record in self._stores.product_files.items():
            if (
                record.product_id == existing_product.product_id
                and record.relative_path == canonical_dem_rel
            ):
                existing_file_id = file_id
                break
        if existing_file_id is None:
            self._register_file(
                product_id=existing_product.product_id,
                scenario_id=scenario.scenario_id,
                scenario_root=Path(scenario.directory).resolve(),
                relative_path=canonical_dem_rel,
                media_type="image/tiff",
                role="primary",
            )

    def _register_or_update_canonical_hillshade_product(
        self,
        *,
        scenario: Scenario,
    ) -> None:
        scenario_root = Path(scenario.directory).resolve()
        hillshade_rel = "hillshade.tif"
        hillshade_path = (scenario_root / hillshade_rel).resolve()
        _ensure_within_root(scenario_root, hillshade_path)
        if not hillshade_path.exists() or not hillshade_path.is_file():
            return

        existing_product: Product | None = None
        for product in self._stores.products.values():
            if (
                product.scenario_id == scenario.scenario_id
                and product.kind == "lighting"
                and product.subkind == "hillshade"
            ):
                existing_product = product
                break
        if existing_product is None:
            rasterio = import_rasterio()
            with rasterio.open(hillshade_path) as ds:
                crs = ds.crs.to_string() if ds.crs is not None else scenario.primary_dem_crs
                left, bottom, right, top = ds.bounds
            footprint = {
                "type": "Polygon",
                "coordinates": [
                    [
                        [left, bottom],
                        [right, bottom],
                        [right, top],
                        [left, top],
                        [left, bottom],
                    ]
                ],
            }
            existing_product = self._register_product_internal(
                RegisterProductRequest(
                    scenario_id=scenario.scenario_id,
                    kind="lighting",
                    subkind="hillshade",
                    producer=Producer.IMPORT,
                    crs=crs,
                    footprint=footprint,
                    lineage={"source": "scenario_filesystem_reconcile"},
                )
            )
        for record in self._stores.product_files.values():
            if (
                record.product_id == existing_product.product_id
                and record.relative_path == hillshade_rel
            ):
                return
        self._register_file(
            product_id=existing_product.product_id,
            scenario_id=scenario.scenario_id,
            scenario_root=scenario_root,
            relative_path=hillshade_rel,
            media_type="image/tiff",
            role="primary",
        )

    def _has_registered_relative_path(self, *, scenario_id: str, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/").lower()
        for record in self._stores.product_files.values():
            if record.scenario_id != scenario_id:
                continue
            if record.relative_path.replace("\\", "/").lower() == normalized:
                return True
        return False

    def _scenario_needs_reconcile(
        self,
        *,
        scenario_id: str,
        scenario_dir: Path,
        file_count: int,
        size_bytes: int,
        last_touched_utc: str,
    ) -> bool:
        _ = file_count  # reserved for future signature persistence
        scenario = self._stores.scenarios[scenario_id]
        if scenario.size_bytes != size_bytes:
            return True
        if scenario.last_touched_utc != last_touched_utc:
            return True
        dem_rel = _normalize_primary_dem_path(scenario.primary_dem_path)
        dem_path = (scenario_dir / dem_rel).resolve()
        if dem_path.exists() and dem_path.is_file() and not self._has_registered_relative_path(
            scenario_id=scenario_id, relative_path=dem_rel
        ):
            return True
        hillshade_path = (scenario_dir / "hillshade.tif").resolve()
        if hillshade_path.exists() and hillshade_path.is_file() and not self._has_registered_relative_path(
            scenario_id=scenario_id, relative_path="hillshade.tif"
        ):
            return True
        scenario_db = (scenario_dir / "scenario.db").resolve()
        if not scenario_db.exists():
            return True
        return False

    def _upsert_bootstrap_metadata(
        self,
        *,
        scenario: Scenario,
        config: ScenarioTomlConfig,
        canonical_dem_rel: str,
    ) -> None:
        db_path = Path(scenario.directory).resolve() / "scenario.db"
        ensure_schema(db_path)
        now = _utc_now()
        surrounding_abs = [str(path.resolve()) for path in config.surrounding_paths]
        config_rel = config.config_path.relative_to(Path(scenario.directory).resolve()).as_posix()
        with _connect_sqlite(db_path) as conn:
            row = conn.execute(
                "SELECT created_at_utc FROM scenario_bootstrap_metadata WHERE scenario_id = ?",
                (scenario.scenario_id,),
            ).fetchone()
            created_at = row[0] if row is not None else now
            conn.execute(
                """
                INSERT OR REPLACE INTO scenario_bootstrap_metadata(
                    scenario_id, config_rel_path, config_sha256,
                    dem_primary_original_path, dem_primary_canonical_relative_path,
                    surrounding_dem_paths_json, time_start_utc, time_end_utc, time_stop_utc,
                    time_step_hours, metadata_json, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scenario.scenario_id,
                    config_rel,
                    config.raw_sha256,
                    str(config.primary_path),
                    canonical_dem_rel,
                    json.dumps(surrounding_abs),
                    config.time_start_utc,
                    config.time_stop_utc,
                    config.time_stop_utc,
                    config.time_step_hours,
                    json.dumps(config.metadata, sort_keys=True),
                    created_at,
                    now,
                ),
            )
            conn.commit()

    def _ensure_catalog_schema(self) -> None:
        self._stores.catalog_db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect_sqlite(self._stores.catalog_db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS scenario_catalog (
                    scenario_id TEXT PRIMARY KEY,
                    scenario_root TEXT NOT NULL,
                    name TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    directory TEXT NOT NULL,
                    primary_dem_path TEXT NOT NULL,
                    primary_dem_crs TEXT NOT NULL,
                    primary_dem_footprint TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    last_touched_utc TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS horizon_sets (
                    horizon_key TEXT PRIMARY KEY,
                    key_version INTEGER NOT NULL,
                    algorithm_id TEXT NOT NULL,
                    algorithm_version TEXT NOT NULL,
                    dem_content_sha256 TEXT NOT NULL,
                    dem_crs TEXT NOT NULL,
                    dem_geotransform_hash TEXT NOT NULL,
                    analysis_extent_hash TEXT NOT NULL,
                    observer_policy_json TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    storage_rel_dir TEXT NOT NULL,
                    status TEXT NOT NULL,
                    file_count INTEGER NOT NULL DEFAULT 0,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    last_accessed_at_utc TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_horizon_sets_dem_hash
                ON horizon_sets (dem_content_sha256);

                CREATE INDEX IF NOT EXISTS idx_horizon_sets_status
                ON horizon_sets (status);

                CREATE TABLE IF NOT EXISTS horizon_set_refs (
                    scenario_id TEXT NOT NULL,
                    horizon_key TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    access_mode TEXT NOT NULL,
                    materialized_relative_path TEXT,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (scenario_id, product_id)
                );

                CREATE INDEX IF NOT EXISTS idx_horizon_set_refs_horizon_key
                ON horizon_set_refs (horizon_key);

                CREATE INDEX IF NOT EXISTS idx_horizon_set_refs_scenario_id
                ON horizon_set_refs (scenario_id);

                CREATE TABLE IF NOT EXISTS moon_trek_catalog_cache (
                    cache_key TEXT PRIMARY KEY,
                    layers_json TEXT NOT NULL,
                    fetched_at_utc TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_moon_trek_catalog_cache_expires
                ON moon_trek_catalog_cache (expires_at_utc);

                CREATE TABLE IF NOT EXISTS lunar_features (
                    feature_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    clean_name TEXT NOT NULL,
                    feature_type TEXT,
                    diameter_km REAL,
                    importance_score REAL,
                    description TEXT,
                    center_x REAL,
                    center_y REAL,
                    min_x REAL,
                    min_y REAL,
                    max_x REAL,
                    max_y REAL,
                    origin_description TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_lunar_features_clean_name
                ON lunar_features(clean_name);

                CREATE INDEX IF NOT EXISTS idx_lunar_features_type
                ON lunar_features(feature_type);

                CREATE INDEX IF NOT EXISTS idx_lunar_features_importance
                ON lunar_features(importance_score);

                CREATE VIRTUAL TABLE IF NOT EXISTS lunar_features_fts USING fts5(
                    name,
                    clean_name,
                    feature_type,
                    content='lunar_features',
                    content_rowid='feature_id'
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS lunar_features_rtree USING rtree(
                    feature_id,
                    min_x, max_x,
                    min_y, max_y
                );

                CREATE TABLE IF NOT EXISTS nomenclature_dataset_metadata (
                    dataset_key TEXT PRIMARY KEY,
                    source_uri TEXT NOT NULL,
                    source_revision TEXT,
                    source_sha256 TEXT,
                    ingested_at_utc TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def _load_catalog(self) -> None:
        if not self._stores.catalog_db_path.exists():
            return
        with _connect_sqlite(self._stores.catalog_db_path) as conn:
            rows = conn.execute(
                """
                SELECT
                    scenario_id, scenario_root, name, owner, directory, primary_dem_path,
                    primary_dem_crs, primary_dem_footprint, size_bytes, last_touched_utc,
                    created_at_utc, updated_at_utc
                FROM scenario_catalog
                """
            ).fetchall()
        for row in rows:
            scenario = Scenario(
                scenario_id=row[0],
                scenario_root=row[1],
                name=row[2],
                owner=row[3],
                directory=row[4],
                primary_dem_path=_normalize_primary_dem_path(str(row[5])),
                primary_dem_crs=row[6],
                primary_dem_footprint=json.loads(row[7]),
                size_bytes=int(row[8]),
                last_touched_utc=row[9],
                created_at_utc=row[10],
                updated_at_utc=row[11],
            )
            self._stores.scenarios[scenario.scenario_id] = scenario
            self._stores.scenario_roots[scenario.scenario_id] = Path(scenario.directory).resolve()

    def _upsert_catalog_scenario(self, scenario: Scenario) -> None:
        with _connect_sqlite(self._stores.catalog_db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scenario_catalog (
                    scenario_id, scenario_root, name, owner, directory,
                    primary_dem_path, primary_dem_crs, primary_dem_footprint,
                    size_bytes, last_touched_utc, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scenario.scenario_id,
                    scenario.scenario_root,
                    scenario.name,
                    scenario.owner,
                    scenario.directory,
                    scenario.primary_dem_path,
                    scenario.primary_dem_crs,
                    json.dumps(scenario.primary_dem_footprint.model_dump()),
                    scenario.size_bytes,
                    scenario.last_touched_utc,
                    scenario.created_at_utc,
                    scenario.updated_at_utc,
                ),
            )
            conn.commit()

    def _persist_scenario_to_local_db(self, scenario: Scenario) -> None:
        scenario_root = self._stores.scenario_roots[scenario.scenario_id]
        db_path = scenario_root / "scenario.db"
        ensure_schema(db_path)
        with _connect_sqlite(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scenarios (
                    scenario_id, scenario_root, name, owner, directory,
                    primary_dem_path, primary_dem_crs, primary_dem_footprint,
                    size_bytes, last_touched_utc, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scenario.scenario_id,
                    scenario.scenario_root,
                    scenario.name,
                    scenario.owner,
                    scenario.directory,
                    scenario.primary_dem_path,
                    scenario.primary_dem_crs,
                    json.dumps(scenario.primary_dem_footprint.model_dump()),
                    scenario.size_bytes,
                    scenario.last_touched_utc,
                    scenario.created_at_utc,
                    scenario.updated_at_utc,
                ),
            )
            conn.commit()

    def _register_product_internal(self, request: RegisterProductRequest) -> Product:
        if request.scenario_id not in self._stores.scenarios:
            raise KeyError(f"Scenario not found: {request.scenario_id}")
        product_id = f"prd_{uuid4().hex[:12]}"
        created_at = _utc_now()
        product = Product(
            product_id=product_id,
            scenario_id=request.scenario_id,
            kind=request.kind,
            subkind=request.subkind,
            producer=request.producer,
            crs=request.crs,
            footprint=request.footprint,
            created_at_utc=created_at,
            lineage=request.lineage,
        )
        self._stores.products[product_id] = product
        self._persist_product(product)
        return product

    def _register_file(
        self,
        *,
        product_id: str,
        scenario_id: str,
        scenario_root: Path,
        relative_path: str,
        media_type: str,
        role: str,
    ) -> ProductFileRecord:
        file_id = f"fil_{uuid4().hex[:16]}"
        record = ProductFileRecord(
            file_id=file_id,
            product_id=product_id,
            scenario_id=scenario_id,
            scenario_root=scenario_root,
            relative_path=relative_path,
            media_type=media_type,
            role=role,
            created_at_utc=_utc_now(),
        )
        self._stores.product_files[file_id] = record
        self._persist_product_file(record)
        return record

    def _latest_file_id_for_product(self, product_id: str) -> str:
        matches = [r for r in self._stores.product_files.values() if r.product_id == product_id]
        if not matches:
            raise KeyError(f"No files registered for product: {product_id}")
        return sorted(matches, key=lambda m: m.created_at_utc)[-1].file_id

    def _record_import(
        self,
        *,
        scenario_root: Path,
        scenario_id: str,
        source_path: Path,
        output_file_id: str,
    ) -> None:
        db_path = scenario_root / "scenario.db"
        ensure_schema(db_path)
        with _connect_sqlite(db_path) as conn:
            conn.execute(
                """
                INSERT INTO imports(import_id, scenario_id, source_path, output_file_id, created_at_utc)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    f"imp_{uuid4().hex[:12]}",
                    scenario_id,
                    str(source_path),
                    output_file_id,
                    _utc_now(),
                ),
            )
            conn.commit()

    def _persist_product(self, product: Product) -> None:
        scenario_root = self._stores.scenario_roots[product.scenario_id]
        db_path = scenario_root / "scenario.db"
        ensure_schema(db_path)
        with _connect_sqlite(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO products(
                    product_id, scenario_id, kind, subkind, producer, crs, footprint, created_at_utc, lineage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product.product_id,
                    product.scenario_id,
                    product.kind,
                    product.subkind,
                    product.producer.value,
                    product.crs,
                    json.dumps(product.footprint.model_dump()),
                    product.created_at_utc,
                    json.dumps(product.lineage),
                ),
            )
            conn.commit()

    def _persist_product_file(self, record: ProductFileRecord) -> None:
        db_path = self._stores.scenario_roots[record.scenario_id] / "scenario.db"
        ensure_schema(db_path)
        with _connect_sqlite(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO product_files(
                    file_id, product_id, scenario_id, relative_path, media_type, role, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.file_id,
                    record.product_id,
                    record.scenario_id,
                    record.relative_path,
                    record.media_type,
                    record.role,
                    record.created_at_utc,
                ),
            )
            conn.commit()

    def _delete_product_file_from_local_db(self, scenario_id: str, file_id: str) -> None:
        db_path = self._stores.scenario_roots[scenario_id] / "scenario.db"
        ensure_schema(db_path)
        with _connect_sqlite(db_path) as conn:
            conn.execute("DELETE FROM product_files WHERE file_id = ?", (file_id,))
            conn.commit()

    def _delete_product_from_local_db(self, scenario_id: str, product_id: str) -> None:
        db_path = self._stores.scenario_roots[scenario_id] / "scenario.db"
        ensure_schema(db_path)
        with _connect_sqlite(db_path) as conn:
            conn.execute("DELETE FROM products WHERE product_id = ?", (product_id,))
            conn.commit()


class ProductService:
    def __init__(self, stores: InMemoryStores, scenario_service: ScenarioService) -> None:
        self._stores = stores
        self._scenario_service = scenario_service

    def register_product(self, request: RegisterProductRequest) -> Product:
        return self._scenario_service._register_product_internal(request)

    def get_product(self, product_id: str) -> Product:
        if product_id not in self._stores.products:
            raise KeyError(f"Product not found: {product_id}")
        return self._stores.products[product_id]

    def delete_product(self, product_id: str) -> None:
        product = self._stores.products.pop(product_id, None)
        if product is None:
            raise KeyError(f"Product not found: {product_id}")
        scenario_id = product.scenario_id
        file_ids = [
            file_id
            for file_id, record in self._stores.product_files.items()
            if record.product_id == product_id
        ]
        for file_id in file_ids:
            self._stores.product_files.pop(file_id, None)
            self._scenario_service._delete_product_file_from_local_db(scenario_id, file_id)
        self._scenario_service._delete_product_from_local_db(scenario_id, product_id)

    def list_products(self, scenario_id: str) -> list[Product]:
        self._scenario_service.reconcile_scenario_filesystem(scenario_id, force=True)
        self._scenario_service.ensure_product_catalog_hydrated(scenario_id)
        return [p for p in self._stores.products.values() if p.scenario_id == scenario_id]

    def list_explorer_nodes(self, scenario_id: str, *, include_hidden: bool = False) -> list[ExplorerNode]:
        scenario = self._scenario_service.get_scenario(scenario_id)
        self._scenario_service.reconcile_scenario_filesystem(scenario_id, force=True)
        scenario_root = Path(scenario.directory).resolve()
        products_by_id = {
            product.product_id: product
            for product in self._stores.products.values()
            if product.scenario_id == scenario_id
        }

        records_by_path: dict[str, ProductFileRecord] = {}
        for record in sorted(
            (r for r in self._stores.product_files.values() if r.scenario_id == scenario_id),
            key=lambda r: r.created_at_utc,
            reverse=True,
        ):
            records_by_path.setdefault(_normalize_relative_path(record.relative_path).lower(), record)

        collection_paths = {
            _normalize_relative_path(str(product.lineage.get("collection_path", "")))
            for product in products_by_id.values()
            if product.kind == "collection" and product.subkind == "directory"
            and isinstance(product.lineage.get("collection_path"), str)
            and str(product.lineage.get("collection_path")).strip()
        }

        discovered_dirs: set[str] = set()
        file_rows: list[ExplorerNode] = []
        for node in sorted(scenario_root.rglob("*")):
            rel = _normalize_relative_path(node.relative_to(scenario_root).as_posix())
            if node.is_dir():
                discovered_dirs.add(rel)
                continue
            if not node.is_file():
                continue
            hidden = _is_hidden_default_path(rel)
            if hidden and not include_hidden:
                continue
            is_renderable = _is_renderable_relative_path(rel)
            rec = records_by_path.get(rel.lower())
            product = products_by_id.get(rec.product_id) if rec is not None else None
            try:
                stat_result = node.stat()
                size_bytes = stat_result.st_size
                modified_at_utc = _utc_from_timestamp(stat_result.st_mtime)
            except (OSError, FileNotFoundError):
                size_bytes = 0
                modified_at_utc = None

            file_rows.append(
                ExplorerNode(
                    node_type=ExplorerNodeType.FILE,
                    name=node.name,
                    relative_path=rel,
                    parent_relative_path=_parent_relative_path(rel),
                    is_renderable=is_renderable,
                    is_hidden_default=hidden,
                    product_id=rec.product_id if rec is not None else None,
                    file_id=rec.file_id if rec is not None else None,
                    kind=product.kind if product is not None else None,
                    subkind=product.subkind if product is not None else None,
                    created_at_utc=rec.created_at_utc if rec is not None else None,
                    modified_at_utc=modified_at_utc,
                    size_bytes=size_bytes,
                )
            )

        folder_rows: list[ExplorerNode] = []
        for rel in sorted(discovered_dirs):
            hidden = _is_hidden_default_path(rel)
            if hidden and not include_hidden:
                continue
            folder_abs = (scenario_root / rel).resolve()
            try:
                child_count = sum(1 for _ in folder_abs.iterdir()) if folder_abs.exists() else 0
            except (OSError, FileNotFoundError):
                child_count = 0
            
            node_type = (
                ExplorerNodeType.COLLECTION
                if rel in collection_paths
                else ExplorerNodeType.FOLDER
            )
            folder_rows.append(
                ExplorerNode(
                    node_type=node_type,
                    name=Path(rel).name,
                    relative_path=rel,
                    parent_relative_path=_parent_relative_path(rel),
                    is_renderable=False,
                    is_hidden_default=hidden,
                    child_count=child_count,
                )
            )

        scenario_row = ExplorerNode(
            node_type=ExplorerNodeType.SCENARIO,
            name=scenario.name,
            relative_path="",
            parent_relative_path=None,
            is_renderable=False,
            is_hidden_default=False,
            created_at_utc=scenario.created_at_utc,
            size_bytes=scenario.size_bytes,
        )
        nodes = [scenario_row, *folder_rows, *file_rows]
        nodes.sort(
            key=lambda n: (
                0 if n.node_type == ExplorerNodeType.SCENARIO else 1,
                n.relative_path.lower(),
                n.name.lower(),
            )
        )
        return nodes

    def get_file_record(self, file_id: str) -> ProductFileRecord:
        record = self._stores.product_files.get(file_id)
        if record is None:
            raise KeyError(f"File not found: {file_id}")
        return record

    def list_product_files(self, product_id: str) -> list[ProductFile]:
        records = [
            rec
            for rec in self._stores.product_files.values()
            if rec.product_id == product_id
        ]
        records.sort(key=lambda rec: rec.created_at_utc)
        return [
            ProductFile(
                file_id=rec.file_id,
                product_id=rec.product_id,
                scenario_id=rec.scenario_id,
                relative_path=rec.relative_path,
                media_type=rec.media_type,
                role=rec.role,
                created_at_utc=rec.created_at_utc,
            )
            for rec in records
        ]

    def resolve_file_path(self, file_id: str) -> tuple[Path, ProductFileRecord]:
        record = self.get_file_record(file_id)
        candidate = (record.scenario_root / record.relative_path).resolve()
        _ensure_within_root(record.scenario_root, candidate)
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"Registered file missing on disk: {candidate}")
        return candidate, record


class LayerService:
    def __init__(
        self,
        stores: InMemoryStores,
        scenario_service: ScenarioService,
        *,
        layer_state_repository: LayerStateRepository | None = None,
    ) -> None:
        self._stores = stores
        self._scenario_service = scenario_service
        self._layer_state_repository = layer_state_repository or LayerStateRepository()
        self._hydrated_scenarios: set[str] = set()

    def hydrate_layers_from_db(self, scenario_id: str) -> None:
        self._scenario_service.ensure_product_catalog_hydrated(scenario_id)
        scenario = self._stores.scenarios.get(scenario_id)
        if scenario is None:
            return
        scenario_root = Path(scenario.directory).resolve()
        self._stores.scenario_roots[scenario_id] = scenario_root
        _ensure_within_root(self._stores.workspace_root, scenario_root)
        db_path = scenario_root / "scenario.db"
        rows = self._layer_state_repository.list_layers_for_scenario(db_path, scenario_id)

        stale_layer_ids: list[str] = []
        hydrated: dict[str, LayerState] = {}
        for row in rows:
            layer_id = str(row[0])
            product_id = str(row[2]) if row[2] is not None else None
            source_file_id_raw = str(row[8])
            source_file_id = source_file_id_raw
            if not self._is_source_file_id_resolvable(scenario_id, source_file_id):
                rebound_file_id = self._latest_resolvable_file_id_for_product(scenario_id, product_id)
                if rebound_file_id is None:
                    stale_layer_ids.append(layer_id)
                    continue
                logger.info(
                    "layer hydrate source rebound scenario_id=%s layer_id=%s source_file_id=%s -> %s product_id=%s",
                    scenario_id,
                    layer_id,
                    source_file_id,
                    rebound_file_id,
                    product_id,
                )
                source_file_id = rebound_file_id

            try:
                style_payload = json.loads(str(row[9])) if row[9] is not None else {}
                if not isinstance(style_payload, dict):
                    raise ValueError("layer style must deserialize to an object")
                hydrated_layer = LayerState(
                    layer_id=layer_id,
                    scenario_id=str(row[1]),
                    product_id=product_id,
                    title=str(row[3]),
                    visible=bool(int(row[4])),
                    opacity=float(row[5]),
                    z_index=int(row[6]),
                    render_mode=RenderMode(str(row[7])),
                    source_file_id=source_file_id,
                    style=style_payload,
                    updated_at_utc=str(row[10]),
                )
                hydrated[layer_id] = hydrated_layer
                if source_file_id != source_file_id_raw:
                    self._persist_layer(
                        hydrated_layer.model_copy(update={"updated_at_utc": _utc_now()})
                    )
            except Exception:
                stale_layer_ids.append(layer_id)
                continue

        for layer_id, layer in list(self._stores.layers.items()):
            if layer.scenario_id == scenario_id:
                self._stores.layers.pop(layer_id, None)
        self._stores.layers.update(hydrated)

        if stale_layer_ids:
            self._delete_layer_rows_from_db(scenario_id, stale_layer_ids)
        self._hydrated_scenarios.add(scenario_id)

    def create_layer(self, request: CreateLayerStateRequest) -> LayerState:
        if request.scenario_id not in self._stores.scenarios:
            raise KeyError(f"Scenario not found: {request.scenario_id}")
        if request.scenario_id not in self._hydrated_scenarios:
            self.hydrate_layers_from_db(request.scenario_id)
        record = self._stores.product_files.get(request.source_file_id)
        if record is None:
            raise KeyError(f"File not found: {request.source_file_id}")
        if record.scenario_id != request.scenario_id:
            raise KeyError(
                "File not found in scenario: "
                f"source_file_id={request.source_file_id} scenario_id={request.scenario_id}"
            )
        if request.product_id is not None and record.product_id != request.product_id:
            raise KeyError(
                "Product/file mismatch for layer create: "
                f"product_id={request.product_id} source_file_id={request.source_file_id}"
            )

        layer = LayerState(
            layer_id=f"lyr_{uuid4().hex[:12]}",
            scenario_id=request.scenario_id,
            product_id=request.product_id,
            title=request.title,
            visible=request.visible,
            opacity=request.opacity,
            z_index=request.z_index,
            render_mode=request.render_mode,
            source_file_id=request.source_file_id,
            style=request.style,
            updated_at_utc=_utc_now(),
        )
        self._stores.layers[layer.layer_id] = layer
        self._emit_layer_event(layer, JobEventName.LAYER_ADDED)
        self._persist_layer(layer)
        return layer

    def update_layer(self, layer_id: str, request: UpdateLayerStateRequest) -> LayerState:
        if layer_id not in self._stores.layers:
            self._hydrate_all_scenarios()
        if layer_id not in self._stores.layers:
            raise KeyError(f"Layer not found: {layer_id}")
        layer = self._stores.layers[layer_id]
        data = request.model_dump(exclude_none=True)
        updated = layer.model_copy(update={**data, "updated_at_utc": _utc_now()})
        self._stores.layers[layer_id] = updated
        self._emit_layer_event(updated, JobEventName.LAYER_UPDATED)
        self._persist_layer(updated)
        return updated

    def delete_layer(self, layer_id: str) -> None:
        if layer_id not in self._stores.layers:
            self._hydrate_all_scenarios()
        if layer_id not in self._stores.layers:
            raise KeyError(f"Layer not found: {layer_id}")
        layer = self._stores.layers.pop(layer_id)
        self._emit_layer_event(layer, JobEventName.LAYER_REMOVED)
        self._delete_layer_from_db(layer)

    def list_layers(self, scenario_id: str) -> list[LayerState]:
        if scenario_id not in self._hydrated_scenarios:
            self.hydrate_layers_from_db(scenario_id)
        self._revalidate_scenario_layers(scenario_id)
        return sorted(
            [l for l in self._stores.layers.values() if l.scenario_id == scenario_id],
            key=lambda l: l.z_index,
        )

    def _revalidate_scenario_layers(self, scenario_id: str) -> None:
        scenario_layers = [l for l in self._stores.layers.values() if l.scenario_id == scenario_id]
        if not scenario_layers:
            return

        stale_layer_ids: list[str] = []
        for layer in scenario_layers:
            if self._is_layer_source_resolvable(layer):
                continue
            rebound = self._rebind_layer_source_file(layer)
            if rebound is None:
                stale_layer_ids.append(layer.layer_id)
                logger.info(
                    "layer source unresolved; dropping stale layer scenario_id=%s layer_id=%s source_file_id=%s product_id=%s",
                    scenario_id,
                    layer.layer_id,
                    layer.source_file_id,
                    layer.product_id,
                )
                continue
            self._stores.layers[layer.layer_id] = rebound
            self._persist_layer(rebound)
            logger.info(
                "layer source rebound scenario_id=%s layer_id=%s source_file_id=%s -> %s product_id=%s",
                scenario_id,
                layer.layer_id,
                layer.source_file_id,
                rebound.source_file_id,
                rebound.product_id,
            )

        if stale_layer_ids:
            for layer_id in stale_layer_ids:
                self._stores.layers.pop(layer_id, None)
            self._delete_layer_rows_from_db(scenario_id, stale_layer_ids)

    def _is_layer_source_resolvable(self, layer: LayerState) -> bool:
        return self._is_source_file_id_resolvable(layer.scenario_id, layer.source_file_id)

    def _rebind_layer_source_file(self, layer: LayerState) -> LayerState | None:
        rebound_file_id = self._latest_resolvable_file_id_for_product(layer.scenario_id, layer.product_id)
        if rebound_file_id is not None:
            return layer.model_copy(update={"source_file_id": rebound_file_id, "updated_at_utc": _utc_now()})
        return None

    def _latest_resolvable_file_id_for_product(
        self,
        scenario_id: str,
        product_id: str | None,
    ) -> str | None:
        if not product_id:
            return None
        candidates = [
            record
            for record in self._stores.product_files.values()
            if record.scenario_id == scenario_id and record.product_id == product_id
        ]
        for record in sorted(candidates, key=lambda entry: entry.created_at_utc, reverse=True):
            if self._is_source_file_id_resolvable(scenario_id, record.file_id):
                return record.file_id
        return None

    def _is_source_file_id_resolvable(self, scenario_id: str, source_file_id: str) -> bool:
        file_record = self._stores.product_files.get(source_file_id)
        if file_record is None or file_record.scenario_id != scenario_id:
            return False
        try:
            source_path = (file_record.scenario_root / file_record.relative_path).resolve()
            _ensure_within_root(file_record.scenario_root, source_path)
        except Exception:
            return False
        return source_path.exists() and source_path.is_file()

    def _hydrate_all_scenarios(self) -> None:
        for scenario_id in list(self._stores.scenarios.keys()):
            if scenario_id in self._hydrated_scenarios:
                continue
            self.hydrate_layers_from_db(scenario_id)

    def _emit_layer_event(self, layer: LayerState, event_name: JobEventName) -> None:
        payload = WsEnvelope(
            event=event_name,
            scenario_id=layer.scenario_id,
            timestamp_utc=_utc_now(),
            data={"layer_id": layer.layer_id, "source_file_id": layer.source_file_id},
        )
        self._stores.ws_events.append(payload.model_dump(mode="json"))

    def _persist_layer(self, layer: LayerState) -> None:
        db_path = self._stores.scenario_roots[layer.scenario_id] / "scenario.db"
        self._layer_state_repository.upsert_layer(db_path, layer)

    def _delete_layer_from_db(self, layer: LayerState) -> None:
        db_path = self._stores.scenario_roots[layer.scenario_id] / "scenario.db"
        self._layer_state_repository.delete_layer(db_path, layer.layer_id)

    def _delete_layer_rows_from_db(self, scenario_id: str, layer_ids: list[str]) -> None:
        if not layer_ids:
            return
        db_path = self._stores.scenario_roots[scenario_id] / "scenario.db"
        self._layer_state_repository.delete_layers(db_path, layer_ids)


class IdPathAccessor:
    """Single access point for ID/path resolution in the scenario workspace.

    This helper centralizes common lookups needed by handlers and adapters:
    - `scenario_id` <-> scenario root path
    - `product_id` -> latest `file_id`
    - `file_id` -> absolute file path
    - absolute file path -> `file_id`
    """

    def __init__(
        self,
        stores: InMemoryStores,
        scenario_service: ScenarioService,
        product_service: ProductService,
    ) -> None:
        self._stores = stores
        self._scenario_service = scenario_service
        self._product_service = product_service

    def scenario_root_from_id(self, scenario_id: str) -> Path:
        scenario = self._scenario_service.get_scenario(scenario_id)
        return Path(scenario.directory).resolve()

    def scenario_id_from_root(self, scenario_root: str | Path) -> str:
        target = Path(scenario_root).expanduser().resolve()
        for scenario in self._scenario_service.list_scenarios():
            if Path(scenario.directory).resolve() == target:
                return scenario.scenario_id
        raise KeyError(f"Scenario not found for root path: {target}")

    def product_from_id(self, product_id: str) -> Product:
        return self._product_service.get_product(product_id)

    def latest_file_id_for_product(self, product_id: str) -> str:
        files = self._product_service.list_product_files(product_id)
        if not files:
            raise KeyError(f"No files registered for product: {product_id}")
        return files[-1].file_id

    def product_id_from_file_id(self, file_id: str) -> str:
        record = self._product_service.get_file_record(file_id)
        return record.product_id

    def scenario_id_from_file_id(self, file_id: str) -> str:
        record = self._product_service.get_file_record(file_id)
        return record.scenario_id

    def file_path_from_id(self, file_id: str) -> Path:
        path, _ = self._product_service.resolve_file_path(file_id)
        return path

    def file_id_from_path(self, path: str | Path, *, scenario_id: str | None = None) -> str:
        target = Path(path).expanduser().resolve()
        for record in self._stores.product_files.values():
            if scenario_id is not None and record.scenario_id != scenario_id:
                continue
            candidate = (record.scenario_root / record.relative_path).resolve()
            if candidate == target:
                return record.file_id
        raise KeyError(f"No file ID registered for path: {target}")


class HorizonKeyService:
    """Build deterministic horizon keys from normalized input metadata."""

    KEY_VERSION = 1

    def _canonical_json(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _hash_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def build_inputs(
        self,
        *,
        dem_path: Path,
        dem_crs: str,
        analysis_extent: dict[str, Any],
        observer_height_m: float,
        azimuth_step_deg: float,
        algorithm_id: str,
        algorithm_version: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        rasterio = import_rasterio()
        with rasterio.open(dem_path) as ds:
            geotransform = tuple(ds.transform) if ds.transform is not None else ()
        geotransform_hash = self._hash_text(self._canonical_json({"transform": geotransform}))
        extent_hash = self._hash_text(self._canonical_json(analysis_extent))
        return {
            "key_version": self.KEY_VERSION,
            "algorithm_id": algorithm_id,
            "algorithm_version": algorithm_version,
            "dem_content_sha256": self._hash_file(dem_path),
            "dem_crs": dem_crs,
            "dem_geotransform_hash": geotransform_hash,
            "analysis_extent_hash": extent_hash,
            "observer_policy": {
                "observer_height_m": float(observer_height_m),
            },
            "azimuth_step_deg": float(azimuth_step_deg),
            "params": params,
        }

    def horizon_key(self, normalized_inputs: dict[str, Any]) -> str:
        canonical = self._canonical_json(normalized_inputs)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SharedHorizonStoreService:
    """Workspace-level shared horizon resolver/attach service for Phase 4.5 MVP."""

    def __init__(
        self,
        stores: InMemoryStores,
        scenario_service: ScenarioService,
        product_service: ProductService,
        id_path_accessor: IdPathAccessor,
        key_service: HorizonKeyService,
    ) -> None:
        self._stores = stores
        self._scenario_service = scenario_service
        self._product_service = product_service
        self._id_path_accessor = id_path_accessor
        self._key_service = key_service

    def resolve(
        self,
        *,
        scenario_id: str,
        request: ResolveHorizonSetRequest,
    ) -> ResolveHorizonSetResponse:
        if request.materialize:
            raise NotImplementedError(
                "Materialization is deferred in this MVP. Use reference mode only."
            )

        dem_path = self._id_path_accessor.file_path_from_id(request.dem_file_id)
        if not dem_path.exists() or not dem_path.is_file():
            raise FileNotFoundError(f"DEM file does not exist: {dem_path}")

        scenario = self._scenario_service.get_scenario(scenario_id)
        product_id = self._id_path_accessor.product_id_from_file_id(request.dem_file_id)
        product = self._product_service.get_product(product_id)
        normalized_inputs = self._key_service.build_inputs(
            dem_path=dem_path,
            dem_crs=product.crs,
            analysis_extent=scenario.primary_dem_footprint.model_dump(),
            observer_height_m=request.observer_height_m,
            azimuth_step_deg=request.azimuth_step_deg,
            algorithm_id=request.algorithm_id,
            algorithm_version=request.algorithm_version,
            params=request.params,
        )
        horizon_key = self._key_service.horizon_key(normalized_inputs)
        self._ensure_ready_set(horizon_key=horizon_key, normalized_inputs=normalized_inputs, scenario=scenario, dem_path=dem_path)

        attached_product_id: str | None = None
        if request.attach_product:
            attached_product_id = self._attach_reference(
                scenario_id=scenario_id,
                horizon_key=horizon_key,
                dem_file_id=request.dem_file_id,
                dem_crs=product.crs,
                footprint=scenario.primary_dem_footprint.model_dump(),
            )
        self._touch_horizon_set(horizon_key)
        return ResolveHorizonSetResponse(
            horizon_key=horizon_key,
            status="ready",
            product_id=attached_product_id,
            reference_count=self._reference_count(horizon_key),
            shared_storage_path=str(self._shared_dir_for_key(horizon_key)),
        )

    def inspect(self, horizon_key: str) -> HorizonSetStatusResponse:
        with _connect_sqlite(self._stores.catalog_db_path) as conn:
            row = conn.execute(
                """
                SELECT
                    horizon_key, key_version, algorithm_id, algorithm_version, status,
                    file_count, total_bytes, dem_content_sha256, dem_crs, created_at_utc, updated_at_utc
                FROM horizon_sets
                WHERE horizon_key = ?
                """,
                (horizon_key,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Horizon set not found: {horizon_key}")
        return HorizonSetStatusResponse(
            horizon_key=row[0],
            key_version=int(row[1]),
            algorithm_id=row[2],
            algorithm_version=row[3],
            status=row[4],
            file_count=int(row[5]),
            total_bytes=int(row[6]),
            dem_content_sha256=row[7],
            dem_crs=row[8],
            reference_count=self._reference_count(horizon_key),
            created_at_utc=row[9],
            updated_at_utc=row[10],
        )

    def detach(self, *, scenario_id: str, product_id: str) -> HorizonSetDetachResponse:
        with _connect_sqlite(self._stores.catalog_db_path) as conn:
            row = conn.execute(
                """
                SELECT horizon_key
                FROM horizon_set_refs
                WHERE scenario_id = ? AND product_id = ?
                """,
                (scenario_id, product_id),
            ).fetchone()
            if row is None:
                raise KeyError(
                    f"Horizon set reference not found: scenario_id={scenario_id}, product_id={product_id}"
                )
            horizon_key = row[0]
            conn.execute(
                "DELETE FROM horizon_set_refs WHERE scenario_id = ? AND product_id = ?",
                (scenario_id, product_id),
            )
            conn.commit()
        if product_id in self._stores.products:
            self._product_service.delete_product(product_id)
        return HorizonSetDetachResponse(
            scenario_id=scenario_id,
            product_id=product_id,
            horizon_key=horizon_key,
            status="detached",
        )

    def _shared_root(self) -> Path:
        return (self._stores.workspace_root / "_shared" / "horizons").resolve()

    def _shared_dir_for_key(self, horizon_key: str) -> Path:
        return (self._shared_root() / horizon_key).resolve()

    def _reference_count(self, horizon_key: str) -> int:
        with _connect_sqlite(self._stores.catalog_db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM horizon_set_refs WHERE horizon_key = ?",
                (horizon_key,),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def _touch_horizon_set(self, horizon_key: str) -> None:
        with _connect_sqlite(self._stores.catalog_db_path) as conn:
            conn.execute(
                """
                UPDATE horizon_sets
                SET last_accessed_at_utc = ?, updated_at_utc = ?
                WHERE horizon_key = ?
                """,
                (_utc_now(), _utc_now(), horizon_key),
            )
            conn.commit()

    def _ensure_ready_set(
        self,
        *,
        horizon_key: str,
        normalized_inputs: dict[str, Any],
        scenario: Scenario,
        dem_path: Path,
    ) -> None:
        with _connect_sqlite(self._stores.catalog_db_path) as conn:
            row = conn.execute(
                "SELECT status FROM horizon_sets WHERE horizon_key = ?",
                (horizon_key,),
            ).fetchone()
            if row is not None and row[0] == "ready":
                return
        shared_dir = self._shared_dir_for_key(horizon_key)
        if shared_dir.exists() and shared_dir.is_dir():
            file_count, total_bytes = _dir_stats(shared_dir)
            self._upsert_horizon_set(
                horizon_key=horizon_key,
                normalized_inputs=normalized_inputs,
                status="ready",
                storage_rel_dir=shared_dir.relative_to(self._stores.workspace_root).as_posix(),
                file_count=file_count,
                total_bytes=total_bytes,
            )
            return

        self._build_horizon_set(
            horizon_key=horizon_key,
            normalized_inputs=normalized_inputs,
            scenario=scenario,
            dem_path=dem_path,
        )

    def _build_horizon_set(
        self,
        *,
        horizon_key: str,
        normalized_inputs: dict[str, Any],
        scenario: Scenario,
        dem_path: Path,
    ) -> None:
        shared_root = self._shared_root()
        shared_root.mkdir(parents=True, exist_ok=True)
        final_dir = self._shared_dir_for_key(horizon_key)
        storage_rel_dir = final_dir.relative_to(self._stores.workspace_root).as_posix()
        self._upsert_horizon_set(
            horizon_key=horizon_key,
            normalized_inputs=normalized_inputs,
            status="building",
            storage_rel_dir=storage_rel_dir,
            file_count=0,
            total_bytes=0,
        )

        tmp_dir = shared_root / f".building-{horizon_key}-{uuid4().hex[:8]}"
        try:
            compress_horizons = bool(normalized_inputs["params"].get("compress_horizons", True))
            try:
                self._run_generate_horizons_subprocess(
                    scenario_id=scenario.scenario_id,
                    scenario_root_dir=scenario.directory,
                    dem_path=str(dem_path),
                    horizons_dir=str(tmp_dir),
                    overwrite_horizons=True,
                    compress_horizons=compress_horizons,
                )
            except Exception as exc:
                logger.warning(
                    "shared horizon native generation failed; using placeholder output horizon_key=%s reason=%s",
                    horizon_key,
                    exc,
                )
                self._write_placeholder_horizons(tmp_dir, compress_horizons=compress_horizons)
            file_count, total_bytes = _dir_stats(tmp_dir)
            manifest = {
                "horizon_key": horizon_key,
                "key_version": int(normalized_inputs["key_version"]),
                "algorithm_id": normalized_inputs["algorithm_id"],
                "algorithm_version": normalized_inputs["algorithm_version"],
                "file_count": file_count,
                "total_bytes": total_bytes,
                "normalized_inputs": normalized_inputs,
                "created_at_utc": _utc_now(),
            }
            (tmp_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            if final_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            else:
                tmp_dir.rename(final_dir)
            file_count, total_bytes = _dir_stats(final_dir)
            self._upsert_horizon_set(
                horizon_key=horizon_key,
                normalized_inputs=normalized_inputs,
                status="ready",
                storage_rel_dir=storage_rel_dir,
                file_count=file_count,
                total_bytes=total_bytes,
            )
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self._upsert_horizon_set(
                horizon_key=horizon_key,
                normalized_inputs=normalized_inputs,
                status="failed",
                storage_rel_dir=storage_rel_dir,
                file_count=0,
                total_bytes=0,
            )
            raise

    def _write_placeholder_horizons(self, target_dir: Path, *, compress_horizons: bool) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".hbin" if compress_horizons else ".bin"
        placeholder = target_dir / f"horizon_000000{suffix}"
        placeholder.write_bytes(b"lunar-analyst-placeholder-horizon\n")

    def _run_generate_horizons_subprocess(
        self,
        *,
        scenario_id: str,
        scenario_root_dir: str,
        dem_path: str,
        horizons_dir: str,
        overwrite_horizons: bool,
        compress_horizons: bool,
    ) -> None:
        repo_root = _repo_root()
        timeout_seconds = self._shared_horizon_native_timeout_seconds()
        with tempfile.TemporaryDirectory(prefix="shared_horizon_native_") as run_dir_text:
            run_dir = Path(run_dir_text).resolve()
            paths = build_worker_protocol_paths(run_dir)
            _write_worker_json_file(
                paths.context_path,
                worker_context_payload(
                    implementation_name="generate_horizons",
                    job_id=f"shared-horizon-{uuid4().hex}",
                    scenario_id=scenario_id,
                    args={
                        "scenario_id": scenario_id,
                        "scenario_root_dir": scenario_root_dir,
                        "dem_path": dem_path,
                        "horizons_dir": horizons_dir,
                        "overwrite_horizons": bool(overwrite_horizons),
                        "compress_horizons": bool(compress_horizons),
                    },
                    paths=paths,
                ),
            )
            command = [
                sys.executable,
                "-m",
                "backend.worker.native_job_dispatcher",
                "--context",
                str(paths.context_path),
            ]
            with paths.stdout_log_path.open("w", encoding="utf-8") as stdout_handle, paths.stderr_log_path.open(
                "w",
                encoding="utf-8",
            ) as stderr_handle:
                completed = subprocess.run(
                    command,
                    cwd=str(repo_root),
                    env=_native_worker_env(),
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                    check=False,
                    timeout=timeout_seconds,
                )
            if not paths.result_path.exists():
                stderr = paths.stderr_log_path.read_text(encoding="utf-8", errors="replace").strip()
                stdout = paths.stdout_log_path.read_text(encoding="utf-8", errors="replace").strip()
                raise RuntimeError(
                    "Native horizon worker did not produce a result payload. "
                    f"returncode={completed.returncode}; stderr={stderr or '<empty>'}; stdout={stdout or '<empty>'}"
                )
            payload = json.loads(paths.result_path.read_text(encoding="utf-8"))
            if completed.returncode != 0 or not bool(payload.get("ok", False)):
                error = str(payload.get("error", "")).strip()
                stderr = paths.stderr_log_path.read_text(encoding="utf-8", errors="replace").strip()
                stdout = paths.stdout_log_path.read_text(encoding="utf-8", errors="replace").strip()
                raise RuntimeError(error or stderr or stdout or "Native horizon worker failed.")

    def _shared_horizon_native_timeout_seconds(self) -> float | None:
        raw = os.getenv("LUNAR_ANALYST_SHARED_HORIZON_NATIVE_TIMEOUT_SECONDS", "").strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            logger.warning("invalid LUNAR_ANALYST_SHARED_HORIZON_NATIVE_TIMEOUT_SECONDS=%r", raw)
            return None
        if value <= 0:
            logger.warning("LUNAR_ANALYST_SHARED_HORIZON_NATIVE_TIMEOUT_SECONDS must be > 0: %s", value)
            return None
        return value

    def _upsert_horizon_set(
        self,
        *,
        horizon_key: str,
        normalized_inputs: dict[str, Any],
        status: str,
        storage_rel_dir: str,
        file_count: int,
        total_bytes: int,
    ) -> None:
        now = _utc_now()
        with _connect_sqlite(self._stores.catalog_db_path) as conn:
            existing = conn.execute(
                "SELECT created_at_utc FROM horizon_sets WHERE horizon_key = ?",
                (horizon_key,),
            ).fetchone()
            created_at = existing[0] if existing is not None else now
            conn.execute(
                """
                INSERT OR REPLACE INTO horizon_sets (
                    horizon_key, key_version, algorithm_id, algorithm_version,
                    dem_content_sha256, dem_crs, dem_geotransform_hash, analysis_extent_hash,
                    observer_policy_json, params_json, storage_rel_dir, status, file_count,
                    total_bytes, created_at_utc, updated_at_utc, last_accessed_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    horizon_key,
                    int(normalized_inputs["key_version"]),
                    str(normalized_inputs["algorithm_id"]),
                    str(normalized_inputs["algorithm_version"]),
                    str(normalized_inputs["dem_content_sha256"]),
                    str(normalized_inputs["dem_crs"]),
                    str(normalized_inputs["dem_geotransform_hash"]),
                    str(normalized_inputs["analysis_extent_hash"]),
                    json.dumps(normalized_inputs["observer_policy"], sort_keys=True),
                    json.dumps(normalized_inputs["params"], sort_keys=True),
                    storage_rel_dir,
                    status,
                    int(file_count),
                    int(total_bytes),
                    created_at,
                    now,
                    now,
                ),
            )
            conn.commit()

    def _attach_reference(
        self,
        *,
        scenario_id: str,
        horizon_key: str,
        dem_file_id: str,
        dem_crs: str,
        footprint: dict[str, Any],
    ) -> str:
        with _connect_sqlite(self._stores.catalog_db_path) as conn:
            existing = conn.execute(
                """
                SELECT product_id
                FROM horizon_set_refs
                WHERE scenario_id = ? AND horizon_key = ?
                ORDER BY created_at_utc DESC
                LIMIT 1
                """,
                (scenario_id, horizon_key),
            ).fetchone()
        if existing is not None:
            return str(existing[0])

        product = self._scenario_service._register_product_internal(
            RegisterProductRequest(
                scenario_id=scenario_id,
                kind="lighting",
                subkind="horizon_set",
                producer=Producer.NEW_HORIZON,
                crs=dem_crs,
                footprint=footprint,
                lineage={
                    "horizon_key": horizon_key,
                    "storage_scope": "shared_workspace",
                    "access_mode": "reference",
                    "dem_file_id": dem_file_id,
                },
            )
        )
        now = _utc_now()
        with _connect_sqlite(self._stores.catalog_db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO horizon_set_refs(
                    scenario_id, horizon_key, product_id, access_mode,
                    materialized_relative_path, pinned, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scenario_id,
                    horizon_key,
                    product.product_id,
                    "reference",
                    None,
                    0,
                    now,
                    now,
                ),
            )
            conn.commit()
        return product.product_id


class JobService:
    WORKER_ONLY_TAG = "worker-only"
    WORKER_ONLY_HANDLER_NAMES: frozenset[str] = frozenset(
        {
            "generate_horizons",
            "generate_average_sun_fraction_raster",
            "generate_earth_above_terrain_duration_raster",
            "generate_combined_sun_earth_max_contiguous_duration_raster",
            "generate_lightmap_timeseries",
            "generate_psr_raster",
        }
    )
    DEFAULT_NATIVE_WORKER_HEARTBEAT_INTERVAL_SECONDS = 1.0

    @dataclass(frozen=True)
    class _QueuedJob:
        job_id: str
        handler_name: str
        scenario_id: str
        args: dict[str, Any]
        queued_at_monotonic: float

    @dataclass(frozen=True)
    class _NativeWorkerResult:
        result: Any

    class _JobCancelledError(RuntimeError):
        pass

    def __init__(
        self,
        stores: InMemoryStores,
        notebook_job_service: "NotebookJobService | None" = None,
    ) -> None:
        self._stores = stores
        self._notebook_job_service = notebook_job_service
        self._queue: Queue[JobService._QueuedJob | None] = Queue()
        self._stop_event = threading.Event()
        self._job_lock = threading.RLock()
        self._live_progress_jobs: set[str] = set()
        self._native_worker_processes: dict[str, subprocess.Popen[str]] = {}
        self._native_worker_cancel_paths: dict[str, Path] = {}
        if self._notebook_job_service is not None:
            self._notebook_job_service.set_progress_reporter(self._on_notebook_progress)
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="job-service-worker",
            daemon=True,
        )
        self._worker.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        self._queue.put(None)
        with self._job_lock:
            processes = list(self._native_worker_processes.values())
            self._native_worker_processes.clear()
            cancel_paths = list(self._native_worker_cancel_paths.values())
            self._native_worker_cancel_paths.clear()
        for cancel_path in cancel_paths:
            try:
                _request_worker_cancel(cancel_path, reason="service shutdown")
            except Exception:
                continue
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
            except Exception:
                continue
        self._worker.join(timeout=2.0)

    def _normalize_job_exception(self, exc: Exception) -> Exception:
        if isinstance(exc, ApiError):
            return exc
        message = str(exc).strip()
        match = HORIZON_DIMENSION_ERROR_RE.search(message)
        if match is None:
            return exc
        axis = match.group(1).lower()
        value = int(match.group(2))
        multiple = int(match.group(3))
        return ApiError(
            status_code=422,
            code="invalid_dem_dimensions",
            message=(
                f"DEM {axis} ({value}) is incompatible with horizon generation; "
                f"it must be an even multiple of {multiple}."
            ),
            details={
                "dimension": axis,
                "value": value,
                "multiple": multiple,
                "requirement": f"Both DEM width and height must be divisible by {multiple}.",
                "hint": "Resample or pad the DEM to a 128-aligned grid before generating horizons.",
                "native_error": message,
            },
        )

    @classmethod
    def _native_inline_escape_hatch_enabled(cls) -> bool:
        return os.getenv("LUNAR_ANALYST_NATIVE_INLINE_HANDLERS", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @classmethod
    def _handler_contract_tags(cls, handler: Any) -> set[str]:
        contract = getattr(handler, "__contract__", None)
        tool_meta = getattr(contract, "tool", None)
        tags = getattr(tool_meta, "tags", ()) if tool_meta is not None else ()
        return {str(tag).strip().lower() for tag in tags if str(tag).strip()}

    @classmethod
    def _is_worker_only_handler(cls, handler_name: str, handler: Any) -> bool:
        return (
            str(handler_name).strip() in cls.WORKER_ONLY_HANDLER_NAMES
            or cls.WORKER_ONLY_TAG in cls._handler_contract_tags(handler)
        )

    def _on_notebook_progress(self, job_id: str, scenario_id: str, payload: dict[str, Any]) -> None:
        event_kind = str(payload.get("event_kind", "")).strip().lower()
        if event_kind == "log_line":
            stream_name = str(payload.get("log_stream", "")).strip().lower()
            line = payload.get("log_line")
            if stream_name in {"stdout", "stderr"} and isinstance(line, str):
                self._emit_ws_event(
                    JobEventName.JOB_PROGRESS,
                    scenario_id,
                    {
                        "job_id": job_id,
                        "event_kind": "log_line",
                        "log_stream": stream_name,
                        "log_line": line,
                    },
                )
            return
        self._emit_live_progress(job_id, scenario_id, payload)

    def _emit_live_progress(self, job_id: str, scenario_id: str, payload: dict[str, Any]) -> None:
        if self._is_cancelled(job_id):
            return
        with self._job_lock:
            if self._is_cancelled_locked(job_id):
                return
            self._live_progress_jobs.add(job_id)
            self._stores.job_events.setdefault(job_id, []).append(
                _new_job_event(
                    job_id,
                    scenario_id,
                    JobEventName.JOB_PROGRESS,
                    dict(payload),
                )
            )
        ws_payload = dict(payload)
        ws_payload["job_id"] = job_id
        self._emit_ws_event(
            JobEventName.JOB_PROGRESS,
            scenario_id,
            ws_payload,
        )

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                queued = self._queue.get(timeout=0.2)
            except Empty:
                continue
            if queued is None:
                continue
            dequeue_ts = time.monotonic()
            queue_wait_ms = max(0, int((dequeue_ts - queued.queued_at_monotonic) * 1000))
            try:
                queue_depth = int(self._queue.qsize())
            except Exception:
                queue_depth = -1
            logger.info(
                "job queue dequeue job_id=%s scenario_id=%s handler=%s queue_wait_ms=%s queue_depth=%s",
                queued.job_id,
                queued.scenario_id,
                queued.handler_name,
                queue_wait_ms,
                queue_depth,
            )
            try:
                self._execute_job(
                    queued.job_id,
                    queued.scenario_id,
                    queued.handler_name,
                    queued.args,
                    raise_on_error=False,
                )
            except Exception:
                logger.exception(
                    "queued job execution crashed job_id=%s handler=%s",
                    queued.job_id,
                    queued.handler_name,
                )

    @staticmethod
    def _job_ws_payload(
        *,
        job_id: str,
        handler_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": job_id,
            "job_type": handler_name,
            "handler_name": handler_name,
            "title": handler_name,
        }
        notebook_job_id = str(args.get("notebook_job_id", "")).strip()
        if notebook_job_id:
            payload["notebook_job_id"] = notebook_job_id
        return payload

    def run_typed_job(self, handler_name: str, args: dict[str, Any]) -> Job:
        handler = getattr(ToolImplementations, handler_name, None)
        if handler is None or not callable(handler) or not hasattr(handler, "__contract__"):
            raise KeyError(f"Tool implementation not found: {handler_name}")

        requested_at = _utc_now()
        job_id = str(uuid4())
        mode_arg = args.get("mode", JobMode.QUEUED)
        mode = mode_arg if isinstance(mode_arg, JobMode) else JobMode(str(mode_arg))
        scenario_id = str(args.get("scenario_id", "unknown"))

        queued_job = Job(
            job_id=job_id,
            scenario_id=scenario_id,
            job_type=handler_name,
            mode=mode,
            status=JobStatus.QUEUED,
            params=dict(args),
            requested_at_utc=requested_at,
            updated_at_utc=requested_at,
        )
        with self._job_lock:
            self._stores.jobs[job_id] = queued_job
            self._stores.job_events[job_id] = [
                _new_job_event(job_id, scenario_id, JobEventName.JOB_QUEUED, {})
            ]
        self._emit_ws_event(
            JobEventName.JOB_QUEUED,
            scenario_id,
            self._job_ws_payload(job_id=job_id, handler_name=handler_name, args=args),
        )
        logger.info(
            "job queued job_id=%s scenario_id=%s handler=%s mode=%s",
            job_id,
            scenario_id,
            handler_name,
            mode.value,
        )
        if mode == JobMode.IMMEDIATE:
            logger.info(
                "job immediate execution start job_id=%s scenario_id=%s handler=%s",
                job_id,
                scenario_id,
                handler_name,
            )
            self._execute_job(
                job_id,
                scenario_id,
                handler_name,
                dict(args),
                raise_on_error=True,
            )
            return self.get_job(job_id)

        enqueue_ts = time.monotonic()
        self._queue.put(
            JobService._QueuedJob(
                job_id=job_id,
                handler_name=handler_name,
                scenario_id=scenario_id,
                args=dict(args),
                queued_at_monotonic=enqueue_ts,
            )
        )
        try:
            queue_depth = int(self._queue.qsize())
        except Exception:
            queue_depth = -1
        logger.info(
            "job queue enqueue job_id=%s scenario_id=%s handler=%s queue_depth=%s",
            job_id,
            scenario_id,
            handler_name,
            queue_depth,
        )
        return queued_job

    def _execute_job(
        self,
        job_id: str,
        scenario_id: str,
        handler_name: str,
        args: dict[str, Any],
        *,
        raise_on_error: bool,
    ) -> None:
        started_monotonic = time.monotonic()
        logger.info(
            "job execution begin job_id=%s scenario_id=%s handler=%s raise_on_error=%s",
            job_id,
            scenario_id,
            handler_name,
            raise_on_error,
        )
        handler = getattr(ToolImplementations, handler_name, None)
        if handler is None or not callable(handler) or not hasattr(handler, "__contract__"):
            missing = KeyError(f"Tool implementation not found: {handler_name}")
            self._mark_job_failed(job_id, scenario_id, str(missing))
            if raise_on_error:
                raise missing
            return

        running = self._mark_job_running(job_id, scenario_id, handler_name=handler_name, args=args)
        if not running:
            return

        normalized_error: Exception | None = None
        result: Any | None = None
        token = CURRENT_JOB_ID.set(job_id)
        try:
            try:
                worker_only = self._is_worker_only_handler(handler_name, handler)
                inline_native = self._native_inline_escape_hatch_enabled()
                if worker_only and not inline_native:
                    logger.info(
                        "job handler invoke native_subprocess job_id=%s scenario_id=%s handler=%s",
                        job_id,
                        scenario_id,
                        handler_name,
                    )
                    result = self._run_native_handler_subprocess(
                        job_id=job_id,
                        scenario_id=scenario_id,
                        handler_name=handler_name,
                        args=args,
                    ).result
                    logger.info(
                        "job handler returned native_subprocess job_id=%s scenario_id=%s handler=%s",
                        job_id,
                        scenario_id,
                        handler_name,
                    )
                else:
                    if worker_only and inline_native:
                        logger.warning(
                            "worker-only handler executing inline via development escape hatch job_id=%s scenario_id=%s handler=%s",
                            job_id,
                            scenario_id,
                            handler_name,
                        )
                    logger.info(
                        "job handler invoke inline job_id=%s scenario_id=%s handler=%s",
                        job_id,
                        scenario_id,
                        handler_name,
                    )
                    result = handler(**args)
                    logger.info(
                        "job handler returned inline job_id=%s scenario_id=%s handler=%s",
                        job_id,
                        scenario_id,
                        handler_name,
                    )
            except JobService._JobCancelledError:
                logger.info(
                    "job execution cancelled job_id=%s scenario_id=%s handler=%s",
                    job_id,
                    scenario_id,
                    handler_name,
                )
                return
            except Exception as exc:
                normalized_error = self._normalize_job_exception(exc)
        finally:
            CURRENT_JOB_ID.reset(token)

        if normalized_error is not None:
            logger.warning(
                "job execution failed job_id=%s scenario_id=%s handler=%s error=%s",
                job_id,
                scenario_id,
                handler_name,
                str(normalized_error),
            )
            self._mark_job_failed(job_id, scenario_id, str(normalized_error))
            if raise_on_error:
                raise normalized_error
            return

        if self._is_cancelled(job_id):
            return

        progress_payloads: list[dict[str, Any]] = []
        with self._job_lock:
            has_live_progress = job_id in self._live_progress_jobs
            if has_live_progress:
                self._live_progress_jobs.discard(job_id)

        if isinstance(result, BaseModel) and not has_live_progress:
            raw_progress = getattr(result, "progress_events", [])
            if isinstance(raw_progress, list):
                for item in raw_progress:
                    if (
                        isinstance(item, dict)
                        and "percent" in item
                        and "message" in item
                    ):
                        progress_payloads.append(dict(item))

        with self._job_lock:
            if self._is_cancelled_locked(job_id):
                return
            finished_at = _utc_now()
            current = self._stores.jobs.get(job_id)
            if current is None:
                return
            completed_job = current.model_copy(
                update={
                    "status": JobStatus.COMPLETED,
                    "finished_at_utc": finished_at,
                    "updated_at_utc": finished_at,
                }
            )
            self._stores.jobs[job_id] = completed_job
            events = self._stores.job_events.setdefault(job_id, [])
            for payload in progress_payloads:
                events.append(
                    _new_job_event(
                        job_id,
                        scenario_id,
                        JobEventName.JOB_PROGRESS,
                        payload,
                    )
                )
            events.append(
                _new_job_event(
                    job_id,
                    scenario_id,
                    JobEventName.JOB_PROGRESS,
                    {"percent": 100.0, "message": "Job completed."},
                )
            )
            events.append(
                _new_job_event(
                    job_id,
                    scenario_id,
                    JobEventName.JOB_COMPLETED,
                    {"result": _serialize_result(result)},
                )
            )

        for payload in progress_payloads:
            ws_payload = dict(payload)
            ws_payload["job_id"] = job_id
            self._emit_ws_event(
                JobEventName.JOB_PROGRESS,
                scenario_id,
                ws_payload,
            )
        self._emit_ws_event(
            JobEventName.JOB_PROGRESS,
            scenario_id,
            {"job_id": job_id, "percent": 100.0, "message": "Job completed."},
        )
        self._emit_ws_event(
            JobEventName.JOB_COMPLETED,
            scenario_id,
            {"job_id": job_id, "result": _serialize_result(result)},
        )
        elapsed_ms = max(0, int((time.monotonic() - started_monotonic) * 1000))
        logger.info(
            "job execution completed job_id=%s scenario_id=%s handler=%s elapsed_ms=%s",
            job_id,
            scenario_id,
            handler_name,
            elapsed_ms,
        )

    def _run_native_handler_subprocess(
        self,
        *,
        job_id: str,
        scenario_id: str,
        handler_name: str,
        args: dict[str, Any],
    ) -> _NativeWorkerResult:
        repo_root = _repo_root()
        with tempfile.TemporaryDirectory(prefix="native_job_") as run_dir_text:
            run_dir = Path(run_dir_text).resolve()
            paths = build_worker_protocol_paths(run_dir)
            _write_worker_json_file(
                paths.context_path,
                worker_context_payload(
                    implementation_name=handler_name,
                    job_id=job_id,
                    scenario_id=scenario_id,
                    args=args,
                    paths=paths,
                ),
            )
            command = [
                sys.executable,
                "-m",
                "backend.worker.native_job_dispatcher",
                "--context",
                str(paths.context_path),
            ]
            process = subprocess.Popen(
                command,
                cwd=str(repo_root),
                env=_native_worker_env(),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout_done = threading.Event()
            stderr_done = threading.Event()
            stdout_thread = self._stream_native_worker_pipe_to_log(
                pipe=process.stdout,
                log_path=paths.stdout_log_path,
                done_event=stdout_done,
            )
            stderr_thread = self._stream_native_worker_pipe_to_log(
                pipe=process.stderr,
                log_path=paths.stderr_log_path,
                done_event=stderr_done,
            )
            if stdout_thread is None:
                stdout_done.set()
            if stderr_thread is None:
                stderr_done.set()
            with self._job_lock:
                self._native_worker_processes[job_id] = process
                self._native_worker_cancel_paths[job_id] = paths.cancel_path
            try:
                next_percent = 5.0
                last_pulse = time.monotonic()
                last_structured_progress = last_pulse
                heartbeat_interval = self._native_worker_heartbeat_interval_seconds()
                progress_line_index = 0
                while process.poll() is None:
                    if self._is_cancelled(job_id):
                        try:
                            _request_worker_cancel(paths.cancel_path, reason="cancel requested")
                        except Exception:
                            logger.warning(
                                "failed to write native worker cancel flag job_id=%s path=%s",
                                job_id,
                                paths.cancel_path,
                            )
                        try:
                            process.terminate()
                        except Exception:
                            pass
                        raise JobService._JobCancelledError("cancel requested")
                    progress_line_index, progress_events = _read_worker_progress_events_since_line(
                        paths.progress_path,
                        progress_line_index,
                    )
                    if progress_events:
                        last_structured_progress = time.monotonic()
                        for payload in progress_events:
                            self._emit_live_progress(job_id, scenario_id, payload)
                    now = time.monotonic()
                    if (
                        now - last_pulse >= heartbeat_interval
                        and now - last_structured_progress >= heartbeat_interval
                    ):
                        self._emit_live_progress(
                            job_id,
                            scenario_id,
                            {
                                "percent": min(95.0, next_percent),
                                "message": "Native worker running...",
                                "stage": "native_worker",
                            },
                        )
                        next_percent = min(95.0, next_percent + 5.0)
                        last_pulse = now
                    time.sleep(0.05)
                progress_line_index, progress_events = _read_worker_progress_events_since_line(
                    paths.progress_path,
                    progress_line_index,
                )
                for payload in progress_events:
                    self._emit_live_progress(job_id, scenario_id, payload)
                if stdout_thread is not None:
                    stdout_thread.join(timeout=2.0)
                if stderr_thread is not None:
                    stderr_thread.join(timeout=2.0)
            finally:
                with self._job_lock:
                    self._native_worker_processes.pop(job_id, None)
                    self._native_worker_cancel_paths.pop(job_id, None)

            returncode = int(process.returncode or 0)
            if self._is_cancelled(job_id):
                raise JobService._JobCancelledError("cancel requested")
            if not paths.result_path.exists():
                raise RuntimeError(
                    "Native worker did not produce a result payload. "
                    f"handler={handler_name} returncode={returncode}"
                )
            payload = json.loads(paths.result_path.read_text(encoding="utf-8"))
            if returncode != 0 or not bool(payload.get("ok", False)):
                error = str(payload.get("error", "")).strip()
                stderr = (
                    paths.stderr_log_path.read_text(encoding="utf-8", errors="replace").strip()
                    if paths.stderr_log_path.exists()
                    else ""
                )
                stdout = (
                    paths.stdout_log_path.read_text(encoding="utf-8", errors="replace").strip()
                    if paths.stdout_log_path.exists()
                    else ""
                )
                raise RuntimeError(error or stderr or stdout or "Native worker execution failed.")
            return JobService._NativeWorkerResult(result=payload.get("result"))

    def _stream_native_worker_pipe_to_log(
        self,
        *,
        pipe: Any,
        log_path: Path,
        done_event: threading.Event,
    ) -> threading.Thread | None:
        if pipe is None:
            return None
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None

        def _pump() -> None:
            try:
                with log_path.open("w", encoding="utf-8", errors="replace") as handle:
                    for chunk in iter(pipe.readline, ""):
                        if chunk == "":
                            break
                        handle.write(chunk)
                        handle.flush()
            except Exception as exc:
                logger.warning("failed streaming native worker log path=%s error=%s", log_path, exc)
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass
                done_event.set()

        thread = threading.Thread(
            target=_pump,
            name=f"native-worker-log-pump-{log_path.name}",
            daemon=True,
        )
        thread.start()
        return thread

    @classmethod
    def _native_worker_heartbeat_interval_seconds(cls) -> float:
        raw = os.getenv("LUNAR_ANALYST_NATIVE_WORKER_HEARTBEAT_INTERVAL_SECONDS", "").strip()
        if not raw:
            return cls.DEFAULT_NATIVE_WORKER_HEARTBEAT_INTERVAL_SECONDS
        try:
            value = float(raw)
        except ValueError:
            logger.warning("invalid LUNAR_ANALYST_NATIVE_WORKER_HEARTBEAT_INTERVAL_SECONDS=%r", raw)
            return cls.DEFAULT_NATIVE_WORKER_HEARTBEAT_INTERVAL_SECONDS
        if value <= 0.0:
            logger.warning("LUNAR_ANALYST_NATIVE_WORKER_HEARTBEAT_INTERVAL_SECONDS must be > 0: %s", value)
            return cls.DEFAULT_NATIVE_WORKER_HEARTBEAT_INTERVAL_SECONDS
        return value

    def _mark_job_running(
        self,
        job_id: str,
        scenario_id: str,
        *,
        handler_name: str,
        args: dict[str, Any],
    ) -> bool:
        with self._job_lock:
            current = self._stores.jobs.get(job_id)
            if current is None:
                return False
            if current.status != JobStatus.QUEUED:
                return False
            started_at = _utc_now()
            running_job = current.model_copy(
                update={
                    "status": JobStatus.RUNNING,
                    "started_at_utc": started_at,
                    "updated_at_utc": started_at,
                }
            )
            self._stores.jobs[job_id] = running_job
            self._stores.job_events.setdefault(job_id, []).append(
                _new_job_event(job_id, scenario_id, JobEventName.JOB_STARTED, {})
            )
        self._emit_ws_event(
            JobEventName.JOB_STARTED,
            scenario_id,
            self._job_ws_payload(job_id=job_id, handler_name=handler_name, args=args),
        )
        logger.info(
            "job started job_id=%s scenario_id=%s handler=%s",
            job_id,
            scenario_id,
            handler_name,
        )
        return True

    def _mark_job_failed(self, job_id: str, scenario_id: str, error: str) -> None:
        with self._job_lock:
            current = self._stores.jobs.get(job_id)
            if current is None or self._is_cancelled_locked(job_id):
                return
            finished_at = _utc_now()
            failed_job = current.model_copy(
                update={
                    "status": JobStatus.FAILED,
                    "finished_at_utc": finished_at,
                    "updated_at_utc": finished_at,
                }
            )
            self._stores.jobs[job_id] = failed_job
            self._stores.job_events.setdefault(job_id, []).append(
                _new_job_event(
                    job_id,
                    scenario_id,
                    JobEventName.JOB_FAILED,
                    {"error": error},
                )
            )
        self._emit_ws_event(
            JobEventName.JOB_FAILED,
            scenario_id,
            {"job_id": job_id, "error": error},
        )
        logger.error(
            "job failed job_id=%s scenario_id=%s error=%s",
            job_id,
            scenario_id,
            error,
        )

    def _is_cancelled(self, job_id: str) -> bool:
        with self._job_lock:
            return self._is_cancelled_locked(job_id)

    def _is_cancelled_locked(self, job_id: str) -> bool:
        current = self._stores.jobs.get(job_id)
        if current is None:
            return False
        return current.status == JobStatus.CANCELLED

    def get_job(self, job_id: str) -> Job:
        with self._job_lock:
            if job_id not in self._stores.jobs:
                raise KeyError(f"Job not found: {job_id}")
            return self._stores.jobs[job_id]

    def list_job_events(self, job_id: str) -> list[JobEvent]:
        with self._job_lock:
            return list(self._stores.job_events.get(job_id, []))

    def cancel_job(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return job
        if self._notebook_job_service is not None:
            self._notebook_job_service.request_cancel(job_id, reason="cancel requested")
        with self._job_lock:
            native_process = self._native_worker_processes.get(job_id)
            native_cancel_path = self._native_worker_cancel_paths.get(job_id)
        if native_cancel_path is not None:
            try:
                _request_worker_cancel(native_cancel_path, reason="cancel requested")
            except Exception:
                logger.warning(
                    "failed to write native worker cancel flag job_id=%s path=%s",
                    job_id,
                    native_cancel_path,
                )
        if native_process is not None and native_process.poll() is None:
            try:
                native_process.terminate()
            except Exception:
                pass
        cancelled_at = _utc_now()
        updated = job.model_copy(
            update={
                "status": JobStatus.CANCELLED,
                "finished_at_utc": cancelled_at,
                "updated_at_utc": cancelled_at,
            }
        )
        with self._job_lock:
            self._stores.jobs[job_id] = updated
            self._stores.job_events.setdefault(job_id, []).append(
                _new_job_event(
                    job_id,
                    job.scenario_id,
                    JobEventName.JOB_CANCELLED,
                    {"reason": "cancel requested"},
                )
            )
        self._emit_ws_event(
            JobEventName.JOB_CANCELLED,
            job.scenario_id,
            {"job_id": job_id, "reason": "cancel requested"},
        )
        return updated

    def _emit_ws_event(self, event_name: JobEventName, scenario_id: str, data: dict[str, Any]) -> None:
        payload = WsEnvelope(
            event=event_name,
            scenario_id=scenario_id,
            timestamp_utc=_utc_now(),
            data=data,
        )
        self._stores.ws_events.append(payload.model_dump(mode="json"))


class NotebookJobService:
    def __init__(
        self,
        stores: InMemoryStores,
        scenario_service: ScenarioService,
    ) -> None:
        self._stores = stores
        self._scenario_service = scenario_service
        self._progress_reporter: Callable[[str, str, dict[str, Any]], None] | None = None

    def set_progress_reporter(
        self,
        reporter: Callable[[str, str, dict[str, Any]], None] | None,
    ) -> None:
        self._progress_reporter = reporter

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except Exception:
            return
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except Exception:
                return
            try:
                process.wait(timeout=2.0)
            except Exception:
                return

    def _register_running_process(
        self,
        *,
        job_id: str,
        process: subprocess.Popen[str],
        cancel_path: Path,
    ) -> None:
        with self._stores.notebook_job_lock:
            self._stores.notebook_job_processes[job_id] = process
            self._stores.notebook_job_cancel_paths[job_id] = cancel_path

    def _clear_running_process(self, *, job_id: str) -> None:
        with self._stores.notebook_job_lock:
            self._stores.notebook_job_processes.pop(job_id, None)
            self._stores.notebook_job_cancel_paths.pop(job_id, None)

    def request_cancel(self, job_id: str, *, reason: str) -> bool:
        with self._stores.notebook_job_lock:
            process = self._stores.notebook_job_processes.get(job_id)
            cancel_path = self._stores.notebook_job_cancel_paths.get(job_id)
        if cancel_path is not None:
            try:
                _request_worker_cancel(cancel_path, reason=reason)
            except Exception:
                logger.warning("notebook cancel flag write failed job_id=%s", job_id)
        if process is None:
            return False
        self._terminate_process(process)
        return True

    def terminate_all_running(self, *, reason: str) -> int:
        with self._stores.notebook_job_lock:
            process_entries = list(self._stores.notebook_job_processes.items())
            cancel_entries = dict(self._stores.notebook_job_cancel_paths)
        for job_id, cancel_path in cancel_entries.items():
            try:
                _request_worker_cancel(cancel_path, reason=reason)
            except Exception:
                logger.warning("notebook cancel flag write failed during shutdown job_id=%s", job_id)
        for job_id, process in process_entries:
            self._terminate_process(process)
            self._clear_running_process(job_id=job_id)
        return len(process_entries)

    def list_job_definitions(self, scenario_id: str | None = None) -> JobDefinitionsResponse:
        definitions: list[JobDefinition] = []

        from backend.api.job_runtime import discover_tool_implementations

        for spec in discover_tool_implementations().values():
            definition = spec.tool_definition
            definitions.append(
                JobDefinition(
                    job_definition_id=f"native:{spec.implementation_name}",
                    job_type=JobDefinitionType.NATIVE,
                    title=definition.title,
                    description=definition.description,
                    visibility=definition.visibility.value,
                    tags=list(definition.tags),
                    handler_name=spec.implementation_name,
                    implementation_name=spec.implementation_name,
                    route_path=definition.route_path,
                    params=list(definition.params),
                    params_schema=dict(definition.params_schema),
                    outputs_schema=dict(definition.outputs_schema),
                )
            )

        for job in self._discover_notebook_jobs(scenario_id=scenario_id):
            definitions.append(
                JobDefinition(
                    job_definition_id=f"notebook:{job.metadata.job_id}",
                    job_type=JobDefinitionType.NOTEBOOK,
                    title=job.metadata.title,
                    description=job.metadata.description,
                    visibility=job.metadata.visibility,
                    tags=list(job.metadata.tags),
                    handler_name="run_notebook_definition",
                    implementation_name="run_notebook_definition",
                    route_path="/api/v1/jobs/run-notebook-definition",
                    params=[
                        JobDefinitionParam(
                            name="scenario_id",
                            type="str",
                            required=True,
                            default=None,
                        ),
                        JobDefinitionParam(
                            name="notebook_job_id",
                            type="str",
                            required=True,
                            default=job.metadata.job_id,
                        ),
                        JobDefinitionParam(
                            name="params",
                            type="dict",
                            required=False,
                            default={},
                        ),
                        JobDefinitionParam(
                            name="runtime_mode",
                            type="str",
                            required=False,
                            default="osgeo",
                        ),
                    ],
                    params_schema=job.metadata.params_schema,
                    outputs_schema=job.metadata.outputs_schema,
                    notebook_path=str(job.notebook_path),
                    notebook_hash=job.notebook_hash,
                )
            )

        definitions.sort(
            key=lambda item: (
                0 if item.job_type == JobDefinitionType.NOTEBOOK else 1,
                item.title.lower(),
            )
        )
        return JobDefinitionsResponse(definitions=definitions)

    def execute_notebook_job(
        self,
        scenario_id: str,
        notebook_job_id: str,
        params: dict[str, Any],
        runtime_mode: str = "osgeo",
    ) -> dict[str, Any]:
        scenario = self._scenario_service.get_scenario(scenario_id)
        scenario_root = Path(scenario.directory).resolve()
        _ensure_within_root(self._stores.workspace_root, scenario_root)
        discovered = self._discover_notebook_jobs(scenario_id=scenario_id)
        by_job_id = {
            item.metadata.job_id: item
            for item in discovered
        }
        target = by_job_id.get(notebook_job_id)
        if target is None:
            raise KeyError(f"Notebook job definition not found: {notebook_job_id}")

        before_renderables = self._snapshot_renderable_files(scenario_root)
        run_id = f"nbr_{uuid4().hex[:12]}"
        run_root = self._prepare_notebook_run_root(
            scenario_id=scenario.scenario_id,
            scenario_root=scenario_root,
            run_id=run_id,
        )
        protocol_paths = build_worker_protocol_paths(run_root)
        context_path = protocol_paths.context_path
        result_path = protocol_paths.result_path
        progress_path = protocol_paths.progress_path
        cancel_path = protocol_paths.cancel_path
        stdout_log_path = protocol_paths.stdout_log_path
        stderr_log_path = protocol_paths.stderr_log_path
        context = {
            "protocol_version": 1,
            "scenario_id": scenario_id,
            "job_id": run_id,
            "scenario_root_dir": str(scenario_root),
            "notebook_job_id": notebook_job_id,
            "notebook_path": str(target.notebook_path),
            "params": params,
            "runtime_mode": str(runtime_mode or "osgeo"),
            "result_path": str(result_path),
            "progress_path": str(progress_path),
            "cancel_path": str(cancel_path),
            "stdout_log_path": str(stdout_log_path),
            "stderr_log_path": str(stderr_log_path),
        }
        context_path.write_text(
            json.dumps(context, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        command = [
            self._python_executable(),
            "-m",
            "backend.notebook.job_runner",
            "--context",
            str(context_path),
        ]
        runner_env = _build_notebook_runner_env()
        tracked_job_id = CURRENT_JOB_ID.get() or run_id
        process = subprocess.Popen(
            command,
            cwd=str(scenario_root),
            env=runner_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        stdout_lock = threading.Lock()
        stderr_lock = threading.Lock()
        stdout_done_event = threading.Event()
        stderr_done_event = threading.Event()
        stdout_thread = self._stream_subprocess_pipe_to_log(
            pipe=process.stdout,
            log_path=stdout_log_path,
            sink_chunks=stdout_chunks,
            sink_lock=stdout_lock,
            done_event=stdout_done_event,
            stream_name="stdout",
            job_id=tracked_job_id,
            scenario_id=scenario_id,
        )
        stderr_thread = self._stream_subprocess_pipe_to_log(
            pipe=process.stderr,
            log_path=stderr_log_path,
            sink_chunks=stderr_chunks,
            sink_lock=stderr_lock,
            done_event=stderr_done_event,
            stream_name="stderr",
            job_id=tracked_job_id,
            scenario_id=scenario_id,
        )
        if stdout_thread is None:
            stdout_done_event.set()
        if stderr_thread is None:
            stderr_done_event.set()
        self._stores.notebook_run_info[tracked_job_id] = {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "notebook_job_id": notebook_job_id,
            "runtime_mode": str(runtime_mode or "osgeo"),
            "run_root": str(run_root),
            "stdout_log_path": str(stdout_log_path),
            "stderr_log_path": str(stderr_log_path),
            "process_exited": False,
            "logs_finalized": False,
            "stdout_done_event": stdout_done_event,
            "stderr_done_event": stderr_done_event,
        }
        self._register_running_process(
            job_id=tracked_job_id,
            process=process,
            cancel_path=cancel_path,
        )
        progress_events: list[dict[str, Any]] = []
        progress_line_index = 0
        try:
            while process.poll() is None:
                progress_line_index, new_events = self._read_progress_events_since_line(
                    progress_path,
                    progress_line_index,
                )
                if new_events:
                    progress_events.extend(new_events)
                    reporter = self._progress_reporter
                    if reporter is not None:
                        for event in new_events:
                            reporter(tracked_job_id, scenario_id, event)
                time.sleep(0.05)
            process.wait()
            self._mark_notebook_run_process_exited(job_id=tracked_job_id)
            if stdout_thread is not None:
                stdout_thread.join(timeout=2.0)
            if stderr_thread is not None:
                stderr_thread.join(timeout=2.0)
            with stdout_lock:
                stdout_text = "".join(stdout_chunks)
            with stderr_lock:
                stderr_text = "".join(stderr_chunks)
            progress_line_index, new_events = self._read_progress_events_since_line(
                progress_path,
                progress_line_index,
            )
            if new_events:
                progress_events.extend(new_events)
                reporter = self._progress_reporter
                if reporter is not None:
                    for event in new_events:
                        reporter(tracked_job_id, scenario_id, event)
            self._refresh_notebook_run_log_finalization(job_id=tracked_job_id)
        finally:
            self._clear_running_process(job_id=tracked_job_id)
        returncode = int(process.returncode or 0)
        if not result_path.exists():
            raise RuntimeError(
                "Notebook job runner did not produce a result payload. "
                f"job={notebook_job_id} returncode={returncode}"
            )
        result_payload = json.loads(result_path.read_text(encoding="utf-8"))
        if returncode != 0 or not bool(result_payload.get("ok", False)):
            stderr = (stderr_text or "").strip()
            error = str(result_payload.get("error", "")).strip()
            message = error or stderr or "Notebook job failed."
            raise RuntimeError(message)

        outputs = self._register_outputs(
            scenario=scenario,
            scenario_root=scenario_root,
            notebook_job_id=notebook_job_id,
            notebook_hash=target.notebook_hash,
            raw_outputs=result_payload.get("outputs", []),
        )
        outputs = self._auto_register_outputs_if_needed(
            scenario=scenario,
            scenario_root=scenario_root,
            notebook_job_id=notebook_job_id,
            notebook_hash=target.notebook_hash,
            outputs=outputs,
            before_renderables=before_renderables,
        )
        return {
            "scenario_id": scenario_id,
            "notebook_job_id": notebook_job_id,
            "runtime_mode": str(runtime_mode or "osgeo"),
            "notebook_path": str(target.notebook_path),
            "notebook_hash": target.notebook_hash,
            "outputs": outputs,
            "result": result_payload.get("result", {}),
            "progress_events": progress_events,
            "run_id": run_id,
            "run_root": str(run_root),
            "stdout_log_path": str(stdout_log_path),
            "stderr_log_path": str(stderr_log_path),
        }

    def get_notebook_run_logs(
        self,
        *,
        run_id: str,
        stream: str = "combined",
        head_lines: int = 40,
        tail_lines: int = 80,
    ) -> dict[str, Any]:
        run_id_norm = str(run_id).strip()
        if not run_id_norm:
            raise ValueError("run_id is required")
        stream_norm = str(stream or "combined").strip().lower() or "combined"
        head_lines = max(0, min(2000, int(head_lines)))
        tail_lines = max(0, min(2000, int(tail_lines)))
        run_info = self._stores.notebook_run_info.get(run_id_norm)
        if not run_info:
            job = self._stores.jobs.get(run_id_norm)
            pending = bool(job and job.status in {JobStatus.QUEUED, JobStatus.RUNNING})
            status_value = job.status.value if job is not None else "unknown"
            is_final = not pending
            empty_slice = {
                "job_id": run_id_norm,
                "run_id": run_id_norm,
                "head": [],
                "tail": [],
                "total_lines": 0,
                "total_bytes": 0,
                "path_exists": False,
                "status": status_value,
                "pending": pending,
                "is_final": is_final,
            }
            if stream_norm == "stdout":
                return dict(empty_slice, stream="stdout")
            if stream_norm == "stderr":
                return dict(empty_slice, stream="stderr")
            if stream_norm == "combined":
                return {
                    "job_id": run_id_norm,
                    "run_id": run_id_norm,
                    "stream": "combined",
                    "streams": {
                        "stdout": dict(empty_slice, stream="stdout"),
                        "stderr": dict(empty_slice, stream="stderr"),
                    },
                    "total_bytes": 0,
                    "total_lines": 0,
                    "status": status_value,
                    "pending": pending,
                    "is_final": is_final,
                }
            raise ValueError("stream must be one of: stdout, stderr, combined")

        is_final = self._refresh_notebook_run_log_finalization(job_id=run_id_norm)
        stdout_path = Path(str(run_info.get("stdout_log_path", "")).strip())
        stderr_path = Path(str(run_info.get("stderr_log_path", "")).strip())
        if stream_norm == "stdout":
            return {
                **self._read_notebook_log_slice(
                run_id=run_id_norm,
                stream="stdout",
                path=stdout_path,
                head_lines=head_lines,
                tail_lines=tail_lines,
                ),
                "is_final": is_final,
            }
        if stream_norm == "stderr":
            return {
                **self._read_notebook_log_slice(
                run_id=run_id_norm,
                stream="stderr",
                path=stderr_path,
                head_lines=head_lines,
                tail_lines=tail_lines,
                ),
                "is_final": is_final,
            }
        if stream_norm == "combined":
            stdout = self._read_notebook_log_slice(
                run_id=run_id_norm,
                stream="stdout",
                path=stdout_path,
                head_lines=head_lines,
                tail_lines=tail_lines,
            )
            stderr = self._read_notebook_log_slice(
                run_id=run_id_norm,
                stream="stderr",
                path=stderr_path,
                head_lines=head_lines,
                tail_lines=tail_lines,
            )
            return {
                "job_id": run_id_norm,
                "run_id": run_id_norm,
                "stream": "combined",
                "streams": {
                    "stdout": {**stdout, "is_final": is_final},
                    "stderr": {**stderr, "is_final": is_final},
                },
                "total_bytes": int(stdout.get("total_bytes", 0)) + int(stderr.get("total_bytes", 0)),
                "total_lines": int(stdout.get("total_lines", 0)) + int(stderr.get("total_lines", 0)),
                "is_final": is_final,
            }
        raise ValueError("stream must be one of: stdout, stderr, combined")

    def _mark_notebook_run_process_exited(self, *, job_id: str) -> None:
        with self._stores.notebook_job_lock:
            run_info = self._stores.notebook_run_info.get(job_id)
            if not run_info:
                return
            run_info["process_exited"] = True
            run_info.setdefault("process_exited_at_utc", _utc_now())
        self._refresh_notebook_run_log_finalization(job_id=job_id)

    def _refresh_notebook_run_log_finalization(self, *, job_id: str) -> bool:
        with self._stores.notebook_job_lock:
            run_info = self._stores.notebook_run_info.get(job_id)
            if not run_info:
                return False
            if bool(run_info.get("logs_finalized")):
                return True
            process_exited = bool(run_info.get("process_exited"))
            stdout_done = run_info.get("stdout_done_event")
            stderr_done = run_info.get("stderr_done_event")
            stdout_ready = bool(stdout_done is None or getattr(stdout_done, "is_set", lambda: False)())
            stderr_ready = bool(stderr_done is None or getattr(stderr_done, "is_set", lambda: False)())
            if process_exited and stdout_ready and stderr_ready:
                run_info["logs_finalized"] = True
                run_info.setdefault("logs_finalized_at_utc", _utc_now())
                return True
            return False

    def _prepare_notebook_run_root(
        self,
        *,
        scenario_id: str,
        scenario_root: Path,
        run_id: str,
    ) -> Path:
        self._prune_old_notebook_run_dirs(
            scenario_id=scenario_id,
            scenario_root=scenario_root,
        )
        primary = (scenario_root / ".notebook_jobs" / "runs" / run_id).resolve()
        fallback = (
            self._stores.workspace_root
            / ".notebook_job_runs"
            / str(scenario_id)
            / run_id
        ).resolve()
        repo_fallback = (
            _repo_root()
            / ".notebook_job_runs"
            / str(scenario_id)
            / run_id
        ).resolve()
        candidates: list[tuple[str, Path, Path]] = [
            ("scenario", scenario_root, primary),
            ("workspace", self._stores.workspace_root, fallback),
            ("repo", _repo_root(), repo_fallback),
        ]
        errors: list[str] = []

        for label, anchor, run_root in candidates:
            try:
                _ensure_within_root(anchor, run_root)
                run_root.mkdir(parents=True, exist_ok=True)
                if label == "workspace":
                    logger.warning(
                        "notebook run root fallback used scenario_id=%s run_root=%s",
                        scenario_id,
                        run_root,
                    )
                return run_root
            except Exception as exc:  # pragma: no cover - exercised via fallback test
                errors.append(f"{label}:{run_root} -> {exc}")

        raise RuntimeError(
            "Unable to create notebook run directory. "
            + "; ".join(errors)
        )

    def _prune_old_notebook_run_dirs(
        self,
        *,
        scenario_id: str,
        scenario_root: Path,
    ) -> None:
        retention_hours = self._notebook_run_dir_retention_hours()
        if retention_hours is None:
            return
        cutoff_utc = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
        candidates: list[tuple[str, Path, Path]] = [
            (
                "scenario",
                scenario_root,
                (scenario_root / ".notebook_jobs" / "runs").resolve(),
            ),
            (
                "workspace",
                self._stores.workspace_root,
                (
                    self._stores.workspace_root
                    / ".notebook_job_runs"
                    / str(scenario_id)
                ).resolve(),
            ),
            (
                "repo",
                _repo_root(),
                (
                    _repo_root()
                    / ".notebook_job_runs"
                    / str(scenario_id)
                ).resolve(),
            ),
        ]

        for label, anchor, runs_root in candidates:
            try:
                _ensure_within_root(anchor, runs_root)
            except Exception:
                continue
            if not runs_root.exists() or not runs_root.is_dir():
                continue
            for entry in runs_root.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    modified_utc = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
                except OSError:
                    continue
                if modified_utc >= cutoff_utc:
                    continue
                try:
                    shutil.rmtree(entry, ignore_errors=False)
                    logger.info(
                        "pruned notebook run directory label=%s scenario_id=%s path=%s retention_hours=%s",
                        label,
                        scenario_id,
                        entry,
                        retention_hours,
                    )
                except Exception as exc:
                    logger.warning(
                        "failed to prune notebook run directory label=%s scenario_id=%s path=%s error=%s",
                        label,
                        scenario_id,
                        entry,
                        exc,
                    )

    def _notebook_run_dir_retention_hours(self) -> float | None:
        config = _load_app_config()
        backend_cfg = config.get("backend", {})
        if not isinstance(backend_cfg, dict):
            return None
        notebook_jobs_cfg = backend_cfg.get("notebook_jobs", {})
        if not isinstance(notebook_jobs_cfg, dict):
            return None
        raw = notebook_jobs_cfg.get("run_dir_retention_hours")
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            logger.warning("invalid backend.notebook_jobs.run_dir_retention_hours: %r", raw)
            return None
        if value <= 0.0:
            logger.warning("backend.notebook_jobs.run_dir_retention_hours must be > 0: %s", value)
            return None
        return value

    def _write_notebook_runner_logs(
        self,
        *,
        run_root: Path,
        stdout_text: str | None,
        stderr_text: str | None,
    ) -> None:
        entries = [
            ("runner_stdout.log", stdout_text or ""),
            ("runner_stderr.log", stderr_text or ""),
        ]
        for filename, raw_text in entries:
            if not raw_text:
                continue
            target = (run_root / filename).resolve()
            try:
                _ensure_within_root(run_root, target)
                target.write_text(raw_text, encoding="utf-8", errors="replace")
            except Exception as exc:
                logger.warning(
                    "failed to persist notebook runner log run_root=%s file=%s error=%s",
                    run_root,
                    filename,
                    exc,
                )

    def _stream_subprocess_pipe_to_log(
        self,
        *,
        pipe: Any,
        log_path: Path,
        sink_chunks: list[str],
        sink_lock: threading.Lock,
        done_event: threading.Event,
        stream_name: str,
        job_id: str,
        scenario_id: str,
    ) -> threading.Thread | None:
        if pipe is None:
            return None
        try:
            _ensure_within_root(log_path.parent, log_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None

        def _pump() -> None:
            try:
                with log_path.open("w", encoding="utf-8", errors="replace") as handle:
                    for chunk in iter(pipe.readline, ""):
                        if chunk == "":
                            break
                        handle.write(chunk)
                        handle.flush()
                        with sink_lock:
                            sink_chunks.append(chunk)
                        reporter = self._progress_reporter
                        if reporter is not None:
                            reporter(
                                job_id,
                                scenario_id,
                                {
                                    "event_kind": "log_line",
                                    "log_stream": stream_name,
                                    "log_line": chunk.rstrip("\r\n"),
                                },
                            )
            except Exception as exc:
                logger.warning("failed streaming subprocess logs path=%s error=%s", log_path, exc)
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass
                done_event.set()

        thread = threading.Thread(
            target=_pump,
            name=f"notebook-log-pump-{log_path.name}",
            daemon=True,
        )
        thread.start()
        return thread

    def _read_notebook_log_slice(
        self,
        *,
        run_id: str,
        stream: str,
        path: Path,
        head_lines: int,
        tail_lines: int,
    ) -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            return {
                "job_id": run_id,
                "run_id": run_id,
                "stream": stream,
                "path": str(path),
                "exists": False,
                "path_exists": False,
                "total_bytes": 0,
                "total_lines": 0,
                "head": [],
                "tail": [],
            }
        raw = path.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        return {
            "job_id": run_id,
            "run_id": run_id,
            "stream": stream,
            "path": str(path),
            "exists": True,
            "path_exists": True,
            "total_bytes": path.stat().st_size,
            "total_lines": len(lines),
            "head": lines[:head_lines],
            "tail": list(deque(lines, maxlen=tail_lines)) if tail_lines > 0 else [],
        }

    def _register_outputs(
        self,
        *,
        scenario: Scenario,
        scenario_root: Path,
        notebook_job_id: str,
        notebook_hash: str,
        raw_outputs: Any,
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_outputs, list):
            return []
        outputs: list[dict[str, Any]] = []
        for entry in raw_outputs:
            if not isinstance(entry, dict):
                continue
            rel = _normalize_relative_path(str(entry.get("relative_path", "")))
            if not rel:
                continue
            output_path = (scenario_root / rel).resolve()
            _ensure_within_root(scenario_root, output_path)
            if not output_path.exists() or not output_path.is_file():
                raise FileNotFoundError(f"Notebook output file not found: {output_path}")
            kind = str(entry.get("kind", "analysis")).strip() or "analysis"
            subkind = str(entry.get("subkind", "output")).strip() or "output"
            render_mode_value = entry.get("render_mode")
            render_mode = str(render_mode_value) if isinstance(render_mode_value, str) else None
            metadata = entry.get("metadata", {})
            metadata_payload = metadata if isinstance(metadata, dict) else {}
            product = self._scenario_service._register_product_internal(
                RegisterProductRequest(
                    scenario_id=scenario.scenario_id,
                    kind=kind,
                    subkind=subkind,
                    producer=Producer.PYTHON_PIPELINE,
                    crs=scenario.primary_dem_crs,
                    footprint=scenario.primary_dem_footprint,
                    lineage={
                        "source": "notebook_job",
                        "notebook_job_id": notebook_job_id,
                        "notebook_hash": notebook_hash,
                        "relative_path": rel,
                    },
                )
            )
            file_record = self._scenario_service._register_file(
                product_id=product.product_id,
                scenario_id=scenario.scenario_id,
                scenario_root=scenario_root,
                relative_path=rel,
                media_type=_guess_media_type_from_path(rel),
                role="primary",
            )
            normalized: dict[str, Any] = {
                "relative_path": rel,
                "kind": kind,
                "subkind": subkind,
                "metadata": metadata_payload,
                "product_id": product.product_id,
                "file_id": file_record.file_id,
            }
            if render_mode is not None:
                normalized["render_mode"] = render_mode
            outputs.append(normalized)
        return outputs

    def _auto_register_outputs_if_needed(
        self,
        *,
        scenario: Scenario,
        scenario_root: Path,
        notebook_job_id: str,
        notebook_hash: str,
        outputs: list[dict[str, Any]],
        before_renderables: set[str],
    ) -> list[dict[str, Any]]:
        existing_paths = {
            _normalize_relative_path(str(item.get("relative_path", ""))).lower()
            for item in outputs
            if str(item.get("relative_path", "")).strip()
        }
        for file_record in self._stores.product_files.values():
            if file_record.scenario_id != scenario.scenario_id:
                continue
            existing_paths.add(_normalize_relative_path(file_record.relative_path).lower())

        for rel in sorted(self._snapshot_renderable_files(scenario_root)):
            if rel in before_renderables:
                continue
            if rel.lower() in existing_paths:
                continue
            product = self._scenario_service._register_product_internal(
                RegisterProductRequest(
                    scenario_id=scenario.scenario_id,
                    kind="analysis",
                    subkind=Path(rel).suffix.lstrip(".") or "output",
                    producer=Producer.PYTHON_PIPELINE,
                    crs=scenario.primary_dem_crs,
                    footprint=scenario.primary_dem_footprint,
                    lineage={
                        "source": "notebook_job_auto_discovery",
                        "notebook_job_id": notebook_job_id,
                        "notebook_hash": notebook_hash,
                        "relative_path": rel,
                    },
                )
            )
            file_record = self._scenario_service._register_file(
                product_id=product.product_id,
                scenario_id=scenario.scenario_id,
                scenario_root=scenario_root,
                relative_path=rel,
                media_type=_guess_media_type_from_path(rel),
                role="primary",
            )
            outputs.append(
                {
                    "relative_path": rel,
                    "kind": "analysis",
                    "subkind": Path(rel).suffix.lstrip(".") or "output",
                    "metadata": {"auto_registered": True},
                    "product_id": product.product_id,
                    "file_id": file_record.file_id,
                }
            )
            existing_paths.add(rel.lower())
        return outputs

    @staticmethod
    def _snapshot_renderable_files(scenario_root: Path) -> set[str]:
        found: set[str] = set()
        for node in scenario_root.rglob("*"):
            if not node.is_file():
                continue
            rel = _normalize_relative_path(node.relative_to(scenario_root).as_posix())
            if not rel:
                continue
            if rel.startswith(".notebook_jobs/runs/"):
                continue
            if not _is_renderable_relative_path(rel):
                continue
            found.add(rel.lower())
        return found

    def list_scenario_python_entries(self, scenario_id: str) -> list[dict[str, Any]]:
        scenario = self._scenario_service.get_scenario(scenario_id)
        scenario_root = Path(scenario.directory).resolve()
        _ensure_within_root(self._stores.workspace_root, scenario_root)
        discovered = self._discover_notebook_jobs(scenario_id=scenario_id)
        entries: list[dict[str, Any]] = []
        for item in discovered:
            notebook_path = item.notebook_path.resolve()
            if notebook_path.suffix.lower() != ".py":
                continue
            if scenario_root != notebook_path and scenario_root not in notebook_path.parents:
                continue
            relative_path = _normalize_relative_path(notebook_path.relative_to(scenario_root).as_posix())
            entry_kind = "marimo_notebook" if self._looks_like_marimo_notebook(notebook_path) else "script"
            entries.append(
                {
                    "scenario_id": scenario_id,
                    "relative_path": relative_path,
                    "notebook_job_id": item.metadata.job_id,
                    "entry_kind": entry_kind,
                    "title": item.metadata.title,
                }
            )
        entries.sort(key=lambda row: str(row["relative_path"]).lower())
        return entries

    def resolve_scenario_notebook_job_id(
        self,
        *,
        scenario_id: str,
        relative_path: str,
        expect_marimo: bool | None = None,
    ) -> str:
        normalized = _normalize_relative_path(relative_path)
        entries = self.list_scenario_python_entries(scenario_id)
        match = next(
            (item for item in entries if str(item.get("relative_path", "")).lower() == normalized.lower()),
            None,
        )
        if match is None:
            raise KeyError(f"Scenario Python entry not found: {relative_path}")
        if expect_marimo is True and match.get("entry_kind") != "marimo_notebook":
            raise ValueError(f"Entry is not a Marimo notebook: {relative_path}")
        if expect_marimo is False and match.get("entry_kind") == "marimo_notebook":
            raise ValueError(f"Entry is a Marimo notebook, not a plain script: {relative_path}")
        return str(match["notebook_job_id"])

    @staticmethod
    def _looks_like_marimo_notebook(path: Path) -> bool:
        name = path.name.lower()
        if name.endswith(".mo.py"):
            return True
        try:
            snippet = path.read_text(encoding="utf-8", errors="replace")[:4096].lower()
        except Exception:
            return False
        return ("import marimo" in snippet) or ("marimo.app(" in snippet)

    def _read_progress_events(self, progress_path: Path) -> list[dict[str, Any]]:
        _line_index, events = _read_worker_progress_events_since_line(progress_path, 0)
        return events

    def _read_progress_events_since_line(
        self,
        progress_path: Path,
        start_index: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        return _read_worker_progress_events_since_line(progress_path, start_index)

    def _discover_notebook_jobs(self, scenario_id: str | None = None) -> list[DiscoveredNotebookJob]:
        config = _load_app_config()
        config_path = _resolve_config_path()
        roots = resolve_notebook_job_roots(
            config=config,
            config_path=config_path,
            workspace_root=self._stores.workspace_root,
        )
        scenario_root: Path | None = None
        if scenario_id:
            scenario = self._stores.scenarios.get(str(scenario_id))
            if scenario is not None:
                scenario_root = Path(scenario.directory).resolve()
                _ensure_within_root(self._stores.workspace_root, scenario_root)
                roots = [*roots, (scenario_root / ".notebook_jobs").resolve()]
        explicit_jobs = discover_notebook_jobs(roots)
        seen: set[str] = set()
        for item in explicit_jobs:
            if item.metadata.job_id in seen:
                raise ValueError(f"Duplicate notebook job_id discovered: {item.metadata.job_id}")
            seen.add(item.metadata.job_id)

        implicit_configured = self._discover_implicit_root_script_jobs(roots=roots)
        merged = list(explicit_jobs)
        explicit_ids = {item.metadata.job_id for item in explicit_jobs}
        for item in implicit_configured:
            if item.metadata.job_id in explicit_ids:
                continue
            merged.append(item)

        if scenario_root is not None:
            implicit_jobs = self._discover_implicit_scenario_script_jobs(scenario_root=scenario_root)
            merged_ids = {item.metadata.job_id for item in merged}
            for item in implicit_jobs:
                if item.metadata.job_id in merged_ids:
                    continue
                merged.append(item)
        merged.sort(key=lambda item: (item.metadata.job_id, str(item.notebook_path)))
        return merged

    def _discover_implicit_root_script_jobs(self, *, roots: list[Path]) -> list[DiscoveredNotebookJob]:
        discovered: list[DiscoveredNotebookJob] = []
        seen_ids: set[str] = set()
        for root in roots:
            if not root.exists() or not root.is_dir():
                continue
            for item in self._discover_implicit_script_jobs_under_root(
                root=root,
                description="Implicit configured-root script job.",
                tags=["configured-script", "implicit"],
                recursive=False,
            ):
                if item.metadata.job_id in seen_ids:
                    raise ValueError(
                        f"Duplicate implicit configured-root script job_id discovered: "
                        f"{item.metadata.job_id}"
                    )
                seen_ids.add(item.metadata.job_id)
                discovered.append(item)
        return discovered

    def _discover_implicit_scenario_script_jobs(self, *, scenario_root: Path) -> list[DiscoveredNotebookJob]:
        return self._discover_implicit_script_jobs_under_root(
            root=scenario_root,
            description="Implicit scenario script job.",
            tags=["scenario-script", "implicit"],
            recursive=True,
        )

    def _discover_implicit_script_jobs_under_root(
        self,
        *,
        root: Path,
        description: str,
        tags: list[str],
        recursive: bool,
    ) -> list[DiscoveredNotebookJob]:
        if not root.exists() or not root.is_dir():
            return []
        discovered: list[DiscoveredNotebookJob] = []
        entries = root.rglob("*.py") if recursive else root.glob("*.py")
        for entry in sorted(entries, key=lambda item: str(item).lower()):
            if not entry.is_file():
                continue
            try:
                rel_path = _normalize_relative_path(entry.relative_to(root).as_posix())
            except Exception:
                continue
            if not rel_path:
                continue
            if rel_path.startswith(".notebook_jobs/runs/"):
                continue
            name = entry.name
            if name.startswith(".") or name.startswith("_") or name == "__init__.py":
                continue
            rel_stem = Path(rel_path).with_suffix("").as_posix().strip()
            slug = rel_stem.replace("/", "-").replace("\\", "-")
            if not slug:
                continue
            notebook_hash = hashlib.sha256(entry.read_bytes()).hexdigest()
            metadata = NotebookJobMetadata(
                job_id=f"script-{slug}",
                title=name,
                notebook_path=rel_path,
                description=description,
                visibility="default",
                tags=tags,
            )
            discovered.append(
                DiscoveredNotebookJob(
                    metadata=metadata,
                    definition_path=entry.resolve(),
                    notebook_path=entry.resolve(),
                    notebook_hash=notebook_hash,
                )
            )
        return discovered

    def _python_executable(self) -> str:
        config = _load_app_config()
        backend_cfg = config.get("backend", {})
        notebook_jobs_cfg = (
            backend_cfg.get("notebook_jobs", {})
            if isinstance(backend_cfg, dict)
            else {}
        )
        if isinstance(notebook_jobs_cfg, dict):
            raw = notebook_jobs_cfg.get("python_executable")
            if isinstance(raw, str) and raw.strip():
                candidate = Path(raw).expanduser()
                if candidate.exists():
                    return str(candidate)
        repo_py = _preferred_repo_python()
        if repo_py is not None:
            return str(repo_py)
        current = Path(sys.executable).expanduser()
        if current.exists():
            return str(current)
        return "python3"


# Backward compatibility aliases while external imports migrate to canonical names.
StubScenarioService = ScenarioService
StubProductService = ProductService
StubLayerService = LayerService
StubJobService = JobService
StubNotebookJobService = NotebookJobService
StubNotebookSessionService = NotebookSessionService
StubMarimoService = MarimoService


@dataclass(frozen=True)
class ServiceContainer:
    scenario_service: ScenarioService
    product_service: ProductService
    layer_service: LayerService
    job_service: JobService
    assistant_service: AssistantService
    notebook_job_service: NotebookJobService
    id_path_accessor: IdPathAccessor
    horizon_key_service: HorizonKeyService
    shared_horizon_store_service: SharedHorizonStoreService
    notebook_session_service: NotebookSessionService
    marimo_service: MarimoService
    mcp_server: McpServer
    stores: InMemoryStores


def build_service_container() -> ServiceContainer:
    previous = SERVICES
    if previous is not None:
        try:
            previous.job_service.shutdown()
            previous.notebook_job_service.terminate_all_running(reason="service rebuild")
            previous.marimo_service.stop_if_running()
        except Exception:
            logger.warning("failed shutting down previous services during rebuild", exc_info=True)
    workspace_root = _resolve_workspace_root()
    workspace_root.mkdir(parents=True, exist_ok=True)
    stores = InMemoryStores(
        workspace_root=workspace_root,
        catalog_db_path=workspace_root / "scenario_catalog.db",
    )
    # Full filesystem reconcile at container construction can dominate app startup on
    # large workspaces. Startup favors fast local-db hydration; explicit discovery/reconcile
    # paths remain available via startup discovery config and API routes.
    scenario_service = ScenarioService(stores, reconcile_on_startup=False)
    product_service = ProductService(stores, scenario_service)
    notebook_job_service = NotebookJobService(stores, scenario_service)
    job_service = JobService(stores, notebook_job_service)
    layer_service = LayerService(stores, scenario_service)
    llm_cfg = _load_llm_config()
    assistant_store_path = _resolve_assistant_store_path(
        workspace_root,
        llm_cfg,
    )
    legacy_json_path = _resolve_assistant_legacy_json_path(
        workspace_root,
        llm_cfg,
        assistant_store_path=assistant_store_path,
    )
    assistant_store = AssistantSessionStore(
        assistant_store_path,
        legacy_json_path=legacy_json_path,
    )
    assistant_policy = AssistantPolicyService(
        require_confirmation_for_mutations=bool(llm_cfg.get("require_confirmation_for_mutations", True))
    )
    provider_metadata_cache_db_path: str | None = str(assistant_store_path)
    assistant_providers = AssistantProviderRegistry(
        config=llm_cfg,
        workspace_root=str(workspace_root),
        model_metadata_cache_db_path=provider_metadata_cache_db_path,
    )
    assistant_service = AssistantService(
        store=assistant_store,
        policy_service=assistant_policy,
        provider_registry=assistant_providers,
        tool_services=ToolExecutionServices(
            scenario_service=scenario_service,
            product_service=product_service,
            layer_service=layer_service,
            job_service=job_service,
            notebook_job_service=notebook_job_service,
            stores=stores,
        ),
        assistant_ws_events=stores.assistant_ws_events,
        action_router_spec_path=_resolve_action_router_spec_path(llm_cfg),
    )
    mcp_server = McpServer()
    for scenario_id in list(stores.scenarios.keys()):
        try:
            scenario = stores.scenarios.get(scenario_id)
            if scenario is None:
                continue
            scenario_dir = Path(scenario.directory).resolve()
            if not scenario_dir.exists() or not scenario_dir.is_dir():
                continue
            layer_service.hydrate_layers_from_db(scenario_id)
        except Exception as exc:  # pragma: no cover - defensive bootstrap logging
            logger.warning("layer hydrate skipped scenario_id=%s reason=%s", scenario_id, exc)
    id_path_accessor = IdPathAccessor(stores, scenario_service, product_service)
    horizon_key_service = HorizonKeyService()

    def _resolve_scenario_paths(scenario_id: str) -> ScenarioPaths:
        scenario = scenario_service.get_scenario(scenario_id)
        scenario_root_dir = Path(scenario.directory).expanduser().resolve()
        dem_rel = _normalize_primary_dem_path(scenario.primary_dem_path)
        dem_path = (scenario_root_dir / dem_rel).resolve()
        _ensure_within_root(scenario_root_dir, dem_path)
        hillshade_path = (scenario_root_dir / "hillshade.tif").resolve()
        _ensure_within_root(scenario_root_dir, hillshade_path)
        return ScenarioPaths(
            scenario_root_dir=scenario_root_dir,
            dem_path=dem_path,
            hillshade_path=hillshade_path,
        )

    def _register_generated_raster(
        scenario_id: str,
        relative_path: str,
        lineage: dict[str, Any],
    ) -> RegisteredRasterOutput:
        scenario = scenario_service.get_scenario(scenario_id)
        scenario_root = Path(scenario.directory).expanduser().resolve()
        rel = _normalize_relative_path(relative_path)
        if not rel:
            raise ValueError("relative_path is required")
        output_path = (scenario_root / rel).resolve()
        _ensure_within_root(scenario_root, output_path)
        if not output_path.exists() or not output_path.is_file():
            raise FileNotFoundError(f"Generated raster file not found: {output_path}")

        product = scenario_service._register_product_internal(
            RegisterProductRequest(
                scenario_id=scenario.scenario_id,
                kind="analysis",
                subkind="map_algebra",
                producer=Producer.PYTHON_PIPELINE,
                crs=scenario.primary_dem_crs,
                footprint=scenario.primary_dem_footprint,
                lineage=dict(lineage),
            )
        )
        file_record = scenario_service._register_file(
            product_id=product.product_id,
            scenario_id=scenario.scenario_id,
            scenario_root=scenario_root,
            relative_path=rel,
            media_type=_guess_media_type_from_path(rel),
            role="primary",
        )
        return RegisteredRasterOutput(
            product_id=product.product_id,
            file_id=file_record.file_id,
            relative_path=rel,
        )

    def _publish_generated_raster_layer(
        scenario_id: str,
        product_id: str,
        file_id: str,
        title: str | None,
        visible: bool,
        opacity: float,
        z_index: int | None,
        style: dict[str, Any] | None,
        on_existing: str,
    ) -> PublishedLayerOutput:
        layers = layer_service.list_layers(scenario_id)
        normalized_title = str(title or "").strip()
        existing = next((item for item in layers if str(item.source_file_id) == file_id), None)
        if existing is None and normalized_title:
            existing = next(
                (item for item in layers if str(item.title).strip().lower() == normalized_title.lower()),
                None,
            )
        on_existing_key = str(on_existing or "update").strip().lower() or "update"
        if on_existing_key not in {"update", "error", "new"}:
            raise ValueError("publish_layer.on_existing must be one of: update, error, new")
        if existing is not None and on_existing_key == "error":
            raise ValueError(f"Layer already exists for publish request: {existing.layer_id}")
        target_title = normalized_title or Path(str(file_id)).stem
        if z_index is None:
            target_z = (max((int(item.z_index) for item in layers), default=0) + 1)
        else:
            target_z = int(z_index)
        if existing is not None and on_existing_key == "update":
            updated = layer_service.update_layer(
                existing.layer_id,
                UpdateLayerStateRequest(
                    title=target_title,
                    visible=bool(visible),
                    opacity=float(opacity),
                    z_index=int(target_z),
                    style=dict(style or {}),
                ),
            )
            return PublishedLayerOutput(
                layer_id=updated.layer_id,
                title=updated.title,
                visible=bool(updated.visible),
            )
        created = layer_service.create_layer(
            CreateLayerStateRequest(
                scenario_id=scenario_id,
                product_id=product_id,
                title=target_title,
                visible=bool(visible),
                opacity=float(opacity),
                z_index=int(target_z),
                render_mode=RenderMode.RASTER,
                source_file_id=file_id,
                style=dict(style or {}),
            )
        )
        return PublishedLayerOutput(
            layer_id=created.layer_id,
            title=created.title,
            visible=bool(created.visible),
        )

    def _emit_handler_progress(payload: dict[str, Any]) -> None:
        job_id = CURRENT_JOB_ID.get()
        if not job_id:
            return
        if not isinstance(payload, dict):
            return
        with job_service._job_lock:  # noqa: SLF001
            current = job_service._stores.jobs.get(job_id)  # noqa: SLF001
        if current is None:
            return
        job_service._emit_live_progress(  # noqa: SLF001
            job_id,
            current.scenario_id,
            dict(payload),
        )

    def _is_handler_job_cancelled() -> bool:
        job_id = CURRENT_JOB_ID.get()
        if not job_id:
            return False
        return bool(job_service._is_cancelled(job_id))  # noqa: SLF001

    set_scenario_paths_resolver(_resolve_scenario_paths)
    set_notebook_job_executor(notebook_job_service.execute_notebook_job)
    set_generated_raster_registrar(_register_generated_raster)
    set_generated_raster_layer_publisher(_publish_generated_raster_layer)
    set_job_progress_emitter(_emit_handler_progress)
    set_job_cancel_checker(_is_handler_job_cancelled)

    return ServiceContainer(
        scenario_service=scenario_service,
        product_service=product_service,
        layer_service=layer_service,
        job_service=job_service,
        assistant_service=assistant_service,
        notebook_job_service=notebook_job_service,
        id_path_accessor=id_path_accessor,
        horizon_key_service=horizon_key_service,
        shared_horizon_store_service=SharedHorizonStoreService(
            stores,
            scenario_service,
            product_service,
            id_path_accessor,
            horizon_key_service,
        ),
        notebook_session_service=NotebookSessionService(stores),
        marimo_service=MarimoService(stores),
        mcp_server=mcp_server,
        stores=stores,
    )


SERVICES: ServiceContainer | None = None
_SERVICES_LOCK = threading.Lock()


def get_services() -> ServiceContainer:
    global SERVICES
    if SERVICES is not None:
        return SERVICES
    with _SERVICES_LOCK:
        if SERVICES is None:
            SERVICES = build_service_container()
    return SERVICES


def shutdown_services() -> None:
    global SERVICES
    services = SERVICES
    if services is None:
        return
    services.job_service.shutdown()
    terminated = services.notebook_job_service.terminate_all_running(reason="service shutdown")
    if terminated:
        logger.info("terminated %s active notebook job process(es) during shutdown", terminated)
    services.assistant_service.shutdown()
    services.marimo_service.stop_if_running()
    SERVICES = None
