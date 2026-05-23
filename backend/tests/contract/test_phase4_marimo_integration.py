from __future__ import annotations

import os
import socket
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import InMemoryStores, StubMarimoService, build_service_container


def _port_is_open(host: str, port: int, *, timeout_s: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _wait_for_port(host: str, port: int, *, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _port_is_open(host, port, timeout_s=0.2):
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def ensure_local_port_2718(tmp_path: Path, monkeypatch):
    host = "127.0.0.1"
    port = 2718
    if _port_is_open(host, port):
        # Respect an existing listener and avoid interfering with it.
        yield
        return

    # Provide a minimal HTTP listener so marimo readiness checks can succeed.
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", host],
        cwd=str(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        if _wait_for_port(host, port, timeout_s=5.0):
            yield
            return

        # Some restricted environments disallow local listeners; preserve test
        # intent by bypassing only readiness probing in that case.
        monkeypatch.setattr(
            "backend.api.dependencies.MarimoService._wait_until_ready",
            lambda self, **kwargs: None,
        )
        yield
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3.0)


def _reset_services(monkeypatch, tmp_path: Path, *, require_token: bool = False) -> None:
    import backend.api.dependencies as dependencies_module

    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv(
        "LUNAR_ANALYST_REQUIRE_SESSION_TOKEN",
        "1" if require_token else "0",
    )
    dependencies_module.SERVICES = build_service_container()


def _copy_test_tif(tmp_path: Path, name: str = "input.tif") -> Path:
    source = Path("test_data/test_hillshade_viper.tif").resolve()
    target = tmp_path / name
    shutil.copy2(source, target)
    return target


def test_phase4_notebook_session_auth_and_generate_register_render_loop(
    monkeypatch, tmp_path: Path
) -> None:
    _reset_services(monkeypatch, tmp_path, require_token=True)
    client = TestClient(create_app())
    source_tif = _copy_test_tif(tmp_path)

    unauthorized = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase4a", "name": "Phase4 A", "owner": "tester"},
    )
    assert unauthorized.status_code == 401
    assert unauthorized.json()["code"] == "unauthorized"

    session = client.post("/api/v1/notebook/sessions", json={"client_name": "phase4-test"})
    assert session.status_code == 200
    token = session.json()["api_token"]
    headers = {"x-lunar-session-token": token}

    scenario = client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={"scenario_root": "phase4a", "name": "Phase4 A", "owner": "tester"},
    )
    assert scenario.status_code == 200
    scenario_id = scenario.json()["scenario_id"]

    imported = client.post(
        f"/api/v1/scenarios/{scenario_id}/imports/geotiff",
        headers=headers,
        json={"source_path": str(source_tif), "bypass_cog": True},
    )
    assert imported.status_code == 200
    product_id = imported.json()["product_id"]

    files = client.get(f"/api/v1/products/{product_id}/files", headers=headers)
    assert files.status_code == 200
    file_id = files.json()[-1]["file_id"]

    layer = client.post(
        "/api/v1/layers",
        headers=headers,
        json={
            "scenario_id": scenario_id,
            "product_id": product_id,
            "title": "Notebook Layer",
            "visible": True,
            "opacity": 1.0,
            "z_index": 30,
            "render_mode": "raster",
            "source_file_id": file_id,
            "style": {"colormap": "gray"},
        },
    )
    assert layer.status_code == 200

    layers = client.get(f"/api/v1/scenarios/{scenario_id}/layers", headers=headers)
    assert layers.status_code == 200
    assert any(entry["title"] == "Notebook Layer" for entry in layers.json())


def test_phase4_notebook_events_reconnect(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path, require_token=True)
    client = TestClient(create_app())
    source_tif = _copy_test_tif(tmp_path, "events_input.tif")
    token = client.post("/api/v1/notebook/sessions", json={"client_name": "phase4-events"}).json()[
        "api_token"
    ]
    headers = {"x-lunar-session-token": token}

    scenario = client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={"scenario_root": "phase4ws", "name": "Phase4 WS", "owner": "tester"},
    ).json()
    scenario_id = scenario["scenario_id"]
    imported = client.post(
        f"/api/v1/scenarios/{scenario_id}/imports/geotiff",
        headers=headers,
        json={"source_path": str(source_tif), "bypass_cog": True},
    ).json()
    product_id = imported["product_id"]
    file_id = client.get(f"/api/v1/products/{product_id}/files", headers=headers).json()[-1]["file_id"]

    with client.websocket_connect(f"/api/v1/notebook/events?token={token}") as ws:
        first_layer = client.post(
            "/api/v1/layers",
            headers=headers,
            json={
                "scenario_id": scenario_id,
                "product_id": product_id,
                "title": "First Notebook WS Layer",
                "visible": True,
                "opacity": 0.8,
                "z_index": 31,
                "render_mode": "raster",
                "source_file_id": file_id,
                "style": {"colormap": "gray"},
            },
        ).json()
        seen = False
        for _ in range(8):
            event = ws.receive_json()
            if event["event"] == "layer_added" and event["data"]["layer_id"] == first_layer["layer_id"]:
                seen = True
                break
        assert seen

    with client.websocket_connect(f"/api/v1/notebook/events?token={token}") as ws:
        second_layer = client.post(
            "/api/v1/layers",
            headers=headers,
            json={
                "scenario_id": scenario_id,
                "product_id": product_id,
                "title": "Second Notebook WS Layer",
                "visible": True,
                "opacity": 0.6,
                "z_index": 32,
                "render_mode": "raster",
                "source_file_id": file_id,
                "style": {"colormap": "viridis"},
            },
        ).json()
        seen = False
        for _ in range(12):
            event = ws.receive_json()
            if event["event"] == "layer_added" and event["data"]["layer_id"] == second_layer["layer_id"]:
                seen = True
                break
        assert seen


def test_phase4_marimo_launch_attach_and_stop(
    monkeypatch,
    tmp_path: Path,
    ensure_local_port_2718,
) -> None:
    _reset_services(monkeypatch, tmp_path, require_token=False)
    client = TestClient(create_app())

    attached = client.post(
        "/api/v1/marimo/launch",
        json={"attach_url": "http://127.0.0.1:2718"},
    )
    assert attached.status_code == 200
    assert attached.json()["status"] == "attached"
    assert attached.json()["mode"] == "attach"

    launched = client.post(
        "/api/v1/marimo/launch",
        json={
            "command": [sys.executable, "-c", "import time; time.sleep(30)"],
            "cwd": str(tmp_path),
        },
    )
    assert launched.status_code == 200
    payload = launched.json()
    assert payload["status"] == "running"
    assert payload["mode"] == "launch"
    assert isinstance(payload["pid"], int)

    status = client.get("/api/v1/marimo/status")
    assert status.status_code == 200
    assert status.json()["status"] in {"running", "stopped"}

    stopped = client.post("/api/v1/marimo/stop")
    assert stopped.status_code == 200
    assert isinstance(stopped.json()["stopped"], bool)


def test_phase4_marimo_launch_by_scenario_conflict_and_restart(
    monkeypatch,
    tmp_path: Path,
    ensure_local_port_2718,
) -> None:
    _reset_services(monkeypatch, tmp_path, require_token=False)
    client = TestClient(create_app())

    scenario_a = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase4_m_a", "name": "Phase4 Marimo A", "owner": "tester"},
    ).json()
    scenario_b = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase4_m_b", "name": "Phase4 Marimo B", "owner": "tester"},
    ).json()

    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    launched_a = client.post(
        "/api/v1/marimo/launch",
        json={"command": command, "scenario_id": scenario_a["scenario_id"]},
    )
    assert launched_a.status_code == 200
    assert launched_a.json()["status"] == "running"
    assert launched_a.json()["cwd"] == str(Path(scenario_a["directory"]).resolve())

    conflict = client.post(
        "/api/v1/marimo/launch",
        json={"command": command, "scenario_id": scenario_b["scenario_id"]},
    )
    assert conflict.status_code == 409
    payload = conflict.json()
    assert payload["code"] == "marimo_launch_conflict"
    assert payload["details"]["current_cwd"] == str(Path(scenario_a["directory"]).resolve())
    assert payload["details"]["requested_cwd"] == str(Path(scenario_b["directory"]).resolve())

    relaunched = client.post(
        "/api/v1/marimo/launch",
        json={
            "command": command,
            "scenario_id": scenario_b["scenario_id"],
            "restart_if_running": True,
        },
    )
    assert relaunched.status_code == 200
    assert relaunched.json()["status"] == "running"
    assert relaunched.json()["cwd"] == str(Path(scenario_b["directory"]).resolve())
    client.post("/api/v1/marimo/stop")


def test_phase4_marimo_open_notebook_creates_unique_file_and_returns_direct_url(
    monkeypatch, tmp_path: Path
) -> None:
    _reset_services(monkeypatch, tmp_path, require_token=False)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase4_notebook", "name": "Phase4 Notebook", "owner": "tester"},
    ).json()

    captured: dict[str, object] = {}

    class _FakePopen:
        def __init__(self, command, **kwargs):  # noqa: ANN001
            captured["command"] = command
            captured["cwd"] = kwargs.get("cwd")
            captured["env"] = kwargs.get("env")
            self.pid = 4242

        def poll(self):  # noqa: ANN201
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None):  # noqa: ANN001, ANN201
            _ = timeout
            return 0

        def kill(self) -> None:
            return None

    monkeypatch.setattr("backend.api.dependencies.subprocess.Popen", _FakePopen)
    monkeypatch.setattr(
        "backend.api.dependencies.MarimoService._wait_until_ready",
        lambda self, **kwargs: None,
    )

    opened = client.post(
        "/api/v1/marimo/open-notebook",
        json={"scenario_id": scenario["scenario_id"], "create_new": True},
    )
    assert opened.status_code == 200
    payload = opened.json()
    assert payload["status"] == "ready"
    assert payload["created_new"] is True
    assert payload["relative_path"].startswith("notebook_")
    assert payload["relative_path"].endswith(".mo.py")
    assert "file=" in payload["file_url"]
    notebook_path = Path(payload["absolute_file_path"])
    assert notebook_path.exists()
    assert "import marimo" in notebook_path.read_text(encoding="utf-8")
    assert captured["cwd"] == str(Path(scenario["directory"]).resolve())
    client.post("/api/v1/marimo/stop")


def test_phase4_marimo_open_notebook_rejects_non_marimo_python_file(
    monkeypatch, tmp_path: Path
) -> None:
    _reset_services(monkeypatch, tmp_path, require_token=False)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase4_notebook_invalid", "name": "Phase4 Notebook Invalid", "owner": "tester"},
    ).json()
    script_path = Path(scenario["directory"]) / "analysis.py"
    script_path.write_text("print('plain python script')\n", encoding="utf-8")

    opened = client.post(
        "/api/v1/marimo/open-notebook",
        json={"scenario_id": scenario["scenario_id"], "relative_path": "analysis.py"},
    )
    assert opened.status_code == 422
    payload = opened.json()
    assert payload["code"] == "invalid_notebook_target"
    assert "not a Marimo notebook" in payload["message"]


def test_phase4_marimo_launch_rejects_scenario_rehome_when_attached(
    monkeypatch, tmp_path: Path
) -> None:
    _reset_services(monkeypatch, tmp_path, require_token=False)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase4_attach", "name": "Phase4 Attach", "owner": "tester"},
    ).json()

    attached = client.post("/api/v1/marimo/launch", json={"attach_url": "http://127.0.0.1:2718"})
    assert attached.status_code == 200
    assert attached.json()["status"] == "attached"

    conflict = client.post(
        "/api/v1/marimo/launch",
        json={"scenario_id": scenario["scenario_id"]},
    )
    assert conflict.status_code == 409
    payload = conflict.json()
    assert payload["code"] == "marimo_launch_conflict"
    assert payload["details"]["mode"] == "attach"
    client.post("/api/v1/marimo/stop")


def test_phase4_marimo_launch_injects_repo_and_moonlayers_pythonpath(
    monkeypatch, tmp_path: Path
) -> None:
    _reset_services(monkeypatch, tmp_path, require_token=False)
    client = TestClient(create_app())
    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase4_env", "name": "Phase4 Env", "owner": "tester"},
    ).json()

    captured: dict[str, object] = {}

    class _FakePopen:
        def __init__(self, command, **kwargs):  # noqa: ANN001
            captured["command"] = command
            captured["cwd"] = kwargs.get("cwd")
            captured["env"] = kwargs.get("env")
            self.pid = 31337

        def poll(self):  # noqa: ANN201
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout=None):  # noqa: ANN001, ANN201
            _ = timeout
            return 0

        def kill(self) -> None:
            return None

    monkeypatch.setattr("backend.api.dependencies.subprocess.Popen", _FakePopen)
    monkeypatch.setattr(
        "backend.api.dependencies.MarimoService._wait_until_ready",
        lambda self, **kwargs: None,
    )
    monkeypatch.setenv("PYTHONPATH", "/d/existing/pythonpath")

    launched = client.post(
        "/api/v1/marimo/launch",
        json={
            "command": [sys.executable, "-c", "import time; time.sleep(1)"],
            "scenario_id": scenario["scenario_id"],
        },
    )
    assert launched.status_code == 200
    env = captured["env"]
    assert isinstance(env, dict)
    py_entries = str(env["PYTHONPATH"]).split(os.pathsep)
    assert py_entries[0] == str(Path(__file__).resolve().parents[3])
    assert py_entries[1] == str((Path(__file__).resolve().parents[3] / "moonlayers_pkg").resolve())
    assert Path(py_entries[-1]).as_posix().endswith("/existing/pythonpath")

    stopped = client.post("/api/v1/marimo/stop")
    assert stopped.status_code == 200


def test_phase4_marimo_default_command_can_disable_token_auth(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "lunar_analyst.toml"
    config_path.write_text(
        """
[backend.marimo]
use_token_auth = false
python_executable = "/e/projects/env_311/Scripts/python.exe"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))
    stores = InMemoryStores(workspace_root=tmp_path / "workspace", catalog_db_path=tmp_path / "catalog.db")
    service = StubMarimoService(stores)
    command = service._default_command()
    assert "--no-token" in command
    assert "--token" not in command


def test_phase4_map_zoom_command_emits_event(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path, require_token=False)
    client = TestClient(create_app())
    source_tif = _copy_test_tif(tmp_path, "zoom_input.tif")

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase4_zoom", "name": "Phase4 Zoom", "owner": "tester"},
    ).json()
    scenario_id = scenario["scenario_id"]

    imported = client.post(
        f"/api/v1/scenarios/{scenario_id}/imports/geotiff",
        json={"source_path": str(source_tif), "bypass_cog": True},
    ).json()
    product_id = imported["product_id"]
    file_id = client.get(f"/api/v1/products/{product_id}/files").json()[-1]["file_id"]

    zoomed = client.post(
        f"/api/v1/scenarios/{scenario_id}/map-commands/zoom-to-file",
        json={"file_id": file_id, "padding_px": 40, "max_zoom": 11.5},
    )
    assert zoomed.status_code == 200
    assert zoomed.json() == {"status": "queued", "event": "map_zoom_requested"}

    import backend.api.dependencies as dependencies_module

    ws_payload = dependencies_module.get_services().stores.ws_events[-1]
    assert ws_payload["event"] == "map_zoom_requested"
    assert ws_payload["scenario_id"] == scenario_id
    assert ws_payload["data"]["file_id"] == file_id
    assert ws_payload["data"]["padding_px"] == 40
    assert ws_payload["data"]["max_zoom"] == 11.5
    assert len(ws_payload["data"]["extent"]) == 4


def test_phase4_map_zoom_command_rejects_cross_scenario_file(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path, require_token=False)
    client = TestClient(create_app())
    source_tif = _copy_test_tif(tmp_path, "zoom_cross_input.tif")

    scenario_a = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase4_zoom_a", "name": "Phase4 Zoom A", "owner": "tester"},
    ).json()
    scenario_b = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase4_zoom_b", "name": "Phase4 Zoom B", "owner": "tester"},
    ).json()

    imported = client.post(
        f"/api/v1/scenarios/{scenario_a['scenario_id']}/imports/geotiff",
        json={"source_path": str(source_tif), "bypass_cog": True},
    ).json()
    file_id = client.get(f"/api/v1/products/{imported['product_id']}/files").json()[-1]["file_id"]

    rejected = client.post(
        f"/api/v1/scenarios/{scenario_b['scenario_id']}/map-commands/zoom-to-file",
        json={"file_id": file_id},
    )
    assert rejected.status_code == 404
    assert rejected.json()["code"] == "file_not_found_in_scenario"
