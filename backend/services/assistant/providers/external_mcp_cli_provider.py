from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable

from .base import ProviderCompletion, ProviderToolCall

logger = logging.getLogger(__name__)
CLI_TEXT_ENCODING = "utf-8"
CLI_TEXT_ERRORS = "replace"
LOG_TEXT_PREVIEW_CHARS = 240
LOG_ERROR_PREVIEW_CHARS = 1600
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_ANSI_BARE_SGR_RE = re.compile(r"\[[0-9;]*m")


def _render_conversation(conversation: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for message in conversation:
        role = str(message.get("role", "user")).strip() or "user"
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"{role.upper()}:\n{content}")
    return "\n\n".join(lines).strip()


def _render_prompt(system_prompt: str, conversation: list[dict[str, str]]) -> str:
    blocks = [f"SYSTEM:\n{system_prompt.strip()}"]
    convo = _render_conversation(conversation)
    if convo:
        blocks.append(convo)
    return "\n\n".join(blocks).strip() + "\n"


def _latest_user_prompt(conversation: list[dict[str, str]]) -> str | None:
    for message in reversed(conversation):
        if str(message.get("role", "")).strip().lower() != "user":
            continue
        text = str(message.get("content", "")).strip()
        if text:
            return text
    return None


def _parse_tool_calls(raw: object) -> list[ProviderToolCall]:
    if not isinstance(raw, list):
        return []
    out: list[ProviderToolCall] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        arguments = item.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        call_id = str(item.get("call_id", "")).strip() or f"external_mcp_cli_call_{idx + 1}"
        out.append(ProviderToolCall(call_id=call_id, name=name, arguments=arguments))
    return out


def _parse_usage(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        raw = {}
    models = raw.get("models") if isinstance(raw, dict) else None
    if isinstance(models, dict) and models:
        first = next(iter(models.values()))
        if isinstance(first, dict):
            tokens = first.get("tokens")
            if isinstance(tokens, dict):
                return {
                    "prompt_tokens": int(tokens.get("prompt", 0) or 0),
                    "completion_tokens": int(tokens.get("candidates", 0) or 0),
                    "cached_prompt_tokens": int(tokens.get("cached", 0) or 0),
                }
    return {
        "prompt_tokens": int(raw.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(raw.get("completion_tokens", 0) or 0),
        "cached_prompt_tokens": int(raw.get("cached_prompt_tokens", 0) or 0),
    }


def _safe_template(value: str, tokens: dict[str, str]) -> str:
    out = str(value)
    for key, token in tokens.items():
        out = out.replace("{" + key + "}", token)
    return out


def _stderr_tail(stderr_text: str, *, max_chars: int = 1200) -> str:
    text = str(stderr_text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _preview_text(value: object, *, max_chars: int = LOG_TEXT_PREVIEW_CHARS) -> str:
    text = str(value or "").replace("\r", "\\r").replace("\n", "\\n").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...<truncated>"


def _preview_head_tail(value: object, *, max_chars: int = LOG_ERROR_PREVIEW_CHARS) -> str:
    text = str(value or "").replace("\r", "\\r").replace("\n", "\\n").strip()
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-(max_chars // 2) :]
    return f"{head}...<truncated>...{tail}"


def _payload_summary(payload: dict[str, Any]) -> str:
    event = str(payload.get("event", payload.get("type", ""))).strip()
    keys = sorted(payload.keys())
    text = _extract_completion_text(payload)
    error = _extract_payload_error(payload)
    return (
        f"event_or_type={event!r} keys={keys} "
        f"text_len={len(text)} error={bool(error)} text_preview={_preview_text(text)}"
    )


def _sanitize_cli_text(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = _ANSI_ESCAPE_RE.sub("", text)
    text = _ANSI_BARE_SGR_RE.sub("", text)
    return text


def _normalize_access_mode(raw: str) -> str:
    mode = str(raw or "").strip().lower()
    if mode in {"mcp_only", "scenario_root"}:
        return mode
    return "mcp_only"


def _resolve_path(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _string_value(value: object) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _extract_payload_error(payload: dict[str, Any]) -> str:
    event = str(payload.get("event", payload.get("type", ""))).strip().lower()
    if event in {"error", "assistant_error", "turn.failed"}:
        nested = payload.get("error")
        if isinstance(nested, dict):
            nested_message = _string_value(nested.get("message"))
            if nested_message:
                return nested_message
        nested_text = _string_value(nested)
        if nested_text:
            return nested_text
        message = _string_value(payload.get("message"))
        if message:
            return message
    nested = payload.get("error")
    if isinstance(nested, dict):
        nested_message = _string_value(nested.get("message"))
        if nested_message:
            return nested_message
    return _string_value(nested)


def _extract_completion_text(payload: dict[str, Any]) -> str:
    for key in ("text", "content", "response", "output"):
        text = _string_value(payload.get(key))
        if text:
            return text
    message = payload.get("message")
    if isinstance(message, dict):
        text = _string_value(message.get("content"))
        if text:
            return text
    result = payload.get("result")
    if isinstance(result, dict):
        for key in ("text", "content", "response", "output"):
            text = _string_value(result.get(key))
            if text:
                return text

    nested = _extract_nested_assistant_text(payload)
    if nested:
        return nested
    return ""


def _extract_nested_assistant_text(payload: object) -> str:
    parts: list[str] = []

    def _append(text: object) -> None:
        value = _string_value(text)
        if not value:
            return
        if value not in parts:
            parts.append(value)

    def _walk(node: object, *, assistant_context: bool, depth: int) -> None:
        if depth > 10:
            return
        if isinstance(node, list):
            for item in node:
                _walk(item, assistant_context=assistant_context, depth=depth + 1)
            return
        if not isinstance(node, dict):
            return

        node_type = str(node.get("type", "")).strip().lower()
        role = str(node.get("role", node.get("author", ""))).strip().lower()
        is_assistant = assistant_context or role == "assistant" or node_type in {
            "agent_message",
            "assistant_message",
            "assistant",
        }

        if node_type == "output_text":
            _append(node.get("text"))

        if is_assistant:
            _append(node.get("text"))
            _append(node.get("response"))
            _append(node.get("output"))
            content = node.get("content")
            if isinstance(content, str):
                _append(content)

        content = node.get("content")
        if isinstance(content, (dict, list)):
            _walk(content, assistant_context=is_assistant, depth=depth + 1)

        parts_node = node.get("parts")
        if isinstance(parts_node, list):
            for part in parts_node:
                if isinstance(part, dict):
                    _append(part.get("text"))
                _walk(part, assistant_context=is_assistant, depth=depth + 1)

        for key in ("message", "item", "result", "response", "output", "candidates", "candidate"):
            if key in node:
                _walk(node[key], assistant_context=is_assistant, depth=depth + 1)

    _walk(payload, assistant_context=False, depth=0)
    return "\n".join(parts).strip()


def _extract_stats_tool_failure(payload: dict[str, Any]) -> str:
    stats = payload.get("stats")
    if not isinstance(stats, dict):
        return ""
    tools = stats.get("tools")
    if not isinstance(tools, dict):
        return ""
    total_fail = int(tools.get("totalFail", 0) or 0)
    if total_fail <= 0:
        return ""
    by_name = tools.get("byName")
    failed: list[str] = []
    if isinstance(by_name, dict):
        for name, data in by_name.items():
            if not isinstance(data, dict):
                continue
            if int(data.get("fail", 0) or 0) > 0:
                failed.append(str(name))
    if failed:
        return f"provider reported tool failures with no assistant response: {', '.join(failed[:5])}"
    return "provider reported tool failures with no assistant response"


def _resolve_executable(raw: str) -> str:
    token = str(raw or "").strip()
    if not token:
        return token
    path = Path(token)
    if path.is_absolute() or path.parent != Path("."):
        return str(path)
    resolved = shutil.which(token)
    if resolved:
        return resolved
    return token


def _ensure_within_root(*, root: Path, candidate: Path) -> None:
    if candidate == root:
        return
    if root not in candidate.parents:
        raise RuntimeError(f"Working directory escapes scenario root: {candidate}")


def _extract_delta_text(payload: dict[str, Any]) -> str:
    event = str(payload.get("event", payload.get("type", ""))).strip().lower()
    if event.endswith(".delta"):
        for key in ("delta", "text_delta", "text", "chunk"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    if "text_delta" in payload:
        return str(payload.get("text_delta", ""))
    if "delta" in payload and not isinstance(payload.get("delta"), dict):
        return str(payload.get("delta", ""))
    if "chunk" in payload and not isinstance(payload.get("chunk"), dict):
        return str(payload.get("chunk", ""))
    if "text" in payload and str(payload.get("event", "")).strip().lower() in {
        "delta",
        "assistant_delta",
        "chunk",
        "token",
    }:
        return str(payload.get("text", ""))
    return ""


def _payload_is_delta(payload: dict[str, Any]) -> bool:
    event = str(payload.get("event", payload.get("type", ""))).strip().lower()
    if event in {"delta", "assistant_delta", "chunk", "token", "text_delta"}:
        return True
    if event.endswith(".delta"):
        return True
    if "text_delta" in payload:
        return True
    if "delta" in payload and not any(
        key in payload for key in ("finish_reason", "tool_calls", "usage", "cache_attempted", "cache_applied")
    ):
        return True
    return False


def _payload_to_completion(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    event = str(payload.get("event", payload.get("type", ""))).strip().lower()
    if event == "item.completed":
        item = payload.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type", "")).strip().lower()
            if item_type in {"agent_message", "assistant_message", "assistant"}:
                text = _extract_completion_text(payload)
                if text:
                    return {"text": text}

    if event in {
        "final",
        "assistant_final",
        "complete",
        "done",
        "result",
        "turn.completed",
        "response.completed",
    }:
        result = payload.get("result")
        if isinstance(result, dict):
            return result
        return payload

    if bool(payload.get("done", False)):
        result = payload.get("result")
        if isinstance(result, dict):
            return result
        return payload

    if _payload_is_delta(payload):
        return None

    completion_keys = {
        "text",
        "content",
        "response",
        "output",
        "tool_calls",
        "finish_reason",
        "usage",
        "cache_attempted",
        "cache_applied",
    }
    if any(key in payload for key in completion_keys):
        return payload

    return None


class PersistentCliProcess:
    """Persistent interactive CLI process with timeout-safe stdout parsing."""

    def __init__(
        self,
        *,
        fingerprint: str,
        cmd: list[str],
        env: dict[str, str],
        cwd: str | None,
        timeout_seconds: float,
        cleanup_callback: Callable[[], None] | None = None,
    ) -> None:
        self.fingerprint = str(fingerprint)
        self.cmd = list(cmd)
        self.env = dict(env)
        self.cwd = cwd
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._cleanup_callback = cleanup_callback

        self._proc: subprocess.Popen[str] | None = None
        self._state_lock = threading.RLock()
        self._turn_lock = threading.Lock()
        self._stdout_chars: Queue[str | None] = Queue()
        self._stderr_lines: deque[str] = deque(maxlen=200)
        self._last_active = time.monotonic()
        self._in_flight = False

    def is_alive(self) -> bool:
        with self._state_lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def idle_seconds(self) -> float:
        with self._state_lock:
            return max(0.0, time.monotonic() - self._last_active)

    @property
    def in_flight(self) -> bool:
        with self._state_lock:
            return self._in_flight

    def start(self) -> None:
        with self._state_lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            self._stderr_lines.clear()
            self._stdout_chars = Queue()
            self._proc = subprocess.Popen(
                self.cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=self.env,
                text=True,
                encoding=CLI_TEXT_ENCODING,
                errors=CLI_TEXT_ERRORS,
                bufsize=0,
            )
            self._last_active = time.monotonic()
            threading.Thread(target=self._stdout_reader_loop, daemon=True).start()
            threading.Thread(target=self._stderr_reader_loop, daemon=True).start()

    def _stdout_reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._stdout_chars.put(None)
            return
        try:
            while True:
                ch = proc.stdout.read(1)
                if ch == "":
                    break
                self._stdout_chars.put(ch)
        except Exception:
            pass
        finally:
            self._stdout_chars.put(None)

    def _stderr_reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                with self._state_lock:
                    self._stderr_lines.append(str(line))
        except Exception:
            return

    def _drain_stdout(self) -> None:
        # Drain any trailing bytes from prior turns and wait briefly for pipe quiescence.
        quiet_window_seconds = 0.1
        max_wait_seconds = 1.0
        deadline = time.monotonic() + max_wait_seconds
        quiet_until = time.monotonic() + quiet_window_seconds
        while True:
            now = time.monotonic()
            if now >= deadline or now >= quiet_until:
                return
            timeout = min(0.02, deadline - now, quiet_until - now)
            if timeout <= 0:
                return
            try:
                self._stdout_chars.get(timeout=timeout)
                quiet_until = time.monotonic() + quiet_window_seconds
            except Empty:
                continue

    def _stderr_snapshot(self) -> str:
        with self._state_lock:
            return "".join(self._stderr_lines)

    def send_and_receive(
        self,
        prompt: str,
        *,
        on_delta: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        self.start()
        with self._turn_lock:
            with self._state_lock:
                proc = self._proc
                self._in_flight = True
                self._last_active = time.monotonic()
            if proc is None or proc.stdin is None:
                raise RuntimeError("Persistent CLI process is not available.")

            self._drain_stdout()
            try:
                proc.stdin.write(prompt)
                if not prompt.endswith("\n"):
                    proc.stdin.write("\n")
                proc.stdin.flush()
                payload = self._read_turn_completion(proc=proc, on_delta=on_delta)
            except Exception:
                self.stop()
                raise
            finally:
                with self._state_lock:
                    self._in_flight = False
                    self._last_active = time.monotonic()
            return payload

    def _read_turn_completion(
        self,
        *,
        proc: subprocess.Popen[str],
        on_delta: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        collecting = False
        depth = 0
        in_string = False
        escape = False
        object_chars: list[str] = []
        plain_text_parts: list[str] = []

        while True:
            now = time.monotonic()
            if now >= deadline:
                stderr = _stderr_tail(self._stderr_snapshot())
                message = f"CLI turn timed out after {self.timeout_seconds}s."
                if stderr:
                    message = f"{message} stderr: {stderr}"
                raise RuntimeError(message)

            if proc.poll() is not None and self._stdout_chars.empty():
                stderr = _stderr_tail(self._stderr_snapshot())
                if plain_text_parts:
                    return {"text": "".join(plain_text_parts).strip()}
                message = "CLI process exited before returning completion payload."
                if stderr:
                    message = f"{message} stderr: {stderr}"
                raise RuntimeError(message)

            try:
                ch = self._stdout_chars.get(timeout=min(0.2, max(0.01, deadline - now)))
            except Empty:
                continue

            if ch is None:
                if proc.poll() is None:
                    continue
                if plain_text_parts:
                    return {"text": "".join(plain_text_parts).strip()}
                stderr = _stderr_tail(self._stderr_snapshot())
                message = "CLI process closed stdout before completion payload."
                if stderr:
                    message = f"{message} stderr: {stderr}"
                raise RuntimeError(message)

            if not collecting:
                if ch == "{":
                    collecting = True
                    depth = 1
                    in_string = False
                    escape = False
                    object_chars = [ch]
                else:
                    plain_text_parts.append(ch)
                continue

            object_chars.append(ch)
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                depth += 1
                continue
            if ch != "}":
                continue

            depth -= 1
            if depth > 0:
                continue

            collecting = False
            raw_payload = "".join(object_chars)
            object_chars = []
            try:
                payload = json.loads(raw_payload)
            except Exception:
                plain_text_parts.append(raw_payload)
                continue
            if not isinstance(payload, dict):
                continue

            if _payload_is_delta(payload):
                if on_delta:
                    delta_text = _extract_delta_text(payload)
                    if delta_text:
                        on_delta(delta_text)
                continue

            completion = _payload_to_completion(payload)
            if completion is not None:
                return completion

    def stop(self) -> None:
        cleanup: Callable[[], None] | None = None
        with self._state_lock:
            proc = self._proc
            self._proc = None
            self._in_flight = False
            cleanup = self._cleanup_callback
            self._cleanup_callback = None
            self._stdout_chars.put(None)

        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        if cleanup is not None:
            try:
                cleanup()
            except Exception:
                logger.debug("Persistent CLI cleanup callback failed", exc_info=True)


@dataclass
class ExternalMcpCliProvider:
    provider_id: str
    command: list[str]
    default_model: str
    models: list[str]
    mcp_sse_url: str
    args: list[str] = field(default_factory=list)
    access_mode: str = "mcp_only"
    mcp_only_args: list[str] = field(default_factory=list)
    scenario_root_args: list[str] = field(default_factory=list)
    scenario_root: str | None = None
    mcp_auth_token_env: str | None = None
    working_directory: str | None = None
    timeout_seconds: float = 180.0
    mcp_server_name: str = "lunar_analyst"
    mcp_registration_mode: str = "none"
    execution_mode: str = "external_mcp_agent"
    kind: str = "local"
    persistent: bool = False
    stdin_mode: str = "stream"
    idle_timeout_seconds: float = 600.0

    _processes: dict[str, PersistentCliProcess] = field(default_factory=dict, init=False)
    _session_generation: dict[str, int] = field(default_factory=dict, init=False)
    _process_lock: threading.RLock = field(default_factory=threading.RLock, init=False)

    def list_models(self) -> list[str]:
        return list(self.models)

    def cleanup_idle_processes(self) -> None:
        if not self.persistent:
            return
        stale: list[tuple[str, PersistentCliProcess]] = []
        with self._process_lock:
            for session_id, proc in list(self._processes.items()):
                if not proc.is_alive():
                    stale.append((session_id, proc))
                    continue
                if proc.in_flight:
                    continue
                if proc.idle_seconds > max(1.0, float(self.idle_timeout_seconds)):
                    stale.append((session_id, proc))
            for session_id, _ in stale:
                self._processes.pop(session_id, None)

        for session_id, proc in stale:
            logger.info("Stopping idle persistent CLI process provider=%s session=%s", self.provider_id, session_id)
            proc.stop()

    def reset_session(self, session_id: str) -> None:
        target = str(session_id or "").strip()
        if not target:
            return
        proc: PersistentCliProcess | None = None
        with self._process_lock:
            self._session_generation[target] = int(self._session_generation.get(target, 0)) + 1
            proc = self._processes.pop(target, None)
        if proc is not None:
            proc.stop()

    def shutdown(self) -> None:
        with self._process_lock:
            all_processes = list(self._processes.values())
            self._processes.clear()
        for proc in all_processes:
            proc.stop()

    def complete(
        self,
        *,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        session_id: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        cache_context: dict[str, str] | None = None,
        tool_schema: list[dict[str, object]] | None = None,
        max_output_tokens: int | None = None,
        access_mode: str | None = None,
        scenario_working_directory: str | None = None,
    ) -> ProviderCompletion:
        del cache_context
        del tool_schema
        del max_output_tokens

        if not self.command:
            raise RuntimeError(f"{self.provider_id}: CLI command is not configured.")

        selected_model = str(model_id or self.default_model).strip() or self.default_model
        selected_access_mode = _normalize_access_mode(access_mode or self.access_mode)
        auth_token = ""
        if self.mcp_auth_token_env:
            auth_token = os.getenv(self.mcp_auth_token_env, "").strip()

        normalized_session_id = str(session_id or "").strip()
        if self.persistent and not normalized_session_id:
            raise RuntimeError(f"{self.provider_id}: persistent mode requires assistant session_id.")
        effective_session_id = self._effective_external_session_id(normalized_session_id)
        stdin_mode = str(self.stdin_mode or "stream").strip().lower()
        if stdin_mode not in {"stream", "turn_eof"}:
            stdin_mode = "stream"

        prompt_text = _render_prompt(system_prompt, conversation)
        configured_scenario_root = str(self.scenario_root or "").strip()
        scenario_root = configured_scenario_root if selected_access_mode == "scenario_root" else ""
        tokens = {
            "model_id": selected_model,
            "session_id": effective_session_id,
            "mcp_sse_url": self.mcp_sse_url,
            "mcp_auth_token": auth_token,
            "provider_id": self.provider_id,
            "access_mode": selected_access_mode,
            "scenario_root": scenario_root,
            "prompt_text": prompt_text,
            "mcp_server_name": str(self.mcp_server_name or "lunar_analyst"),
        }
        mode_args = self.mcp_only_args if selected_access_mode == "mcp_only" else self.scenario_root_args
        prompt_is_arg = any(
            "{prompt_text}" in str(item) for item in [*self.command, *self.args, *mode_args]
        )
        if self.persistent and stdin_mode == "stream" and prompt_is_arg:
            raise RuntimeError(
                f"{self.provider_id}: persistent mode requires stdin prompt delivery; remove {{prompt_text}} args."
            )

        cmd = [
            *self.command,
            *[_safe_template(item, tokens) for item in self.args],
            *[_safe_template(item, tokens) for item in mode_args],
        ]
        if cmd:
            cmd[0] = _resolve_executable(cmd[0])
        cmd = self._inject_codex_mcp_overrides(cmd, auth_token=auth_token)
        codex_mcp_overrides_applied = any("mcp_servers." in str(item) for item in cmd)

        env = os.environ.copy()
        env["LUNAR_ANALYST_MCP_SSE_URL"] = self.mcp_sse_url
        if auth_token:
            env["LUNAR_ANALYST_MCP_TOKEN"] = auth_token
        env["LUNAR_ANALYST_MODEL_ID"] = selected_model
        if effective_session_id:
            env["LUNAR_ANALYST_SESSION_ID"] = effective_session_id
        env["LUNAR_ANALYST_PROVIDER_ID"] = self.provider_id
        env["LUNAR_ANALYST_ACCESS_MODE"] = selected_access_mode
        if scenario_root:
            env["LUNAR_ANALYST_SCENARIO_ROOT"] = scenario_root

        final_cwd = self._resolve_final_cwd(
            selected_access_mode=selected_access_mode,
            scenario_root=scenario_root,
            scenario_working_directory=scenario_working_directory,
        )
        logger.info(
            "assistant external cli turn start provider=%s model=%s session=%s access_mode=%s stdin_mode=%s cwd=%s codex_mcp_overrides=%s cmd=%s",
            self.provider_id,
            selected_model,
            effective_session_id or "<none>",
            selected_access_mode,
            stdin_mode,
            final_cwd or "<none>",
            codex_mcp_overrides_applied,
            [cmd[0], *cmd[1: min(7, len(cmd))]],
        )

        if self.persistent and stdin_mode == "stream":
            incremental_prompt = _latest_user_prompt(conversation)
            parsed = self._complete_persistent(
                session_id=normalized_session_id,
                cmd=cmd,
                env=env,
                cwd=final_cwd,
                full_prompt_text=prompt_text,
                incremental_prompt_text=incremental_prompt,
                auth_token=auth_token,
                model_id=selected_model,
                access_mode=selected_access_mode,
                on_delta=on_delta,
                effective_session_id=effective_session_id,
            )
        elif self.persistent and stdin_mode == "turn_eof":
            parsed = self._complete_oneshot(
                cmd=cmd,
                env=env,
                cwd=final_cwd,
                prompt_text="" if prompt_is_arg else prompt_text,
                auth_token=auth_token,
                on_delta=on_delta,
            )
        else:
            parsed = self._complete_oneshot(
                cmd=cmd,
                env=env,
                cwd=final_cwd,
                prompt_text="" if prompt_is_arg else prompt_text,
                auth_token=auth_token,
                on_delta=on_delta,
            )

        text = _string_value(parsed.get("text"))
        if not text:
            text = _string_value(parsed.get("content"))
        if not text:
            text = _string_value(parsed.get("response"))
        if not text:
            text = _string_value(parsed.get("output"))
        if not text:
            text = _extract_completion_text(parsed)
        text = _sanitize_cli_text(text)
        if not text:
            stats_failure = _extract_stats_tool_failure(parsed)
            if stats_failure:
                raise RuntimeError(f"{self.provider_id}: {stats_failure}")
        if not text:
            logger.warning(
                "assistant external cli parsed empty response provider=%s model=%s session=%s parsed_summary=%s",
                self.provider_id,
                selected_model,
                effective_session_id or "<none>",
                _payload_summary(parsed),
            )
        tool_calls = _parse_tool_calls(parsed.get("tool_calls"))
        finish_reason = str(parsed.get("finish_reason", "")).strip() or ("tool_calls" if tool_calls else "stop")
        usage = _parse_usage(parsed.get("usage") or parsed.get("stats"))
        logger.info(
            "assistant external cli turn parsed provider=%s model=%s session=%s text_len=%s tool_calls=%s finish_reason=%s usage=%s",
            self.provider_id,
            selected_model,
            effective_session_id or "<none>",
            len(text),
            len(tool_calls),
            finish_reason,
            usage,
        )
        return ProviderCompletion(
            text=text,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            cache_attempted=bool(parsed.get("cache_attempted", False)),
            cache_applied=bool(parsed.get("cache_applied", False)),
        )

    def _effective_external_session_id(self, session_id: str) -> str:
        target = str(session_id or "").strip()
        if not target:
            return ""
        with self._process_lock:
            generation = int(self._session_generation.get(target, 0))
        return f"{target}:g{generation}"

    def _resolve_final_cwd(
        self,
        *,
        selected_access_mode: str,
        scenario_root: str,
        scenario_working_directory: str | None,
    ) -> str | None:
        configured_cwd = str(self.working_directory or "").strip() or None
        per_turn_cwd = str(scenario_working_directory or "").strip() or None
        if selected_access_mode == "scenario_root":
            if not scenario_root:
                raise RuntimeError(
                    f"{self.provider_id}: scenario_root access mode requires configured workspace root."
                )
            root_path = _resolve_path(scenario_root)
            if per_turn_cwd:
                per_turn_cwd_path = _resolve_path(per_turn_cwd)
                _ensure_within_root(root=root_path, candidate=per_turn_cwd_path)
                return str(per_turn_cwd_path)
            if configured_cwd:
                cwd_path = _resolve_path(configured_cwd)
                _ensure_within_root(root=root_path, candidate=cwd_path)
                return str(cwd_path)
            return str(root_path)

        if configured_cwd:
            return configured_cwd
        return None

    def _build_process_fingerprint(
        self,
        *,
        cmd: list[str],
        cwd: str | None,
        model_id: str,
        access_mode: str,
        effective_session_id: str,
    ) -> str:
        payload = {
            "cmd": cmd,
            "cwd": cwd,
            "model_id": model_id,
            "access_mode": access_mode,
            "session": effective_session_id,
            "provider_id": self.provider_id,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _complete_oneshot(
        self,
        *,
        cmd: list[str],
        env: dict[str, str],
        cwd: str | None,
        prompt_text: str,
        auth_token: str,
        on_delta: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        result = self._run_cli_oneshot(
            cmd=cmd,
            prompt_text=prompt_text,
            env=env,
            cwd=cwd,
            auth_token=auth_token,
        )
        logger.info(
            "assistant external cli oneshot completed provider=%s returncode=%s stdout_chars=%s stderr_chars=%s",
            self.provider_id,
            result.returncode,
            len(str(result.stdout or "")),
            len(str(result.stderr or "")),
        )

        stderr_tail = _stderr_tail(result.stderr)
        if result.returncode != 0:
            logger.warning(
                "assistant external cli nonzero exit provider=%s returncode=%s cmd=%s stderr_full_preview=%s stdout_full_preview=%s",
                self.provider_id,
                result.returncode,
                [cmd[0], *cmd[1: min(20, len(cmd))]],
                _preview_head_tail(result.stderr),
                _preview_head_tail(result.stdout),
            )
            message = f"{self.provider_id}: CLI exited with code {result.returncode}."
            if stderr_tail:
                message = f"{message} stderr: {stderr_tail}"
            raise RuntimeError(message)

        stdout_text = str(result.stdout or "").strip()
        if not stdout_text:
            if stderr_tail:
                logger.info(
                    "assistant external cli oneshot empty stdout provider=%s stderr_tail=%s",
                    self.provider_id,
                    _preview_text(stderr_tail),
                )
            return {}
        return self._parse_oneshot_stdout(stdout_text=stdout_text, on_delta=on_delta)

    def _parse_oneshot_stdout(
        self,
        *,
        stdout_text: str,
        on_delta: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        try:
            payload = json.loads(stdout_text)
        except Exception:
            logger.info(
                "assistant external cli parse mode=jsonl provider=%s stdout_preview=%s",
                self.provider_id,
                _preview_text(stdout_text),
            )
            return self._parse_json_lines_stdout(stdout_text=stdout_text, on_delta=on_delta)
        if isinstance(payload, dict):
            error_text = _extract_payload_error(payload)
            if error_text:
                raise RuntimeError(f"{self.provider_id}: {error_text}")
            logger.info(
                "assistant external cli parse mode=json-object provider=%s payload=%s",
                self.provider_id,
                _payload_summary(payload),
            )
            return payload
        if isinstance(payload, list):
            merged = [item for item in payload if isinstance(item, dict)]
            if not merged:
                return {}
            logger.info(
                "assistant external cli parse mode=json-array provider=%s payload_count=%s",
                self.provider_id,
                len(merged),
            )
            return self._parse_payload_sequence(merged, on_delta=on_delta)
        logger.info(
            "assistant external cli parse mode=text provider=%s stdout_preview=%s",
            self.provider_id,
            _preview_text(stdout_text),
        )
        return {"text": stdout_text}

    def _parse_json_lines_stdout(
        self,
        *,
        stdout_text: str,
        on_delta: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        payloads: list[dict[str, Any]] = []
        dropped_lines = 0
        for line in stdout_text.splitlines():
            item = line.strip()
            if not item:
                continue
            try:
                loaded = json.loads(item)
            except Exception:
                dropped_lines += 1
                continue
            if isinstance(loaded, dict):
                payloads.append(loaded)
        if not payloads:
            logger.warning(
                "assistant external cli jsonl parse produced no json payloads provider=%s dropped_lines=%s stdout_preview=%s",
                self.provider_id,
                dropped_lines,
                _preview_text(stdout_text),
            )
            return {"text": stdout_text}
        logger.info(
            "assistant external cli jsonl parsed provider=%s payloads=%s dropped_lines=%s first_events=%s",
            self.provider_id,
            len(payloads),
            dropped_lines,
            [str(p.get("event", p.get("type", ""))) for p in payloads[:5]],
        )
        return self._parse_payload_sequence(payloads, on_delta=on_delta)

    def _parse_payload_sequence(
        self,
        payloads: list[dict[str, Any]],
        *,
        on_delta: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        final_payload: dict[str, Any] | None = None
        delta_parts: list[str] = []
        last_text = ""
        last_error = ""
        delta_count = 0

        for payload in payloads:
            error_text = _extract_payload_error(payload)
            if error_text:
                last_error = error_text

            if _payload_is_delta(payload):
                delta = _extract_delta_text(payload)
                if delta:
                    delta_parts.append(delta)
                    delta_count += 1
                    if on_delta is not None:
                        on_delta(delta)

            completion = _payload_to_completion(payload)
            if completion is not None:
                if final_payload is None:
                    final_payload = completion
                else:
                    if _extract_completion_text(completion):
                        final_payload = completion

            completion_text = _extract_completion_text(payload)
            if completion_text:
                last_text = completion_text

        if final_payload is not None:
            final_text = _extract_completion_text(final_payload)
            if not final_text and delta_parts:
                merged = dict(final_payload)
                merged["text"] = "".join(delta_parts)
                logger.info(
                    "assistant external cli payload sequence resolved via deltas provider=%s payloads=%s deltas=%s",
                    self.provider_id,
                    len(payloads),
                    delta_count,
                )
                return merged
            if not final_text and last_text:
                merged = dict(final_payload)
                merged["text"] = last_text
                logger.info(
                    "assistant external cli payload sequence resolved via prior text provider=%s payloads=%s deltas=%s text_len=%s",
                    self.provider_id,
                    len(payloads),
                    delta_count,
                    len(last_text),
                )
                return merged
            logger.info(
                "assistant external cli payload sequence resolved via final payload provider=%s payloads=%s deltas=%s summary=%s",
                self.provider_id,
                len(payloads),
                delta_count,
                _payload_summary(final_payload),
            )
            return final_payload

        if delta_parts:
            logger.info(
                "assistant external cli payload sequence resolved via delta concat provider=%s payloads=%s deltas=%s text_len=%s",
                self.provider_id,
                len(payloads),
                delta_count,
                len("".join(delta_parts)),
            )
            return {"text": "".join(delta_parts)}
        if last_text:
            logger.info(
                "assistant external cli payload sequence resolved via trailing text provider=%s payloads=%s text_len=%s",
                self.provider_id,
                len(payloads),
                len(last_text),
            )
            return {"text": last_text}
        if last_error:
            raise RuntimeError(f"{self.provider_id}: {last_error}")
        logger.warning(
            "assistant external cli payload sequence produced empty result provider=%s payloads=%s first_payload=%s",
            self.provider_id,
            len(payloads),
            _payload_summary(payloads[0]) if payloads else "<none>",
        )
        return {}

    def _complete_persistent(
        self,
        *,
        session_id: str,
        cmd: list[str],
        env: dict[str, str],
        cwd: str | None,
        full_prompt_text: str,
        incremental_prompt_text: str | None,
        auth_token: str,
        model_id: str,
        access_mode: str,
        on_delta: Callable[[str], None] | None,
        effective_session_id: str,
    ) -> dict[str, Any]:
        fingerprint = self._build_process_fingerprint(
            cmd=cmd,
            cwd=cwd,
            model_id=model_id,
            access_mode=access_mode,
            effective_session_id=effective_session_id,
        )

        to_stop: PersistentCliProcess | None = None
        is_new = False
        with self._process_lock:
            proc = self._processes.get(session_id)
            if proc is not None and (not proc.is_alive() or proc.fingerprint != fingerprint):
                to_stop = proc
                proc = None
                self._processes.pop(session_id, None)
            if proc is None:
                cleanup = self._configure_mcp_server(cwd=cwd, env=env, auth_token=auth_token)
                proc = PersistentCliProcess(
                    fingerprint=fingerprint,
                    cmd=cmd,
                    env=env,
                    cwd=cwd,
                    timeout_seconds=self.timeout_seconds,
                    cleanup_callback=cleanup,
                )
                self._processes[session_id] = proc
                is_new = True

        if to_stop is not None:
            to_stop.stop()

        prompt_to_send = full_prompt_text if is_new else str(incremental_prompt_text or "").strip()
        if not prompt_to_send:
            prompt_to_send = full_prompt_text

        try:
            return proc.send_and_receive(prompt_to_send, on_delta=on_delta)
        except Exception:
            with self._process_lock:
                if self._processes.get(session_id) is proc:
                    self._processes.pop(session_id, None)
            raise

    def _run_cli_oneshot(
        self,
        *,
        cmd: list[str],
        prompt_text: str,
        env: dict[str, str],
        cwd: str | None,
        auth_token: str,
    ) -> subprocess.CompletedProcess[str]:
        timeout_seconds = float(self.timeout_seconds)

        def _invoke(actual_cwd: str | None) -> subprocess.CompletedProcess[str]:
            try:
                return subprocess.run(
                    cmd,
                    input=prompt_text,
                    capture_output=True,
                    text=True,
                    encoding=CLI_TEXT_ENCODING,
                    errors=CLI_TEXT_ERRORS,
                    timeout=timeout_seconds,
                    check=False,
                    cwd=actual_cwd,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"{self.provider_id}: CLI invocation timed out after {self.timeout_seconds}s."
                ) from exc
            except Exception as exc:
                raise RuntimeError(f"{self.provider_id}: CLI launch failed: {exc}") from exc

        cleanup = self._configure_mcp_server(cwd=cwd, env=env, auth_token=auth_token)
        try:
            if cwd:
                return _invoke(cwd)
            with tempfile.TemporaryDirectory(prefix="lunar_analyst_cli_safe_") as tmp_dir:
                return _invoke(tmp_dir)
        finally:
            if cleanup:
                cleanup()

    def _configure_mcp_server(
        self,
        *,
        cwd: str | None,
        env: dict[str, str],
        auth_token: str,
    ) -> Callable[[], None] | None:
        mode = str(self.mcp_registration_mode or "none").strip().lower()
        if mode == "codex":
            return None
        if mode != "gemini":
            return None
        server_name = str(self.mcp_server_name or "lunar_analyst").strip() or "lunar_analyst"
        cli = _resolve_executable(self.command[0] if self.command else "")
        if not cli:
            return None
        self._run_mcp_setup_command(
            [cli, "mcp", "remove", server_name],
            cwd=cwd,
            env=env,
            allow_failure=True,
        )
        add_cmd = [
            cli,
            "mcp",
            "add",
            server_name,
            self.mcp_sse_url,
            "--transport",
            "sse",
            "--scope",
            "project",
        ]
        if auth_token:
            add_cmd.extend(["--header", f"Authorization: Bearer {auth_token}"])
        self._run_mcp_setup_command(add_cmd, cwd=cwd, env=env, allow_failure=False)
        return lambda: self._run_mcp_setup_command(
            [cli, "mcp", "remove", server_name],
            cwd=cwd,
            env=env,
            allow_failure=True,
        )

    def _run_mcp_setup_command(
        self,
        cmd: list[str],
        *,
        cwd: str | None,
        env: dict[str, str],
        allow_failure: bool,
    ) -> None:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding=CLI_TEXT_ENCODING,
                errors=CLI_TEXT_ERRORS,
                timeout=min(float(self.timeout_seconds), 30.0),
                check=False,
                cwd=cwd,
                env=env,
            )
        except Exception as exc:
            if allow_failure:
                return
            raise RuntimeError(f"{self.provider_id}: MCP CLI setup failed: {exc}") from exc
        if allow_failure or result.returncode == 0:
            return
        stderr_tail = _stderr_tail(result.stderr)
        msg = f"{self.provider_id}: MCP CLI setup command failed with code {result.returncode}."
        if stderr_tail:
            msg = f"{msg} stderr: {stderr_tail}"
        raise RuntimeError(msg)

    def _inject_codex_mcp_overrides(self, cmd: list[str], *, auth_token: str = "") -> list[str]:
        mode = str(self.mcp_registration_mode or "none").strip().lower()
        if mode != "codex":
            return cmd
        server_name = str(self.mcp_server_name or "lunar_analyst").strip() or "lunar_analyst"
        out = list(cmd)
        out.extend(
            [
                "-c",
                f"mcp_servers.{server_name}.url={json.dumps(self.mcp_sse_url)}",
            ]
        )
        if self.mcp_auth_token_env and str(auth_token or "").strip():
            out.extend(
                [
                    "-c",
                    f"mcp_servers.{server_name}.bearer_token_env_var={json.dumps(self.mcp_auth_token_env)}",
                ]
            )
        return out
