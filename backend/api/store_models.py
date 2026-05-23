from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.contracts.models import Job, JobEvent, LayerState, Product, Scenario
from backend.api.runtime_state import (
    BoundedEventBuffer,
    BoundedRunInfoMap,
    new_assistant_ws_event_buffer,
    new_notebook_run_info_map,
    new_ws_event_buffer,
)


@dataclass(frozen=True)
class ProductFileRecord:
    file_id: str
    product_id: str
    scenario_id: str
    scenario_root: Path
    relative_path: str
    media_type: str
    role: str
    created_at_utc: str


@dataclass
class NotebookSessionRecord:
    session_id: str
    api_token: str
    client_name: str
    created_at_utc: str
    last_seen_at_utc: str


@dataclass
class MarimoProcessRecord:
    mode: str = "none"
    process: subprocess.Popen[str] | None = None
    base_url: str | None = None
    log_path: str | None = None
    log_handle: Any | None = None
    command: list[str] = field(default_factory=list)
    cwd: str | None = None
    started_at_utc: str | None = None


@dataclass
class DiscoveryRunRecord:
    last_run_utc: str | None = None
    discovered_count: int = 0
    ingested_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ScenarioTomlConfig:
    schema_version: int
    primary_path: Path
    surrounding_paths: list[Path]
    time_start_utc: str
    time_stop_utc: str
    time_step_hours: float
    metadata: dict[str, Any]
    raw_sha256: str
    config_path: Path


@dataclass
class InMemoryStores:
    workspace_root: Path
    catalog_db_path: Path
    scenarios: dict[str, Scenario] = field(default_factory=dict)
    scenario_roots: dict[str, Path] = field(default_factory=dict)
    products: dict[str, Product] = field(default_factory=dict)
    product_files: dict[str, ProductFileRecord] = field(default_factory=dict)
    layers: dict[str, LayerState] = field(default_factory=dict)
    jobs: dict[str, Job] = field(default_factory=dict)
    job_events: dict[str, list[JobEvent]] = field(default_factory=dict)
    ws_events: BoundedEventBuffer = field(default_factory=new_ws_event_buffer)
    assistant_ws_events: BoundedEventBuffer = field(default_factory=new_assistant_ws_event_buffer)
    notebook_sessions: dict[str, NotebookSessionRecord] = field(default_factory=dict)
    notebook_sessions_by_token: dict[str, str] = field(default_factory=dict)
    notebook_job_processes: dict[str, subprocess.Popen[str]] = field(default_factory=dict)
    notebook_job_cancel_paths: dict[str, Path] = field(default_factory=dict)
    notebook_run_info: BoundedRunInfoMap = field(default_factory=new_notebook_run_info_map)
    notebook_job_lock: threading.RLock = field(default_factory=threading.RLock)
    marimo: MarimoProcessRecord = field(default_factory=MarimoProcessRecord)
    discovery: DiscoveryRunRecord = field(default_factory=DiscoveryRunRecord)
    product_catalog_hydrated_scenarios: set[str] = field(default_factory=set)
