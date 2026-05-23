from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib import error, request

from .base import ProviderCompletion, ProviderToolCall

_MODEL_METADATA_CACHE_TABLE = "assistant_provider_model_metadata_cache"


@dataclass(frozen=True)
class OllamaProvider:
    provider_id: str
    base_url: str
    default_model: str
    models: list[str]
    timeout_seconds: float = 60.0
    keep_alive: str | None = None
    discover_models: bool = True
    model_metadata_cache_db_path: str | None = None
    model_metadata_cache_ttl_seconds: int = 86400
    max_context_tokens: int | None = None
    _model_metadata_cache: dict[str, dict[str, Any]] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _model_context_limit_cache: dict[str, int | None] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _active_num_ctx_by_model: dict[str, int] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )

    def list_models(self) -> list[str]:
        configured = _dedupe_models(self.models)
        if not self.discover_models:
            return configured
        discovered = self._discover_models()
        if not discovered:
            return configured
        return _dedupe_models([*discovered, *configured])

    def list_model_metadata(self, *, models: list[str] | None = None) -> dict[str, dict[str, Any]]:
        selected_models = _dedupe_models(models or self.list_models())
        metadata: dict[str, dict[str, Any]] = {}
        for model_id in selected_models:
            metadata[model_id] = self._fetch_model_metadata(model_id)
        return metadata

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
        thinking: bool | str | None = None,
    ) -> ProviderCompletion:
        del session_id
        del on_delta
        del cache_context
        url = f"{self.base_url.rstrip('/')}/api/chat"
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(conversation)
        payload = {
            "model": model_id or self.default_model,
            "messages": messages,
            "stream": False,
        }
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        options: dict[str, int] = {}
        num_ctx = self._select_num_ctx(
            model_id=str(payload["model"]),
            messages=messages,
            max_output_tokens=max_output_tokens,
        )
        if num_ctx is not None:
            options["num_ctx"] = int(num_ctx)
        if max_output_tokens is not None and max_output_tokens > 0:
            options["num_predict"] = int(max_output_tokens)
        if options:
            payload["options"] = options
        if tool_schema:
            payload["tools"] = tool_schema
        normalized_thinking = _normalize_thinking_value(thinking)
        if normalized_thinking is not None:
            payload["think"] = normalized_thinking
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=url,
            method="POST",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw)
        except error.URLError as exc:
            raise RuntimeError(f"Ollama provider unavailable: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Ollama provider failed: {exc}") from exc

        message = parsed.get("message", {})
        text = ""
        tool_calls: list[ProviderToolCall] = []
        if isinstance(message, dict):
            text = str(message.get("content", "")).strip()
            tool_calls = _extract_ollama_tool_calls(message.get("tool_calls"))
        if not text:
            text = str(parsed.get("response", "")).strip()

        prompt_eval_count = _as_int(parsed.get("prompt_eval_count"))
        eval_count = _as_int(parsed.get("eval_count"))
        usage = {
            "prompt_tokens": prompt_eval_count,
            "completion_tokens": eval_count,
            "cached_prompt_tokens": 0,
        }
        completion_metadata: dict[str, object] = {}
        requested_num_ctx = options.get("num_ctx")
        if isinstance(requested_num_ctx, int) and requested_num_ctx > 0:
            completion_metadata["num_ctx"] = int(requested_num_ctx)
        requested_num_predict = options.get("num_predict")
        if isinstance(requested_num_predict, int) and requested_num_predict > 0:
            completion_metadata["num_predict"] = int(requested_num_predict)
        return ProviderCompletion(
            text=text,
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            usage=usage,
            cache_attempted=False,
            cache_applied=False,
            metadata=completion_metadata,
        )

    def _fetch_model_metadata(self, model_id: str) -> dict[str, Any]:
        cached = self._model_metadata_cache.get(model_id)
        if cached is not None:
            return dict(cached)
        persisted = self._read_persisted_model_metadata(model_id)
        if persisted is not None:
            self._model_metadata_cache[model_id] = dict(persisted)
            return persisted
        payload = self._show(model_id)
        capabilities = _normalize_capabilities(payload.get("capabilities"))
        modelfile = str(payload.get("modelfile", "") or "")
        thinking_mode = "none"
        if "thinking" in capabilities:
            if "ThinkLevel" in modelfile or model_id.startswith("gpt-oss:"):
                thinking_mode = "level"
            else:
                thinking_mode = "boolean"
        metadata = {
            "capabilities": capabilities,
            "thinking_mode": thinking_mode,
        }
        self._model_metadata_cache[model_id] = dict(metadata)
        if payload:
            self._write_persisted_model_metadata(model_id, metadata)
        return metadata

    def _select_num_ctx(
        self,
        *,
        model_id: str,
        messages: list[dict[str, str]],
        max_output_tokens: int | None,
    ) -> int | None:
        del messages
        del max_output_tokens
        hard_max = _positive_int_or_none(self.max_context_tokens)
        model_limit = self._get_model_context_limit_tokens(model_id)
        if model_limit is not None:
            hard_max = min(hard_max, model_limit) if hard_max is not None else model_limit
        if hard_max is None:
            hard_max = 32768
        target_num_ctx = max(1024, int(hard_max))
        current_num_ctx = self._active_num_ctx_by_model.get(model_id)
        if current_num_ctx is None:
            self._active_num_ctx_by_model[model_id] = target_num_ctx
            return target_num_ctx
        if target_num_ctx != current_num_ctx:
            self._active_num_ctx_by_model[model_id] = target_num_ctx
            return target_num_ctx
        return current_num_ctx

    def _get_model_context_limit_tokens(self, model_id: str) -> int | None:
        if model_id in self._model_context_limit_cache:
            return self._model_context_limit_cache[model_id]
        payload = self._show(model_id)
        limit = _extract_model_context_limit(payload)
        self._model_context_limit_cache[model_id] = limit
        return limit

    def _show(self, model_id: str) -> dict[str, Any]:
        show_url = f"{self.base_url.rstrip('/')}/api/show"
        body = json.dumps({"model": model_id}).encode("utf-8")
        req = request.Request(
            url=show_url,
            method="POST",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=min(self.timeout_seconds, 5.0)) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _discover_models(self) -> list[str]:
        tags_url = f"{self.base_url.rstrip('/')}/api/tags"
        req = request.Request(url=tags_url, method="GET")
        try:
            with request.urlopen(req, timeout=min(self.timeout_seconds, 5.0)) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw)
        except Exception:
            return []
        entries = parsed.get("models")
        if not isinstance(entries, list):
            return []
        discovered: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            model_name = str(entry.get("name") or entry.get("model") or "").strip()
            if model_name:
                discovered.append(model_name)
        return _dedupe_models(discovered)

    def _read_persisted_model_metadata(self, model_id: str) -> dict[str, Any] | None:
        cache_path = self._resolve_cache_db_path()
        ttl_seconds = _normalize_cache_ttl_seconds(self.model_metadata_cache_ttl_seconds)
        if cache_path is None or ttl_seconds <= 0:
            return None
        if not cache_path.exists():
            return None
        try:
            with sqlite3.connect(str(cache_path), timeout=5.0) as conn:
                _ensure_model_metadata_cache_table(conn)
                row = conn.execute(
                    f"""
SELECT metadata_json, fetched_at_epoch
FROM {_MODEL_METADATA_CACHE_TABLE}
WHERE provider_id = ? AND model_id = ?
                    """,
                    (self.provider_id, model_id),
                ).fetchone()
        except Exception:
            return None
        if row is None:
            return None
        try:
            fetched_at_epoch = int(row[1])
        except Exception:
            return None
        if fetched_at_epoch <= 0:
            return None
        now = int(time.time())
        if (now - fetched_at_epoch) > ttl_seconds:
            return None
        raw_json = row[0]
        if not isinstance(raw_json, str) or not raw_json.strip():
            return None
        try:
            parsed = json.loads(raw_json)
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        return _normalize_model_metadata_payload(parsed)

    def _write_persisted_model_metadata(self, model_id: str, metadata: dict[str, Any]) -> None:
        cache_path = self._resolve_cache_db_path()
        ttl_seconds = _normalize_cache_ttl_seconds(self.model_metadata_cache_ttl_seconds)
        if cache_path is None or ttl_seconds <= 0:
            return
        normalized = _normalize_model_metadata_payload(metadata)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload_json = json.dumps(normalized, separators=(",", ":"), sort_keys=True)
        now = int(time.time())
        try:
            with sqlite3.connect(str(cache_path), timeout=5.0) as conn:
                _ensure_model_metadata_cache_table(conn)
                conn.execute(
                    f"""
INSERT INTO {_MODEL_METADATA_CACHE_TABLE} (provider_id, model_id, metadata_json, fetched_at_epoch)
VALUES (?, ?, ?, ?)
ON CONFLICT(provider_id, model_id)
DO UPDATE SET metadata_json = excluded.metadata_json, fetched_at_epoch = excluded.fetched_at_epoch
                    """,
                    (self.provider_id, model_id, payload_json, now),
                )
                conn.commit()
        except Exception:
            return

    def _resolve_cache_db_path(self) -> Path | None:
        raw = str(self.model_metadata_cache_db_path or "").strip()
        if not raw:
            return None
        return Path(raw).expanduser().resolve()


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _extract_ollama_tool_calls(raw: Any) -> list[ProviderToolCall]:
    if not isinstance(raw, list):
        return []
    tool_calls: list[ProviderToolCall] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        function = item.get("function", {})
        if not isinstance(function, dict):
            continue
        name = str(function.get("name", "")).strip()
        if not name:
            continue
        args_raw = function.get("arguments", {})
        arguments = _parse_arguments(args_raw)
        call_id = str(item.get("id", "")).strip() or f"ollama_call_{idx + 1}"
        tool_calls.append(
            ProviderToolCall(
                call_id=call_id,
                name=name,
                arguments=arguments,
            )
        )
    return tool_calls


def _parse_arguments(raw: Any) -> dict[str, object]:
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return {str(k): v for k, v in parsed.items()}
    return {}


def _dedupe_models(models: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in models:
        item = str(raw).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _normalize_capabilities(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    capabilities: list[str] = []
    for item in raw:
        value = str(item or "").strip()
        if value:
            capabilities.append(value)
    return _dedupe_models(capabilities)


def _normalize_thinking_value(value: bool | str | None) -> bool | str | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if text in {"low", "medium", "high"}:
        return text
    return None


def _ensure_model_metadata_cache_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
CREATE TABLE IF NOT EXISTS {_MODEL_METADATA_CACHE_TABLE} (
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    fetched_at_epoch INTEGER NOT NULL,
    PRIMARY KEY (provider_id, model_id)
)
        """
    )


def _normalize_cache_ttl_seconds(raw: Any) -> int:
    try:
        parsed = int(raw)
    except Exception:
        return 86400
    return parsed if parsed >= 0 else 86400


def _normalize_model_metadata_payload(raw: dict[str, Any]) -> dict[str, Any]:
    capabilities = _normalize_capabilities(raw.get("capabilities"))
    thinking_mode = str(raw.get("thinking_mode", "none") or "").strip().lower()
    if thinking_mode not in {"none", "boolean", "level"}:
        thinking_mode = "none"
    return {
        "capabilities": capabilities,
        "thinking_mode": thinking_mode,
    }


def _positive_int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _extract_model_context_limit(payload: dict[str, Any]) -> int | None:
    if not isinstance(payload, dict) or not payload:
        return None
    candidates: list[int] = []
    for key in ("model_info", "details"):
        obj = payload.get(key)
        if isinstance(obj, dict):
            for inner_key, value in obj.items():
                token = str(inner_key or "").strip().lower()
                if any(marker in token for marker in ("context_length", "num_ctx", "n_ctx", "ctx")):
                    parsed = _positive_int_or_none(value)
                    if parsed is not None:
                        candidates.append(parsed)
    for key in ("modelfile", "parameters"):
        text = str(payload.get(key, "") or "")
        if not text:
            continue
        for match in re.finditer(r"(?im)\b(?:parameter\s+)?num_ctx\s+(\d+)\b", text):
            parsed = _positive_int_or_none(match.group(1))
            if parsed is not None:
                candidates.append(parsed)
    if not candidates:
        return None
    return max(candidates)
