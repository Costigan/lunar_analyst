from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.api.dependencies_constants import MARIMO_PYTHON_ENV
from backend.api.dependency_helpers import (
    build_marimo_launch_env as _build_marimo_launch_env,
    create_unique_scenario_python_file as _create_unique_scenario_python_file,
    ensure_within_root as _ensure_within_root,
    load_app_config as _load_app_config,
    normalize_relative_path as _normalize_relative_path,
    preferred_repo_python as _preferred_repo_python,
    repo_root as _repo_root,
    resolve_config_path as _resolve_config_path,
    utc_from_timestamp as _utc_from_timestamp,
    utc_now as _utc_now,
)
from backend.contracts.models import (
    MarimoLaunchRequest,
    MarimoOpenNotebookRequest,
    MarimoOpenNotebookResponse,
    MarimoStatus,
)
from backend.core.config import resolve_config_relative_path as core_resolve_config_relative_path


logger = logging.getLogger(__name__)


class MarimoService:
    def __init__(self, stores: Any) -> None:
        self._stores = stores

    def _wait_until_ready(
        self,
        *,
        base_url: str,
        process: subprocess.Popen[str] | None,
        timeout_seconds: float = 20.0,
    ) -> None:
        parsed = urlparse(base_url)
        host = parsed.hostname or "127.0.0.1"
        if parsed.port is not None:
            port = parsed.port
        elif parsed.scheme == "https":
            port = 443
        else:
            port = 80

        deadline = time.monotonic() + max(0.1, timeout_seconds)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                raise RuntimeError(
                    f"Marimo exited before becoming ready (returncode={process.poll()})."
                )
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    return
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
        if last_error is not None:
            raise RuntimeError(f"Marimo did not become ready at {base_url}: {last_error}")
        raise RuntimeError(f"Marimo did not become ready at {base_url}")

    def open_notebook(self, request: MarimoOpenNotebookRequest) -> MarimoOpenNotebookResponse:
        scenario_id = request.scenario_id.strip()
        scenario = self._stores.scenarios.get(scenario_id)
        if scenario is None:
            raise KeyError(f"Scenario not found: {scenario_id}")
        scenario_root = Path(scenario.directory).expanduser().resolve()
        _ensure_within_root(self._stores.workspace_root, scenario_root)

        if request.create_new:
            if request.relative_path is not None and request.relative_path.strip():
                raise ValueError("create_new cannot be combined with relative_path.")
            target_path = self._create_unique_notebook_file(scenario_root)
            created_new = True
        else:
            normalized_relative_path = _normalize_relative_path(request.relative_path or "")
            if not normalized_relative_path:
                raise ValueError("relative_path is required when create_new is false.")
            target_path = (scenario_root / normalized_relative_path).resolve()
            _ensure_within_root(scenario_root, target_path)
            if not target_path.exists() or not target_path.is_file():
                raise KeyError(f"Notebook target not found: {normalized_relative_path}")
            if target_path.suffix.lower() != ".py":
                raise ValueError(f"Notebook target must be a Python file: {normalized_relative_path}")
            if not self._looks_like_marimo_notebook(target_path):
                raise ValueError(f"File is not a Marimo notebook: {normalized_relative_path}")
            created_new = False

        launch_status = self.launch_or_attach(
            MarimoLaunchRequest(
                scenario_id=scenario_id,
                restart_if_running=request.restart_if_running,
            )
        )
        base_url = str(launch_status.base_url or "").strip()
        if not base_url:
            raise ValueError("Marimo launch completed without a base URL.")

        stat_result = target_path.stat()
        return MarimoOpenNotebookResponse(
            status="ready",
            scenario_id=scenario_id,
            relative_path=_normalize_relative_path(target_path.relative_to(scenario_root).as_posix()),
            absolute_file_path=str(target_path),
            file_url=self._build_marimo_file_url(base_url, str(target_path)),
            file_name=target_path.name,
            notebook_capability="marimo_notebook",
            created_new=created_new,
            modified_at_utc=_utc_from_timestamp(stat_result.st_mtime),
        )

    def launch_or_attach(self, request: MarimoLaunchRequest) -> MarimoStatus:
        if (
            request.attach_url is not None
            and request.attach_url.strip()
            and request.scenario_id is not None
            and request.scenario_id.strip()
        ):
            raise ValueError("Marimo attach_url cannot be combined with scenario_id.")

        if request.attach_url is not None and request.attach_url.strip():
            self.stop_if_running()
            self._stores.marimo.mode = "attach"
            self._stores.marimo.process = None
            self._stores.marimo.base_url = request.attach_url.strip()
            self._stores.marimo.log_path = None
            self._stores.marimo.log_handle = None
            self._stores.marimo.command = []
            self._stores.marimo.cwd = None
            self._stores.marimo.started_at_utc = _utc_now()
            logger.info("marimo attach configured base_url=%s", self._stores.marimo.base_url)
            return self.status()

        requested_cwd = self._resolve_launch_cwd(request)
        explicit_target_cwd = bool(
            (request.scenario_id is not None and request.scenario_id.strip())
            or (request.cwd is not None and request.cwd.strip())
        )
        if (
            self._stores.marimo.mode == "attach"
            and request.scenario_id is not None
            and request.scenario_id.strip()
        ):
            raise MarimoLaunchConflictError(
                message="Cannot launch scenario-scoped Marimo while attached to an external Marimo server.",
                details={
                    "mode": "attach",
                    "scenario_id": request.scenario_id,
                    "requested_cwd": requested_cwd,
                },
            )

        current = self.status()
        if current.status == "running":
            if request.restart_if_running:
                self.stop_if_running()
                current = self.status()
            else:
                if not explicit_target_cwd:
                    return current
                current_cwd = self._normalize_cwd(self._stores.marimo.cwd)
                target_cwd = self._normalize_cwd(requested_cwd)
                if current_cwd == target_cwd:
                    return current
                raise MarimoLaunchConflictError(
                    message=(
                        "Marimo is already running with a different cwd. "
                        "Set restart_if_running=true to relaunch in the requested directory."
                    ),
                    details={
                        "current_cwd": current_cwd,
                        "requested_cwd": target_cwd,
                    },
                )

        command = request.command if request.command is not None else self._default_command()
        if not command:
            raise ValueError("Marimo launch command cannot be empty.")
        cwd = requested_cwd
        log_path = self._resolve_log_path()
        log_handle = None
        stdout_target: Any = subprocess.DEVNULL
        stderr_target: Any = subprocess.DEVNULL
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = log_path.open("a", encoding="utf-8")
            stdout_target = log_handle
            stderr_target = log_handle

        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=stdout_target,
            stderr=stderr_target,
            text=True,
            env=_build_marimo_launch_env(),
        )
        self._stores.marimo.mode = "launch"
        self._stores.marimo.process = process
        self._stores.marimo.base_url = self._default_base_url()
        self._stores.marimo.log_path = str(log_path) if log_path is not None else None
        self._stores.marimo.log_handle = log_handle
        self._stores.marimo.command = list(command)
        self._stores.marimo.cwd = cwd
        self._stores.marimo.started_at_utc = _utc_now()
        logger.info(
            "marimo launched pid=%s base_url=%s cwd=%s log_path=%s command=%s",
            process.pid,
            self._stores.marimo.base_url,
            cwd,
            self._stores.marimo.log_path,
            command,
        )
        self._wait_until_ready(
            base_url=self._stores.marimo.base_url or self._default_base_url(),
            process=process,
        )
        return self.status()

    def auto_start_if_enabled(self) -> MarimoStatus | None:
        if os.getenv("PYTEST_CURRENT_TEST"):
            return None
        config = _load_app_config()
        backend_cfg = config.get("backend", {})
        marimo_cfg = backend_cfg.get("marimo", {}) if isinstance(backend_cfg, dict) else {}
        if not isinstance(marimo_cfg, dict):
            return None

        auto_start_raw = marimo_cfg.get("auto_start", False)
        auto_start = False
        if isinstance(auto_start_raw, bool):
            auto_start = auto_start_raw
        elif isinstance(auto_start_raw, str):
            auto_start = auto_start_raw.strip().lower() in {"1", "true", "yes", "on"}

        if not auto_start:
            return None

        attach_url = marimo_cfg.get("attach_url")
        request = MarimoLaunchRequest(
            attach_url=str(attach_url).strip() if isinstance(attach_url, str) and attach_url.strip() else None,
            command=None,
            cwd=None,
        )
        return self.launch_or_attach(request)

    def status(self) -> MarimoStatus:
        process = self._stores.marimo.process
        if self._stores.marimo.mode == "attach":
            return MarimoStatus(
                status="attached",
                mode="attach",
                pid=None,
                base_url=self._stores.marimo.base_url,
                log_path=self._stores.marimo.log_path,
                command=[],
                cwd=None,
                started_at_utc=self._stores.marimo.started_at_utc,
            )
        if process is not None and process.poll() is None:
            return MarimoStatus(
                status="running",
                mode="launch",
                pid=process.pid,
                base_url=self._stores.marimo.base_url,
                log_path=self._stores.marimo.log_path,
                command=list(self._stores.marimo.command),
                cwd=self._stores.marimo.cwd,
                started_at_utc=self._stores.marimo.started_at_utc,
            )
        return MarimoStatus(
            status="stopped",
            mode="none",
            pid=None,
            base_url=self._stores.marimo.base_url,
            log_path=self._stores.marimo.log_path,
            command=list(self._stores.marimo.command),
            cwd=self._stores.marimo.cwd,
            started_at_utc=self._stores.marimo.started_at_utc,
        )

    def stop_if_running(self) -> bool:
        process = self._stores.marimo.process
        if process is None:
            self._stores.marimo.mode = "none"
            self._close_log_handle()
            return False
        if process.poll() is not None:
            self._stores.marimo.process = None
            self._stores.marimo.mode = "none"
            self._close_log_handle()
            return False
        logger.info("stopping marimo pid=%s", process.pid)
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)
        self._stores.marimo.process = None
        self._stores.marimo.mode = "none"
        self._close_log_handle()
        logger.info("marimo stopped")
        return True

    def _resolve_launch_cwd(self, request: MarimoLaunchRequest) -> str:
        if request.scenario_id is not None and request.scenario_id.strip():
            scenario_id = request.scenario_id.strip()
            scenario = self._stores.scenarios.get(scenario_id)
            if scenario is None:
                raise KeyError(f"Scenario not found: {scenario_id}")
            return str(Path(scenario.directory).expanduser().resolve())
        if request.cwd is not None and request.cwd.strip():
            return str(Path(request.cwd).expanduser().resolve())
        return str(_repo_root())

    def _normalize_cwd(self, cwd: str | None) -> str | None:
        if cwd is None:
            return None
        return str(Path(cwd).expanduser().resolve())

    def _create_unique_notebook_file(self, scenario_root: Path) -> Path:
        return _create_unique_scenario_python_file(
            scenario_root,
            stem_prefix="notebook",
            suffix=".mo.py",
            initial_content=_default_marimo_notebook_template(),
        )

    def _default_base_url(self) -> str:
        config = _load_app_config()
        backend_cfg = config.get("backend", {})
        marimo_cfg = backend_cfg.get("marimo", {}) if isinstance(backend_cfg, dict) else {}
        if isinstance(marimo_cfg, dict):
            base_url = marimo_cfg.get("base_url")
            if isinstance(base_url, str) and base_url.strip():
                return base_url.strip()
        return "http://127.0.0.1:2718"

    def _build_marimo_file_url(self, base_url: str, absolute_file_path: str) -> str:
        from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

        parsed = urlparse(base_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["file"] = absolute_file_path
        return urlunparse(parsed._replace(query=urlencode(query)))

    @staticmethod
    def _looks_like_marimo_notebook(path: Path) -> bool:
        name = path.name.lower()
        if name.endswith(".mo.py"):
            return True
        try:
            snippet = path.read_text(encoding="utf-8", errors="replace")[:4096].lower()
        except Exception:
            return False
        return ("import marimo" in snippet) or ("marimo.app(" in snippet)

    def _default_command(self) -> list[str]:
        config = _load_app_config()
        backend_cfg = config.get("backend", {})
        marimo_cfg = backend_cfg.get("marimo", {}) if isinstance(backend_cfg, dict) else {}
        token_flag = self._token_flag_from_config(marimo_cfg)
        if isinstance(marimo_cfg, dict):
            command = marimo_cfg.get("command")
            if isinstance(command, list) and command and all(isinstance(p, str) for p in command):
                return [str(p) for p in command]

            python_executable = marimo_cfg.get("python_executable")
            if isinstance(python_executable, str) and python_executable.strip():
                py = str(Path(python_executable).expanduser())
                return [py, "-m", "marimo", "edit", "--headless", "--port", "2718", token_flag]

        env_py = os.getenv(MARIMO_PYTHON_ENV)
        if env_py and env_py.strip():
            return [env_py.strip(), "-m", "marimo", "edit", "--headless", "--port", "2718", token_flag]

        repo_py = _preferred_repo_python()
        if repo_py is not None:
            return [str(repo_py), "-m", "marimo", "edit", "--headless", "--port", "2718", token_flag]

        return [
            sys.executable,
            "-m",
            "marimo",
            "edit",
            "--headless",
            "--port",
            "2718",
            token_flag,
        ]

    def _token_flag_from_config(self, marimo_cfg: Any) -> str:
        use_token_auth_raw: Any = True
        if isinstance(marimo_cfg, dict):
            use_token_auth_raw = marimo_cfg.get("use_token_auth", True)

        use_token_auth = True
        if isinstance(use_token_auth_raw, bool):
            use_token_auth = use_token_auth_raw
        elif isinstance(use_token_auth_raw, str):
            use_token_auth = use_token_auth_raw.strip().lower() in {"1", "true", "yes", "on"}
        return "--token" if use_token_auth else "--no-token"

    def _resolve_log_path(self) -> Path | None:
        config = _load_app_config()
        backend_cfg = config.get("backend", {})
        marimo_cfg = backend_cfg.get("marimo", {}) if isinstance(backend_cfg, dict) else {}
        if isinstance(marimo_cfg, dict):
            raw = marimo_cfg.get("log_path")
            if isinstance(raw, str) and raw.strip():
                config_path = _resolve_config_path()
                return core_resolve_config_relative_path(raw, config_path=config_path)
        return None

    def _close_log_handle(self) -> None:
        handle = self._stores.marimo.log_handle
        self._stores.marimo.log_handle = None
        if handle is None:
            return
        try:
            handle.close()
        except Exception:
            return


def _default_marimo_notebook_template() -> str:
    return """import marimo

app = marimo.App(width=\"medium\")


@app.cell
def _():
    # New scenario notebook.
    return


if __name__ == \"__main__\":
    app.run()
"""


def _default_python_script_template() -> str:
    return """# New scenario Python script.

def main() -> None:
    print(\"Hello from the scenario script.\")


if __name__ == \"__main__\":
    main()
"""


class MarimoLaunchConflictError(Exception):
    def __init__(self, *, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
