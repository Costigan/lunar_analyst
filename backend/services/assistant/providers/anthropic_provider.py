from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request

from .base import ProviderCompletion, ProviderToolCall


@dataclass(frozen=True)
class AnthropicProvider:
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
            raise RuntimeError(f"Anthropic API key missing: {self.api_key_env}")

        messages: list[dict[str, Any]] = []
        for msg in conversation:
            role = str(msg.get("role", "user"))
            if role not in {"user", "assistant"}:
                continue
            messages.append({"role": role, "content": str(msg.get("content", ""))})

        system: Any = system_prompt
        if self.enable_token_caching and cache_context:
            system = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        payload: dict[str, Any] = {
            "model": model_id or self.default_model,
            "max_tokens": int(max_output_tokens) if max_output_tokens and max_output_tokens > 0 else 1024,
            "system": system,
            "messages": messages,
        }
        if tool_schema:
            payload["tools"] = _anthropic_tools(tool_schema)

        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=f"{self.base_url.rstrip('/')}/v1/messages",
            method="POST",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                parsed = json.loads(resp.read().decode("utf-8"))
        except error.URLError as exc:
            raise RuntimeError(f"Anthropic provider unavailable: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Anthropic provider failed: {exc}") from exc

        text = _extract_anthropic_text(parsed)
        tool_calls = _extract_anthropic_tool_calls(parsed)
        usage = _extract_anthropic_usage(parsed)
        return ProviderCompletion(
            text=text,
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            usage=usage,
            cache_attempted=self.enable_token_caching and bool(cache_context),
            cache_applied=usage.get("cached_prompt_tokens", 0) > 0,
        )


def _extract_anthropic_text(parsed: dict[str, Any]) -> str:
    content = parsed.get("content", [])
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts).strip()


def _extract_anthropic_usage(parsed: dict[str, Any]) -> dict[str, int]:
    usage = parsed.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}
    prompt_tokens = int(usage.get("input_tokens", 0) or 0)
    completion_tokens = int(usage.get("output_tokens", 0) or 0)
    cached_prompt_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_prompt_tokens": cached_prompt_tokens,
    }


def _extract_anthropic_tool_calls(parsed: dict[str, Any]) -> list[ProviderToolCall]:
    content = parsed.get("content", [])
    if not isinstance(content, list):
        return []
    out: list[ProviderToolCall] = []
    for idx, item in enumerate(content):
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")).strip() != "tool_use":
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        input_payload = item.get("input", {})
        arguments = input_payload if isinstance(input_payload, dict) else {}
        call_id = str(item.get("id", "")).strip() or f"anthropic_call_{idx + 1}"
        out.append(ProviderToolCall(call_id=call_id, name=name, arguments=arguments))
    return out


def _anthropic_tools(tool_schema: list[dict[str, object]]) -> list[dict[str, object]]:
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
                "input_schema": fn.get("parameters", {"type": "object"}),
            }
        )
    return out
