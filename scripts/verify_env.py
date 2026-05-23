from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _version_text(module: object) -> str:
    for attr in ("__version__", "version"):
        value = getattr(module, attr, None)
        if value:
            return str(value)
    return "<unknown>"


def _require_python_311() -> None:
    major, minor = sys.version_info[:2]
    if (major, minor) != (3, 11):
        raise RuntimeError(
            f"Lunar Analyst requires Python 3.11.x; found {sys.version.split()[0]}"
        )


def _import_required_modules() -> None:
    required = [
        "httpx",
        "jsonschema",
        "fastapi",
        "anywidget",
        "ipywidgets",
        "marimo",
        "matplotlib",
        "numba",
        "numpy",
        "openpyxl",
        "pydantic",
        "PyPDF2",
        "pypdf",
        "pyproj",
        "pythonnet",
        "rasterio",
        "requests",
        "scipy",
        "spacy",
        "traitlets",
        "uvicorn",
        "websockets",
        "yaml",
    ]
    for name in required:
        module = importlib.import_module(name)
        print(f"[ok] import {name} {_version_text(module)}")


def _verify_spacy_model() -> None:
    spacy = importlib.import_module("spacy")
    model_name = "en_core_web_sm"
    nlp = spacy.load(model_name, disable=["ner"])
    doc = nlp("Write a notebook and run a hillshade job.")
    if not list(doc.sents):
        raise RuntimeError("spaCy model loaded but produced no sentence boundaries.")
    print(f"[ok] spaCy model {model_name}")


def _verify_gdal_array() -> None:
    gdal = importlib.import_module("osgeo.gdal")
    importlib.import_module("osgeo.gdal_array")
    numpy = importlib.import_module("numpy")

    gdal.UseExceptions()
    driver = gdal.GetDriverByName("MEM")
    if driver is None:
        raise RuntimeError("GDAL MEM driver is unavailable.")
    ds = driver.Create("", 2, 2, 1, gdal.GDT_Float32)
    band = ds.GetRasterBand(1)
    band.WriteArray(numpy.array([[1.0, 2.0], [3.0, 4.0]], dtype=numpy.float32))
    roundtrip = band.ReadAsArray()
    if roundtrip.shape != (2, 2):
        raise RuntimeError(f"Unexpected GDAL array shape: {roundtrip.shape!r}")
    if float(roundtrip[1, 1]) != 4.0:
        raise RuntimeError("GDAL array roundtrip failed.")
    print(f"[ok] GDAL {gdal.VersionInfo()} with gdal_array")


def _verify_moonlayers() -> None:
    moonlayers = importlib.import_module("moonlayers")
    package_root = Path(moonlayers.__file__).resolve().parent
    print(f"[ok] moonlayers {package_root}")


def main() -> int:
    _require_python_311()
    print(f"[ok] python {sys.version.split()[0]}")
    _import_required_modules()
    _verify_spacy_model()
    _verify_gdal_array()
    _verify_moonlayers()
    print("[ok] environment verification complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
