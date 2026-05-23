from __future__ import annotations

from pathlib import Path

from lightmap_from_horizons import generate_lightmap_geotiff


def main() -> None:
    dem_path = Path("/d/datasets/viper_v71_2024_medium/other/dem.tif")
    horizon_dir = Path("/d/projects/new_horizon/output_horizons/")
    output_tif_path = Path("/d/projects/new_horizon/python_test_image.tif")

    # ISO-8601 UTC timestamp (the function accepts this string format).
    timestamp_utc = "2012-12-02T10:51:27Z"

    out = generate_lightmap_geotiff(
        dem_path=dem_path,
        horizon_dir=horizon_dir,
        output_tif_path=output_tif_path,
        timestamp_utc=timestamp_utc,
    )
    print(f"Lightmap written: {out}")


if __name__ == "__main__":
    main()
