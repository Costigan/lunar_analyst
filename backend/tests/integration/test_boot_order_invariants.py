from __future__ import annotations

import os
import subprocess
import sys


def test_import_app_skips_native_preflight_when_flag_set() -> None:
    env = dict(os.environ)
    env["LUNAR_ANALYST_SKIP_IMPORT_TIME_NATIVE_PREFLIGHT"] = "1"
    cmd = [
        sys.executable,
        "-c",
        "import backend.api.app as app_module; assert callable(app_module.create_app)",
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_native_health_endpoint_remains_available_without_probe() -> None:
    env = dict(os.environ)
    env["LUNAR_ANALYST_SKIP_IMPORT_TIME_NATIVE_PREFLIGHT"] = "1"
    cmd = [
        sys.executable,
        "-c",
        (
            "from fastapi.testclient import TestClient;"
            "from backend.api.app import create_app;"
            "c=TestClient(create_app());"
            "r=c.get('/api/v1/health/native');"
            "assert r.status_code==200"
        ),
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
