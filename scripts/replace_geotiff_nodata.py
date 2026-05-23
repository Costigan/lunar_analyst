#!/usr/bin/env python3
"""Replace NoData pixels in a GeoTIFF using values from a backup GeoTIFF.

This script writes a new GeoTIFF whose grid matches the base raster exactly:

- same extent
- same pixel size
- same width and height
- same projection/georeferencing

For each band, pixels in the base raster that equal that band's NoData value are
replaced with values sampled from the backup raster after the backup has been
reprojected/resampled onto the base raster grid.

Typical usage:

    python replace_geotiff_nodata.py base.tif backup.tif output.tif

If the backup raster also has NoData at a location, the output remains NoData at
that pixel.
"""

from __future__ import annotations

import argparse
import math
import sys

import numpy as np
from osgeo import gdal


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create an output GeoTIFF that matches the base raster grid and fills "
            "base NoData pixels with values sampled from a backup GeoTIFF."
        )
    )
    parser.add_argument("basefile", help="Input GeoTIFF whose grid and valid pixels are preserved.")
    parser.add_argument("backup", help="Input GeoTIFF used to fill NoData gaps in the base raster.")
    parser.add_argument("output", help="Output GeoTIFF filename.")
    parser.add_argument(
        "--resampling",
        choices=("nearest", "bilinear", "cubic", "cubicspline", "lanczos", "average", "mode"),
        default="bilinear",
        help=(
            "Resampling method used when aligning the backup raster to the base "
            "raster grid. Default: bilinear."
        ),
    )
    parser.add_argument(
        "--creation-option",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=(
            "GTiff creation option to pass through to GDAL. May be supplied more "
            "than once, for example --creation-option COMPRESS=DEFLATE."
        ),
    )
    return parser


def _is_nodata(array: np.ndarray, nodata: float | None) -> np.ndarray:
    if nodata is None:
        return np.zeros(array.shape, dtype=bool)
    if np.issubdtype(array.dtype, np.floating) and math.isnan(nodata):
        return np.isnan(array)
    return array == nodata


def _gdal_resampling(name: str) -> int:
    mapping = {
        "nearest": gdal.GRA_NearestNeighbour,
        "bilinear": gdal.GRA_Bilinear,
        "cubic": gdal.GRA_Cubic,
        "cubicspline": gdal.GRA_CubicSpline,
        "lanczos": gdal.GRA_Lanczos,
        "average": gdal.GRA_Average,
        "mode": gdal.GRA_Mode,
    }
    return mapping[name]


def _validate_inputs(base_ds: gdal.Dataset, backup_ds: gdal.Dataset) -> None:
    if base_ds.RasterCount < 1:
        raise ValueError("Base raster has no bands.")
    if backup_ds.RasterCount != base_ds.RasterCount:
        raise ValueError(
            f"Band count mismatch: base has {base_ds.RasterCount} band(s), "
            f"backup has {backup_ds.RasterCount} band(s)."
        )


def _warp_backup_to_base_grid(base_ds: gdal.Dataset, backup_path: str, resampling: str) -> gdal.Dataset:
    transform = base_ds.GetGeoTransform()
    x_min = transform[0]
    y_max = transform[3]
    x_max = x_min + transform[1] * base_ds.RasterXSize
    y_min = y_max + transform[5] * base_ds.RasterYSize

    warped = gdal.Warp(
        "",
        backup_path,
        format="MEM",
        dstSRS=base_ds.GetProjection(),
        outputBounds=(x_min, y_min, x_max, y_max),
        width=base_ds.RasterXSize,
        height=base_ds.RasterYSize,
        resampleAlg=_gdal_resampling(resampling),
    )
    if warped is None:
        raise RuntimeError("GDAL failed to warp the backup raster onto the base grid.")
    return warped


def _copy_dataset_metadata(src: gdal.Dataset, dst: gdal.Dataset) -> None:
    dst.SetGeoTransform(src.GetGeoTransform())
    dst.SetProjection(src.GetProjection())
    dst.SetMetadata(src.GetMetadata())


def replace_nodata(basefile: str, backup: str, output: str, resampling: str, creation_options: list[str]) -> None:
    gdal.UseExceptions()

    base_ds = gdal.Open(basefile, gdal.GA_ReadOnly)
    if base_ds is None:
        raise FileNotFoundError(f"Could not open base raster: {basefile}")

    backup_ds = gdal.Open(backup, gdal.GA_ReadOnly)
    if backup_ds is None:
        raise FileNotFoundError(f"Could not open backup raster: {backup}")

    _validate_inputs(base_ds, backup_ds)
    warped_backup_ds = _warp_backup_to_base_grid(base_ds, backup, resampling)

    driver = gdal.GetDriverByName("GTiff")
    if driver is None:
        raise RuntimeError("GTiff driver is not available in this GDAL build.")

    out_ds = driver.Create(
        output,
        base_ds.RasterXSize,
        base_ds.RasterYSize,
        base_ds.RasterCount,
        base_ds.GetRasterBand(1).DataType,
        options=creation_options,
    )
    if out_ds is None:
        raise RuntimeError(f"Could not create output raster: {output}")

    _copy_dataset_metadata(base_ds, out_ds)

    for band_index in range(1, base_ds.RasterCount + 1):
        base_band = base_ds.GetRasterBand(band_index)
        backup_band = warped_backup_ds.GetRasterBand(band_index)
        out_band = out_ds.GetRasterBand(band_index)

        base_array = base_band.ReadAsArray()
        backup_array = backup_band.ReadAsArray()

        base_nodata = base_band.GetNoDataValue()
        backup_nodata = backup_band.GetNoDataValue()

        fill_mask = _is_nodata(base_array, base_nodata)
        backup_valid_mask = ~_is_nodata(backup_array, backup_nodata)

        out_array = np.array(base_array, copy=True)
        out_array[fill_mask & backup_valid_mask] = backup_array[fill_mask & backup_valid_mask]

        out_band.WriteArray(out_array)
        if base_nodata is not None:
            out_band.SetNoDataValue(base_nodata)

        out_band.SetMetadata(base_band.GetMetadata())
        out_band.FlushCache()

    out_ds.FlushCache()

    base_ds = None
    backup_ds = None
    warped_backup_ds = None
    out_ds = None


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        replace_nodata(
            basefile=args.basefile,
            backup=args.backup,
            output=args.output,
            resampling=args.resampling,
            creation_options=args.creation_option,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
