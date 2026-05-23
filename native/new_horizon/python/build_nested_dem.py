#!/usr/bin/env python3
"""Merge nested DEMs into the footprint of the innermost DEM plus a border.

Example
-------
python build_nested_dem.py \
    --border-meters 300 \
    --output output_dem.tif \
    /d/datasets/viper_v71_2024_medium/other/dem.tif \
    /d/viper/maps/gsfc/site_20v2/site20v2_final_adj_5mpp_surf.tif \
    /d/viper/maps/lola/ldem_80s_20m-2017-06-15-processed.tif

The DEM list must be ordered from innermost (highest resolution) to outermost.
"""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import numpy as np
from osgeo import gdal, gdal_array


gdal.UseExceptions()


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
        if not candidate:
            continue
        proj_db = candidate / "proj.db"
        if proj_db.exists():
            os.environ["PROJ_LIB"] = str(candidate)
            gdal.SetConfigOption("PROJ_LIB", str(candidate))
            print(f"Using PROJ data directory: {candidate}")
            return

    raise RuntimeError("Could not locate proj.db. Set PROJ_LIB to a valid PROJ data directory.")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Construct a bordered DEM from nested sources.")
    parser.add_argument(
        "dems",
        nargs="+",
        help="Ordered DEM paths (inner to outer). Use at least two for border coverage.",
    )
    parser.add_argument(
        "--border-meters",
        type=float,
        default=300.0,
        help="Border width around the innermost DEM, in meters (default: 300).",
    )
    parser.add_argument(
        "--output",
        default="output_dem.tif",
        help="Path to the output DEM (default: ./output_dem.tif).",
    )
    parser.add_argument(
        "--resample",
        default="bilinear",
        choices=["nearest", "bilinear", "cubic", "cubicspline"],
        help="GDAL resampling kernel for reprojection.",
    )
    parser.add_argument(
        "--compress",
        default="LZW",
        help="Compression to apply to the GeoTIFF output (default: LZW).",
    )
    return parser.parse_args()


def open_dataset(path: str) -> gdal.Dataset:
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"Unable to open DEM: {path}")
    return ds


def compute_output_grid(inner_ds: gdal.Dataset, border_m: float) -> Tuple[Tuple[float, ...], int, int]:
    if border_m < 0:
        raise ValueError("border_meters must be non-negative.")

    gt = inner_ds.GetGeoTransform()
    pixel_width = gt[1]
    pixel_height = gt[5]

    if pixel_width <= 0 or pixel_height >= 0:
        raise ValueError("Unexpected geotransform (non-standard orientation).")

    border_px_x = int(math.ceil(border_m / pixel_width))
    border_px_y = int(math.ceil(border_m / abs(pixel_height)))

    cols = inner_ds.RasterXSize + 2 * border_px_x
    rows = inner_ds.RasterYSize + 2 * border_px_y

    origin_x = gt[0] - border_px_x * pixel_width
    origin_y = gt[3] + border_px_y * abs(pixel_height)

    new_gt = (
        origin_x,
        pixel_width,
        0.0,
        origin_y,
        0.0,
        pixel_height,
    )
    return new_gt, cols, rows


def geotransform_bounds(gt: Sequence[float], cols: int, rows: int) -> Tuple[float, float, float, float]:
    min_x = gt[0]
    max_y = gt[3]
    max_x = gt[0] + gt[1] * cols
    min_y = gt[3] + gt[5] * rows
    return min(min_x, max_x), min(min_y, max_y), max(min_x, max_x), max(min_y, max_y)


def get_resample(resample_name: str) -> int:
    return {
        "nearest": gdal.GRA_NearestNeighbour,
        "bilinear": gdal.GRA_Bilinear,
        "cubic": gdal.GRA_Cubic,
        "cubicspline": gdal.GRA_CubicSpline,
    }[resample_name]


def allocate_output(inner_ds: gdal.Dataset, rows: int, cols: int) -> Tuple[np.ndarray, int, float]:
    band = inner_ds.GetRasterBand(1)
    target_dtype = band.DataType
    np_dtype = gdal_array.GDALTypeCodeToNumericTypeCode(target_dtype)
    if np_dtype is None:
        raise RuntimeError("Unsupported GDAL data type for numpy conversion.")

    nodata = band.GetNoDataValue()
    if nodata is None:
        nodata = -9999.0

    data = np.full((rows, cols), nodata, dtype=np_dtype)
    return data, target_dtype, nodata


def warp_to_grid(
    src_path: str,
    bounds: Tuple[float, float, float, float],
    pixel_size_x: float,
    pixel_size_y: float,
    rows: int,
    cols: int,
    dst_srs_wkt: str,
    dst_nodata: float,
    resample_alg: int,
    target_dtype: int,
) -> np.ndarray:
    ds = open_dataset(src_path)
    band = ds.GetRasterBand(1)
    src_nodata = band.GetNoDataValue()

    warp_options = gdal.WarpOptions(
        format="MEM",
        dstSRS=dst_srs_wkt,
        outputBounds=bounds,
        xRes=pixel_size_x,
        yRes=abs(pixel_size_y),
        resampleAlg=resample_alg,
        srcNodata=src_nodata,
        dstNodata=dst_nodata,
        multithread=True,
        outputType=target_dtype,
    )

    warped = gdal.Warp("", ds, options=warp_options)
    if warped is None:
        raise RuntimeError(f"Warp failed for {src_path}")

    array = warped.ReadAsArray()
    if array is None:
        array = np.full((rows, cols), dst_nodata, dtype=gdal_array.GDALTypeCodeToNumericTypeCode(target_dtype))

    warped = None
    ds = None
    return array


def main() -> None:
    args = parse_args()
    if len(args.dems) < 2:
        raise SystemExit("Provide at least two DEMs (inner to outer order).")

    ensure_proj_data()

    inner_ds = open_dataset(args.dems[0])
    target_srs = inner_ds.GetProjection()
    gt, cols, rows = compute_output_grid(inner_ds, args.border_meters)
    bounds = geotransform_bounds(gt, cols, rows)

    out_array, target_dtype, target_nodata = allocate_output(inner_ds, rows, cols)
    pixel_width = gt[1]
    pixel_height = gt[5]
    resample_alg = get_resample(args.resample)

    for dem_path in reversed(args.dems):
        print(f"Warping {dem_path} ...")
        warped = warp_to_grid(
            dem_path,
            bounds,
            pixel_width,
            pixel_height,
            rows,
            cols,
            target_srs,
            target_nodata,
            resample_alg,
            target_dtype,
        )
        valid_mask = ~np.isclose(warped, target_nodata)
        out_array[valid_mask] = warped[valid_mask]

    driver = gdal.GetDriverByName("GTiff")
    creation_options = ["TILED=YES", f"COMPRESS={args.compress}", "BIGTIFF=IF_SAFER"]
    out_ds = driver.Create(args.output, cols, rows, 1, target_dtype, options=creation_options)
    out_ds.SetGeoTransform(gt)
    out_ds.SetProjection(target_srs)

    out_band = out_ds.GetRasterBand(1)
    out_band.SetNoDataValue(target_nodata)
    out_band.WriteArray(out_array)
    out_band.FlushCache()

    out_ds.FlushCache()
    out_band = None
    out_ds = None
    inner_ds = None
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
