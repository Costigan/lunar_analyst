from __future__ import annotations

import json
import hashlib
import re
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.contracts.assistant_models import (
    AssistantBugReport,
    AssistantBugReportProgramState,
    AssistantBugReportSummary,
)

_SENSITIVE_KEY_RE = re.compile(r"(token|secret|password|api[_-]?key|authorization|bearer)", re.IGNORECASE)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b\s*[:=]\s*([^\s,;\"']+)"
)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def project_utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def bug_report_root(workspace_root: Path) -> Path:
    return (Path(workspace_root).expanduser().resolve() / "debugging" / "assistant-bug-reports").resolve()


def backend_log_path(workspace_root: Path) -> Path:
    candidate = (Path(workspace_root).expanduser().resolve() / ".assistant" / "logs" / "backend.log").resolve()
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    except Exception:
        fallback_name = hashlib.sha1(str(Path(workspace_root).expanduser().resolve()).encode("utf-8")).hexdigest()[:12]
        fallback = (Path(tempfile.gettempdir()) / "lunar-analyst" / fallback_name / "backend.log").resolve()
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback


def _redact_text(text: str) -> str:
    out = _BEARER_RE.sub("Bearer ***REDACTED***", text)
    return _ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=***REDACTED***", out)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY_RE.search(key_text):
                redacted[key_text] = "***REDACTED***"
            else:
                redacted[key_text] = _redact_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if is_dataclass(value):
        return _redact_value(asdict(value))
    return value


def redact_program_state(program_state: AssistantBugReportProgramState) -> AssistantBugReportProgramState:
    return AssistantBugReportProgramState.model_validate(_redact_value(program_state.model_dump(mode="json")))


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    if max_lines <= 0:
        return []
    return lines[-max_lines:]


def _find_matching_excerpt(
    lines: list[str],
    *,
    session_id: str | None,
    turn_id: str | None,
    window_before: int = 8,
    window_after: int = 120,
) -> list[str]:
    if not lines:
        return []
    match_index: int | None = None
    needles = [needle for needle in (session_id, turn_id) if needle]
    if needles:
        for idx in range(len(lines) - 1, -1, -1):
            line = lines[idx]
            if all(needle in line for needle in needles):
                match_index = idx
                break
        if match_index is None:
            for idx in range(len(lines) - 1, -1, -1):
                line = lines[idx]
                if any(needle in line for needle in needles):
                    match_index = idx
                    break
    if match_index is None:
        return lines[-window_after:]
    start = max(0, match_index - window_before)
    end = min(len(lines), match_index + window_after)
    return lines[start:end]


def read_backend_log_excerpt(
    workspace_root: Path,
    *,
    session_id: str | None,
    turn_id: str | None,
    max_lines: int = 240,
) -> list[str]:
    path = backend_log_path(workspace_root)
    tail = _tail_lines(path, max_lines=max_lines)
    excerpt = _find_matching_excerpt(tail, session_id=session_id, turn_id=turn_id)
    return [_redact_text(line) for line in excerpt]


def _coerce_recent_messages(messages: list[Any], *, limit: int = 12) -> list[dict[str, Any]]:
    recent = []
    for message in messages[-limit:]:
        payload = message.model_dump(mode="json") if hasattr(message, "model_dump") else dict(message)
        recent.append(
            {
                "message_id": payload.get("message_id"),
                "role": payload.get("role"),
                "created_at_utc": payload.get("created_at_utc"),
                "turn_id": payload.get("turn_id"),
                "content": _redact_text(str(payload.get("content", ""))),
            }
        )
    return recent


def _coerce_recent_turns(turns: list[Any], *, limit: int = 5) -> list[dict[str, Any]]:
    recent = []
    for turn in turns[:limit]:
        payload = turn.model_dump(mode="json") if hasattr(turn, "model_dump") else dict(turn)
        recent.append(
            {
                "turn_id": payload.get("turn_id"),
                "status": payload.get("status"),
                "provider_id": payload.get("provider_id"),
                "model_id": payload.get("model_id"),
                "created_at_utc": payload.get("created_at_utc"),
                "updated_at_utc": payload.get("updated_at_utc"),
                "error": _redact_text(str(payload.get("error", ""))) if payload.get("error") else None,
            }
        )
    return recent


def build_bug_report_bundle(
    *,
    workspace_root: Path,
    session_store: Any,
    session_id: str,
    report_text: str,
    program_state: AssistantBugReportProgramState,
    provider_request_id: str | None = None,
    model_tool_schema: dict[str, Any] | None = None,
    model_tool_names: list[str] | None = None,
    turn_id: str | None = None,
) -> AssistantBugReport:
    session = session_store.get_session(session_id)
    recent_turns = list(session_store.list_turns(session_id, limit=20))
    selected_turn = turn_id or program_state.active_assistant_turn_id
    if not selected_turn and recent_turns:
        selected_turn = str(recent_turns[0].turn_id)
    turn = None
    if selected_turn:
        try:
            turn = session_store.get_turn(selected_turn)
        except Exception:
            turn = None
    recent_messages = []
    try:
        recent_messages = list(session_store.list_messages(session_id, limit=20))
    except Exception:
        recent_messages = []

    assistant_context = _redact_value(
        {
            "session": session.model_dump(mode="json"),
            "selected_turn": turn.model_dump(mode="json") if turn is not None else None,
            "recent_turns": _coerce_recent_turns(recent_turns),
            "recent_messages": _coerce_recent_messages(recent_messages),
        }
    )
    redacted_state = redact_program_state(program_state)
    created_at_utc = project_utc_timestamp()
    bug_report_id = f"br_{utc_stamp()}_{uuid4().hex[:8]}"
    log_excerpt = read_backend_log_excerpt(
        workspace_root,
        session_id=session_id,
        turn_id=selected_turn,
    )
    report = AssistantBugReport(
        bug_report_id=bug_report_id,
        created_at_utc=created_at_utc,
        report_text=_redact_text(report_text.strip()),
        assistant_session_id=session_id,
        assistant_turn_id=selected_turn,
        assistant_provider_id=str(redacted_state.active_provider_id or "") or None,
        assistant_model_id=str(redacted_state.active_model_id or "") or None,
        provider_request_id=provider_request_id,
        model_tool_schema=model_tool_schema,
        model_tool_names=list(model_tool_names or []),
        scenario_id=str(redacted_state.active_scenario_id or "") or None,
        assistant_context=assistant_context,
        program_state=redacted_state,
        log_excerpt=log_excerpt,
        redactions_applied=True,
    )
    return report


def bug_report_dir(workspace_root: Path, bug_report_id: str) -> Path:
    root = bug_report_root(workspace_root)
    return (root / bug_report_id).resolve()


def save_bug_report_bundle(workspace_root: Path, bug_report: AssistantBugReport) -> Path:
    report_dir = bug_report_dir(workspace_root, bug_report.bug_report_id)
    report_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = report_dir / "bug-report.json"
    bundle_path.write_text(
        json.dumps(bug_report.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    markdown_path = report_dir / "bug-report.md"
    markdown_lines = [
        f"# Assistant Bug Report {bug_report.bug_report_id}",
        "",
        f"- Created: {bug_report.created_at_utc}",
        f"- Session: {bug_report.assistant_session_id or '(none)'}",
        f"- Turn: {bug_report.assistant_turn_id or '(none)'}",
        f"- Scenario: {bug_report.scenario_id or '(none)'}",
        f"- Provider: {bug_report.assistant_provider_id or '(none)'}",
        f"- Model: {bug_report.assistant_model_id or '(none)'}",
        "",
        "## User Report",
        "",
        bug_report.report_text,
        "",
        "## Program State",
        "",
        "```json",
        json.dumps(bug_report.program_state.model_dump(mode="json"), indent=2, sort_keys=True),
        "```",
        "",
        "## Assistant Context",
        "",
        "```json",
        json.dumps(bug_report.assistant_context, indent=2, sort_keys=True),
        "```",
        "",
        "## Log Excerpt",
        "",
        "```text",
        "\n".join(bug_report.log_excerpt),
        "```",
        "",
    ]
    markdown_path.write_text("\n".join(markdown_lines), encoding="utf-8")
    return bundle_path


def load_bug_report_bundle(workspace_root: Path, bug_report_id: str) -> AssistantBugReport:
    bundle_path = bug_report_dir(workspace_root, bug_report_id) / "bug-report.json"
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    return AssistantBugReport.model_validate(payload)


def list_bug_report_summaries(workspace_root: Path) -> list[AssistantBugReportSummary]:
    root = bug_report_root(workspace_root)
    if not root.exists():
        return []
    summaries: list[AssistantBugReportSummary] = []
    for report_dir in sorted([child for child in root.iterdir() if child.is_dir()], reverse=True):
        bundle_path = report_dir / "bug-report.json"
        if not bundle_path.exists():
            continue
        try:
            payload = json.loads(bundle_path.read_text(encoding="utf-8"))
            bug_report = AssistantBugReport.model_validate(payload)
        except Exception:
            continue
        summaries.append(
            AssistantBugReportSummary(
                bug_report_id=bug_report.bug_report_id,
                created_at_utc=bug_report.created_at_utc,
                report_text=bug_report.report_text,
                assistant_session_id=bug_report.assistant_session_id,
                assistant_turn_id=bug_report.assistant_turn_id,
                scenario_id=bug_report.scenario_id,
                bundle_path=str(bundle_path),
            )
        )
    return sorted(
        summaries,
        key=lambda summary: (summary.created_at_utc, summary.bug_report_id),
        reverse=True,
    )
