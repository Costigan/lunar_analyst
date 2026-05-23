from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable

from .base import ProviderCompletion, ProviderToolCall


@dataclass(frozen=True)
class SubprocessProvider:
    provider_id: str
    command: list[str]
    default_model: str
    models: list[str]
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
        del on_delta
        payload = {
            "model_id": model_id or self.default_model,
            "session_id": session_id,
            "system_prompt": system_prompt,
            "conversation": conversation,
            "cache_context": cache_context or {},
            "tool_schema": tool_schema or [],
            "max_output_tokens": int(max_output_tokens) if max_output_tokens and max_output_tokens > 0 else None,
        }
        try:
            result = subprocess.run(
                self.command,
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except Exception as exc:
            raise RuntimeError(f"Subprocess provider launch failed: {exc}") from exc
        if result.returncode != 0:
            raise RuntimeError(f"Subprocess provider failed: {result.stderr.strip()}")

        try:
            parsed = json.loads(result.stdout or "{}")
        except Exception as exc:
            raise RuntimeError(f"Subprocess provider returned invalid JSON: {exc}") from exc

        text = str(parsed.get("text", "")).strip()
        usage = parsed.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        tool_calls = _parse_tool_calls(parsed.get("tool_calls"))
        finish_reason = str(parsed.get("finish_reason", "")).strip() or ("tool_calls" if tool_calls else "stop")
        return ProviderCompletion(
            text=text,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage={
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "cached_prompt_tokens": int(usage.get("cached_prompt_tokens", 0) or 0),
            },
            cache_attempted=bool(parsed.get("cache_attempted", False)),
            cache_applied=bool(parsed.get("cache_applied", False)),
        )


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
        call_id = str(item.get("call_id", "")).strip() or f"subprocess_call_{idx + 1}"
        out.append(ProviderToolCall(call_id=call_id, name=name, arguments=arguments))
    return out
