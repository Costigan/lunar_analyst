from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.api import dependencies as deps
from backend.api.dependencies import ServiceContainer, get_services
from backend.mcp.transports.http_transport import handle_http_mcp
from backend.mcp.transports.sse_transport import (
    McpSseSessionManager,
    encode_sse_event,
    encode_sse_keepalive,
)


router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])
_SSE_SESSION_MANAGER = McpSseSessionManager()


def _mcp_config() -> dict[str, Any]:
    config = deps._load_app_config()
    backend_cfg = config.get("backend", {})
    if not isinstance(backend_cfg, dict):
        return {}
    mcp_cfg = backend_cfg.get("mcp", {})
    if not isinstance(mcp_cfg, dict):
        return {}
    return mcp_cfg


def _mcp_sse_enabled() -> bool:
    mcp_cfg = _mcp_config()
    if "sse_enabled" in mcp_cfg:
        return bool(mcp_cfg.get("sse_enabled", False))
    if "enabled" in mcp_cfg:
        return bool(mcp_cfg.get("enabled", False))
    return False


def _expected_mcp_auth_token() -> str | None:
    mcp_cfg = _mcp_config()
    env_name = str(mcp_cfg.get("http_auth_token_env", "")).strip()
    if not env_name:
        return None
    token = os.getenv(env_name, "").strip()
    return token or None


def _presented_mcp_auth_token(request: Request) -> str:
    auth = str(request.headers.get("authorization", "")).strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(request.headers.get("x-lunar-analyst-mcp-token", "")).strip()


def _require_mcp_auth(request: Request) -> None:
    expected = _expected_mcp_auth_token()
    if expected is None:
        return
    presented = _presented_mcp_auth_token(request)
    if presented == expected:
        return
    raise HTTPException(status_code=401, detail="mcp_auth_required")


def _resolve_sse_session_id(session_ref: str) -> str | None:
    raw = str(session_ref or "").strip()
    if not raw:
        return None
    if raw.startswith("/api/v1/mcp/sse/"):
        sid = raw.rsplit("/", 1)[-1].strip()
        return sid or None
    if raw.startswith("mcp_sse_"):
        return raw
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    sid = str(parsed.get("session_id", "")).strip()
    if sid:
        return sid
    post_path = str(parsed.get("post_path", "")).strip()
    if post_path.startswith("/api/v1/mcp/sse/"):
        sid = post_path.rsplit("/", 1)[-1].strip()
        return sid or None
    return None


@router.post("")
def mcp_http(
    request: Request,
    payload: dict[str, Any],
    services: ServiceContainer = Depends(get_services),
) -> dict[str, Any]:
    _require_mcp_auth(request)
    return handle_http_mcp(services.mcp_server, services, payload)


@router.get("/sse")
async def mcp_sse_connect(
    request: Request,
    oneshot: bool = False,
    services: ServiceContainer = Depends(get_services),
) -> StreamingResponse:
    del services
    _require_mcp_auth(request)
    if not _mcp_sse_enabled():
        raise HTTPException(status_code=404, detail="mcp_sse_disabled")

    session = _SSE_SESSION_MANAGER.create()
    post_path = f"/api/v1/mcp/sse/{session.session_id}"

    async def event_stream() -> Any:
        try:
            yield encode_sse_event(
                event="endpoint",
                data={
                    "session_id": session.session_id,
                    "post_path": post_path,
                },
            )
            if oneshot:
                return
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(session.queue.get(), timeout=15.0)
                except TimeoutError:
                    yield encode_sse_keepalive()
                    continue
                yield encode_sse_event(event="message", data=payload)
        finally:
            if not oneshot:
                _SSE_SESSION_MANAGER.remove(session.session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-MCP-SSE-Session-Id": session.session_id,
            "X-MCP-SSE-Post-Path": post_path,
        },
    )


@router.post("/sse")
def mcp_sse_post_compat(
    request: Request,
    payload: dict[str, Any],
    services: ServiceContainer = Depends(get_services),
) -> dict[str, Any]:
    """Compatibility endpoint for clients that POST JSON-RPC directly to the configured /sse URL."""
    _require_mcp_auth(request)
    if not _mcp_sse_enabled():
        raise HTTPException(status_code=404, detail="mcp_sse_disabled")
    return handle_http_mcp(services.mcp_server, services, payload)


@router.post("/sse/{session_id}")
def mcp_sse_message(
    session_id: str,
    request: Request,
    payload: dict[str, Any],
    services: ServiceContainer = Depends(get_services),
) -> dict[str, Any]:
    _require_mcp_auth(request)
    if not _mcp_sse_enabled():
        raise HTTPException(status_code=404, detail="mcp_sse_disabled")
    response = handle_http_mcp(services.mcp_server, services, payload)
    published = _SSE_SESSION_MANAGER.publish(session_id, response)
    if not published:
        raise HTTPException(status_code=404, detail="mcp_sse_session_not_found")
    return response


@router.post("/{session_ref:path}")
def mcp_sse_message_compat(
    session_ref: str,
    request: Request,
    payload: dict[str, Any],
    services: ServiceContainer = Depends(get_services),
) -> dict[str, Any]:
    """Compatibility endpoint for clients that post endpoint/session JSON to /api/v1/mcp/{...}."""
    _require_mcp_auth(request)
    if not _mcp_sse_enabled():
        raise HTTPException(status_code=404, detail="mcp_sse_disabled")
    session_id = _resolve_sse_session_id(session_ref)
    if not session_id:
        raise HTTPException(status_code=404, detail="mcp_sse_session_not_found")
    response = handle_http_mcp(services.mcp_server, services, payload)
    published = _SSE_SESSION_MANAGER.publish(session_id, response)
    if not published:
        raise HTTPException(status_code=404, detail="mcp_sse_session_not_found")
    return response
