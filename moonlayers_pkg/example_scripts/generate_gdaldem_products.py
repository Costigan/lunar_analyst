from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any


def _ensure_repo_root_on_path() -> None:
    # Allows running this script directly from moonlayers_pkg without install -e .
    repo_root = Path(__file__).resolve().parents[2]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def _safe_relpath(raw: str) -> str:
    rel = str(raw).strip().replace("\\", "/")
    if not rel:
        return "dem.tif"
    if rel.startswith("/") or rel.startswith("../") or "/../" in f"/{rel}/":
        raise ValueError(f"Invalid primary_dem_path from scenario metadata: {raw}")
    return rel


def _resolve_primary_dem_path(scenario_root: Path, scenario_id: str | None) -> Path:
    db_path = scenario_root / "scenario.db"
    if not db_path.exists():
        fallback = scenario_root / "dem.tif"
        if fallback.exists():
            return fallback
        raise FileNotFoundError(f"scenario.db not found and fallback DEM missing: {fallback}")

    query_with_id = "SELECT primary_dem_path FROM scenarios WHERE scenario_id = ? LIMIT 1"
    query_any = "SELECT primary_dem_path FROM scenarios LIMIT 1"
    dem_rel: str | None = None
    with sqlite3.connect(str(db_path)) as conn:
        if scenario_id:
            row = conn.execute(query_with_id, (scenario_id,)).fetchone()
            if row is not None and row[0]:
                dem_rel = str(row[0])
        if dem_rel is None:
            row = conn.execute(query_any).fetchone()
            if row is not None and row[0]:
                dem_rel = str(row[0])

    if dem_rel is None:
        dem_rel = "dem.tif"
    rel = _safe_relpath(dem_rel)
    dem_path = (scenario_root / rel).resolve()
    if not dem_path.exists():
        raise FileNotFoundError(f"Primary DEM does not exist: {dem_path}")
    return dem_path


def _delete_if_exists(path: Path) -> None:
    if path.exists() and path.is_file():
        path.unlink()
    aux = Path(str(path) + ".aux.xml")
    if aux.exists() and aux.is_file():
        aux.unlink()


def _run_derivative(
    *,
    gdal: Any,
    dem_path: Path,
    output_path: Path,
    processing: str,
    options: Any,
) -> None:
    _delete_if_exists(output_path)
    ds = gdal.DEMProcessing(str(output_path), str(dem_path), processing, options=options)
    if ds is not None:
        ds = None
    if not output_path.exists():
        raise RuntimeError(f"{processing} output was not created: {output_path}")


def _try_get_runtime_context():
    try:
        from backend.notebook.runtime import get_context

        return get_context()
    except Exception:
        return None


def _maybe_register_output(relative_path: str, subkind: str, dem_rel: str) -> None:
    try:
        from backend.notebook.runtime import register_output

        register_output(
            relative_path=relative_path,
            kind="raster",
            subkind=subkind,
            render_mode="raster",
            metadata={"source_dem": dem_rel},
        )
    except Exception:
        # Script still works when run outside notebook job runtime.
        pass


def main(argv: list[str] | None = None) -> int:
    _ensure_repo_root_on_path()
    from backend.worker.gdal_runtime import configure_gdal_runtime
    from osgeo import gdal

    parser = argparse.ArgumentParser(
        description=(
            "Generate gdaldem-derived GeoTIFF products (hillshade, slope, aspect, tri, tpi, roughness) "
            "for a scenario."
        )
    )
    parser.add_argument("--scenario-id", default="", help="Scenario ID (optional outside runner context).")
    parser.add_argument(
        "--scenario-root-dir",
        default="",
        help="Scenario root directory (optional outside runner context; defaults to current working directory).",
    )
    args = parser.parse_args(argv)

    context = _try_get_runtime_context()
    scenario_id = context.scenario_id if context is not None else str(args.scenario_id or "").strip()
    if context is not None:
        scenario_root = Path(context.scenario_root_dir).resolve()
    elif str(args.scenario_root_dir or "").strip():
        scenario_root = Path(args.scenario_root_dir).expanduser().resolve()
    else:
        scenario_root = Path.cwd().resolve()

    configure_gdal_runtime()
    gdal.UseExceptions()

    dem_path = _resolve_primary_dem_path(scenario_root, scenario_id if scenario_id else None)
    dem_rel = dem_path.relative_to(scenario_root).as_posix()

    common = gdal.DEMProcessingOptions(format="GTiff", computeEdges=True)
    hillshade = gdal.DEMProcessingOptions(
        format="GTiff",
        zFactor=1.0,
        azimuth=315,
        altitude=45,
        computeEdges=True,
    )

    derivatives: list[tuple[str, str, Any]] = [
        ("hillshade", "hillshade.tif", hillshade),
        ("slope", "slope.tif", common),
        ("aspect", "aspect.tif", common),
        ("TRI", "tri.tif", common),
        ("TPI", "tpi.tif", common),
        ("roughness", "roughness.tif", common),
    ]

    print(f"scenario_id={scenario_id or '<unspecified>'}")
    print(f"scenario_root={scenario_root}")
    print(f"primary_dem={dem_path}")

    for processing, out_name, options in derivatives:
        out_path = (scenario_root / out_name).resolve()
        _run_derivative(
            gdal=gdal,
            dem_path=dem_path,
            output_path=out_path,
            processing=processing,
            options=options,
        )
        print(f"Created {processing}: {out_path}")
        _maybe_register_output(out_name, processing.lower(), dem_rel)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
