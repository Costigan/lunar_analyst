from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def _ensure_repo_root_on_path() -> None:
    # Allows running this script directly from moonlayers_pkg without install -e .
    repo_root = Path(__file__).resolve().parents[2]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def main() -> int:
    _ensure_repo_root_on_path()

    from backend.worker.native_bootstrap import bootstrap_pythonnet, import_moonlib

    bootstrap_pythonnet(force=True)
    moonlib = import_moonlib()
    bridge = moonlib.MoonlibBridge()

    with tempfile.TemporaryDirectory(prefix="moonlib_bridge_demo_") as tmp:
        tmp_dir = Path(tmp)
        dem_path = tmp_dir / "input_dem.tif"
        hillshade_path = tmp_dir / "output_hillshade.tif"

        # The current bridge placeholder copies input to output.
        dem_path.write_bytes(b"demo-geotiff-placeholder")
        bridge.GenerateHillshade(str(dem_path), str(hillshade_path))

        print(f"MoonlibBridge loaded from: {moonlib.__file__}")
        print(f"Input DEM: {dem_path}")
        print(f"Output hillshade: {hillshade_path}")
        print(f"Output exists: {hillshade_path.exists()}")
        print(f"Output size: {hillshade_path.stat().st_size} bytes")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
