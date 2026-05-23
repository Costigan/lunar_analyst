from __future__ import annotations

import time
from pathlib import Path

import pytest
import numpy as np
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from backend.api.app import create_app
from backend.api.dependencies import build_service_container
from backend.worker.native_bootstrap import (
    NativeBootstrapError,
    bootstrap_pythonnet,
    import_moonlib,
    reset_bootstrap_cache,
)


def _require_real_moonlib() -> None:
    try:
        bootstrap_pythonnet(force=True)
        moonlib = import_moonlib()

        # Validate CLR GDAL bindings up front. Some environments can load
        # moonlib bridge smoke methods but still lack gdal_wrap at runtime.
        _ = moonlib.MoonlibBridge.GdalSmokeTest()
    except NativeBootstrapError as exc:
        pytest.skip(f"Real moonlib is unavailable in this environment: {exc}")
    except Exception as exc:
        pytest.skip(f"Real moonlib GDAL bindings are unavailable in this environment: {exc}")


def _write_test_dem(path: Path) -> None:
    data = np.array(
        [
            [100.0, 101.0, 102.0],
            [99.0, 100.0, 103.0],
            [98.0, 99.0, 101.0],
        ],
        dtype=np.float32,
    )
    transform = from_origin(0.0, 3.0, 1.0, 1.0)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=3,
        height=3,
        count=1,
        dtype="float32",
        transform=transform,
    ) as ds:
        ds.write(data, 1)


def _wait_for_terminal_job(
    client: TestClient,
    job_id: str,
    *,
    timeout_seconds: float = 20.0,
) -> dict[str, object]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        status = str(payload.get("status", "")).strip().lower()
        if status in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"job did not reach terminal status within timeout: {job_id}")


def test_real_bridge_smoke_add_one() -> None:
    _require_real_moonlib()
    moonlib = import_moonlib()
    output = float(moonlib.BridgeSmoke.AddOne(1.0))
    assert abs(output - 2.0) < 1e-6
    spice_output = int(moonlib.BridgeSmoke.SpiceSmokeTest(1))
    assert spice_output == 2


def test_real_generate_horizons_endpoint(tmp_path: Path) -> None:
    _require_real_moonlib()

    import backend.api.dependencies as dependencies_module

    dependencies_module.SERVICES = build_service_container()

    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem = scenario_root / "dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    _write_test_dem(dem)

    client = TestClient(create_app())
    response = client.post(
        "/api/v1/jobs/generate-horizons",
        json={
            "scenario_id": "real_s2",
            "scenario_root_dir": str(scenario_root),
            "dem_path": str(dem),
            "horizons_dir": str(horizons_dir),
            "overwrite_horizons": True,
            "compress_horizons": False,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"queued", "running", "completed"}
    assert payload["job_type"] == "generate_horizons"
    terminal = _wait_for_terminal_job(client, str(payload["job_id"]))
    assert terminal["status"] == "completed"

    events = client.get(f"/api/v1/jobs/{payload['job_id']}/events").json()
    assert [e["event_name"] for e in events] == [
        "job_queued",
        "job_started",
        "job_progress",
        "job_completed",
    ]
    assert Path(events[-1]["data"]["result"]["horizons_dir"]).exists()


@pytest.fixture(autouse=True)
def _reset_bootstrap_between_tests() -> None:
    reset_bootstrap_cache()
    yield
    reset_bootstrap_cache()
