from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request

from .base import ProviderCompletion, ProviderToolCall


@dataclass(frozen=True)
class GoogleProvider:
    provider_id: str
    api_key_env: str
    base_url: str
    default_model: str
    models: list[str]
    enable_token_caching: bool = True
    timeout_seconds: float = 90.0

    def list_models(self) -> list[str]:
        return list(self.models)

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
    ) -> ProviderCompletion:
        del session_id
        del on_delta
        api_key = os.getenv(self.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Google API key missing: {self.api_key_env}")

        chosen_model = model_id or self.default_model
        parts: list[dict[str, str]] = [{"text": system_prompt}]
        for msg in conversation:
            role = str(msg.get("role", "user"))
            if role not in {"user", "assistant"}:
                continue
            parts.append({"text": f"{role}: {str(msg.get('content', ''))}"})
        payload: dict[str, Any] = {
            "contents": [{"parts": parts}],
        }
        if max_output_tokens is not None and max_output_tokens > 0:
            payload["generationConfig"] = {"maxOutputTokens": int(max_output_tokens)}
        if tool_schema:
            payload["tools"] = [{"functionDeclarations": _google_function_declarations(tool_schema)}]
        if self.enable_token_caching and cache_context:
            payload["cachedContent"] = cache_context.get("stable_prefix_hash", "")

        url = (
            f"{self.base_url.rstrip('/')}/v1beta/models/{chosen_model}:generateContent"
            f"?key={api_key}"
        )
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=url,
            method="POST",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                parsed = json.loads(resp.read().decode("utf-8"))
        except error.URLError as exc:
            raise RuntimeError(f"Google provider unavailable: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Google provider failed: {exc}") from exc

        text = _extract_google_text(parsed)
        tool_calls = _extract_google_tool_calls(parsed)
        usage = _extract_google_usage(parsed)
        return ProviderCompletion(
            text=text,
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            usage=usage,
            cache_attempted=self.enable_token_caching and bool(cache_context),
            cache_applied=usage.get("cached_prompt_tokens", 0) > 0,
        )


def _extract_google_text(parsed: dict[str, Any]) -> str:
    candidates = parsed.get("candidates", [])
    if not isinstance(candidates, list) or not candidates:
        return ""
    first = candidates[0]
    if not isinstance(first, dict):
        return ""
    content = first.get("content", {})
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts", [])
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            out.append(str(part.get("text", "")))
    return "\n".join(out).strip()


def _extract_google_usage(parsed: dict[str, Any]) -> dict[str, int]:
    usage = parsed.get("usageMetadata", {})
    if not isinstance(usage, dict):
        usage = {}
    prompt = int(usage.get("promptTokenCount", 0) or 0)
    completion = int(usage.get("candidatesTokenCount", 0) or 0)
    cached = int(usage.get("cachedContentTokenCount", 0) or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_prompt_tokens": cached,
    }


def _extract_google_tool_calls(parsed: dict[str, Any]) -> list[ProviderToolCall]:
    candidates = parsed.get("candidates", [])
    if not isinstance(candidates, list) or not candidates:
        return []
    first = candidates[0]
    if not isinstance(first, dict):
        return []
    content = first.get("content", {})
    if not isinstance(content, dict):
        return []
    parts = content.get("parts", [])
    if not isinstance(parts, list):
        return []
    out: list[ProviderToolCall] = []
    for idx, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        fn_call = part.get("functionCall", {})
        if not isinstance(fn_call, dict):
            continue
        name = str(fn_call.get("name", "")).strip()
        if not name:
            continue
        args = fn_call.get("args", {})
        arguments = args if isinstance(args, dict) else {}
        call_id = str(fn_call.get("id", "")).strip() or f"google_call_{idx + 1}"
        out.append(ProviderToolCall(call_id=call_id, name=name, arguments=arguments))
    return out


def _google_function_declarations(tool_schema: list[dict[str, object]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for item in tool_schema:
        fn = item.get("function") if isinstance(item, dict) else None
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name", "")).strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "description": str(fn.get("description", "")),
                "parameters": fn.get("parameters", {"type": "object"}),
            }
        )
    return out
