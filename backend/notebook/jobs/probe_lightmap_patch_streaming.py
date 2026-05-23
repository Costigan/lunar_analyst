from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import numpy as np

from backend.notebook.notebook_helper import bool_param
from backend.notebook.notebook_helper import get_context
from backend.notebook.notebook_helper import is_cancelled
from backend.notebook.notebook_helper import is_running_under_job_runner
from backend.notebook.notebook_helper import register_output_if_available
from backend.notebook.notebook_helper import report_progress
from backend.notebook.notebook_helper import resolve_dem_path_from_params
from backend.notebook.notebook_helper import resolve_scenario_identity_and_root
from backend.notebook.notebook_helper import resolve_scenario_relative_dir
from backend.notebook.notebook_helper import write_json
from backend.worker.lightmap_streaming import LightmapStreamRequestPy
from backend.worker.lightmap_streaming import LightmapStreamingClient


def _safe_report_progress(*, percent: float, message: str, stage: str) -> None:
    if not is_running_under_job_runner():
        return
    report_progress(percent=percent, message=message, stage=stage)


def _safe_is_cancelled() -> bool:
    if not is_running_under_job_runner():
        return False
    return is_cancelled()


def _resolve_runtime_context() -> tuple[str, Path, dict[str, Any]]:
    if is_running_under_job_runner():
        ctx = get_context()
        scenario_id = str(ctx.scenario_id)
        scenario_root = Path(ctx.scenario_root_dir).resolve()
        params = ctx.params if isinstance(ctx.params, dict) else {}
        return scenario_id, scenario_root, params

    scenario_id, scenario_root = resolve_scenario_identity_and_root()
    return scenario_id, scenario_root, {}


def _execute_probe() -> dict[str, Any]:
    scenario_id, scenario_root, params = _resolve_runtime_context()
    if not scenario_root.exists() or not scenario_root.is_dir():
        raise FileNotFoundError(f"Scenario root does not exist: {scenario_root}")

    # Bootstrap first so strict native resolver owns sqlite/gdal load order.
    _safe_report_progress(percent=5.0, message="Bootstrapping streaming client", stage="bootstrap")
    client = LightmapStreamingClient(force_bootstrap=True, verify_bridge_smoke=False)

    _safe_report_progress(percent=10.0, message="Preparing streaming probe inputs", stage="prepare")
    dem_path = resolve_dem_path_from_params(
        scenario_root=scenario_root,
        scenario_id=scenario_id,
        params=params,
    )
    try:
        dem_rel = dem_path.relative_to(scenario_root).as_posix()
    except ValueError:
        dem_rel = str(dem_path)

    horizons_rel, horizons_dir = resolve_scenario_relative_dir(
        scenario_root=scenario_root,
        raw=str(params.get("horizons_relative_dir", "lighting/horizons")).strip(),
        default="lighting/horizons",
        create=False,
    )
    if not horizons_dir.exists() or not horizons_dir.is_dir():
        raise FileNotFoundError(f"Horizons directory does not exist: {horizons_dir}")

    probe_rel, probe_dir = resolve_scenario_relative_dir(
        scenario_root=scenario_root,
        raw=str(params.get("probe_output_relative_dir", "lighting/streaming_probe")).strip(),
        default="lighting/streaming_probe",
        create=True,
    )
    summary_rel = f"{probe_rel.rstrip('/')}/streaming_probe_summary.json"
    summary_path = (scenario_root / summary_rel).resolve()

    surrounding_dem_paths_raw = params.get("surrounding_dem_paths", [])
    if not isinstance(surrounding_dem_paths_raw, list):
        raise ValueError("params.surrounding_dem_paths must be a list of paths.")
    surrounding_dem_paths = [
        Path(str(item)).expanduser().resolve()
        for item in surrounding_dem_paths_raw
        if str(item).strip()
    ]

    time_start_utc = str(params.get("time_start_utc", "2024-01-01T00:00:00Z")).strip()
    time_stop_utc = str(params.get("time_stop_utc", time_start_utc)).strip()
    time_step_hours = float(params.get("time_step_hours", 1.0))
    observer_elevation_meters = float(params.get("observer_elevation_meters", 0.0))
    buffer_count = max(1, int(params.get("buffer_count", 4)))
    max_tiles = max(1, int(params.get("max_tiles", 4)))
    poll_timeout_ms = max(1, int(params.get("poll_timeout_ms", 250)))
    stop_on_error = bool_param(params, "stop_on_error", True)
    use_spice_sun_vectors = bool_param(params, "use_spice_sun_vectors", True)

    request = LightmapStreamRequestPy(
        scenario_root_dir=scenario_root,
        dem_path=dem_path,
        surrounding_dem_paths=surrounding_dem_paths,
        horizon_dir=horizons_dir,
        start_utc=time_start_utc,
        stop_utc=time_stop_utc,
        time_step_hours=time_step_hours,
        observer_elevation_meters=observer_elevation_meters,
        patch_width=128,
        patch_height=128,
        max_read_parallelism=max(1, int(params.get("max_read_parallelism", 4))),
        max_compute_parallelism=max(1, int(params.get("max_compute_parallelism", 24))),
        ready_queue_capacity=max(1, int(params.get("ready_queue_capacity", 64))),
        use_spice_sun_vectors=use_spice_sun_vectors,
    )

    job_id = ""
    first_tile_rel: str | None = None
    tile_summaries: list[dict[str, object]] = []
    error_messages: list[str] = []
    saw_terminal = False
    terminal_message: str | None = None
    status_snapshot: dict[str, object] = {}
    tiles_with_nonzero = 0
    total_nonzero_values = 0

    try:
        if _safe_is_cancelled():
            raise RuntimeError("Job cancelled before streaming probe started.")

        job_id = client.start(request)
        time_count = request.time_count()
        buffers = {
            buffer_id: np.zeros((time_count, request.patch_height, request.patch_width), dtype=np.uint8)
            for buffer_id in range(buffer_count)
        }

        for buffer_id, arr in buffers.items():
            ok = client.register_buffer(job_id, buffer_id, arr)
            if not ok:
                raise RuntimeError(f"Failed to register buffer_id={buffer_id} for job={job_id}.")

        _safe_report_progress(percent=30.0, message="Streaming tiles", stage="stream")
        processed = 0

        while True:
            if _safe_is_cancelled():
                client.cancel(job_id)
                raise RuntimeError("Job cancelled while polling streaming tiles.")

            tile = client.poll_next_tile(job_id, poll_timeout_ms)
            if tile is None:
                continue

            state = tile.state.strip().lower()
            if state == "terminal":
                saw_terminal = True
                terminal_message = tile.message
                break
            if state == "error":
                message = (
                    f"tile_id={tile.tile_id} patch=({tile.patch_row},{tile.patch_col}) "
                    f"message={tile.message or ''}"
                )
                error_messages.append(message)
                if stop_on_error:
                    raise RuntimeError(f"Streaming tile error: {message}")
                continue
            if state != "ready":
                continue

            arr = buffers.get(tile.buffer_id)
            if arr is None:
                raise RuntimeError(f"Tile referenced unknown buffer_id={tile.buffer_id}.")

            tile_copy = np.array(arr, copy=True)
            nonzero_count = int(np.count_nonzero(tile_copy))
            has_nonzero = nonzero_count > 0
            if has_nonzero:
                tiles_with_nonzero += 1
                total_nonzero_values += nonzero_count
            print(
                "[streaming_probe] "
                f"tile_id={tile.tile_id} buffer_id={tile.buffer_id} "
                f"nonzero_count={nonzero_count} has_nonzero={has_nonzero}",
                flush=True,
            )
            digest = hashlib.sha256(tile_copy.tobytes()).hexdigest()
            tile_summaries.append(
                {
                    "tile_id": tile.tile_id,
                    "buffer_id": tile.buffer_id,
                    "patch_row": tile.patch_row,
                    "patch_col": tile.patch_col,
                    "time_count": tile.time_count,
                    "width": tile.width,
                    "height": tile.height,
                    "sum": int(tile_copy.sum(dtype=np.uint64)),
                    "mean": float(tile_copy.mean()),
                    "min": int(tile_copy.min()),
                    "max": int(tile_copy.max()),
                    "nonzero_count": nonzero_count,
                    "has_nonzero": has_nonzero,
                    "sha256": digest,
                }
            )

            if processed == 0:
                first_tile_rel = f"{probe_rel.rstrip('/')}/streaming_probe_first_tile.npy"
                first_tile_path = (scenario_root / first_tile_rel).resolve()
                np.save(first_tile_path, tile_copy)
                register_output_if_available(
                    relative_path=first_tile_rel,
                    kind="analysis",
                    subkind="lightmap_streaming_tile_sample",
                    metadata={
                        "source_dem": dem_rel,
                        "tile_id": tile.tile_id,
                        "patch_row": tile.patch_row,
                        "patch_col": tile.patch_col,
                    },
                )

            if not client.release_buffer(job_id, tile.buffer_id):
                raise RuntimeError(
                    f"Failed to release buffer_id={tile.buffer_id} for job={job_id}."
                )

            processed += 1
            progress = min(90.0, 30.0 + (60.0 * min(processed, max_tiles) / max_tiles))
            _safe_report_progress(
                percent=progress,
                message=f"Processed {processed} streamed tile(s)",
                stage="stream",
            )

            if processed >= max_tiles:
                client.cancel(job_id)
                break

        if job_id:
            for _ in range(20):
                status = client.get_status(job_id)
                status_snapshot = {
                    "state": status.state,
                    "progress01": status.progress01,
                    "tiles_produced": status.tiles_produced,
                    "tiles_consumed": status.tiles_consumed,
                    "ready_queue_depth": status.ready_queue_depth,
                    "free_buffer_count": status.free_buffer_count,
                    "message": status.message,
                }
                if status.state.lower() in {"completed", "cancelled", "failed"}:
                    break
                time.sleep(0.1)
    finally:
        if job_id:
            try:
                client.dispose(job_id)
            except Exception:
                pass

    summary = {
        "scenario_id": scenario_id,
        "scenario_root": str(scenario_root),
        "job_id": job_id,
        "dem_relative_path": dem_rel,
        "horizons_relative_dir": horizons_rel,
        "probe_output_relative_dir": probe_rel,
        "first_tile_relative_path": first_tile_rel,
        "request": {
            "time_start_utc": time_start_utc,
            "time_stop_utc": time_stop_utc,
            "time_step_hours": time_step_hours,
            "observer_elevation_meters": observer_elevation_meters,
            "buffer_count": buffer_count,
            "max_tiles": max_tiles,
            "poll_timeout_ms": poll_timeout_ms,
            "stop_on_error": stop_on_error,
            "use_spice_sun_vectors": use_spice_sun_vectors,
        },
        "status": status_snapshot,
        "saw_terminal": saw_terminal,
        "terminal_message": terminal_message,
        "error_messages": error_messages,
        "tile_count": len(tile_summaries),
        "tiles_with_nonzero": tiles_with_nonzero,
        "total_nonzero_values": total_nonzero_values,
        "tile_summaries": tile_summaries,
    }
    print(
        "[streaming_probe] "
        f"tile_count={len(tile_summaries)} "
        f"tiles_with_nonzero={tiles_with_nonzero} "
        f"total_nonzero_values={total_nonzero_values}",
        flush=True,
    )
    write_json(summary_path, summary, indent=2, sort_keys=True)

    register_output_if_available(
        relative_path=summary_rel,
        kind="analysis",
        subkind="lightmap_streaming_probe",
        metadata={
            "source_dem": dem_rel,
            "horizons_relative_dir": horizons_rel,
            "tile_count": len(tile_summaries),
            "error_count": len(error_messages),
            "saw_terminal": saw_terminal,
        },
    )

    _safe_report_progress(percent=95.0, message="Streaming probe complete", stage="finalize")
    return summary


if __name__ == "__main__" or is_running_under_job_runner():
    print(_execute_probe())
