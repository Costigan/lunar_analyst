import marimo

__generated_with = "0.11.0"
app = marimo.App(width="medium")


@app.cell
def __():
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    import rasterio

    DEM_TIFF_PATHS = [
        "/d/viper/maps/gsfc/Haworth/Haworth_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/mosaic_81/ldec_87s_5mpp.tif",
        "/d/viper/maps/gsfc/other/DM1_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/other/DM2_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/other/NPA_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/other/NPB_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/other/NPC_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/other/NPD_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/other/Site06_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/other/Site23_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/other/Site42_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/Shoemaker/shoemaker_dem_3968x3968.tif",
        "/d/viper/maps/gsfc/site_01/Site01_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/site023/Site23_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/site06/site06_dem.tif",
        "/d/viper/maps/gsfc/site11/Site11_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/site42/Site42_final_adj_5mpp_surf.tif",
        "/d/viper/maps/gsfc/SL2/SL2_final_adj_5mpp_surf.tif",
        "/d/viper/maps/viper_v71/viper_sfs_dem.tif",
    ]
    OUTPUT_GEOJSON_PATH = Path(__file__).resolve().with_name("dem_bounding_boxes.geojson")
    return DEM_TIFF_PATHS, OUTPUT_GEOJSON_PATH, Path, datetime, json, rasterio, timezone


@app.cell
def __(DEM_TIFF_PATHS, Path, rasterio):
    features = []
    missing_files = []
    open_errors = []

    for tif_text in DEM_TIFF_PATHS:
        tif_path = Path(tif_text)
        if not tif_path.exists():
            missing_files.append(str(tif_path))
            continue

        try:
            with rasterio.open(tif_path) as ds:
                left, bottom, right, top = ds.bounds
                crs_text = ds.crs.to_string() if ds.crs is not None else None

                feature = {
                    "type": "Feature",
                    "properties": {
                        "path": str(tif_path),
                        "name": tif_path.name,
                        "crs": crs_text,
                        "width": ds.width,
                        "height": ds.height,
                        "bounds": [left, bottom, right, top],
                    },
                    "bbox": [left, bottom, right, top],
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [left, bottom],
                                [right, bottom],
                                [right, top],
                                [left, top],
                                [left, bottom],
                            ]
                        ],
                    },
                }
                features.append(feature)
        except Exception as exc:
            open_errors.append({"path": str(tif_path), "error": str(exc)})

    return features, missing_files, open_errors


@app.cell
def __(OUTPUT_GEOJSON_PATH, datetime, features, json, open_errors, timezone):
    OUTPUT_GEOJSON_PATH.parent.mkdir(parents=True, exist_ok=True)

    collection = {
        "type": "FeatureCollection",
        "name": "dem_bounding_boxes",
        "metadata": {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "feature_count": len(features),
            "coordinate_note": "Polygon coordinates are in each source raster's native CRS.",
            "open_errors": open_errors,
        },
        "features": features,
    }

    OUTPUT_GEOJSON_PATH.write_text(json.dumps(collection, indent=2), encoding="utf-8")

    result = {
        "output_geojson": str(OUTPUT_GEOJSON_PATH),
        "features_written": len(features),
        "open_errors": len(open_errors),
    }
    print(result)
    return collection, result


@app.cell
def __(missing_files):
    if not missing_files:
        print("All DEM files were found.")
    else:
        print("Missing DEM files (not included in output):")
        for path in missing_files:
            print(f"  - {path}")
    return


if __name__ == "__main__":
    app.run()
