from __future__ import annotations

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


STAGE_ORDER: tuple[str, ...] = (
    "k0_noop",
    "k1_index_write",
    "k2_fixed_loop",
    "k3_one_sample_read",
    "k4_branching_short_loop",
    "k5_long_dry_loop",
    "k6_los_math",
    "k7_handler_mirror",
)


@dataclass(frozen=True)
class LadderConfig:
    rows: int = 2048
    cols: int = 2048
    observer_count: int = 2
    observer_row: float = 1.0
    observer_col_start: float = 1649.0
    observer_height: float = 1.0
    direction_count: int = 360
    step_size_pixels: float = 0.5
    max_steps: int = 11586
    loop_iterations: int = 1000
    threads_per_block: int = 128


def _read_windows_tdr_settings() -> dict[str, Any]:
    settings = {
        "TdrLevel": None,
        "TdrDelay": None,
        "TdrDdiDelay": None,
    }
    if platform.system().lower() != "windows":
        return settings
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
        ) as key:
            for name in settings:
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                    settings[name] = int(value)
                except FileNotFoundError:
                    settings[name] = None
    except Exception as exc:
        settings["error"] = str(exc)
    return settings


def _collect_host_metadata() -> dict[str, Any]:
    return {
        "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "env": {
            "CUDA_LAUNCH_BLOCKING": os.environ.get("CUDA_LAUNCH_BLOCKING"),
            "NUMBA_ENABLE_CUDASIM": os.environ.get("NUMBA_ENABLE_CUDASIM"),
            "NUMBA_CUDA_DEBUGINFO": os.environ.get("NUMBA_CUDA_DEBUGINFO"),
        },
        "windows_tdr": _read_windows_tdr_settings(),
    }


def _parse_stage_names(raw: str) -> list[str]:
    value = raw.strip().lower()
    if value in {"all", "*"}:
        return list(STAGE_ORDER)
    names = [part.strip() for part in raw.split(",") if part.strip()]
    if not names:
        raise ValueError("No stages were provided.")
    unknown = [name for name in names if name not in STAGE_ORDER]
    if unknown:
        raise ValueError(f"Unknown stages: {unknown}. Valid: {list(STAGE_ORDER)}")
    return names


def _build_kernel_inputs(config: LadderConfig) -> dict[str, np.ndarray]:
    rows = max(8, int(config.rows))
    cols = max(8, int(config.cols))
    observer_count = max(1, int(config.observer_count))
    direction_count = max(8, int(config.direction_count))
    observer_row = float(config.observer_row)
    observer_col_start = float(config.observer_col_start)
    observer_height = float(config.observer_height)
    if observer_col_start + observer_count + 2 >= cols:
        observer_col_start = float(max(1, cols - observer_count - 3))
    if observer_row < 0 or observer_row >= rows:
        observer_row = 1.0

    dem = np.zeros((rows, cols), dtype=np.float32, order="C")
    observers = np.zeros((observer_count, 3), dtype=np.float32, order="C")
    for idx in range(observer_count):
        observers[idx, 0] = float(observer_row)
        observers[idx, 1] = float(observer_col_start + idx)
        observers[idx, 2] = float(observer_height)

    theta = (2.0 * math.pi * np.arange(direction_count, dtype=np.float32)) / float(direction_count)
    directions = np.stack((np.cos(theta), np.sin(theta)), axis=1).astype(np.float32, copy=False)

    ray_obs_indices = np.repeat(np.arange(observer_count, dtype=np.int32), direction_count)
    ray_dir_indices = np.tile(np.arange(direction_count, dtype=np.int32), observer_count)
    out_i32 = np.zeros((ray_obs_indices.size,), dtype=np.int32, order="C")
    out_f32 = np.zeros((ray_obs_indices.size,), dtype=np.float32, order="C")

    return {
        "dem": dem,
        "observers": observers,
        "directions": directions,
        "ray_obs_indices": ray_obs_indices,
        "ray_dir_indices": ray_dir_indices,
        "out_i32": out_i32,
        "out_f32": out_f32,
    }


def _collect_cuda_metadata(numba_mod: Any, cuda_mod: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "numba_version": getattr(numba_mod, "__version__", "unknown"),
    }
    try:
        metadata["cuda_available"] = bool(cuda_mod.is_available())
    except Exception as exc:
        metadata["cuda_available"] = False
        metadata["cuda_available_error"] = str(exc)
        return metadata
    if not metadata["cuda_available"]:
        return metadata
    try:
        context = cuda_mod.current_context()
        device = context.device
        name = device.name
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        metadata["device_name"] = str(name)
        compute_capability = getattr(device, "compute_capability", None)
        if compute_capability is not None:
            metadata["compute_capability"] = list(compute_capability)
        try:
            free_mem, total_mem = context.get_memory_info()
            metadata["memory_free_bytes"] = int(free_mem)
            metadata["memory_total_bytes"] = int(total_mem)
        except Exception as exc:
            metadata["memory_info_error"] = str(exc)
    except Exception as exc:
        metadata["device_error"] = str(exc)
    try:
        runtime_version = cuda_mod.runtime.get_version()
        metadata["cuda_runtime_version"] = list(runtime_version) if isinstance(runtime_version, tuple) else runtime_version
    except Exception as exc:
        metadata["cuda_runtime_version_error"] = str(exc)
    return metadata


def _run_stage_once(stage: str, config: LadderConfig) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {
        "stage": stage,
        "ok": False,
        "config": asdict(config),
        "host_metadata": _collect_host_metadata(),
    }
    try:
        import numba  # type: ignore
        from numba import cuda  # type: ignore
    except Exception as exc:
        result["error"] = f"numba import failed: {exc}"
        result["traceback"] = traceback.format_exc()
        result["elapsed_seconds"] = round(time.monotonic() - started, 6)
        return result

    result["cuda_metadata"] = _collect_cuda_metadata(numba, cuda)
    if not bool(result["cuda_metadata"].get("cuda_available", False)):
        result["error"] = "CUDA is not available in this Python process."
        result["elapsed_seconds"] = round(time.monotonic() - started, 6)
        return result

    inputs = _build_kernel_inputs(config)
    rays = int(inputs["ray_obs_indices"].size)
    threads = max(32, int(config.threads_per_block))
    blocks = max(1, int(math.ceil(float(rays) / float(threads))))
    result["kernel_launch"] = {
        "rays": rays,
        "threads_per_block": threads,
        "grid_blocks": blocks,
    }

    try:
        d_dem = cuda.to_device(inputs["dem"])
        d_obs = cuda.to_device(inputs["observers"])
        d_dirs = cuda.to_device(inputs["directions"])
        d_ray_obs = cuda.to_device(inputs["ray_obs_indices"])
        d_ray_dir = cuda.to_device(inputs["ray_dir_indices"])
        d_out_i32 = cuda.to_device(inputs["out_i32"])
        d_out_f32 = cuda.to_device(inputs["out_f32"])

        if stage == "k0_noop":

            @cuda.jit
            def kernel_noop(dem_arr, obs_arr, dirs_arr, ray_obs, ray_dir, out_arr):
                idx = cuda.grid(1)
                if idx < ray_obs.shape[0]:
                    return

            kernel_noop[blocks, threads](d_dem, d_obs, d_dirs, d_ray_obs, d_ray_dir, d_out_i32)
            cuda.synchronize()
            sample = d_out_i32.copy_to_host()[:8].tolist()
        elif stage == "k1_index_write":

            @cuda.jit
            def kernel_index_write(ray_obs, ray_dir, out_arr):
                idx = cuda.grid(1)
                if idx >= out_arr.shape[0]:
                    return
                out_arr[idx] = int(ray_obs[idx]) + int(ray_dir[idx])

            kernel_index_write[blocks, threads](d_ray_obs, d_ray_dir, d_out_i32)
            cuda.synchronize()
            sample = d_out_i32.copy_to_host()[:8].tolist()
        elif stage == "k2_fixed_loop":
            loop_count = max(1, int(config.loop_iterations))

            @cuda.jit
            def kernel_fixed_loop(out_arr, loop_iters):
                idx = cuda.grid(1)
                if idx >= out_arr.shape[0]:
                    return
                acc = 0
                for j in range(loop_iters):
                    acc += (j & 1)
                out_arr[idx] = acc

            kernel_fixed_loop[blocks, threads](d_out_i32, int(loop_count))
            cuda.synchronize()
            sample = d_out_i32.copy_to_host()[:8].tolist()
        elif stage == "k3_one_sample_read":

            @cuda.jit
            def kernel_one_sample_read(dem_arr, obs_arr, ray_obs, out_arr):
                idx = cuda.grid(1)
                if idx >= ray_obs.shape[0]:
                    return
                obs_idx = int(ray_obs[idx])
                rr = int(obs_arr[obs_idx, 0])
                cc = int(obs_arr[obs_idx, 1])
                if rr < 0 or rr >= dem_arr.shape[0] or cc < 0 or cc >= dem_arr.shape[1]:
                    out_arr[idx] = -1
                    return
                out_arr[idx] = int(dem_arr[rr, cc])

            kernel_one_sample_read[blocks, threads](d_dem, d_obs, d_ray_obs, d_out_i32)
            cuda.synchronize()
            sample = d_out_i32.copy_to_host()[:8].tolist()
        elif stage == "k4_branching_short_loop":
            max_steps_short = min(max(1, int(config.max_steps)), 128)
            step_pixels = float(config.step_size_pixels)

            @cuda.jit
            def kernel_branching_short(obs_arr, dirs_arr, ray_obs, ray_dir, out_arr, rows, cols, step_px, max_steps_local):
                idx = cuda.grid(1)
                if idx >= ray_obs.shape[0]:
                    return
                obs_idx = int(ray_obs[idx])
                dir_idx = int(ray_dir[idx])
                orow = obs_arr[obs_idx, 0]
                ocol = obs_arr[obs_idx, 1]
                dx = dirs_arr[dir_idx, 0]
                dy = dirs_arr[dir_idx, 1]
                ox = ocol + 0.5
                oy = orow + 0.5
                hits = 0
                for step_idx in range(1, max_steps_local + 1):
                    x = ox + dx * (float(step_idx) * step_px)
                    y = oy + dy * (float(step_idx) * step_px)
                    if y < 0.0 or y >= float(rows) or x < 0.0 or x >= float(cols):
                        break
                    rr = int(y)
                    cc = int(x)
                    if rr >= 0 and rr < rows and cc >= 0 and cc < cols:
                        hits += 1
                out_arr[idx] = hits

            kernel_branching_short[blocks, threads](
                d_obs,
                d_dirs,
                d_ray_obs,
                d_ray_dir,
                d_out_i32,
                int(config.rows),
                int(config.cols),
                float(step_pixels),
                int(max_steps_short),
            )
            cuda.synchronize()
            sample = d_out_i32.copy_to_host()[:8].tolist()
        elif stage == "k5_long_dry_loop":
            max_steps = max(1, int(config.max_steps))
            step_pixels = float(config.step_size_pixels)

            @cuda.jit
            def kernel_long_dry(obs_arr, dirs_arr, ray_obs, ray_dir, out_arr, rows, cols, step_px, max_steps_local):
                idx = cuda.grid(1)
                if idx >= ray_obs.shape[0]:
                    return
                obs_idx = int(ray_obs[idx])
                dir_idx = int(ray_dir[idx])
                orow = obs_arr[obs_idx, 0]
                ocol = obs_arr[obs_idx, 1]
                dx = dirs_arr[dir_idx, 0]
                dy = dirs_arr[dir_idx, 1]
                ox = ocol + 0.5
                oy = orow + 0.5
                final_v = 0
                for step_idx in range(1, max_steps_local + 1):
                    x = ox + dx * (float(step_idx) * step_px)
                    y = oy + dy * (float(step_idx) * step_px)
                    if y < 0.0 or y >= float(rows) or x < 0.0 or x >= float(cols):
                        break
                    rr = int(y)
                    cc = int(x)
                    final_v = rr + cc
                out_arr[idx] = final_v

            kernel_long_dry[blocks, threads](
                d_obs,
                d_dirs,
                d_ray_obs,
                d_ray_dir,
                d_out_i32,
                int(config.rows),
                int(config.cols),
                float(step_pixels),
                int(max_steps),
            )
            cuda.synchronize()
            sample = d_out_i32.copy_to_host()[:8].tolist()
        elif stage == "k6_los_math":
            max_steps = max(1, int(config.max_steps))
            step_pixels = float(config.step_size_pixels)
            pixel_size = 5.0
            target_h = 4.0

            @cuda.jit
            def kernel_los_math(
                dem_arr,
                obs_arr,
                dirs_arr,
                ray_obs,
                ray_dir,
                out_arr,
                rows,
                cols,
                step_px,
                max_steps_local,
                pixel_size_local,
                target_h_local,
            ):
                idx = cuda.grid(1)
                if idx >= ray_obs.shape[0]:
                    return
                obs_idx = int(ray_obs[idx])
                dir_idx = int(ray_dir[idx])
                orow_f = obs_arr[obs_idx, 0]
                ocol_f = obs_arr[obs_idx, 1]
                oheight = obs_arr[obs_idx, 2]
                orow = int(orow_f)
                ocol = int(ocol_f)
                if orow < 0 or orow >= rows or ocol < 0 or ocol >= cols:
                    out_arr[idx] = -1.0
                    return
                obs_base = dem_arr[orow, ocol] + oheight
                dx = dirs_arr[dir_idx, 0]
                dy = dirs_arr[dir_idx, 1]
                ox = ocol_f + 0.5
                oy = orow_f + 0.5
                max_slope = -1.0e30
                for step_idx in range(1, max_steps_local + 1):
                    x = ox + dx * (float(step_idx) * step_px)
                    y = oy + dy * (float(step_idx) * step_px)
                    if y < 0.0 or y >= float(rows) or x < 0.0 or x >= float(cols):
                        break
                    rr = int(y)
                    cc = int(x)
                    dist = (float(step_idx) * step_px) * pixel_size_local
                    if dist <= 0.0:
                        continue
                    sample = dem_arr[rr, cc]
                    slope_tgt = (sample + target_h_local - obs_base) / dist
                    if slope_tgt > max_slope:
                        max_slope = slope_tgt
                out_arr[idx] = max_slope

            kernel_los_math[blocks, threads](
                d_dem,
                d_obs,
                d_dirs,
                d_ray_obs,
                d_ray_dir,
                d_out_f32,
                int(config.rows),
                int(config.cols),
                float(step_pixels),
                int(max_steps),
                float(pixel_size),
                float(target_h),
            )
            cuda.synchronize()
            sample = [float(v) for v in d_out_f32.copy_to_host()[:8]]
        elif stage == "k7_handler_mirror":
            max_steps = max(1, int(config.max_steps))
            step_pixels = float(config.step_size_pixels)
            pixel_size = 5.0
            target_h = 4.0
            max_range_m = 0.0
            curvature_mode = 0

            out_u8 = np.zeros((int(config.rows), int(config.cols)), dtype=np.uint8, order="C")
            d_out_u8 = cuda.to_device(out_u8)
            d_max_slope = cuda.to_device(np.full((rays,), -1.0e30, dtype=np.float32))
            d_last_rr = cuda.to_device(np.full((rays,), -1, dtype=np.int32))
            d_last_cc = cuda.to_device(np.full((rays,), -1, dtype=np.int32))
            d_active = cuda.to_device(np.ones((rays,), dtype=np.uint8))

            @cuda.jit
            def kernel_handler_mirror(
                dem_arr,
                observers,
                directions,
                ray_obs_indices,
                ray_dir_indices,
                out_arr,
                max_slope_state,
                last_rr_state,
                last_cc_state,
                active_state,
                pixel_size_local,
                max_range_local,
                target_h_local,
                step_pixels_local,
                step_start,
                step_end,
                curvature_mode_local,
            ):
                rows = dem_arr.shape[0]
                cols = dem_arr.shape[1]
                ray_index = cuda.grid(1)
                total_rays_local = ray_obs_indices.shape[0]
                if ray_index >= total_rays_local:
                    return
                if active_state[ray_index] == 0:
                    return
                radius = 1737400.0
                obs_idx = int(ray_obs_indices[ray_index])
                dir_idx = int(ray_dir_indices[ray_index])

                orow_f = observers[obs_idx, 0]
                ocol_f = observers[obs_idx, 1]
                oheight = observers[obs_idx, 2]
                orow = int(orow_f)
                ocol = int(ocol_f)
                if orow < 0 or orow >= rows or ocol < 0 or ocol >= cols:
                    active_state[ray_index] = 0
                    return
                obs_base = dem_arr[orow, ocol] + oheight
                dx_dir = directions[dir_idx, 0]
                dy_dir = directions[dir_idx, 1]
                ox = ocol_f + 0.5
                oy = orow_f + 0.5
                max_slope = max_slope_state[ray_index]
                last_rr = int(last_rr_state[ray_index])
                last_cc = int(last_cc_state[ray_index])

                for step_idx in range(step_start, step_end + 1):
                    current_x = ox + dx_dir * (float(step_idx) * step_pixels_local)
                    current_y = oy + dy_dir * (float(step_idx) * step_pixels_local)
                    stop_ray = False
                    if (not math.isfinite(current_x)) or (not math.isfinite(current_y)):
                        stop_ray = True
                    elif current_y < 0.0 or current_y >= float(rows) or current_x < 0.0 or current_x >= float(cols):
                        stop_ray = True
                    else:
                        rr = int(current_y)
                        cc = int(current_x)
                        if rr == last_rr and cc == last_cc:
                            if stop_ray:
                                break
                            continue
                        last_rr = rr
                        last_cc = cc
                        dist = (float(step_idx) * step_pixels_local) * pixel_size_local
                        if dist > 0.0:
                            if max_range_local > 0.0 and dist > max_range_local:
                                stop_ray = True
                            else:
                                drop = (dist * dist) / (2.0 * radius) if curvature_mode_local == 1 else 0.0
                                sample = dem_arr[rr, cc]
                                slope_occ = (sample - obs_base - drop) / dist
                                slope_tgt = (sample + target_h_local - obs_base - drop) / dist
                                if slope_tgt + 1.0e-8 >= max_slope:
                                    out_arr[rr, cc] = 1
                                if slope_occ > max_slope:
                                    max_slope = slope_occ
                    if stop_ray:
                        active_state[ray_index] = 0
                        break
                max_slope_state[ray_index] = max_slope
                last_rr_state[ray_index] = last_rr
                last_cc_state[ray_index] = last_cc

            step_chunk_size = 512
            for step_start in range(1, int(max_steps) + 1, int(step_chunk_size)):
                step_end = min(int(max_steps), int(step_start + step_chunk_size - 1))
                kernel_handler_mirror[blocks, threads](
                    d_dem,
                    d_obs,
                    d_dirs,
                    d_ray_obs,
                    d_ray_dir,
                    d_out_u8,
                    d_max_slope,
                    d_last_rr,
                    d_last_cc,
                    d_active,
                    float(pixel_size),
                    float(max_range_m),
                    float(target_h),
                    float(step_pixels),
                    int(step_start),
                    int(step_end),
                    int(curvature_mode),
                )
                cuda.synchronize()
            out_host = d_out_u8.copy_to_host()
            sample = [int(np.count_nonzero(out_host))]
        else:
            raise ValueError(f"Unknown stage: {stage}")

        result["ok"] = True
        result["sample_output"] = sample
    except Exception as exc:
        result["error"] = str(exc)
        result["traceback"] = traceback.format_exc()
    finally:
        try:
            cuda.close()
        except Exception:
            pass

    result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return result


def _config_from_args(args: argparse.Namespace) -> LadderConfig:
    return LadderConfig(
        rows=int(args.rows),
        cols=int(args.cols),
        observer_count=int(args.observer_count),
        observer_row=float(args.observer_row),
        observer_col_start=float(args.observer_col_start),
        observer_height=float(args.observer_height),
        direction_count=int(args.direction_count),
        step_size_pixels=float(args.step_size_pixels),
        max_steps=int(args.max_steps),
        loop_iterations=int(args.loop_iterations),
        threads_per_block=int(args.threads_per_block),
    )


def _child_cmd_for_stage(stage: str, config: LadderConfig) -> list[str]:
    return [
        sys.executable,
        "-m",
        "backend.tools.cuda_viewshed_diagnostics",
        "--mode",
        "stage",
        "--stage",
        stage,
        "--rows",
        str(config.rows),
        "--cols",
        str(config.cols),
        "--observer-count",
        str(config.observer_count),
        "--observer-row",
        str(config.observer_row),
        "--observer-col-start",
        str(config.observer_col_start),
        "--observer-height",
        str(config.observer_height),
        "--direction-count",
        str(config.direction_count),
        "--step-size-pixels",
        str(config.step_size_pixels),
        "--max-steps",
        str(config.max_steps),
        "--loop-iterations",
        str(config.loop_iterations),
        "--threads-per-block",
        str(config.threads_per_block),
    ]


def _extract_json_from_stdout(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    text = stdout.strip()
    if not text:
        return None, "empty_stdout"
    try:
        return json.loads(text), None
    except Exception:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                return json.loads(line), None
            except Exception:
                continue
    return None, "json_parse_failed"


def _run_driver(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    stages = _parse_stage_names(str(args.stages))
    timeout_s = max(5, int(args.stage_timeout_sec))
    report: dict[str, Any] = {
        "mode": "driver",
        "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
        "config": asdict(config),
        "stages_requested": stages,
        "host_metadata": _collect_host_metadata(),
        "subprocess_python": sys.executable,
        "stage_timeout_sec": timeout_s,
        "cuda_launch_blocking": bool(args.cuda_launch_blocking),
        "results": [],
    }
    any_failure = False
    for stage in stages:
        cmd = _child_cmd_for_stage(stage, config)
        env = dict(os.environ)
        if bool(args.cuda_launch_blocking):
            env["CUDA_LAUNCH_BLOCKING"] = "1"
        stage_started = time.monotonic()
        entry: dict[str, Any] = {
            "stage": stage,
            "command": cmd,
        }
        try:
            completed = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            stage_json, parse_error = _extract_json_from_stdout(completed.stdout)
            entry.update(
                {
                    "returncode": int(completed.returncode),
                    "elapsed_seconds": round(time.monotonic() - stage_started, 6),
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                    "stage_json": stage_json,
                    "stdout_parse_error": parse_error,
                }
            )
            stage_ok = (
                completed.returncode == 0
                and isinstance(stage_json, dict)
                and bool(stage_json.get("ok", False))
            )
            entry["ok"] = bool(stage_ok)
            if not stage_ok:
                any_failure = True
        except subprocess.TimeoutExpired as exc:
            entry.update(
                {
                    "ok": False,
                    "returncode": None,
                    "elapsed_seconds": round(time.monotonic() - stage_started, 6),
                    "timeout": timeout_s,
                    "stdout": exc.stdout,
                    "stderr": exc.stderr,
                    "error": f"stage timeout after {timeout_s}s",
                }
            )
            any_failure = True
        report["results"].append(entry)
        if any_failure and bool(args.stop_on_failure):
            break

    report["ok"] = not any_failure
    first_failure = next((r["stage"] for r in report["results"] if not bool(r.get("ok", False))), None)
    report["summary"] = {
        "first_failure_stage": first_failure,
        "stages_completed": len(report["results"]),
        "stages_requested": len(stages),
    }
    payload = json.dumps(report, indent=2)
    if args.output_json:
        path = Path(args.output_json).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if not any_failure else 1


def _run_stage_mode(args: argparse.Namespace) -> int:
    if not args.stage:
        raise ValueError("--stage is required in stage mode.")
    config = _config_from_args(args)
    result = _run_stage_once(str(args.stage), config)
    payload = json.dumps(result, indent=2)
    if args.output_json:
        path = Path(args.output_json).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if bool(result.get("ok", False)) else 2


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone CUDA viewshed kernel diagnostics ladder. "
            "Runs outside app/UI and can execute each stage in a fresh subprocess."
        )
    )
    parser.add_argument("--mode", choices=("driver", "stage"), default="driver")
    parser.add_argument("--stage", choices=STAGE_ORDER, default=None)
    parser.add_argument("--stages", default="all", help="Comma-separated list of stages or 'all'.")
    parser.add_argument("--output-json", default=None, help="Optional output report path.")
    parser.add_argument("--stage-timeout-sec", type=int, default=120)
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument(
        "--cuda-launch-blocking",
        dest="cuda_launch_blocking",
        action="store_true",
        default=True,
        help="Set CUDA_LAUNCH_BLOCKING=1 for child stage runs (driver mode).",
    )
    parser.add_argument(
        "--no-cuda-launch-blocking",
        dest="cuda_launch_blocking",
        action="store_false",
        help="Do not set CUDA_LAUNCH_BLOCKING in child stage runs.",
    )
    parser.add_argument("--rows", type=int, default=2048)
    parser.add_argument("--cols", type=int, default=2048)
    parser.add_argument("--observer-count", type=int, default=2)
    parser.add_argument("--observer-row", type=float, default=1.0)
    parser.add_argument("--observer-col-start", type=float, default=1649.0)
    parser.add_argument("--observer-height", type=float, default=1.0)
    parser.add_argument("--direction-count", type=int, default=360)
    parser.add_argument("--step-size-pixels", type=float, default=0.5)
    parser.add_argument("--max-steps", type=int, default=11586)
    parser.add_argument("--loop-iterations", type=int, default=1000)
    parser.add_argument("--threads-per-block", type=int, default=128)
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    if args.mode == "driver":
        return _run_driver(args)
    return _run_stage_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
