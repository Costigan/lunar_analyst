from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _preload_system_libxml2() -> None:
    """Preload the system libxml2 so that the newer system version is
    loaded before any moonlib-bundled older libxml2 can claim the
    ``libxml2.so.2`` SONAME.  This avoids ``undefined symbol:
    xmlNanoHTTPCleanup`` errors from system libspatialite when the
    moonlib LD_LIBRARY_PATH entries are active."""
    system_paths = [
        "/lib/x86_64-linux-gnu/libxml2.so.2",
        "/usr/lib/x86_64-linux-gnu/libxml2.so.2",
        "/usr/lib/libxml2.so.2",
    ]
    for path in system_paths:
        try:
            ctypes.CDLL(path)
            logger.debug("Preloaded system libxml2: %s", path)
            return
        except OSError:
            continue
    logger.debug("Could not preload system libxml2 (not found at expected paths)")


def import_rasterio() -> Any:
    """Import rasterio lazily to avoid module-import side effects."""
    import rasterio

    return rasterio


def _unique_paths(candidates: list[Path]) -> list[Path]:
    seen: set[str] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        try:
            normalized = candidate.expanduser().resolve()
        except Exception:
            normalized = candidate.expanduser()
        key = os.path.normcase(str(normalized))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


def _iter_runtime_prefixes() -> list[Path]:
    candidates: list[Path] = []
    for raw in [
        os.environ.get("VIRTUAL_ENV"),
        os.environ.get("CONDA_PREFIX"),
        sys.prefix,
        sys.exec_prefix,
    ]:
        if raw:
            candidates.append(Path(str(raw)))
    return _unique_paths(candidates)


def _iter_osgeo_package_roots() -> list[Path]:
    try:
        import osgeo
    except Exception:
        return []
    package_root = Path(osgeo.__file__).resolve().parent
    return _unique_paths([package_root, package_root.parent, package_root.parent.parent])


def _iter_module_data_dirs(module_name: str, relative_dir: str) -> list[Path]:
    try:
        module = __import__(module_name)
    except Exception:
        return []
    module_root = Path(module.__file__).resolve().parent
    return _unique_paths([module_root / relative_dir])


def resolve_proj_data_dir() -> Path | None:
    candidates: list[Path] = []

    for root in _iter_osgeo_package_roots():
        candidates.extend([
            root / "data",
            root / "data" / "proj",
            root / "share" / "proj",
        ])

    for prefix in _iter_runtime_prefixes():
        candidates.extend([
            prefix / "share" / "proj",
            prefix / "Library" / "share" / "proj",
        ])

    candidates.extend(_iter_module_data_dirs("rasterio", "proj_data"))
    candidates.extend(_iter_module_data_dirs("pyogrio", "proj_data"))

    for raw in [os.environ.get("PROJ_LIB"), os.environ.get("PROJ_DATA")]:
        if raw:
            candidates.append(Path(raw))

    candidates.extend([
        Path("/usr/share/proj"),
        Path("/usr/local/share/proj"),
        Path("/opt/homebrew/share/proj"),
    ])

    try:
        from pyproj.datadir import get_data_dir
    except Exception:
        pass
    else:
        resolved = str(get_data_dir() or "").strip()
        if resolved:
            candidates.append(Path(resolved))

    for candidate in _unique_paths(candidates):
        if candidate.joinpath("proj.db").exists():
            return candidate
    return None


def resolve_gdal_data_dir() -> Path | None:
    candidates: list[Path] = []

    raw = os.environ.get("GDAL_DATA")
    if raw:
        candidates.append(Path(raw))

    for root in _iter_osgeo_package_roots():
        candidates.extend([
            root / "data" / "gdal",
            root / "data",
            root / "share" / "gdal",
        ])

    for prefix in _iter_runtime_prefixes():
        candidates.extend([
            prefix / "share" / "gdal",
            prefix / "Library" / "share" / "gdal",
        ])

    candidates.extend(_iter_module_data_dirs("rasterio", "gdal_data"))

    candidates.extend([
        Path("/usr/share/gdal"),
        Path("/usr/local/share/gdal"),
        Path("/opt/homebrew/share/gdal"),
    ])

    for candidate in _unique_paths(candidates):
        if candidate.exists() and any(candidate.iterdir()):
            return candidate
    return None


def ensure_proj_data() -> Path:
    """Best-effort detection of PROJ data to avoid runtime errors."""
    from osgeo import gdal

    proj_dir = resolve_proj_data_dir()
    if proj_dir is None:
        raise RuntimeError(
            "Could not locate proj.db. Set PROJ_LIB to a valid PROJ data directory."
        )

    os.environ["PROJ_LIB"] = str(proj_dir)
    os.environ["PROJ_DATA"] = str(proj_dir)
    gdal.SetConfigOption("PROJ_LIB", str(proj_dir))
    gdal.SetConfigOption("PROJ_DATA", str(proj_dir))
    logger.info("Configured PROJ_LIB=%s", proj_dir)
    return proj_dir


def configure_gdal_runtime() -> None:
    """Initialize GDAL runtime defaults for the backend process."""
    _preload_system_libxml2()
    from osgeo import gdal

    gdal.UseExceptions()
    gdal_data_dir = resolve_gdal_data_dir()
    if gdal_data_dir is not None:
        os.environ["GDAL_DATA"] = str(gdal_data_dir)
        gdal.SetConfigOption("GDAL_DATA", str(gdal_data_dir))
    ensure_proj_data()
