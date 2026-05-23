import argparse
import math
from pathlib import Path
from typing import List, Tuple

import numpy as np
from osgeo import gdal, osr

MOON_RADIUS_M = 1_737_400.0


def build_longlat_srs() -> osr.SpatialReference:
    srs = osr.SpatialReference()
    srs.ImportFromProj4(f"+proj=longlat +R={MOON_RADIUS_M} +no_defs")
    return srs


def _invert_geotransform(gt):
    result = gdal.InvGeoTransform(gt)
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], (tuple, list)):
        success, inv_gt = result
        if not success:
            raise RuntimeError("GeoTransform is not invertible")
        return tuple(inv_gt)
    if isinstance(result, tuple) and len(result) == 6:
        return result
    raise RuntimeError(f"Unexpected InvGeoTransform result: {result!r}")


def load_dataset(path: str):
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"Failed to open DEM: {path}")
    gt = ds.GetGeoTransform()
    if gt is None:
        raise RuntimeError("DEM missing GeoTransform")
    inv_gt = _invert_geotransform(gt)

    target = osr.SpatialReference()
    target.ImportFromWkt(ds.GetProjection())
    geog = build_longlat_srs()
    to_geog = osr.CoordinateTransformation(target, geog)
    to_map = osr.CoordinateTransformation(geog, target)
    return ds, gt, inv_gt, to_geog, to_map


def pixel_to_latlon(gt, to_geog, col: float, row: float) -> Tuple[float, float]:
    x = gt[0] + gt[1] * col + gt[2] * row
    y = gt[3] + gt[4] * col + gt[5] * row
    lon_deg, lat_deg, _ = to_geog.TransformPoint(x, y)
    return lat_deg, lon_deg


def latlon_to_pixel(inv_gt, to_map, lat_deg: float, lon_deg: float) -> Tuple[float, float]:
    x, y, _ = to_map.TransformPoint(lon_deg, lat_deg)
    col = inv_gt[0] + inv_gt[1] * x + inv_gt[2] * y
    row = inv_gt[3] + inv_gt[4] * x + inv_gt[5] * y
    return col, row


def geodesic_point(lat_rad: float, lon_rad: float, az_rad: float, dist_m: float):
    ang_dist = dist_m / MOON_RADIUS_M
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    sin_ang = math.sin(ang_dist)
    cos_ang = math.cos(ang_dist)
    sin_az = math.sin(az_rad)
    cos_az = math.cos(az_rad)

    lat2 = math.asin(sin_lat * cos_ang + cos_lat * sin_ang * cos_az)
    lon2 = lon_rad + math.atan2(sin_az * sin_ang * cos_lat, cos_ang - sin_lat * math.sin(lat2))
    return lat2, lon2


def sample_ray_pixels(
    lat_deg: float,
    lon_deg: float,
    az_deg: float,
    max_dist_m: float,
    step_m: float,
    inv_gt,
    to_map,
    width: int,
    height: int,
) -> List[Tuple[float, float, float]]:
    samples: List[Tuple[float, float, float]] = []
    lat_rad = math.radians(lat_deg)
    lon_rad = math.radians(lon_deg)
    az_rad = math.radians(az_deg)
    dist = 0.0
    while dist <= max_dist_m:
        lat2, lon2 = geodesic_point(lat_rad, lon_rad, az_rad, dist)
        col, row = latlon_to_pixel(inv_gt, to_map, math.degrees(lat2), math.degrees(lon2))
        if col < 0 or row < 0 or col >= width or row >= height:
            break
        samples.append((dist, col, row))
        dist += step_m
    return samples


def fit_quartic(distances: np.ndarray, coords: np.ndarray):
    s = distances - distances[0]
    values = coords - coords[0]
    A = np.vstack([s, s**2, s**3, s**4]).T
    coeffs, _, _, _ = np.linalg.lstsq(A, values, rcond=None)
    return coords[0], coeffs


def write_samples(path: Path, samples: List[Tuple[float, float, float]]):
    import csv

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["dist_m", "pixel_x", "pixel_y"])
        for dist, x, y in samples:
            writer.writerow([f"{dist:.4f}", f"{x:.6f}", f"{y:.6f}"])


def main():
    parser = argparse.ArgumentParser(description="Fit a 4th-order polynomial to ray pixel coordinates.")
    parser.add_argument("--dem", required=True)
    parser.add_argument("--origin-x", type=float, required=True)
    parser.add_argument("--origin-y", type=float, required=True)
    parser.add_argument("--azimuth", type=float, required=True)
    parser.add_argument("--max-dist", type=float, default=100_000.0)
    parser.add_argument("--step", type=float, default=1_000.0)
    parser.add_argument("--distances-file", type=Path)
    parser.add_argument("--compare-samples", type=Path)
    parser.add_argument("--samples-out", type=Path)
    args = parser.parse_args()

    ds, gt, inv_gt, to_geog, to_map = load_dataset(args.dem)
    origin_lat, origin_lon = pixel_to_latlon(gt, to_geog, args.origin_x, args.origin_y)

    distances_override = None
    reference_samples = None
    if args.compare_samples:
        reference_samples = []
        for line in args.compare_samples.read_text().splitlines():
            if not line.strip():
                continue
            parts = line.strip().split(":")
            if len(parts) < 3:
                continue
            dist = float(parts[0])
            x = float(parts[1])
            y = float(parts[2])
            reference_samples.append((dist, x, y))
        distances_override = [s[0] for s in reference_samples]
    elif args.distances_file:
        distances_override = [
            float(line.strip()) for line in args.distances_file.read_text().splitlines() if line.strip()
        ]

    if distances_override:
        max_dist = max(distances_override)
        step = None
    else:
        max_dist = args.max_dist
        step = max(args.step, 1.0)

    samples: List[Tuple[float, float, float]] = []
    if distances_override:
        lat_rad = math.radians(origin_lat)
        lon_rad = math.radians(origin_lon)
        az_rad = math.radians(args.azimuth)
        for dist in distances_override:
            lat2, lon2 = geodesic_point(lat_rad, lon_rad, az_rad, dist)
            col, row = latlon_to_pixel(inv_gt, to_map, math.degrees(lat2), math.degrees(lon2))
            if col < 0 or row < 0 or col >= ds.RasterXSize or row >= ds.RasterYSize:
                samples.append((dist, float("nan"), float("nan")))
            else:
                samples.append((dist, col, row))
    else:
        samples = sample_ray_pixels(
            origin_lat,
            origin_lon,
            args.azimuth,
            max_dist,
            step,
            inv_gt,
            to_map,
            ds.RasterXSize,
            ds.RasterYSize,
        )

    valid_samples = [s for s in samples if not (math.isnan(s[1]) or math.isnan(s[2]))]
    if len(valid_samples) < 4:
        raise RuntimeError(f"Insufficient in-bounds samples ({len(valid_samples)}) for fitting.")

    distances = np.array([s[0] for s in valid_samples], dtype=np.float64)
    xs = np.array([s[1] for s in valid_samples], dtype=np.float64)
    ys = np.array([s[2] for s in valid_samples], dtype=np.float64)

    x0, ax = fit_quartic(distances, xs)
    y0, ay = fit_quartic(distances, ys)

    print("Observer lat/lon (deg):", origin_lat, origin_lon)
    print(f"Samples used: {len(valid_samples)}  Final distance: {distances[-1]:.1f} m")
    print("X polynomial coefficients (pixels):")
    print(f"  x0={x0:.9f}")
    print(f"  a1={ax[0]:.9e}  a2={ax[1]:.9e}  a3={ax[2]:.9e}  a4={ax[3]:.9e}")
    print("Y polynomial coefficients (pixels):")
    print(f"  y0={y0:.9f}")
    print(f"  b1={ay[0]:.9e}  b2={ay[1]:.9e}  b3={ay[2]:.9e}  b4={ay[3]:.9e}")

    if args.samples_out:
        write_samples(args.samples_out, samples)
        print(f"Wrote sampled pixels to {args.samples_out}")

    if reference_samples:
        diffs = []
        for (dist, x, y), (_, ref_x, ref_y) in zip(samples, reference_samples):
            diffs.append((dist, x - ref_x, y - ref_y))
        max_dx = max((abs(dx) for _, dx, _ in diffs if not math.isnan(dx)), default=0.0)
        max_dy = max((abs(dy) for _, _, dy in diffs if not math.isnan(dy)), default=0.0)
        print(f"Max |dx| vs reference: {max_dx:.6e} pixels")
        print(f"Max |dy| vs reference: {max_dy:.6e} pixels")
        for dist, dx, dy in diffs:
            if abs(dx) > 5e-5 or abs(dy) > 5e-5:
                print(f"  Dist {dist:.3f} m: dx={dx:.6e}, dy={dy:.6e}")


if __name__ == "__main__":
    main()
