from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ScenarioPaths:
    scenario_root_dir: Path
    dem_path: Path
    hillshade_path: Path


@dataclass(frozen=True)
class RegisteredRasterOutput:
    product_id: str
    file_id: str
    relative_path: str


@dataclass(frozen=True)
class PublishedLayerOutput:
    layer_id: str
    title: str
    visible: bool


_scenario_paths_resolver: Callable[[str], ScenarioPaths] | None = None
_notebook_job_executor: Callable[[str, str, dict[str, Any], str], dict[str, Any]] | None = None
_generated_raster_registrar: Callable[[str, str, dict[str, Any]], RegisteredRasterOutput] | None = None
_generated_raster_layer_publisher: Callable[
    [str, str, str, str | None, bool, float, int | None, dict[str, Any] | None, str],
    PublishedLayerOutput,
] | None = None
_job_progress_emitter: Callable[[dict[str, Any]], None] | None = None
_job_cancel_checker: Callable[[], bool] | None = None


def set_scenario_paths_resolver(resolver: Callable[[str], ScenarioPaths]) -> None:
    global _scenario_paths_resolver
    _scenario_paths_resolver = resolver


def resolve_scenario_paths(scenario_id: str) -> ScenarioPaths:
    if _scenario_paths_resolver is None:
        raise RuntimeError("Scenario path resolver is not configured.")
    return _scenario_paths_resolver(scenario_id)


def set_notebook_job_executor(
    executor: Callable[[str, str, dict[str, Any], str], dict[str, Any]],
) -> None:
    global _notebook_job_executor
    _notebook_job_executor = executor


def set_generated_raster_registrar(
    registrar: Callable[[str, str, dict[str, Any]], RegisteredRasterOutput],
) -> None:
    global _generated_raster_registrar
    _generated_raster_registrar = registrar


def register_generated_raster(
    scenario_id: str,
    relative_path: str,
    lineage: dict[str, Any],
) -> RegisteredRasterOutput:
    if _generated_raster_registrar is None:
        raise RuntimeError("Generated raster registrar is not configured.")
    return _generated_raster_registrar(scenario_id, relative_path, lineage)


def set_generated_raster_layer_publisher(
    publisher: Callable[
        [str, str, str, str | None, bool, float, int | None, dict[str, Any] | None, str],
        PublishedLayerOutput,
    ],
) -> None:
    global _generated_raster_layer_publisher
    _generated_raster_layer_publisher = publisher


def publish_generated_raster_layer(
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
    if _generated_raster_layer_publisher is None:
        raise RuntimeError("Generated raster layer publisher is not configured.")
    return _generated_raster_layer_publisher(
        scenario_id,
        product_id,
        file_id,
        title,
        visible,
        opacity,
        z_index,
        style,
        on_existing,
    )


def set_job_progress_emitter(emitter: Callable[[dict[str, Any]], None] | None) -> None:
    global _job_progress_emitter
    _job_progress_emitter = emitter


def emit_job_progress(payload: dict[str, Any]) -> None:
    if _job_progress_emitter is None:
        return
    _job_progress_emitter(payload)


def set_job_cancel_checker(checker: Callable[[], bool] | None) -> None:
    global _job_cancel_checker
    _job_cancel_checker = checker


def is_job_cancel_requested() -> bool:
    if _job_cancel_checker is None:
        return False
    return bool(_job_cancel_checker())


def execute_notebook_job(
    scenario_id: str,
    notebook_job_id: str,
    params: dict[str, Any],
    runtime_mode: str = "osgeo",
) -> dict[str, Any]:
    if _notebook_job_executor is None:
        raise RuntimeError("Notebook job executor is not configured.")
    try:
        return _notebook_job_executor(scenario_id, notebook_job_id, params, str(runtime_mode or "osgeo"))
    except TypeError as exc:
        if "positional argument" not in str(exc) and "runtime_mode" not in str(exc):
            raise
        return _notebook_job_executor(scenario_id, notebook_job_id, params)  # type: ignore[misc]
