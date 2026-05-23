from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from backend.core.crs_semantics import crs_semantically_equivalent
from backend.worker.gdal_runtime import import_rasterio


MAP_DISPLAY_ROLE = "display_map"
_DISPLAY_WARP_POLICY_VERSION = "3"
_DISPLAY_ALPHA_BAND_TAG = "LUNAR_DISPLAY_ALPHA_BAND"
_DISPLAY_DATA_BAND_COUNT_TAG = "LUNAR_DISPLAY_DATA_BAND_COUNT"
_DERIVATIVE_LOCKS: dict[str, threading.Lock] = {}
_DERIVATIVE_LOCKS_GUARD = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


@contextmanager
def _connect_sqlite(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def _ensure_derivative_schema(db_path: Path) -> None:
    with _connect_sqlite(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS map_display_derivative (
                derivative_id TEXT PRIMARY KEY,
                scenario_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                role TEXT NOT NULL,
                source_product_id TEXT NOT NULL,
                source_file_id TEXT NOT NULL,
                source_path TEXT NOT NULL,
                derivative_path TEXT NOT NULL,
                target_crs TEXT NOT NULL,
                warp_params_hash TEXT NOT NULL,
                created_at_utc TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _build_warp_hash(
    *,
    source_path: Path,
    source_crs: Any,
    target_crs: Any,
    src_nodata: float | int | None,
    dst_nodata: float | int | None,
    resampling: str,
) -> str:
    stat = source_path.stat()
    payload = "|".join(
        [
            _DISPLAY_WARP_POLICY_VERSION,
            str(source_path.resolve()),
            str(stat.st_size),
            str(stat.st_mtime_ns),
            source_crs.to_wkt(),
            target_crs.to_wkt(),
            str(src_nodata),
            str(dst_nodata),
            resampling,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _derivative_path(
    *,
    scenario_root_dir: Path,
    product_id: str,
    source_stem: str,
    warp_hash: str,
) -> Path:
    return (
        scenario_root_dir
        / "display"
        / product_id
        / "esri_103878"
        / f"{source_stem}.{warp_hash}.cog.tif"
    )


def _register_derivative(
    *,
    scenario_root_dir: Path,
    scenario_id: str,
    product_id: str,
    source_file_id: str,
    source_path: Path,
    derivative_path: Path,
    target_crs: str,
    warp_params_hash: str,
) -> None:
    scenario_root_dir.mkdir(parents=True, exist_ok=True)
    db_path = scenario_root_dir / "scenario.db"
    _ensure_derivative_schema(db_path)

    with _connect_sqlite(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO map_display_derivative (
                derivative_id,
                scenario_id,
                product_id,
                role,
                source_product_id,
                source_file_id,
                source_path,
                derivative_path,
                target_crs,
                warp_params_hash,
                created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{product_id}:{warp_params_hash}",
                scenario_id,
                product_id,
                MAP_DISPLAY_ROLE,
                product_id,
                source_file_id,
                str(source_path.resolve()),
                str(derivative_path.resolve()),
                target_crs,
                warp_params_hash,
                _utc_now(),
            ),
        )
        conn.commit()


def _lock_for_path(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _DERIVATIVE_LOCKS_GUARD:
        lock = _DERIVATIVE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _DERIVATIVE_LOCKS[key] = lock
    return lock


def _is_valid_raster(path: Path) -> bool:
    rasterio = import_rasterio()
    try:
        with rasterio.open(path) as ds:
            return ds.width > 0 and ds.height > 0 and ds.count > 0
    except Exception:
        return False


def _is_map_display_optimized(path: Path) -> bool:
    rasterio = import_rasterio()
    try:
        with rasterio.open(path) as ds:
            if ds.width <= 0 or ds.height <= 0 or ds.count <= 0:
                return False
            if not bool(getattr(ds, "is_tiled", False)):
                return False
            # Very small rasters do not need overview pyramids.
            needs_overviews = ds.width >= 512 and ds.height >= 512
            if needs_overviews and len(ds.overviews(1)) == 0:
                return False
            return True
    except Exception:
        return False


def _resolve_resampling(resampling: Any) -> Any:
    rasterio = import_rasterio()
    enum_cls = rasterio.enums.Resampling
    if isinstance(resampling, enum_cls):
        return resampling
    if isinstance(resampling, str):
        candidate = getattr(enum_cls, resampling.lower(), None)
        if candidate is not None:
            return candidate
    return enum_cls.nearest


def _overview_factors(width: int, height: int) -> list[int]:
    factors: list[int] = []
    factor = 2
    # Keep adding 2x pyramid levels while the reduced raster remains reasonably sized.
    while (width // factor) >= 256 and (height // factor) >= 256:
        factors.append(factor)
        factor *= 2
    return factors


def _densified_boundary_points(
    left: float,
    bottom: float,
    right: float,
    top: float,
    *,
    samples_per_edge: int = 17,
) -> list[tuple[float, float]]:
    edge_samples = max(2, samples_per_edge)

    def interpolate(
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        for index in range(edge_samples - 1):
            fraction = index / (edge_samples - 1)
            x = start[0] + ((end[0] - start[0]) * fraction)
            y = start[1] + ((end[1] - start[1]) * fraction)
            points.append((x, y))
        return points

    upper_left = (left, top)
    upper_right = (right, top)
    lower_right = (right, bottom)
    lower_left = (left, bottom)
    return [
        *interpolate(upper_left, upper_right),
        *interpolate(upper_right, lower_right),
        *interpolate(lower_right, lower_left),
        *interpolate(lower_left, upper_left),
    ]


def _polygon_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    for index, (x0, y0) in enumerate(points):
        x1, y1 = points[(index + 1) % len(points)]
        area += (x0 * y1) - (x1 * y0)
    return abs(area) * 0.5


def _output_bounds(transform: Any, width: int, height: int) -> tuple[float, float, float, float]:
    corners = [
        transform * (0, 0),
        transform * (width, 0),
        transform * (width, height),
        transform * (0, height),
    ]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def _warp_validity_mask(
    *,
    src: Any,
    width: int,
    height: int,
    transform: Any,
    target_crs: Any,
) -> np.ndarray:
    rasterio = import_rasterio()
    reproject = rasterio.warp.reproject
    source_mask = np.asarray(src.dataset_mask(), dtype=np.uint8)
    destination = np.zeros((int(height), int(width)), dtype=np.uint8)
    reproject(
        source=source_mask,
        destination=destination,
        src_transform=src.transform,
        src_crs=src.crs,
        src_nodata=0,
        dst_transform=transform,
        dst_crs=target_crs,
        dst_nodata=0,
        resampling=rasterio.enums.Resampling.nearest,
    )
    destination[destination > 0] = np.uint8(255)
    return destination


def _set_display_colorinterp(dst: Any, *, source_band_count: int, alpha_band_index: int | None) -> None:
    rasterio = import_rasterio()
    colorinterp = list(getattr(dst, "colorinterp", ()) or ())
    if len(colorinterp) < source_band_count:
        colorinterp.extend(
            [rasterio.enums.ColorInterp.undefined] * (source_band_count - len(colorinterp))
        )
    if source_band_count == 1 and colorinterp:
        colorinterp[0] = rasterio.enums.ColorInterp.gray
    if alpha_band_index is not None:
        required = max(len(colorinterp), alpha_band_index)
        if len(colorinterp) < required:
            colorinterp.extend(
                [rasterio.enums.ColorInterp.undefined] * (required - len(colorinterp))
            )
        colorinterp[alpha_band_index - 1] = rasterio.enums.ColorInterp.alpha
    if len(colorinterp) == int(dst.count):
        dst.colorinterp = tuple(colorinterp)


def _write_display_copy(
    *,
    src: Any,
    destination_path: Path,
) -> None:
    rasterio = import_rasterio()
    profile = src.profile.copy()
    profile.update(
        {
            "driver": "GTiff",
            "tiled": True,
            "compress": "deflate",
            "predictor": 2,
            "blockxsize": 256,
            "blockysize": 256,
            "bigtiff": "IF_SAFER",
        }
    )
    with rasterio.open(destination_path, "w", **profile) as dst:
        for band in range(1, src.count + 1):
            dst.write(src.read(band), band)
            dst.update_tags(band, **src.tags(band))
        dst.update_tags(**src.tags())
        _set_display_colorinterp(
            dst,
            source_band_count=int(src.count),
            alpha_band_index=None,
        )
    with rasterio.open(destination_path, "r+") as dst:
        factors = _overview_factors(dst.width, dst.height)
        if factors:
            dst.build_overviews(factors, rasterio.enums.Resampling.nearest)
            dst.update_tags(ns="rio_overview", resampling="nearest")


def ensure_map_display_raster(
    *,
    source_path: Path,
    scenario_root_dir: Path,
    scenario_id: str,
    kind: str,
    product_id: str,
    source_file_id: str,
    target_crs_wkt: str,
    target_crs_label: str,
    resampling: Any = "nearest",
) -> Path:
    rasterio = import_rasterio()
    resolved_resampling = _resolve_resampling(resampling)
    calculate_default_transform = rasterio.warp.calculate_default_transform
    reproject = rasterio.warp.reproject

    _ = kind  # kept for API compatibility; storage no longer keys by kind directory
    source_resolved = source_path.expanduser().resolve()
    if not source_resolved.exists() or not source_resolved.is_file():
        raise FileNotFoundError(f"Source raster not found: {source_resolved}")

    target_crs = rasterio.crs.CRS.from_wkt(target_crs_wkt)
    with rasterio.open(source_resolved) as src:
        if src.crs is None:
            raise RuntimeError(f"Source raster has no CRS: {source_resolved}")

        src_nodata = src.nodata
        dst_nodata = src_nodata
        warp_hash = _build_warp_hash(
            source_path=source_resolved,
            source_crs=src.crs,
            target_crs=target_crs,
            src_nodata=src_nodata,
            dst_nodata=dst_nodata,
            resampling=resolved_resampling.name,
        )
        out_path = _derivative_path(
            scenario_root_dir=scenario_root_dir.expanduser().resolve(),
            product_id=product_id,
            source_stem=source_resolved.stem,
            warp_hash=warp_hash,
        )

        if crs_semantically_equivalent(src.crs, target_crs):
            if _is_map_display_optimized(source_resolved):
                _register_derivative(
                    scenario_root_dir=scenario_root_dir,
                    scenario_id=scenario_id,
                    product_id=product_id,
                    source_file_id=source_file_id,
                    source_path=source_resolved,
                    derivative_path=source_resolved,
                    target_crs=target_crs_label,
                    warp_params_hash=warp_hash,
                )
                return source_resolved

            lock = _lock_for_path(out_path)
            with lock:
                if out_path.exists() and _is_valid_raster(out_path):
                    _register_derivative(
                        scenario_root_dir=scenario_root_dir,
                        scenario_id=scenario_id,
                        product_id=product_id,
                        source_file_id=source_file_id,
                        source_path=source_resolved,
                        derivative_path=out_path,
                        target_crs=target_crs_label,
                        warp_params_hash=warp_hash,
                    )
                    return out_path
                out_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    _write_display_copy(src=src, destination_path=out_path)
                except Exception:
                    try:
                        if out_path.exists():
                            out_path.unlink()
                    except OSError:
                        pass
                    raise

            _register_derivative(
                scenario_root_dir=scenario_root_dir,
                scenario_id=scenario_id,
                product_id=product_id,
                source_file_id=source_file_id,
                source_path=source_resolved,
                derivative_path=out_path,
                target_crs=target_crs_label,
                warp_params_hash=warp_hash,
            )
            return out_path

        lock = _lock_for_path(out_path)
        with lock:
            if out_path.exists() and _is_valid_raster(out_path):
                _register_derivative(
                    scenario_root_dir=scenario_root_dir,
                    scenario_id=scenario_id,
                    product_id=product_id,
                    source_file_id=source_file_id,
                    source_path=source_resolved,
                    derivative_path=out_path,
                    target_crs=target_crs_label,
                    warp_params_hash=warp_hash,
                )
                return out_path

            out_path.parent.mkdir(parents=True, exist_ok=True)
            transform, width, height = calculate_default_transform(
                src.crs,
                target_crs,
                src.width,
                src.height,
                *src.bounds,
            )
            validity_mask = _warp_validity_mask(
                src=src,
                width=width,
                height=height,
                transform=transform,
                target_crs=target_crs,
            )
            has_invalid_pixels = bool((validity_mask != 255).any())
            alpha_band_index = (int(src.count) + 1) if has_invalid_pixels else None
            profile = src.profile.copy()
            profile.update(
                {
                    "driver": "GTiff",
                    "crs": target_crs,
                    "transform": transform,
                    "width": width,
                    "height": height,
                    "tiled": True,
                    "compress": "deflate",
                    "predictor": 2,
                    "blockxsize": 256,
                    "blockysize": 256,
                    "bigtiff": "IF_SAFER",
                    "count": int(src.count) + (1 if alpha_band_index is not None else 0),
                    "nodata": None,
                }
            )
            try:
                with rasterio.open(out_path, "w", **profile) as dst:
                    for band in range(1, src.count + 1):
                        reproject(
                            source=rasterio.band(src, band),
                            destination=rasterio.band(dst, band),
                            src_transform=src.transform,
                            src_crs=src.crs,
                            src_nodata=src_nodata,
                            dst_transform=transform,
                            dst_crs=target_crs,
                            dst_nodata=0 if alpha_band_index is not None else dst_nodata,
                            resampling=resolved_resampling,
                        )
                    if alpha_band_index is not None:
                        dst.write(validity_mask, alpha_band_index)
                        dst.update_tags(
                            **{
                                _DISPLAY_ALPHA_BAND_TAG: str(alpha_band_index),
                                _DISPLAY_DATA_BAND_COUNT_TAG: str(int(src.count)),
                            }
                        )
                    _set_display_colorinterp(
                        dst,
                        source_band_count=int(src.count),
                        alpha_band_index=alpha_band_index,
                    )
                with rasterio.open(out_path, "r+") as dst:
                    factors = _overview_factors(dst.width, dst.height)
                    if factors:
                        dst.build_overviews(factors, rasterio.enums.Resampling.nearest)
                        dst.update_tags(ns="rio_overview", resampling="nearest")
            except Exception:
                try:
                    if out_path.exists():
                        out_path.unlink()
                except OSError:
                    pass
                raise

    _register_derivative(
        scenario_root_dir=scenario_root_dir,
        scenario_id=scenario_id,
        product_id=product_id,
        source_file_id=source_file_id,
        source_path=source_resolved,
        derivative_path=out_path,
        target_crs=target_crs_label,
        warp_params_hash=warp_hash,
    )
    return out_path
