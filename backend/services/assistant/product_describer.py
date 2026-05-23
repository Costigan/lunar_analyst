from __future__ import annotations

import base64
import csv
import datetime as dt
import struct
import zlib
from pathlib import Path
from typing import Any

import numpy as np

from backend.worker.gdal_runtime import import_rasterio

_MAX_TABLE_SAMPLE_ROWS = 25
_MAX_TABLE_SCAN_ROWS = 2000
_MAX_INLINE_IMAGE_BYTES = 350_000
_PREVIEW_SIZE = 256
_GEOTIFF_STATS_PERCENTILES = (5, 25, 50, 75, 95)


def _make_output(
    *,
    output_id: str,
    kind: str,
    mime_type: str,
    storage: str,
    title: str | None = None,
    caption: str | None = None,
    file_id: str | None = None,
    data: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "output_id": output_id,
        "kind": kind,
        "mime_type": mime_type,
        "storage": storage,
        "title": title,
        "caption": caption,
        "file_id": file_id,
        "data": dict(data or {}),
        "metadata": dict(metadata or {}),
    }


def _artifact_card_output(
    *,
    path: Path,
    source_file_id: str | None,
    summary_text: str,
    key_stats: dict[str, Any],
) -> dict[str, Any]:
    return _make_output(
        output_id=f"{path.name}-artifact-card",
        kind="artifact_card",
        mime_type="application/vnd.lunar-analyst.artifact-card+json",
        storage="inline",
        title=path.name,
        data={
            "name": path.name,
            "path": str(path),
            "suffix": path.suffix.lower(),
            "size_bytes": int(path.stat().st_size),
            "summary_text": summary_text,
            "key_stats": key_stats,
            "source_file_id": source_file_id,
        },
    )


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    return (
        struct.pack("!I", len(payload))
        + chunk_type
        + payload
        + struct.pack("!I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    )


def _encode_png_rgba(image: np.ndarray) -> bytes:
    height, width, channels = image.shape
    if channels != 4:
        raise ValueError("PNG encoder expects RGBA image data.")
    raw = b"".join(b"\x00" + image[row].tobytes() for row in range(height))
    header = struct.pack("!IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", header),
            _png_chunk(b"IDAT", zlib.compress(raw, level=6)),
            _png_chunk(b"IEND", b""),
        ]
    )


def _normalize_channel(channel: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    out = np.zeros(channel.shape, dtype=np.uint8)
    if not np.any(valid_mask):
        return out
    values = channel[valid_mask]
    low = float(np.nanpercentile(values, 2.0))
    high = float(np.nanpercentile(values, 98.0))
    if not np.isfinite(low):
        low = float(np.nanmin(values))
    if not np.isfinite(high):
        high = float(np.nanmax(values))
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        high = low + 1.0
    scaled = np.clip((channel - low) / (high - low), 0.0, 1.0)
    out[valid_mask] = np.round(scaled[valid_mask] * 255.0).astype(np.uint8)
    return out


def render_geotiff_preview_png(path: Path) -> tuple[bytes, dict[str, int]]:
    rasterio = import_rasterio()
    with rasterio.open(path) as ds:
        height = max(1, min(_PREVIEW_SIZE, int(ds.height)))
        width = max(1, min(_PREVIEW_SIZE, int(ds.width)))
        count = max(1, int(ds.count))
        if count >= 3:
            bands = ds.read(
                indexes=[1, 2, 3],
                out_shape=(3, height, width),
                masked=True,
            ).astype(np.float32)
            valid_mask = ~np.all(np.ma.getmaskarray(bands), axis=0)
            rgb = np.stack(
                [
                    _normalize_channel(np.ma.filled(bands[idx], 0.0), valid_mask)
                    for idx in range(3)
                ],
                axis=-1,
            )
        else:
            band = ds.read(indexes=1, out_shape=(height, width), masked=True).astype(np.float32)
            valid_mask = ~np.ma.getmaskarray(band)
            gray = _normalize_channel(np.ma.filled(band, 0.0), valid_mask)
            rgb = np.repeat(gray[:, :, np.newaxis], 3, axis=2)
        alpha = np.where(valid_mask, 255, 0).astype(np.uint8)
        rgba = np.dstack([rgb, alpha]).astype(np.uint8)
    encoded = _encode_png_rgba(rgba)
    return encoded, {"width": int(width), "height": int(height)}


def _infer_dtype(values: list[str]) -> str:
    cleaned = [value.strip() for value in values if value is not None and value.strip()]
    if not cleaned:
        return "string"
    lowered = [value.lower() for value in cleaned]
    if all(value in {"true", "false"} for value in lowered):
        return "boolean"
    try:
        for value in cleaned:
            int(value)
    except Exception:
        pass
    else:
        return "integer"
    try:
        for value in cleaned:
            float(value)
    except Exception:
        pass
    else:
        return "number"
    try:
        for value in cleaned:
            dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return "string"
    return "datetime"


def _band_statistics(masked: np.ma.MaskedArray) -> dict[str, Any]:
    compressed = masked.compressed()
    valid_count = int(compressed.size)
    total_count = int(masked.size)
    valid_percent = (valid_count / total_count * 100.0) if total_count > 0 else 0.0
    if valid_count == 0:
        return {
            "valid_count": 0,
            "total_count": total_count,
            "valid_percent": valid_percent,
            "min": None,
            "max": None,
            "mean": None,
            "std": None,
            "percentiles": {},
        }
    values = np.asarray(compressed, dtype=np.float64)
    return {
        "valid_count": valid_count,
        "total_count": total_count,
        "valid_percent": valid_percent,
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        "percentiles": {
            f"p{pct:02d}": float(np.percentile(values, pct))
            for pct in _GEOTIFF_STATS_PERCENTILES
        },
    }


def describe_geotiff_stats(path: Path, *, source_file_id: str | None = None) -> dict[str, Any]:
    rasterio = import_rasterio()
    warnings: list[str] = []
    band_stats: list[dict[str, Any]] = []
    with rasterio.open(path) as ds:
        bounds = ds.bounds
        for band_index in range(1, int(ds.count) + 1):
            stats = _band_statistics(ds.read(band_index, masked=True))
            band_stats.append({"band": band_index, **stats})
        primary_band = band_stats[0] if band_stats else {}
        key_stats = {
            "width": int(ds.width),
            "height": int(ds.height),
            "band_count": int(ds.count),
            "dtype": str(ds.dtypes[0]) if ds.dtypes else "",
            "crs": str(ds.crs) if ds.crs else "",
            "bounds": [float(bounds.left), float(bounds.bottom), float(bounds.right), float(bounds.top)],
            "valid_count": int(primary_band.get("valid_count", 0) or 0),
            "total_count": int(primary_band.get("total_count", 0) or 0),
            "valid_percent": float(primary_band.get("valid_percent", 0.0) or 0.0),
            "min": primary_band.get("min"),
            "max": primary_band.get("max"),
            "mean": primary_band.get("mean"),
            "std": primary_band.get("std"),
            "percentiles": dict(primary_band.get("percentiles", {})),
            "band_stats": band_stats,
        }
        summary_text = (
            f"GeoTIFF `{path.name}` statistics ready: "
            f"band_count={ds.count}, dtype={ds.dtypes[0] if ds.dtypes else 'unknown'}, "
            f"valid={int(primary_band.get('valid_count', 0) or 0)}/{int(primary_band.get('total_count', 0) or 0)}."
        )
    return {
        "summary_text": summary_text,
        "key_stats": key_stats,
        "warnings": warnings,
        "source_files": [str(path)],
        "artifact_file_id": source_file_id,
        "artifacts": [],
    }


def describe_geotiff(path: Path, *, source_file_id: str | None = None) -> dict[str, Any]:
    rasterio = import_rasterio()
    warnings: list[str] = []
    with rasterio.open(path) as ds:
        bounds = ds.bounds
        key_stats = {
            "width": int(ds.width),
            "height": int(ds.height),
            "band_count": int(ds.count),
            "dtype": str(ds.dtypes[0]) if ds.dtypes else "",
            "crs": str(ds.crs) if ds.crs else "",
            "bounds": [float(bounds.left), float(bounds.bottom), float(bounds.right), float(bounds.top)],
        }
        summary_text = (
            f"GeoTIFF `{path.name}` with {ds.count} band(s), "
            f"{ds.width}x{ds.height} pixels, dtype={ds.dtypes[0]}."
        )
    outputs: list[dict[str, Any]] = [
        _artifact_card_output(
            path=path,
            source_file_id=source_file_id,
            summary_text=summary_text,
            key_stats=key_stats,
        )
    ]
    return {
        "summary_text": summary_text,
        "key_stats": key_stats,
        "warnings": warnings,
        "source_files": [str(path)],
        "artifact_file_id": source_file_id,
        "artifacts": outputs,
    }


def describe_table(path: Path, *, source_file_id: str | None = None) -> dict[str, Any]:
    sample_rows: list[dict[str, str]] = []
    columns: list[str] = []
    column_values: dict[str, list[str]] = {}
    row_count = 0
    truncated = False
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = [str(item or "").strip() for item in (reader.fieldnames or []) if str(item or "").strip()]
        column_values = {column: [] for column in columns}
        for idx, row in enumerate(reader):
            if idx >= _MAX_TABLE_SCAN_ROWS:
                truncated = True
                break
            row_count += 1
            if len(sample_rows) < _MAX_TABLE_SAMPLE_ROWS:
                normalized: dict[str, str] = {}
                for column in columns:
                    value = "" if row is None else str(row.get(column, "") or "")
                    normalized[column] = value
                    column_values[column].append(value)
                sample_rows.append(normalized)
    column_defs = [
        {"key": column, "label": column, "dtype": _infer_dtype(column_values.get(column, []))}
        for column in columns[:50]
    ]
    summary_text = f"Tabular file `{path.name}` with {row_count} row(s) scanned."
    key_stats = {"rows_estimate": row_count, "columns": columns[:50], "truncated": truncated}
    outputs = [
        _make_output(
            output_id=f"{path.name}-table-sample",
            kind="table",
            mime_type="application/vnd.lunar-analyst.table+json",
            storage="inline",
            title=f"{path.name} sample",
            data={
                "columns": column_defs,
                "rows": sample_rows,
                "row_count": row_count,
                "truncated": truncated,
                "source_file_id": source_file_id,
            },
        ),
        _artifact_card_output(
            path=path,
            source_file_id=source_file_id,
            summary_text=summary_text,
            key_stats=key_stats,
        ),
    ]
    return {
        "summary_text": summary_text,
        "key_stats": key_stats,
        "warnings": [],
        "source_files": [str(path)],
        "artifact_file_id": source_file_id,
        "artifacts": outputs,
    }


def describe_plot(path: Path, *, source_file_id: str | None = None) -> dict[str, Any]:
    suffix = path.suffix.lower()
    size_bytes = int(path.stat().st_size)
    summary_text = f"Plot/image artifact `{path.name}` ({suffix or 'unknown format'})."
    key_stats = {"size_bytes": size_bytes, "suffix": suffix}
    outputs: list[dict[str, Any]] = []
    if suffix in {".png", ".jpg", ".jpeg", ".svg"}:
        if source_file_id:
            mime_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".svg": "image/svg+xml",
            }[suffix]
            outputs.append(
                _make_output(
                    output_id=f"{path.name}-plot",
                    kind="plot",
                    mime_type=mime_type,
                    storage="file",
                    title=path.name,
                    file_id=source_file_id,
                    metadata={"source_file_id": source_file_id},
                )
            )
        elif size_bytes <= _MAX_INLINE_IMAGE_BYTES:
            mime_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".svg": "image/svg+xml",
            }[suffix]
            outputs.append(
                _make_output(
                    output_id=f"{path.name}-plot-inline",
                    kind="plot",
                    mime_type=mime_type,
                    storage="inline",
                    title=path.name,
                    data={"base64": base64.b64encode(path.read_bytes()).decode("ascii")},
                    metadata={"source_file_id": source_file_id},
                )
            )
    outputs.append(
        _artifact_card_output(
            path=path,
            source_file_id=source_file_id,
            summary_text=summary_text,
            key_stats=key_stats,
        )
    )
    return {
        "summary_text": summary_text,
        "key_stats": key_stats,
        "warnings": [],
        "source_files": [str(path)],
        "artifact_file_id": source_file_id,
        "artifacts": outputs,
    }
