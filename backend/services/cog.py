from __future__ import annotations

from pathlib import Path

from backend.worker.gdal_runtime import import_rasterio


def _overview_factors(width: int, height: int) -> list[int]:
    factors: list[int] = []
    factor = 2
    while width // factor >= 256 and height // factor >= 256:
        factors.append(factor)
        factor *= 2
    return factors


def convert_geotiff_to_cog(source_path: Path, destination_path: Path) -> Path:
    rasterio = import_rasterio()
    resampling = rasterio.enums.Resampling
    src = source_path.expanduser().resolve()
    dst = destination_path.expanduser().resolve()
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"GeoTIFF source path not found: {src}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with rasterio.open(src) as ds:
            profile = ds.profile.copy()
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
            with rasterio.open(dst, "w", **profile) as out:
                for band in range(1, ds.count + 1):
                    out.write(ds.read(band), band)
                out.update_tags(AREA_OR_POINT=ds.tags().get("AREA_OR_POINT", "Area"))

            with rasterio.open(dst, "r+") as out:
                factors = _overview_factors(out.width, out.height)
                if factors:
                    out.build_overviews(factors, resampling.average)
                    out.update_tags(ns="rio_overview", resampling="average")
    except Exception:
        try:
            if dst.exists():
                dst.unlink()
        except OSError:
            pass
        raise
    return dst
