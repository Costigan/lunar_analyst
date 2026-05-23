from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import backend.worker.gdal_runtime as gdal_runtime_module
from backend.worker.gdal_runtime import ensure_proj_data


def test_ensure_proj_data_finds_proj_db_and_sets_proj_lib(
    monkeypatch, tmp_path: Path
) -> None:
    fake_osgeo_root = tmp_path / "fake_osgeo"
    fake_osgeo_root.mkdir(parents=True, exist_ok=True)
    (fake_osgeo_root / "__init__.py").write_text("", encoding="utf-8")
    proj_dir = fake_osgeo_root / "data"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "proj.db").write_bytes(b"proj-db")

    gdal_mod = types.ModuleType("osgeo.gdal")
    cfg: dict[str, str] = {}

    def _get_config_option(key: str) -> str | None:
        return cfg.get(key)

    def _set_config_option(key: str, value: str) -> None:
        cfg[key] = value

    gdal_mod.GetConfigOption = _get_config_option  # type: ignore[attr-defined]
    gdal_mod.SetConfigOption = _set_config_option  # type: ignore[attr-defined]

    osgeo_mod = types.ModuleType("osgeo")
    osgeo_mod.__file__ = str((fake_osgeo_root / "__init__.py").resolve())
    osgeo_mod.gdal = gdal_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "osgeo", osgeo_mod)
    monkeypatch.setitem(sys.modules, "osgeo.gdal", gdal_mod)
    monkeypatch.delenv("PROJ_LIB", raising=False)
    monkeypatch.delenv("PROJ_DATA", raising=False)

    ensure_proj_data()

    assert os.environ["PROJ_LIB"] == str(proj_dir.resolve())
    assert os.environ["PROJ_DATA"] == str(proj_dir.resolve())
    assert cfg["PROJ_LIB"] == str(proj_dir.resolve())
    assert cfg["PROJ_DATA"] == str(proj_dir.resolve())


def test_ensure_proj_data_finds_proj_db_in_runtime_prefix(
    monkeypatch, tmp_path: Path
) -> None:
    fake_osgeo_root = tmp_path / "fake_osgeo"
    fake_osgeo_root.mkdir(parents=True, exist_ok=True)
    (fake_osgeo_root / "__init__.py").write_text("", encoding="utf-8")

    runtime_prefix = tmp_path / "runtime_prefix"
    proj_dir = runtime_prefix / "share" / "proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "proj.db").write_bytes(b"proj-db")

    gdal_mod = types.ModuleType("osgeo.gdal")
    cfg: dict[str, str] = {}

    def _get_config_option(key: str) -> str | None:
        return cfg.get(key)

    def _set_config_option(key: str, value: str) -> None:
        cfg[key] = value

    gdal_mod.GetConfigOption = _get_config_option  # type: ignore[attr-defined]
    gdal_mod.SetConfigOption = _set_config_option  # type: ignore[attr-defined]

    osgeo_mod = types.ModuleType("osgeo")
    osgeo_mod.__file__ = str((fake_osgeo_root / "__init__.py").resolve())
    osgeo_mod.gdal = gdal_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "osgeo", osgeo_mod)
    monkeypatch.setitem(sys.modules, "osgeo.gdal", gdal_mod)
    monkeypatch.setattr(gdal_runtime_module.sys, "prefix", str(runtime_prefix.resolve()))
    monkeypatch.setattr(gdal_runtime_module.sys, "base_prefix", str(runtime_prefix.resolve()))
    monkeypatch.setattr(gdal_runtime_module.sys, "exec_prefix", str(runtime_prefix.resolve()))
    monkeypatch.setattr(gdal_runtime_module.sys, "base_exec_prefix", str(runtime_prefix.resolve()))
    monkeypatch.delenv("PROJ_LIB", raising=False)
    monkeypatch.delenv("PROJ_DATA", raising=False)

    ensure_proj_data()

    assert os.environ["PROJ_LIB"] == str(proj_dir.resolve())
    assert os.environ["PROJ_DATA"] == str(proj_dir.resolve())
    assert cfg["PROJ_LIB"] == str(proj_dir.resolve())
    assert cfg["PROJ_DATA"] == str(proj_dir.resolve())
