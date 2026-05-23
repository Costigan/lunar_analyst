from __future__ import annotations

from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.transform import from_origin

from backend.api.routers.lunar_analyst import ESRI_103878_WKT
from backend.services.raster_delivery import ensure_map_display_raster
from backend.worker.gdal_runtime import configure_gdal_runtime, import_rasterio


UNNAMED_SOUTH_POLAR_WKT = """PROJCRS["unnamed",
    BASEGEOGCRS["unnamed ellipse",
        DATUM["unknown",
            ELLIPSOID["unnamed",1737400,0,
                LENGTHUNIT["metre",1,
                    ID["EPSG",9001]]]],
        PRIMEM["Greenwich",0,
            ANGLEUNIT["degree",0.0174532925199433,
                ID["EPSG",9122]]]],
    CONVERSION["Polar Stereographic (variant A)",
        METHOD["Polar Stereographic (variant A)",
            ID["EPSG",9810]],
        PARAMETER["Latitude of natural origin",-90,
            ANGLEUNIT["degree",0.0174532925199433],
            ID["EPSG",8801]],
        PARAMETER["Longitude of natural origin",0,
            ANGLEUNIT["degree",0.0174532925199433],
            ID["EPSG",8802]],
        PARAMETER["Scale factor at natural origin",1,
            SCALEUNIT["unity",1],
            ID["EPSG",8805]],
        PARAMETER["False easting",0,
            LENGTHUNIT["metre",1],
            ID["EPSG",8806]],
        PARAMETER["False northing",0,
            LENGTHUNIT["metre",1],
            ID["EPSG",8807]]],
    CS[Cartesian,2],
        AXIS["(E)",north,
            MERIDIAN[90,
                ANGLEUNIT["degree",0.0174532925199433,
                    ID["EPSG",9122]]],
            ORDER[1],
            LENGTHUNIT["metre",1]],
        AXIS["(N)",north,
            MERIDIAN[0,
                ANGLEUNIT["degree",0.0174532925199433,
                    ID["EPSG",9122]]],
            ORDER[2],
            LENGTHUNIT["metre",1]]]"""

NON_EQUIVALENT_SOUTH_POLAR_PROJ4 = (
    "+proj=stere +lat_0=-90 +lon_0=12 +k=1 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs"
)


def _write_raster(
    path: Path,
    data: np.ndarray,
    *,
    crs_wkt: str,
    transform,
    nodata: float | None,
) -> None:
    rasterio = import_rasterio()
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=int(data.shape[1]),
        height=int(data.shape[0]),
        count=1,
        dtype=str(data.dtype),
        crs=crs_wkt,
        transform=transform,
        nodata=nodata,
        ) as ds:
        ds.write(data, 1)


def _display_alpha_band(ds) -> int | None:
    raw = str(ds.tags().get("LUNAR_DISPLAY_ALPHA_BAND", "")).strip()
    if not raw:
        return None
    return int(raw)


def test_display_derivative_clears_nodata_when_reprojected_output_is_fully_valid(tmp_path: Path) -> None:
    configure_gdal_runtime()
    data = np.arange(64, dtype=np.float32).reshape(8, 8)
    source = tmp_path / "slope_equivalent.tif"
    _write_raster(
        source,
        data,
        crs_wkt=UNNAMED_SOUTH_POLAR_WKT,
        transform=from_origin(-20.0, 20.0, 5.0, 5.0),
        nodata=-9999.0,
    )

    out_path = ensure_map_display_raster(
        source_path=source,
        scenario_root_dir=tmp_path / "scenario_root",
        scenario_id="scn_test",
        kind="raster",
        product_id="prd_test",
        source_file_id="fil_test",
        target_crs_wkt=ESRI_103878_WKT,
        target_crs_label="ESRI:103878",
        resampling="nearest",
    )

    assert out_path != source
    rasterio = import_rasterio()
    with rasterio.open(out_path) as ds:
        assert ds.nodata == -9999.0
        assert ds.count == 1
        assert _display_alpha_band(ds) is None
        assert ds.is_tiled is True
        assert len(ds.overviews(1)) == 0
        assert np.all(ds.read_masks(1) == 255)


def test_display_derivative_preserves_nodata_for_internal_invalid_pixels(tmp_path: Path) -> None:
    configure_gdal_runtime()
    data = np.arange(64, dtype=np.float32).reshape(8, 8)
    data[3, 4] = np.float32(-9999.0)
    source = tmp_path / "slope_with_hole.tif"
    _write_raster(
        source,
        data,
        crs_wkt=NON_EQUIVALENT_SOUTH_POLAR_PROJ4,
        transform=from_origin(-20.0, 20.0, 5.0, 5.0),
        nodata=-9999.0,
    )

    out_path = ensure_map_display_raster(
        source_path=source,
        scenario_root_dir=tmp_path / "scenario_root",
        scenario_id="scn_test",
        kind="raster",
        product_id="prd_test",
        source_file_id="fil_test",
        target_crs_wkt=ESRI_103878_WKT,
        target_crs_label="ESRI:103878",
        resampling="nearest",
    )

    rasterio = import_rasterio()
    with rasterio.open(out_path) as ds:
        assert ds.nodata is None
        assert ds.count == 2
        assert _display_alpha_band(ds) == 2
        alpha = ds.read(2)
        assert bool((alpha == 0).any())


def test_display_derivative_preserves_nodata_when_rotated_source_grid_creates_edge_gaps(tmp_path: Path) -> None:
    configure_gdal_runtime()
    data = np.arange(64, dtype=np.float32).reshape(8, 8)
    source = tmp_path / "rotated_slope.tif"
    _write_raster(
        source,
        data,
        crs_wkt=NON_EQUIVALENT_SOUTH_POLAR_PROJ4,
        transform=Affine(5.0, 2.0, -20.0, 2.0, -5.0, 20.0),
        nodata=-9999.0,
    )

    out_path = ensure_map_display_raster(
        source_path=source,
        scenario_root_dir=tmp_path / "scenario_root",
        scenario_id="scn_test",
        kind="raster",
        product_id="prd_test",
        source_file_id="fil_test",
        target_crs_wkt=ESRI_103878_WKT,
        target_crs_label="ESRI:103878",
        resampling="nearest",
    )

    rasterio = import_rasterio()
    with rasterio.open(out_path) as ds:
        assert ds.nodata is None
        assert ds.count == 2
        assert _display_alpha_band(ds) == 2
        alpha = ds.read(2)
        assert bool((alpha == 0).any())


def test_display_derivative_adds_alpha_for_byte_zero_collision_case(tmp_path: Path) -> None:
    configure_gdal_runtime()
    data = np.array(
        [
            [0, 0, 64, 128],
            [0, 32, 96, 160],
            [16, 48, 80, 192],
            [8, 24, 72, 255],
        ],
        dtype=np.uint8,
    )
    source = tmp_path / "hillshade_byte_zero.tif"
    _write_raster(
        source,
        data,
        crs_wkt=NON_EQUIVALENT_SOUTH_POLAR_PROJ4,
        transform=Affine(5.0, 2.0, -20.0, 2.0, -5.0, 20.0),
        nodata=None,
    )

    out_path = ensure_map_display_raster(
        source_path=source,
        scenario_root_dir=tmp_path / "scenario_root",
        scenario_id="scn_test",
        kind="raster",
        product_id="prd_test",
        source_file_id="fil_test",
        target_crs_wkt=ESRI_103878_WKT,
        target_crs_label="ESRI:103878",
        resampling="nearest",
    )

    rasterio = import_rasterio()
    with rasterio.open(out_path) as ds:
        assert ds.nodata is None
        assert ds.count == 2
        assert ds.dtypes[0] == "uint8"
        assert _display_alpha_band(ds) == 2
        band1 = ds.read(1)
        alpha = ds.read(2)
        assert bool(((band1 == 0) & (alpha == 255)).any())
        assert bool((alpha == 0).any())
