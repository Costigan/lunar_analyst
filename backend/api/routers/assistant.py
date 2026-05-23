from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from backend.api.dependencies import ServiceContainer, get_services
from backend.contracts.assistant_models import (
    AssistantBugReportRequest,
    AssistantBugReportResponse,
    AssistantConfirmationDecisionRequest,
    AssistantConfirmationDecisionResponse,
    AssistantPolicy,
    AssistantProviderCatalogResponse,
    ListAssistantBugReportsResponse,
    AssistantSession,
    AssistantSessionDetailResponse,
    CompactAssistantSessionRequest,
    CompactAssistantSessionResponse,
    CreateAssistantSessionRequest,
    CreateAssistantTurnRequest,
    CreateAssistantTurnResponse,
    ListAssistantMessagesResponse,
    ListAssistantSessionsResponse,
    UpdateAssistantPolicyRequest,
)


router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])


def _read_event_batch(events: Any, cursor: int) -> tuple[int, list[dict[str, Any]]]:
    reader = getattr(events, "read_since", None)
    if callable(reader):
        next_cursor, payloads = reader(cursor)
        return int(next_cursor), list(payloads)
    if cursor < 0:
        cursor = 0
    total = len(events)
    if cursor >= total:
        return total, []
    return total, list(events[cursor:])


def _latest_cursor(events: Any) -> int:
    reader = getattr(events, "read_since", None)
    if callable(reader):
        next_cursor, _payloads = reader(2**63 - 1)
        return int(next_cursor)
    try:
        return len(events)
    except Exception:
        return 0

async def _wait_event_batch(
    events: Any,
    cursor: int,
    *,
    timeout_seconds: float = 30.0,
) -> tuple[int, list[dict[str, Any]]]:
    waiter = getattr(events, "wait_for_events", None)
    if callable(waiter):
        next_cursor, payloads = await asyncio.to_thread(waiter, cursor, timeout_seconds)
        return int(next_cursor), list(payloads)
    return _read_event_batch(events, cursor)


@router.post("/sessions", response_model=AssistantSession)
def create_session(
    request: CreateAssistantSessionRequest,
    services: ServiceContainer = Depends(get_services),
) -> AssistantSession:
    return services.assistant_service.create_session(request)


@router.get("/sessions", response_model=ListAssistantSessionsResponse)
def list_sessions(
    services: ServiceContainer = Depends(get_services),
) -> ListAssistantSessionsResponse:
    return services.assistant_service.list_sessions()


@router.get("/sessions/{session_id}", response_model=AssistantSessionDetailResponse)
def get_session(
    session_id: str,
    services: ServiceContainer = Depends(get_services),
) -> AssistantSessionDetailResponse:
    return services.assistant_service.get_session_detail(session_id)


@router.get("/sessions/{session_id}/messages", response_model=ListAssistantMessagesResponse)
def list_messages(
    session_id: str,
    services: ServiceContainer = Depends(get_services),
) -> ListAssistantMessagesResponse:
    return services.assistant_service.list_messages(session_id)


@router.get("/bug-reports", response_model=ListAssistantBugReportsResponse)
def list_bug_reports(
    services: ServiceContainer = Depends(get_services),
) -> ListAssistantBugReportsResponse:
    return services.assistant_service.list_bug_reports()


@router.get("/bug-reports/{bug_report_id}", response_model=AssistantBugReportResponse)
def get_bug_report(
    bug_report_id: str,
    services: ServiceContainer = Depends(get_services),
) -> AssistantBugReportResponse:
    return services.assistant_service.get_bug_report(bug_report_id)


@router.post("/sessions/{session_id}/bug-reports", response_model=AssistantBugReportResponse)
def capture_bug_report(
    session_id: str,
    request: AssistantBugReportRequest,
    services: ServiceContainer = Depends(get_services),
) -> AssistantBugReportResponse:
    return services.assistant_service.capture_bug_report(session_id, request)


@router.post("/sessions/{session_id}/turns", response_model=CreateAssistantTurnResponse)
def create_turn(
    session_id: str,
    request: CreateAssistantTurnRequest,
    services: ServiceContainer = Depends(get_services),
) -> CreateAssistantTurnResponse:
    return services.assistant_service.create_turn(session_id, request)


@router.post(
    "/sessions/{session_id}/confirmations/{confirmation_id}",
    response_model=AssistantConfirmationDecisionResponse,
)
def resolve_confirmation(
    session_id: str,
    confirmation_id: str,
    request: AssistantConfirmationDecisionRequest,
    services: ServiceContainer = Depends(get_services),
) -> AssistantConfirmationDecisionResponse:
    return services.assistant_service.resolve_confirmation(session_id, confirmation_id, request)


@router.patch("/sessions/{session_id}/policy", response_model=AssistantPolicy)
def update_policy(
    session_id: str,
    request: UpdateAssistantPolicyRequest,
    services: ServiceContainer = Depends(get_services),
) -> AssistantPolicy:
    return services.assistant_service.update_policy(session_id, request)


@router.post(
    "/sessions/{session_id}:compact",
    response_model=CompactAssistantSessionResponse,
)
def compact_session(
    session_id: str,
    request: CompactAssistantSessionRequest,
    services: ServiceContainer = Depends(get_services),
) -> CompactAssistantSessionResponse:
    return services.assistant_service.compact_session(session_id, request)


@router.get("/providers", response_model=AssistantProviderCatalogResponse)
def provider_catalog(
    services: ServiceContainer = Depends(get_services),
) -> AssistantProviderCatalogResponse:
    return services.assistant_service.provider_catalog()


@router.websocket("/sessions/{session_id}/events")
async def ws_assistant_events(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    services = get_services()
    cursor = _latest_cursor(services.stores.assistant_ws_events)
    try:
        while True:
            services = get_services()
            events = services.stores.assistant_ws_events
            cursor, payloads = await _wait_event_batch(events, cursor)
            for payload in payloads:
                if str(payload.get("session_id", "")) == session_id:
                    await websocket.send_json(payload)
    except WebSocketDisconnect:
        return
