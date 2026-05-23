from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio

from .models import RasterSignature


@dataclass(frozen=True)
class RenderedRaster:
    rgba: np.ndarray



def signature_for_path(path: str) -> RasterSignature:
    with rasterio.open(path) as ds:
        return RasterSignature(width=ds.width, height=ds.height, count=ds.count)


def cache_key_for_path(path: str) -> str:
    p = Path(path)
    stat = p.stat()
    return f"{p.resolve()}::{stat.st_mtime_ns}::{stat.st_size}"


def _scale_to_u8(data: np.ndarray) -> np.ndarray:
    out = data.astype(np.float32)
    finite = np.isfinite(out)
    if not finite.any():
        return np.zeros(out.shape, dtype=np.uint8)
    valid = out[finite]
    lo = np.percentile(valid, 2)
    hi = np.percentile(valid, 98)
    if hi <= lo:
        hi = lo + 1.0
    out = np.clip((out - lo) * 255.0 / (hi - lo), 0.0, 255.0)
    out[~finite] = 0
    return out.astype(np.uint8)


def read_raster_rgba(path: str) -> RenderedRaster:
    with rasterio.open(path) as ds:
        arr = ds.read()
    if arr.shape[0] == 1:
        band = _scale_to_u8(arr[0])
        rgba = np.stack([band, band, band, np.full_like(band, 255)], axis=-1)
    else:
        r = _scale_to_u8(arr[0])
        g = _scale_to_u8(arr[1 if arr.shape[0] > 1 else 0])
        b = _scale_to_u8(arr[2 if arr.shape[0] > 2 else 0])
        rgba = np.stack([r, g, b, np.full_like(r, 255)], axis=-1)
    return RenderedRaster(rgba=rgba)
