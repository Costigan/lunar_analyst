from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request

from .base import ProviderCompletion, ProviderToolCall

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenAIProvider:
    provider_id: str
    api_key_env: str
    base_url: str
    default_model: str
    models: list[str]
    enable_token_caching: bool = True
    prompt_cache_retention: str | None = None
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
            raise RuntimeError(f"OpenAI API key missing: {self.api_key_env}")
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(conversation)
        payload: dict[str, Any] = {
            "model": model_id or self.default_model,
            "messages": messages,
        }
        chosen_model = str(payload["model"])
        token_key: str | None = None
        if max_output_tokens is not None and max_output_tokens > 0:
            token_key = (
                "max_completion_tokens"
                if _prefers_max_completion_tokens(chosen_model)
                else "max_tokens"
            )
            payload[token_key] = int(max_output_tokens)
        tool_name_aliases: dict[str, str] = {}
        if tool_schema:
            try:
                aliased_tools, tool_name_aliases = _alias_tool_names_for_openai(tool_schema)
                payload["tools"] = aliased_tools
                # Preflight-validate model-facing tool schemas for OpenAI compatibility (arrays must have 'items').
                _validate_openai_tool_schema(aliased_tools, tool_name_aliases)
            except RuntimeError as exc:
                # Generate a short provider request id for correlation and diagnostics.
                import uuid

                provider_request_id = f"pr_{uuid.uuid4().hex[:12]}"
                logger.error(
                    "OpenAI tool schema preflight failed provider_request_id=%s error=%s",
                    provider_request_id,
                    exc,
                )
                # Surface a clear error including the provider request id so callers can reference it in bug reports.
                raise RuntimeError(
                    f"OpenAI tool schema validation failed (provider_request_id={provider_request_id}): {exc}"
                ) from exc

        cache_attempted = False
        if self.enable_token_caching and cache_context:
            prompt_cache_key = str(cache_context.get("stable_prefix_hash", "")).strip()
            if prompt_cache_key:
                payload["prompt_cache_key"] = prompt_cache_key
                cache_attempted = True
                retention = _normalize_prompt_cache_retention(self.prompt_cache_retention)
                if retention:
                    payload["prompt_cache_retention"] = retention

        try:
            parsed = _post_chat_completions(
                base_url=self.base_url,
                api_key=api_key,
                payload=payload,
                timeout_seconds=self.timeout_seconds,
            )
        except _OpenAIHttpError as exc:
            if (
                token_key == "max_tokens"
                and max_output_tokens is not None
                and _looks_like_unsupported_max_tokens(exc.detail)
            ):
                retry_payload = dict(payload)
                retry_payload.pop("max_tokens", None)
                retry_payload["max_completion_tokens"] = int(max_output_tokens)
                try:
                    parsed = _post_chat_completions(
                        base_url=self.base_url,
                        api_key=api_key,
                        payload=retry_payload,
                        timeout_seconds=self.timeout_seconds,
                    )
                except _OpenAIHttpError as retry_exc:
                    raise RuntimeError(
                        f"OpenAI provider unavailable: HTTP {retry_exc.code}: {retry_exc.detail}"
                    ) from retry_exc
                except error.URLError as retry_exc:
                    raise RuntimeError(f"OpenAI provider unavailable: {retry_exc}") from retry_exc
                except Exception as retry_exc:
                    raise RuntimeError(f"OpenAI provider failed: {retry_exc}") from retry_exc
            else:
                raise RuntimeError(f"OpenAI provider unavailable: HTTP {exc.code}: {exc.detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"OpenAI provider unavailable: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"OpenAI provider failed: {exc}") from exc

        text = _extract_openai_text(parsed)
        tool_calls = _extract_openai_tool_calls(parsed, tool_name_aliases=tool_name_aliases)
        finish_reason = _extract_openai_finish_reason(parsed, has_tool_calls=bool(tool_calls))
        usage = _extract_openai_usage(parsed)
        if not text.strip() and not tool_calls:
            logger.warning(
                "OpenAI provider returned unparsed completion model=%s finish_reason=%s raw_preview=%s",
                chosen_model,
                finish_reason,
                _raw_preview(parsed),
            )
        return ProviderCompletion(
            text=text,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            cache_attempted=cache_attempted,
            cache_applied=usage.get("cached_prompt_tokens", 0) > 0,
        )


class _OpenAIHttpError(RuntimeError):
    def __init__(self, *, code: int, detail: str) -> None:
        super().__init__(f"HTTP {code}: {detail}")
        self.code = int(code)
        self.detail = str(detail)


def _post_chat_completions(
    *,
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url=f"{base_url.rstrip('/')}/v1/chat/completions",
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = _read_http_error_body(exc)
        raise _OpenAIHttpError(code=int(exc.code), detail=detail) from exc


def _prefers_max_completion_tokens(model: str) -> bool:
    lower = str(model or "").strip().lower()
    return lower.startswith("gpt-5")


def _looks_like_unsupported_max_tokens(detail: str) -> bool:
    text = str(detail or "").lower()
    return "unsupported parameter" in text and "max_tokens" in text and "max_completion_tokens" in text


def _normalize_prompt_cache_retention(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"in_memory", "24h"}:
        return text
    return None


def _extract_openai_text(parsed: dict[str, Any]) -> str:
    choices = parsed.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message", {})
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {"output_text", "text"}:
                continue
            raw_text = item.get("text", "")
            if isinstance(raw_text, dict):
                chunks.append(str(raw_text.get("value", "")))
            else:
                chunks.append(str(raw_text))
        return "\n".join(chunks).strip()
    refusal = message.get("refusal")
    if isinstance(refusal, str):
        return refusal.strip()
    if isinstance(refusal, dict):
        return str(refusal.get("message", "")).strip()
    legacy_text = first.get("text")
    if isinstance(legacy_text, str):
        return legacy_text.strip()
    return ""


def _extract_openai_usage(parsed: dict[str, Any]) -> dict[str, int]:
    usage = parsed.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    cached = 0
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens", 0) or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_prompt_tokens": cached,
    }


def _extract_openai_tool_calls(
    parsed: dict[str, Any],
    *,
    tool_name_aliases: dict[str, str] | None = None,
) -> list[ProviderToolCall]:
    aliases = dict(tool_name_aliases or {})
    choices = parsed.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return []
    first = choices[0]
    if not isinstance(first, dict):
        return []
    message = first.get("message", {})
    if not isinstance(message, dict):
        return []
    raw_calls = message.get("tool_calls", [])
    if not isinstance(raw_calls, list):
        raw_calls = []
    out: list[ProviderToolCall] = []
    for idx, raw in enumerate(raw_calls):
        if not isinstance(raw, dict):
            continue
        function = raw.get("function", {})
        if not isinstance(function, dict):
            continue
        name = str(function.get("name", "")).strip()
        if not name:
            continue
        name = str(aliases.get(name, name)).strip()
        arguments = _parse_tool_arguments(function.get("arguments"))
        call_id = str(raw.get("id", "")).strip() or f"openai_call_{idx + 1}"
        out.append(ProviderToolCall(call_id=call_id, name=name, arguments=arguments))
    if out:
        return out
    legacy_function_call = message.get("function_call")
    if isinstance(legacy_function_call, dict):
        name = str(legacy_function_call.get("name", "")).strip()
        if name:
            mapped = str(aliases.get(name, name)).strip()
            arguments = _parse_tool_arguments(legacy_function_call.get("arguments"))
            return [ProviderToolCall(call_id="openai_call_1", name=mapped, arguments=arguments)]
    return out


def _extract_openai_finish_reason(parsed: dict[str, Any], *, has_tool_calls: bool) -> str:
    if has_tool_calls:
        return "tool_calls"
    choices = parsed.get("choices", [])
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            reason = str(first.get("finish_reason", "")).strip()
            if reason:
                return reason
    return "stop"


def _parse_tool_arguments(raw: Any) -> dict[str, object]:
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


def _alias_tool_names_for_openai(tool_schema: list[dict[str, object]]) -> tuple[list[dict[str, object]], dict[str, str]]:
    aliased: list[dict[str, object]] = []
    aliases: dict[str, str] = {}
    for idx, tool in enumerate(tool_schema):
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            aliased.append(dict(tool))
            continue
        raw_name = str(function.get("name", "")).strip()
        if not raw_name:
            aliased.append(dict(tool))
            continue
        alias = f"la_tool_{idx + 1}"
        aliases[alias] = raw_name
        next_function = dict(function)
        next_function["name"] = alias
        raw_parameters = function.get("parameters")
        if isinstance(raw_parameters, dict):
            next_function["parameters"] = _normalize_openai_schema(raw_parameters)
        next_tool = dict(tool)
        next_tool["function"] = next_function
        aliased.append(next_tool)
    return aliased, aliases


def _read_http_error_body(exc: error.HTTPError) -> str:
    try:
        payload = exc.read()
    except Exception:
        payload = b""
    text = payload.decode("utf-8", errors="replace").strip() if payload else ""
    if not text:
        return str(exc)
    compact = " ".join(text.split())
    return compact[:600]


def _raw_preview(payload: dict[str, Any], *, max_chars: int = 1200) -> str:
    try:
        rendered = json.dumps(payload, ensure_ascii=True, default=str)
    except Exception:
        rendered = str(payload)
    compact = " ".join(rendered.split())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars] + "...<truncated>"


def _normalize_openai_schema(schema: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in schema.items():
        if isinstance(value, dict):
            normalized[key] = _normalize_openai_schema(value)
        elif isinstance(value, list):
            normalized[key] = [
                _normalize_openai_schema(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            normalized[key] = value
    schema_type = str(normalized.get("type", "")).strip().lower()
    if schema_type == "object" and "properties" not in normalized:
        normalized["properties"] = {}
    return normalized


def _validate_openai_tool_schema(aliased_tools: list[dict[str, Any]], aliases: dict[str, str] | None = None) -> None:
    """Preflight-validate aliased OpenAI tool schemas.

    Ensures that every schema node with type 'array' has an 'items' field.
    Raises RuntimeError listing offending tools and JSON Pointers if validation fails.
    """
    aliases = dict(aliases or {})
    offenders: list[dict[str, str]] = []

    def _recurse_schema(node: Any, path: list[str], missing: list[str]) -> None:
        if isinstance(node, dict):
            node_type = str(node.get("type", "")).strip().lower()
            if node_type == "array":
                if "items" not in node:
                    missing.append("/" + "/".join(path))
                else:
                    _recurse_schema(node.get("items"), path + ["items"], missing)
            # properties
            props = node.get("properties")
            if isinstance(props, dict):
                for prop_name, prop_schema in props.items():
                    _recurse_schema(prop_schema, path + ["properties", prop_name], missing)
            # additionalProperties
            addp = node.get("additionalProperties")
            if isinstance(addp, dict):
                _recurse_schema(addp, path + ["additionalProperties"], missing)
            # combiners
            for comb in ("anyOf", "oneOf", "allOf"):
                comb_val = node.get(comb)
                if isinstance(comb_val, list):
                    for idx, item in enumerate(comb_val):
                        _recurse_schema(item, path + [comb, str(idx)], missing)
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                _recurse_schema(item, path + [str(idx)], missing)

    for tool in aliased_tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        alias = str(function.get("name", "")).strip()
        raw_name = aliases.get(alias, alias)
        params = function.get("parameters")
        if not isinstance(params, dict):
            continue
        missing_locations: list[str] = []
        _recurse_schema(params, ["function", "parameters"], missing_locations)
        for ptr in missing_locations:
            offenders.append({"tool_alias": alias, "tool_name": raw_name, "pointer": ptr})

    if offenders:
        msgs = [f"{o['tool_alias']} (original: {o['tool_name']}): missing items at {o['pointer']}" for o in offenders]
        raise RuntimeError("OpenAI tool schema preflight failed: " + "; ".join(msgs))
