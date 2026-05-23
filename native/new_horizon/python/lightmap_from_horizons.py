from __future__ import annotations

import datetime as dt
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numba as nb
import numpy as np
import spiceypy as spice
from osgeo import gdal, osr


PATCH_SIZE = 128
HORIZON_SAMPLES = 1440
MOON_RADIUS_M = 1_737_400.0
LONG_LAT_PROJ4 = "+proj=longlat +R=1737400 +no_defs"

_HORIZON_RE = re.compile(r"^horizon_(\d{5})_(\d{5})_(\d+)\.bin$")
_SUN_IMAGE_RE = re.compile(r"sun_image_(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})")
_SPICE_LOADED = False


@dataclass(frozen=True)
class HorizonTile:
    path: Path
    row: int
    col: int
    observer_elevation_m: float


def _build_half_circle() -> np.ndarray:
    half_circle_ticks = 8
    sun_half_angle_deg = 0.54 / 2.0
    values = []
    for i in range(half_circle_ticks * 2):
        t = half_circle_ticks - 0.5 - i
        values.append(math.sqrt(64.0 - t * t) / half_circle_ticks * sun_half_angle_deg)
    return np.asarray(values, dtype=np.float32)


HALF_CIRCLE = _build_half_circle()
MAX_PHOTONS = np.float32(2.0 * HALF_CIRCLE.sum())


def _parse_horizon_tile(path: Path) -> HorizonTile | None:
    m = _HORIZON_RE.match(path.name)
    if not m:
        return None
    row = int(m.group(1))
    col = int(m.group(2))
    elev = int(m.group(3)) / 10.0
    return HorizonTile(path=path, row=row, col=col, observer_elevation_m=elev)


def _collect_horizon_tiles(horizon_dir: Path) -> list[HorizonTile]:
    tiles: list[HorizonTile] = []
    for path in sorted(horizon_dir.glob("horizon_*.bin")):
        tile = _parse_horizon_tile(path)
        if tile is not None:
            tiles.append(tile)
    if not tiles:
        raise FileNotFoundError(f"No valid horizon files found in {horizon_dir}")
    return tiles


def _infer_timestamp_from_output(output_tif_path: Path) -> dt.datetime | None:
    m = _SUN_IMAGE_RE.search(output_tif_path.stem)
    if not m:
        return None
    parsed = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H-%M-%S")
    return parsed.replace(tzinfo=dt.timezone.utc)


def _normalize_timestamp(timestamp_utc: dt.datetime | str | None, output_tif_path: Path) -> dt.datetime:
    if isinstance(timestamp_utc, str):
        value = dt.datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
    elif isinstance(timestamp_utc, dt.datetime):
        value = timestamp_utc
    else:
        inferred = _infer_timestamp_from_output(output_tif_path)
        if inferred is None:
            raise ValueError(
                "timestamp_utc is required unless output filename contains "
                "sun_image_YYYY-MM-DDTHH-MM-SS."
            )
        value = inferred

    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    else:
        value = value.astimezone(dt.timezone.utc)
    return value


def _load_spice_kernels(kernel_root: Path, metakernel_relpath: str = "kernels/metakernel.txt") -> None:
    global _SPICE_LOADED
    if _SPICE_LOADED:
        return
    metakernel = kernel_root / metakernel_relpath
    if not metakernel.exists():
        raise FileNotFoundError(f"SPICE metakernel not found: {metakernel}")

    for line in metakernel.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        spice.furnsh(str((kernel_root / s).resolve()))
    _SPICE_LOADED = True


def ensure_proj_data() -> None:
    """Best-effort detection of PROJ data to avoid runtime errors."""
    existing = gdal.GetConfigOption("PROJ_LIB") or os.environ.get("PROJ_LIB")
    if existing and Path(existing).joinpath("proj.db").exists():
        return

    import osgeo

    root = Path(osgeo.__file__).resolve().parent
    candidates = [
        root / "data",
        root / "data" / "proj",
        root.parent / "share" / "proj",
    ]

    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        candidates.append(Path(conda_prefix) / "Library" / "share" / "proj")

    for candidate in candidates:
        proj_db = candidate / "proj.db"
        if proj_db.exists():
            os.environ["PROJ_LIB"] = str(candidate)
            gdal.SetConfigOption("PROJ_LIB", str(candidate))
            return

    raise RuntimeError("Could not locate proj.db. Set PROJ_LIB to a valid PROJ data directory.")


def _sun_vector_moon_me_meters(timestamp_utc: dt.datetime) -> np.ndarray:
    et = spice.datetime2et(timestamp_utc)
    # Match C#: spkgeo_c(SUN, et, "MOON_ME", MOON)
    state, _ = spice.spkgeo(10, et, "MOON_ME", 301)
    return np.asarray(state[:3], dtype=np.float64) * 1000.0


def _create_srs_transform_to_longlat(dataset: gdal.Dataset) -> osr.CoordinateTransformation:
    ensure_proj_data()
    src = osr.SpatialReference()
    src.ImportFromWkt(dataset.GetProjectionRef())
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    dst = osr.SpatialReference()
    dst.ImportFromProj4(LONG_LAT_PROJ4)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(src, dst)
    if transform is None:
        raise RuntimeError("Failed to create CRS transform to longlat (check PROJ_LIB/proj.db).")
    return transform


def _patch_lonlat_radians(
    geo_transform: tuple[float, float, float, float, float, float],
    tile_row: int,
    tile_col: int,
    transform_to_longlat: osr.CoordinateTransformation,
) -> tuple[np.ndarray, np.ndarray]:
    rows = np.arange(tile_row, tile_row + PATCH_SIZE, dtype=np.float64)
    cols = np.arange(tile_col, tile_col + PATCH_SIZE, dtype=np.float64)
    cc, rr = np.meshgrid(cols, rows)

    x = geo_transform[0] + geo_transform[1] * cc + geo_transform[2] * rr
    y = geo_transform[3] + geo_transform[4] * cc + geo_transform[5] * rr

    pts = np.column_stack((x.ravel(), y.ravel(), np.zeros(PATCH_SIZE * PATCH_SIZE, dtype=np.float64)))
    longlat = np.asarray(transform_to_longlat.TransformPoints(pts.tolist()), dtype=np.float64)
    lon_rad = np.deg2rad(longlat[:, 0]).reshape(PATCH_SIZE, PATCH_SIZE)
    lat_rad = np.deg2rad(longlat[:, 1]).reshape(PATCH_SIZE, PATCH_SIZE)
    return lon_rad, lat_rad


@nb.njit(cache=True, fastmath=True)
def _builder_sun_fraction(horizon: np.ndarray, az_deg: float, el_deg: float) -> float:
    bucket_width_deg = 360.0 / HORIZON_SAMPLES
    bucket_half_width_deg = bucket_width_deg / 2.0
    sun_half_angle_deg = 0.54 / 2.0
    frac_step = sun_half_angle_deg / bucket_width_deg / 8.0

    sun_left_deg = az_deg - sun_half_angle_deg - bucket_half_width_deg
    sun_left_bucket_float = sun_left_deg * (HORIZON_SAMPLES / 360.0)
    sun_left_bucket = int(sun_left_bucket_float)
    frac = sun_left_bucket_float - sun_left_bucket
    if sun_left_bucket < 0:
        sun_left_bucket += HORIZON_SAMPLES
    elif sun_left_bucket >= HORIZON_SAMPLES:
        sun_left_bucket -= HORIZON_SAMPLES

    left_bucket = sun_left_bucket
    right_bucket = left_bucket + 1
    if right_bucket >= HORIZON_SAMPLES:
        right_bucket -= HORIZON_SAMPLES

    left_elev = horizon[left_bucket]
    right_elev = horizon[right_bucket]
    bucket_delta = right_elev - left_elev
    photons = 0.0

    for i in range(HALF_CIRCLE.shape[0]):
        horizon_elev = frac * bucket_delta + left_elev
        sun_column_deg = HALF_CIRCLE[i]
        sun_top_deg = el_deg + sun_column_deg

        if horizon_elev < sun_top_deg:
            angle_delta = sun_top_deg - horizon_elev
            sun_col2 = sun_column_deg + sun_column_deg
            if angle_delta > sun_col2:
                angle_delta = sun_col2
            photons += angle_delta

        frac += frac_step
        if frac >= 1.0:
            left_bucket = right_bucket
            right_bucket = left_bucket + 1
            if right_bucket >= HORIZON_SAMPLES:
                right_bucket -= HORIZON_SAMPLES
            left_elev = right_elev
            right_elev = horizon[right_bucket]
            bucket_delta = right_elev - left_elev
            frac -= 1.0

    return photons / MAX_PHOTONS


@nb.njit(cache=True, fastmath=True)
def _compute_patch_lightmap(
    horizons: np.ndarray,
    lon_rad: np.ndarray,
    lat_rad: np.ndarray,
    elevation_m: np.ndarray,
    sunvec_me_m: np.ndarray,
) -> np.ndarray:
    out = np.zeros((PATCH_SIZE, PATCH_SIZE), dtype=np.uint8)
    sunx, suny, sunz = sunvec_me_m[0], sunvec_me_m[1], sunvec_me_m[2]
    rad2deg = 180.0 / np.pi

    for r in range(PATCH_SIZE):
        for c in range(PATCH_SIZE):
            lon = lon_rad[r, c]
            lat = lat_rad[r, c]

            cos_lat = math.cos(lat)
            sin_lat = math.sin(lat)
            cos_lon = math.cos(lon)
            sin_lon = math.sin(lon)

            obs_radius = MOON_RADIUS_M + elevation_m[r, c]
            obsx = obs_radius * cos_lat * cos_lon
            obsy = obs_radius * cos_lat * sin_lon
            obsz = obs_radius * sin_lat

            dx = sunx - obsx
            dy = suny - obsy
            dz = sunz - obsz

            east_x = -sin_lon
            east_y = cos_lon
            east_z = 0.0

            north_x = -sin_lat * cos_lon
            north_y = -sin_lat * sin_lon
            north_z = cos_lat

            up_x = cos_lat * cos_lon
            up_y = cos_lat * sin_lon
            up_z = sin_lat

            enu_x = dx * east_x + dy * east_y + dz * east_z
            enu_y = dx * north_x + dy * north_y + dz * north_z
            enu_z = dx * up_x + dy * up_y + dz * up_z

            az = math.atan2(enu_x, enu_y)
            if az < 0.0:
                az += 2.0 * np.pi
            el = math.atan2(enu_z, math.sqrt(enu_x * enu_x + enu_y * enu_y))

            az_deg = az * rad2deg
            el_deg = el * rad2deg
            frac = _builder_sun_fraction(horizons[r, c], az_deg, el_deg)
            val = frac * 255.0
            if val < 0.0:
                val = 0.0
            elif val > 255.0:
                val = 255.0
            out[r, c] = np.uint8(val)
    return out


def generate_lightmap_geotiff(
    dem_path: str | Path,
    horizon_dir: str | Path,
    output_tif_path: str | Path,
    timestamp_utc: dt.datetime | str | None = None,
    spice_kernel_root: str | Path = Path("moonlib/StaticFiles"),
) -> Path:
    dem_path = Path(dem_path)
    horizon_dir = Path(horizon_dir)
    output_tif_path = Path(output_tif_path)
    spice_kernel_root = Path(spice_kernel_root)

    if not dem_path.exists():
        raise FileNotFoundError(f"DEM not found: {dem_path}")
    if not horizon_dir.exists():
        raise FileNotFoundError(f"Horizon directory not found: {horizon_dir}")

    ensure_proj_data()
    timestamp = _normalize_timestamp(timestamp_utc, output_tif_path)
    _load_spice_kernels(spice_kernel_root)
    sunvec_me_m = _sun_vector_moon_me_meters(timestamp)

    tiles = _collect_horizon_tiles(horizon_dir)
    out_width = max(tile.col for tile in tiles) + PATCH_SIZE
    out_height = max(tile.row for tile in tiles) + PATCH_SIZE

    dem_ds = gdal.Open(str(dem_path), gdal.GA_ReadOnly)
    if dem_ds is None:
        raise RuntimeError(f"Unable to open DEM: {dem_path}")
    dem_band = dem_ds.GetRasterBand(1)
    dem_elevation = dem_band.ReadAsArray().astype(np.float64, copy=False)
    geo_transform = dem_ds.GetGeoTransform()
    projection = dem_ds.GetProjectionRef()
    to_longlat = _create_srs_transform_to_longlat(dem_ds)

    output_tif_path.parent.mkdir(parents=True, exist_ok=True)
    driver = gdal.GetDriverByName("GTiff")
    out_ds = None

    if output_tif_path.exists():
        existing = gdal.Open(str(output_tif_path), gdal.GA_Update)
        if existing is not None:
            type_ok = existing.RasterCount >= 1 and existing.GetRasterBand(1).DataType == gdal.GDT_Byte
            size_ok = existing.RasterXSize == out_width and existing.RasterYSize == out_height
            if type_ok and size_ok:
                out_ds = existing
                out_ds.SetProjection(projection)
                out_ds.SetGeoTransform(geo_transform)
                out_ds.GetRasterBand(1).Fill(0)
            else:
                existing = None
        if out_ds is None:
            try:
                gdal.Unlink(str(output_tif_path))
            except Exception:
                pass

    if out_ds is None:
        out_ds = driver.Create(
            str(output_tif_path),
            out_width,
            out_height,
            1,
            gdal.GDT_Byte,
            options=["TILED=YES", "BLOCKXSIZE=128", "BLOCKYSIZE=128", "COMPRESS=LZW", "BIGTIFF=YES", "SPARSE_OK=TRUE"],
        )
        if out_ds is None:
            raise RuntimeError(
                f"Unable to create output GeoTIFF: {output_tif_path}. "
                "If the file exists, ensure it is not open in another application."
            )
        out_ds.SetProjection(projection)
        out_ds.SetGeoTransform(geo_transform)

    for tile in tiles:
        expected_count = PATCH_SIZE * PATCH_SIZE * HORIZON_SAMPLES
        flat = np.fromfile(tile.path, dtype=np.float32, count=expected_count)
        if flat.size != expected_count:
            raise ValueError(f"Invalid horizon file length: {tile.path}")
        horizons = flat.reshape(PATCH_SIZE, PATCH_SIZE, HORIZON_SAMPLES)

        row_end = tile.row + PATCH_SIZE
        col_end = tile.col + PATCH_SIZE
        if row_end > dem_elevation.shape[0] or col_end > dem_elevation.shape[1]:
            continue

        elev_patch = dem_elevation[tile.row:row_end, tile.col:col_end]
        lon_rad, lat_rad = _patch_lonlat_radians(geo_transform, tile.row, tile.col, to_longlat)
        lightmap_patch = _compute_patch_lightmap(horizons, lon_rad, lat_rad, elev_patch, sunvec_me_m)
        out_ds.GetRasterBand(1).WriteArray(lightmap_patch, xoff=tile.col, yoff=tile.row)

    out_ds.FlushCache()
    out_ds = None
    dem_ds = None
    return output_tif_path


def _default_output_name(timestamp: dt.datetime) -> str:
    return f"sun_image_{timestamp.strftime('%Y-%m-%dT%H-%M-%S')}.tif"


def _main(argv: Iterable[str]) -> int:
    args = list(argv)
    if len(args) < 4:
        print(
            "Usage: lightmap_from_horizons.py <dem.tif> <horizon_dir> <output.tif> "
            "[timestamp_utc_iso]"
        )
        return 2

    dem_path, horizon_dir, output_tif = args[1], args[2], args[3]
    timestamp = args[4] if len(args) > 4 else None
    out = generate_lightmap_geotiff(dem_path, horizon_dir, output_tif, timestamp_utc=timestamp)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv))
