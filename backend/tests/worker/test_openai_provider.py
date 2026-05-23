from __future__ import annotations

import io
import json
import logging
from urllib import error

from backend.services.assistant.providers.openai_provider import (
    OpenAIProvider,
    _alias_tool_names_for_openai,
    _extract_openai_tool_calls,
)


def test_alias_tool_names_for_openai_rewrites_function_names() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "capabilities.describe",
                "description": "Describe capabilities",
                "parameters": {"type": "object", "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "scenario.list",
                "description": "List scenarios",
                "parameters": {"type": "object", "additionalProperties": False},
            },
        },
    ]
    aliased, aliases = _alias_tool_names_for_openai(tools)
    assert len(aliased) == 2
    assert aliases == {
        "la_tool_1": "capabilities.describe",
        "la_tool_2": "scenario.list",
    }
    assert aliased[0]["function"]["name"] == "la_tool_1"  # type: ignore[index]
    assert aliased[1]["function"]["name"] == "la_tool_2"  # type: ignore[index]
    assert aliased[0]["function"]["parameters"]["properties"] == {}  # type: ignore[index]
    assert aliased[1]["function"]["parameters"]["properties"] == {}  # type: ignore[index]


def test_extract_openai_tool_calls_maps_alias_to_canonical_name() -> None:
    parsed = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "la_tool_1",
                                "arguments": '{"scenario_id":"scn_test_scenario"}',
                            },
                        }
                    ]
                }
            }
        ]
    }
    calls = _extract_openai_tool_calls(
        parsed,
        tool_name_aliases={"la_tool_1": "scenario.list"},
    )
    assert len(calls) == 1
    assert calls[0].name == "scenario.list"
    assert calls[0].arguments == {"scenario_id": "scn_test_scenario"}


def test_extract_openai_tool_calls_supports_legacy_function_call() -> None:
    parsed = {
        "choices": [
            {
                "message": {
                    "function_call": {
                        "name": "la_tool_1",
                        "arguments": '{"scenario_id":"scn_test_scenario"}',
                    }
                }
            }
        ]
    }
    calls = _extract_openai_tool_calls(
        parsed,
        tool_name_aliases={"la_tool_1": "scenario.list"},
    )
    assert len(calls) == 1
    assert calls[0].name == "scenario.list"
    assert calls[0].arguments == {"scenario_id": "scn_test_scenario"}


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self):  # noqa: ANN001
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_openai_provider_uses_max_completion_tokens_for_gpt5_and_omits_metadata(monkeypatch) -> None:  # noqa: ANN001
    provider = OpenAIProvider(
        provider_id="openai",
        api_key_env="OPENAI_API_KEY",
        base_url="https://api.openai.com",
        default_model="gpt-5-mini",
        models=["gpt-5-mini"],
        prompt_cache_retention="24h",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen_payloads: list[dict[str, object]] = []

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        body = req.data.decode("utf-8") if isinstance(req.data, (bytes, bytearray)) else str(req.data)
        seen_payloads.append(json.loads(body))
        return _FakeResponse({"choices": [{"message": {"content": "ok"}}], "usage": {}})

    monkeypatch.setattr("backend.services.assistant.providers.openai_provider.request.urlopen", _fake_urlopen)
    result = provider.complete(
        model_id="gpt-5-mini",
        system_prompt="system",
        conversation=[{"role": "user", "content": "hi"}],
        cache_context={"stable_prefix_hash": "abc"},
        max_output_tokens=111,
        tool_schema=[],
    )
    assert result.text == "ok"
    assert len(seen_payloads) == 1
    payload = seen_payloads[0]
    assert payload.get("max_completion_tokens") == 111
    assert "max_tokens" not in payload
    assert "metadata" not in payload
    assert payload.get("prompt_cache_key") == "abc"
    assert payload.get("prompt_cache_retention") == "24h"
    assert result.cache_attempted is True


def test_openai_provider_retries_with_max_completion_tokens_after_unsupported_max_tokens(monkeypatch) -> None:  # noqa: ANN001
    provider = OpenAIProvider(
        provider_id="openai",
        api_key_env="OPENAI_API_KEY",
        base_url="https://api.openai.com",
        default_model="gpt-4.1-mini",
        models=["gpt-4.1-mini"],
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen_payloads: list[dict[str, object]] = []
    calls = 0

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        nonlocal calls
        calls += 1
        body = req.data.decode("utf-8") if isinstance(req.data, (bytes, bytearray)) else str(req.data)
        payload = json.loads(body)
        seen_payloads.append(payload)
        if calls == 1:
            err_payload = {
                "error": {
                    "message": "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.",
                    "type": "invalid_request_error",
                    "param": "max_tokens",
                    "code": "unsupported_parameter",
                }
            }
            raise error.HTTPError(
                url="https://api.openai.com/v1/chat/completions",
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=io.BytesIO(json.dumps(err_payload).encode("utf-8")),
            )
        return _FakeResponse({"choices": [{"message": {"content": "ok"}}], "usage": {}})

    monkeypatch.setattr("backend.services.assistant.providers.openai_provider.request.urlopen", _fake_urlopen)
    result = provider.complete(
        model_id="gpt-4.1-mini",
        system_prompt="system",
        conversation=[{"role": "user", "content": "hi"}],
        max_output_tokens=77,
        tool_schema=[],
    )
    assert result.text == "ok"
    assert len(seen_payloads) == 2
    assert seen_payloads[0].get("max_tokens") == 77
    assert "max_completion_tokens" not in seen_payloads[0]
    assert seen_payloads[1].get("max_completion_tokens") == 77
    assert "max_tokens" not in seen_payloads[1]


def test_openai_provider_logs_raw_preview_for_unparsed_completion(monkeypatch, caplog) -> None:  # noqa: ANN001
    provider = OpenAIProvider(
        provider_id="openai",
        api_key_env="OPENAI_API_KEY",
        base_url="https://api.openai.com",
        default_model="gpt-5-mini",
        models=["gpt-5-mini"],
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        del req, timeout
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": [{"type": "unknown_type", "value": "ignored"}],
                        }
                    }
                ],
                "usage": {},
            }
        )

    monkeypatch.setattr("backend.services.assistant.providers.openai_provider.request.urlopen", _fake_urlopen)
    with caplog.at_level(logging.WARNING):
        result = provider.complete(
            model_id="gpt-5-mini",
            system_prompt="system",
            conversation=[{"role": "user", "content": "hi"}],
            max_output_tokens=32,
            tool_schema=[],
    )
    assert result.text == ""
    assert result.tool_calls == []
