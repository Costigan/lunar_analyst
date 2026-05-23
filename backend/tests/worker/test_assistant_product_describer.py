from __future__ import annotations

from pathlib import Path

import numpy as np

from backend.services.assistant.product_describer import (
    describe_geotiff,
    describe_geotiff_stats,
    describe_plot,
    describe_table,
)
from backend.worker.gdal_runtime import import_rasterio


def test_describe_table_returns_typed_preview_outputs(tmp_path: Path) -> None:
    table_path = tmp_path / "stats.csv"
    table_path.write_text("crater_id,slope_deg\nA1,12.4\nA2,8.9\n", encoding="utf-8")

    result = describe_table(table_path, source_file_id="file_table")

    assert result["summary_text"].startswith("Tabular file `stats.csv`")
    assert result["artifact_file_id"] == "file_table"
    assert len(result["artifacts"]) == 2
    table_output = result["artifacts"][0]
    assert table_output["kind"] == "table"
    assert table_output["mime_type"] == "application/vnd.lunar-analyst.table+json"
    assert table_output["data"]["source_file_id"] == "file_table"
    assert table_output["data"]["rows"][0]["crater_id"] == "A1"


def test_describe_plot_uses_file_backed_output_when_file_id_present(tmp_path: Path) -> None:
    plot_path = tmp_path / "plot.svg"
    plot_path.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")

    result = describe_plot(plot_path, source_file_id="file_plot")

    assert result["artifact_file_id"] == "file_plot"
    assert result["artifacts"][0]["kind"] == "plot"
    assert result["artifacts"][0]["storage"] == "file"
    assert result["artifacts"][0]["file_id"] == "file_plot"


def test_describe_geotiff_returns_metadata_only_artifact_card(tmp_path: Path) -> None:
    tif_path = tmp_path / "hillshade.tif"
    rasterio = import_rasterio()
    data = (np.arange(64, dtype=np.uint8).reshape(8, 8) * 4).astype(np.uint8)
    with rasterio.open(
        tif_path,
        "w",
        driver="GTiff",
        width=8,
        height=8,
        count=1,
        dtype="uint8",
    ) as ds:
        ds.write(data, 1)

    result = describe_geotiff(tif_path, source_file_id="file_tif")

    assert result["artifact_file_id"] == "file_tif"
    assert len(result["artifacts"]) == 1
    assert result["artifacts"][0]["kind"] == "artifact_card"
    assert result["key_stats"]["width"] == 8
    assert result["key_stats"]["height"] == 8


def test_describe_geotiff_stats_returns_numeric_summary(tmp_path: Path) -> None:
    tif_path = tmp_path / "slope.tif"
    rasterio = import_rasterio()
    data = np.arange(16, dtype=np.float32).reshape(4, 4)
    with rasterio.open(
        tif_path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="float32",
    ) as ds:
        ds.write(data, 1)

    result = describe_geotiff_stats(tif_path, source_file_id="file_stats")

    assert result["artifact_file_id"] == "file_stats"
    assert result["summary_text"].startswith("GeoTIFF `slope.tif` statistics ready")
    assert result["key_stats"]["valid_count"] == 16
    assert result["key_stats"]["total_count"] == 16
    assert result["key_stats"]["min"] == 0.0
    assert result["key_stats"]["max"] == 15.0
    assert "p50" in result["key_stats"]["percentiles"]
    assert result["artifacts"] == []
