from __future__ import annotations

import logging
import os
import signal
import threading
from typing import Callable
from uuid import uuid4
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.worker.native_bootstrap import NativeBootstrapError, bootstrap_pythonnet, bootstrap_status

_IMPORT_TIME_NATIVE_PREFLIGHT_ERROR: str | None = None
_SKIP_IMPORT_TIME_NATIVE_PREFLIGHT_ENV = "LUNAR_ANALYST_SKIP_IMPORT_TIME_NATIVE_PREFLIGHT"


def _best_effort_native_preflight_before_backend_imports() -> None:
    global _IMPORT_TIME_NATIVE_PREFLIGHT_ERROR
    if str(os.getenv(_SKIP_IMPORT_TIME_NATIVE_PREFLIGHT_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}:
        logger = logging.getLogger(__name__)
        logger.info(
            "skipping import-time native preflight due to %s",
            _SKIP_IMPORT_TIME_NATIVE_PREFLIGHT_ENV,
        )
        return
    # Run before importing modules that may import rasterio/GDAL at module scope.
    # If this succeeds, moonlib/native establishes the process DLL root first.
    try:
        bootstrap_pythonnet(force=False, verify_bridge_smoke=False)
    except NativeBootstrapError as exc:
        _IMPORT_TIME_NATIVE_PREFLIGHT_ERROR = str(exc)
        return
    except Exception as exc:
        _IMPORT_TIME_NATIVE_PREFLIGHT_ERROR = str(exc)
        return


_best_effort_native_preflight_before_backend_imports()
from backend.api.dependencies import get_services, shutdown_services
from backend.api.errors import ApiError
from backend.api.routers.assistant import router as assistant_router
from backend.api.routers.lunar_analyst import router as lunar_analyst_router
from backend.api.routers.mcp import router as mcp_router
from backend.api.routers.nomenclature import router as nomenclature_router
from backend.api.routers.trek import router as trek_router
from backend.api.routers.v1 import router as v1_router
from backend.core.config import load_app_config as core_load_app_config
from backend.api.dependency_helpers import resolve_workspace_root as _resolve_workspace_root
from backend.contracts.models import DiscoverScenariosRequest, ErrorEnvelope
from backend.services.assistant.bug_report_service import backend_log_path as _backend_log_path
from backend.worker.gdal_runtime import configure_gdal_runtime


logger = logging.getLogger(__name__)
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
DEFAULT_WEB_MOUNT_PATH = "/lunar_analyst"
_SIGNAL_HANDLER_LOCK = threading.Lock()
_INSTALLED_SIGNAL_HANDLERS: dict[signal.Signals, object] = {}


def _run_startup_task_in_background(name: str, target: Callable[[], None]) -> None:
    def _runner() -> None:
        try:
            target()
        except Exception:  # pragma: no cover - defensive background-task logging
            logger.exception("startup background task failed: %s", name)

    thread = threading.Thread(target=_runner, name=f"startup-{name}", daemon=True)
    thread.start()


def _terminate_active_notebook_jobs_for_signal(signum: int) -> None:
    try:
        services = get_services()
        terminated = services.notebook_job_service.terminate_all_running(
            reason=f"signal:{signal.Signals(signum).name.lower()}"
        )
        if terminated:
            logger.warning(
                "signal=%s terminated %s active notebook job process(es)",
                signum,
                terminated,
            )
    except Exception:
        logger.exception("failed terminating notebook job processes on signal=%s", signum)


def _install_signal_handlers() -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    with _SIGNAL_HANDLER_LOCK:
        if _INSTALLED_SIGNAL_HANDLERS:
            return
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous = signal.getsignal(sig)
            _INSTALLED_SIGNAL_HANDLERS[sig] = previous

            def _handler(signum: int, frame: object | None, *, _previous=previous) -> None:
                _terminate_active_notebook_jobs_for_signal(signum)
                if callable(_previous):
                    _previous(signum, frame)
                    return
                if _previous is signal.SIG_DFL:
                    if signum == signal.SIGINT:
                        signal.default_int_handler(signum, frame)
                    raise SystemExit(128 + signum)
                # SIG_IGN: no-op

            signal.signal(sig, _handler)


def _restore_signal_handlers() -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    with _SIGNAL_HANDLER_LOCK:
        for sig, previous in list(_INSTALLED_SIGNAL_HANDLERS.items()):
            signal.signal(sig, previous)  # type: ignore[arg-type]
        _INSTALLED_SIGNAL_HANDLERS.clear()


def _normalize_web_mount_path(raw: str | None) -> str:
    if not isinstance(raw, str):
        return DEFAULT_WEB_MOUNT_PATH
    mount = raw.strip()
    if not mount:
        return DEFAULT_WEB_MOUNT_PATH
    if not mount.startswith("/"):
        mount = f"/{mount}"
    mount = mount.rstrip("/")
    if not mount:
        return DEFAULT_WEB_MOUNT_PATH
    return mount


def _config_web_mount_path() -> str:
    payload = core_load_app_config()
    backend_cfg = payload.get("backend", {})
    if not isinstance(backend_cfg, dict):
        return DEFAULT_WEB_MOUNT_PATH
    web_cfg = backend_cfg.get("web", {})
    if not isinstance(web_cfg, dict):
        return DEFAULT_WEB_MOUNT_PATH
    return _normalize_web_mount_path(web_cfg.get("mount_path"))


def _config_log_level() -> str | None:
    payload = core_load_app_config()
    backend_cfg = payload.get("backend", {})
    if not isinstance(backend_cfg, dict):
        return None
    level = backend_cfg.get("log_level")
    if isinstance(level, str) and level.strip():
        return level.strip()
    logging_cfg = backend_cfg.get("logging")
    if isinstance(logging_cfg, dict):
        nested = logging_cfg.get("level")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def _config_logger_overrides() -> dict[str, str]:
    payload = core_load_app_config()

    backend_cfg = payload.get("backend", {})
    if not isinstance(backend_cfg, dict):
        return {}
    logging_cfg = backend_cfg.get("logging")
    if not isinstance(logging_cfg, dict):
        return {}
    loggers_cfg = logging_cfg.get("loggers")
    if not isinstance(loggers_cfg, dict):
        return {}

    overrides: dict[str, str] = {}
    for name, raw_level in loggers_cfg.items():
        if not isinstance(name, str):
            continue
        if not isinstance(raw_level, str):
            continue
        level = raw_level.strip().upper()
        if level in VALID_LOG_LEVELS:
            overrides[name.strip()] = level
    return overrides


def _config_auto_discover_on_startup() -> bool:
    payload = core_load_app_config()
    backend_cfg = payload.get("backend", {})
    if not isinstance(backend_cfg, dict):
        return False
    discovery_cfg = backend_cfg.get("scenario_discovery", {})
    if not isinstance(discovery_cfg, dict):
        return False
    value = discovery_cfg.get("auto_discover_on_startup", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _config_reconcile_missing_on_startup() -> bool:
    payload = core_load_app_config()
    backend_cfg = payload.get("backend", {})
    if not isinstance(backend_cfg, dict):
        return False
    discovery_cfg = backend_cfg.get("scenario_discovery", {})
    if not isinstance(discovery_cfg, dict):
        return False
    value = discovery_cfg.get("reconcile_missing_on_startup", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _configure_backend_logging() -> None:
    level_name = os.getenv("LUNAR_ANALYST_LOG_LEVEL", "").strip()
    if not level_name:
        level_name = _config_log_level() or "INFO"
    level_name = level_name.upper()
    if level_name not in VALID_LOG_LEVELS:
        level_name = "INFO"
    level = getattr(logging, level_name, logging.INFO)
    backend_logger = logging.getLogger("backend")
    backend_logger.setLevel(level)

    uvicorn_error = logging.getLogger("uvicorn.error")
    if uvicorn_error.handlers:
        backend_logger.handlers = uvicorn_error.handlers
        backend_logger.propagate = False
    elif not backend_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
        backend_logger.addHandler(handler)
    try:
        workspace_root = _resolve_workspace_root()
        backend_log_path = _backend_log_path(workspace_root)
        existing_file_handlers = [
            handler
            for handler in backend_logger.handlers
            if isinstance(handler, logging.FileHandler)
            and Path(getattr(handler, "baseFilename", "")).resolve() == backend_log_path
        ]
        if not existing_file_handlers:
            file_handler = logging.FileHandler(backend_log_path, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            backend_logger.addHandler(file_handler)
    except Exception:
        backend_logger.warning("backend log file handler could not be configured", exc_info=True)
    backend_logger.propagate = False

    for logger_name, logger_level in _config_logger_overrides().items():
        logging.getLogger(logger_name).setLevel(getattr(logging, logger_level, logging.INFO))


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _envelope(
    request: Request,
    *,
    code: str,
    message: str,
    details: dict | None = None,
) -> dict:
    payload = ErrorEnvelope(
        code=code,
        message=message,
        details=details or {},
        request_id=_request_id(request),
    )
    return payload.model_dump()


def _web_root() -> str:
    return str(Path(__file__).resolve().parents[1] / "web" / "lunar_analyst")


def _resolve_map_index_path(web_root: Path) -> Path:
    dist_react_index = web_root / "dist" / "index.react.html"
    if dist_react_index.exists():
        return dist_react_index
    # Try generic index.html in dist (Vite default output)
    dist_index = web_root / "dist" / "index.html"
    if dist_index.exists():
        return dist_index
    react_index = web_root / "index.react.html"
    if react_index.exists():
        return react_index
    return web_root / "index.html"


def create_app() -> FastAPI:
    _configure_backend_logging()
    web_root = _web_root()
    web_root_path = Path(web_root)
    web_mount_path = _config_web_mount_path()
    web_mount_path_slash = f"{web_mount_path}/"
    web_mount_name = web_mount_path.strip("/").replace("/", "-") or "lunar-analyst"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        _install_signal_handlers()
        logger.info("native bootstrap status at startup: %s", bootstrap_status())
        if _IMPORT_TIME_NATIVE_PREFLIGHT_ERROR:
            logger.info(
                "native bootstrap preflight before backend imports failed: %s",
                _IMPORT_TIME_NATIVE_PREFLIGHT_ERROR,
            )
        _run_startup_task_in_background("services-warmup", lambda: get_services())
        # Try native bootstrap first so subsequent Python GDAL imports, if any,
        # happen after the moonlib/native resolver has established its build root.
        # Keep this best-effort to preserve GDAL-only startup paths.
        try:
            handle = bootstrap_pythonnet(force=False, verify_bridge_smoke=False)
            logger.info(
                "native bootstrap preflight succeeded runtime=%s moonlib=%s",
                handle.runtime,
                handle.moonlib_dll,
            )
        except NativeBootstrapError as exc:
            logger.warning("native bootstrap preflight skipped/failed: %s", exc)
        try:
            configure_gdal_runtime()
            logger.info("gdal runtime configured (PROJ/GDAL exception mode)")
        except Exception as exc:
            logger.warning("gdal runtime configuration failed: %s", exc)
        probe_on_startup = os.getenv("LUNAR_ANALYST_NATIVE_PROBE_ON_STARTUP", "0").strip()
        if probe_on_startup.lower() in {"1", "true", "yes", "on"}:
            try:
                handle = bootstrap_pythonnet(force=False, verify_bridge_smoke=True)
                logger.info(
                    "native bootstrap probe succeeded runtime=%s moonlib=%s",
                    handle.runtime,
                    handle.moonlib_dll,
                )
            except NativeBootstrapError as exc:
                logger.warning("native bootstrap probe failed: %s", exc)
        def _run_startup_marimo() -> None:
            try:
                marimo_status = get_services().marimo_service.auto_start_if_enabled()
                if marimo_status is not None:
                    logger.info(
                        "marimo auto-start enabled: status=%s mode=%s base_url=%s pid=%s",
                        marimo_status.status,
                        marimo_status.mode,
                        marimo_status.base_url,
                        marimo_status.pid,
                    )
            except Exception as exc:
                logger.warning("marimo auto-start failed: %s", exc)

        _run_startup_task_in_background("marimo-auto-start", _run_startup_marimo)

        def _run_assistant_cleanup() -> None:
            try:
                get_services().assistant_service.start_idle_cleanup_task()
            except Exception as exc:
                logger.warning("assistant idle cleanup task failed to start: %s", exc)

        _run_startup_task_in_background("assistant-idle-cleanup", _run_assistant_cleanup)

        def _run_assistant_rag_refresh() -> None:
            try:
                get_services().assistant_service.refresh_rag_indexes_on_startup()
            except Exception as exc:
                logger.warning("assistant rag startup refresh task failed to start: %s", exc)

        _run_startup_task_in_background("assistant-rag-refresh", _run_assistant_rag_refresh)

        if _config_auto_discover_on_startup():
            reconcile_missing = _config_reconcile_missing_on_startup()

            def _run_startup_discovery() -> None:
                try:
                    services = get_services()
                    summary = services.scenario_service.discover_scenarios(
                        DiscoverScenariosRequest(reconcile_missing=True)
                    )
                    for item in summary.results:
                        scenario_root = str(item.scenario_root or "").strip() or "<unknown>"
                        scenario_id = str(item.scenario_id or "").strip() or "-"
                        reason = str(item.reason or "").strip()
                        message = (
                            "scenario auto-discovery result scenario_root=%s scenario_id=%s status=%s reason=%s"
                        )
                        if item.status == "error":
                            logger.error(message, scenario_root, scenario_id, item.status, reason or "<none>")
                        elif item.status in {"skipped", "forgotten"}:
                            logger.info(message, scenario_root, scenario_id, item.status, reason or "<none>")
                        else:
                            logger.info(message, scenario_root, scenario_id, item.status, reason or "<none>")

                        if item.warnings:
                            logger.warning(
                                "scenario auto-discovery warnings scenario_root=%s scenario_id=%s warnings=%s",
                                scenario_root,
                                scenario_id,
                                "; ".join(str(w).strip() for w in item.warnings if str(w).strip()) or "<none>",
                            )
                    for scenario in list(services.scenario_service.list_scenarios()):
                        scenario_dir = Path(scenario.directory).resolve()
                        if scenario_dir.exists() and scenario_dir.is_dir():
                            continue
                        services.scenario_service.forget_scenario(scenario.scenario_id)
                    logger.info(
                        "scenario auto-discovery completed discovered=%s ingested=%s updated=%s skipped=%s errors=%s reconcile_missing_config=%s",
                        summary.discovered_count,
                        summary.ingested_count,
                        summary.updated_count,
                        summary.skipped_count,
                        summary.error_count,
                        reconcile_missing,
                    )
                except Exception as exc:
                    logger.warning("scenario auto-discovery failed: %s", exc)

            # Discovery is completed before serving requests so scenario APIs
            # observe startup reconciliation deterministically.
            _run_startup_discovery()
        try:
            yield
        finally:
            shutdown_services()
            _restore_signal_handlers()

    app = FastAPI(
        title="Lunar Analyst API",
        version="1.0.0-stage1",
        description="Typed-contract-first FastAPI stub",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    @app.middleware("http")
    async def notebook_auth_middleware(request: Request, call_next):
        path = request.url.path
        method = request.method.upper()
        guarded_methods = {"POST", "PATCH", "PUT", "DELETE"}
        exempt_paths = {"/api/v1/notebook/sessions", "/api/v1/health", "/api/v1/health/native"}
        if method in guarded_methods and path.startswith("/api/v1") and path not in exempt_paths:
            services = get_services()
            if services.notebook_session_service.is_auth_required():
                token = request.headers.get("x-lunar-session-token")
                session = services.notebook_session_service.validate_token(token)
                if session is None:
                    return JSONResponse(
                        status_code=401,
                        content=_envelope(
                            request,
                            code="unauthorized",
                            message="Notebook session token is required.",
                            details={"header": "x-lunar-session-token"},
                        ),
                    )
                request.state.notebook_session_id = session.session_id
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url=web_mount_path_slash)

    async def web_app_root() -> FileResponse:
        return FileResponse(_resolve_map_index_path(web_root_path))

    app.add_api_route(web_mount_path, web_app_root, methods=["GET"], include_in_schema=False)
    app.add_api_route(web_mount_path_slash, web_app_root, methods=["GET"], include_in_schema=False)

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(
                request,
                code=exc.code,
                message=exc.message,
                details=exc.details,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_envelope(
                request,
                code="invalid_request",
                message="Request validation failed.",
                details={"errors": exc.errors()},
            ),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code", f"http_{exc.status_code}"))
            message = str(detail.get("message", "HTTP error"))
            details = detail.get("details", detail)
            if not isinstance(details, dict):
                details = {"detail": details}
        elif isinstance(detail, list):
            code = f"http_{exc.status_code}"
            message = "HTTP error"
            details = {"detail": detail}
        else:
            code = f"http_{exc.status_code}"
            message = str(detail or "HTTP error")
            details = {}

        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(
                request,
                code=code,
                message=message,
                details=details,
            ),
        )

    @app.exception_handler(NotImplementedError)
    async def not_implemented_handler(request: Request, exc: NotImplementedError) -> JSONResponse:
        return JSONResponse(
            status_code=501,
            content=_envelope(
                request,
                code="not_implemented",
                message=str(exc),
                details={},
            ),
        )

    @app.exception_handler(KeyError)
    async def not_found_handler(request: Request, exc: KeyError) -> JSONResponse:
        message = str(exc)
        if message.startswith("'") and message.endswith("'"):
            message = message[1:-1]
        return JSONResponse(
            status_code=404,
            content=_envelope(
                request,
                code="not_found",
                message=message,
                details={},
            ),
        )

    @app.exception_handler(Exception)
    async def unknown_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error request_id=%s path=%s", _request_id(request), request.url.path)
        return JSONResponse(
            status_code=500,
            content=_envelope(
                request,
                code="internal_error",
                message="Unexpected server error.",
                details={},
            ),
        )

    app.include_router(v1_router)
    app.include_router(lunar_analyst_router)
    app.include_router(trek_router)
    app.include_router(nomenclature_router)
    app.include_router(assistant_router)
    app.include_router(mcp_router)
    dist_assets = web_root_path / "dist" / "assets"
    if dist_assets.exists():
        app.mount(
            f"{web_mount_path}/assets",
            StaticFiles(directory=str(dist_assets), html=False),
            name=f"{web_mount_name}-assets",
        )
    app.mount(web_mount_path, StaticFiles(directory=web_root, html=True), name=web_mount_name)
    return app


app = create_app()
