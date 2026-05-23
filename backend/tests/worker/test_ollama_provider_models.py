from __future__ import annotations

import json
from pathlib import Path
from urllib import error

import pytest

from backend.services.assistant.providers.ollama_provider import OllamaProvider


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self):  # noqa: ANN001
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_list_models_uses_ollama_tags_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(
        provider_id="ollama",
        base_url="http://127.0.0.1:11434",
        default_model="qwen3.5:35b-a3b",
        models=["qwen3.5:35b-a3b", "qwen3.5:27b"],
    )

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        return _FakeResponse(
            {
                "models": [
                    {"name": "qwen3.5:35b-a3b"},
                    {"name": "qwen3.5:27b"},
                ]
            }
        )

    monkeypatch.setattr("backend.services.assistant.providers.ollama_provider.request.urlopen", _fake_urlopen)
    models = provider.list_models()
    assert "qwen3.5:35b-a3b" in models
    assert "qwen3.5:27b" in models


def test_list_models_falls_back_to_configured_when_tags_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(
        provider_id="ollama",
        base_url="http://127.0.0.1:11434",
        default_model="qwen3.5:35b-a3b",
        models=["qwen3.5:35b-a3b", "qwen3.5:27b"],
    )

    def _failing_urlopen(req, timeout=0):  # noqa: ANN001
        raise error.URLError("offline")

    monkeypatch.setattr("backend.services.assistant.providers.ollama_provider.request.urlopen", _failing_urlopen)
    models = provider.list_models()
    assert models == ["qwen3.5:35b-a3b", "qwen3.5:27b"]


def test_list_model_metadata_detects_gpt_oss_level_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(
        provider_id="ollama",
        base_url="http://127.0.0.1:11434",
        default_model="gpt-oss:20b",
        models=["gpt-oss:20b"],
    )

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        if req.full_url.endswith("/api/show"):
            return _FakeResponse(
                {
                    "capabilities": ["completion", "tools", "thinking"],
                    "modelfile": "TEMPLATE {{ if .ThinkLevel }}{{ .ThinkLevel }}{{ end }}",
                }
            )
        raise AssertionError(f"Unexpected URL: {req.full_url}")

    monkeypatch.setattr("backend.services.assistant.providers.ollama_provider.request.urlopen", _fake_urlopen)
    metadata = provider.list_model_metadata(models=["gpt-oss:20b"])
    assert metadata["gpt-oss:20b"]["capabilities"] == ["completion", "tools", "thinking"]
    assert metadata["gpt-oss:20b"]["thinking_mode"] == "level"


def test_list_model_metadata_detects_boolean_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(
        provider_id="ollama",
        base_url="http://127.0.0.1:11434",
        default_model="qwen3.5:27b",
        models=["qwen3.5:27b"],
    )

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        if req.full_url.endswith("/api/show"):
            return _FakeResponse(
                {
                    "capabilities": ["completion", "vision", "tools", "thinking"],
                    "modelfile": "TEMPLATE {{ .Prompt }}",
                }
            )
        raise AssertionError(f"Unexpected URL: {req.full_url}")

    monkeypatch.setattr("backend.services.assistant.providers.ollama_provider.request.urlopen", _fake_urlopen)
    metadata = provider.list_model_metadata(models=["qwen3.5:27b"])
    assert metadata["qwen3.5:27b"]["thinking_mode"] == "boolean"


def test_complete_includes_level_thinking_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(
        provider_id="ollama",
        base_url="http://127.0.0.1:11434",
        default_model="gpt-oss:20b",
        models=["gpt-oss:20b"],
    )
    captured_payload: dict[str, object] = {}

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        nonlocal captured_payload
        if req.full_url.endswith("/api/show"):
            return _FakeResponse({"modelfile": "PARAMETER num_ctx 32768"})
        captured_payload = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"message": {"content": "ok"}, "prompt_eval_count": 7, "eval_count": 3})

    monkeypatch.setattr("backend.services.assistant.providers.ollama_provider.request.urlopen", _fake_urlopen)
    result = provider.complete(
        model_id="gpt-oss:20b",
        system_prompt="system",
        conversation=[{"role": "user", "content": "hello"}],
        thinking="high",
    )
    assert captured_payload["think"] == "high"
    assert "options" in captured_payload
    assert int(captured_payload["options"]["num_ctx"]) >= 2048
    assert result.text == "ok"


def test_complete_includes_boolean_thinking_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(
        provider_id="ollama",
        base_url="http://127.0.0.1:11434",
        default_model="qwen3.5:27b",
        models=["qwen3.5:27b"],
    )
    captured_payload: dict[str, object] = {}

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        nonlocal captured_payload
        if req.full_url.endswith("/api/show"):
            return _FakeResponse({"modelfile": "PARAMETER num_ctx 16384"})
        captured_payload = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"message": {"content": "ok"}, "prompt_eval_count": 7, "eval_count": 3})

    monkeypatch.setattr("backend.services.assistant.providers.ollama_provider.request.urlopen", _fake_urlopen)
    provider.complete(
        model_id="qwen3.5:27b",
        system_prompt="system",
        conversation=[{"role": "user", "content": "hello"}],
        thinking=False,
    )
    assert captured_payload["think"] is False
    assert int(captured_payload["options"]["num_ctx"]) >= 2048


def test_complete_num_ctx_is_sticky_and_only_grows(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider(
        provider_id="ollama",
        base_url="http://127.0.0.1:11434",
        default_model="qwen3.5:27b",
        models=["qwen3.5:27b"],
        max_context_tokens=32768,
    )
    payloads: list[dict[str, object]] = []

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        if req.full_url.endswith("/api/show"):
            return _FakeResponse({"modelfile": "PARAMETER num_ctx 32768"})
        payloads.append(json.loads(req.data.decode("utf-8")))
        return _FakeResponse({"message": {"content": "ok"}, "prompt_eval_count": 7, "eval_count": 3})

    monkeypatch.setattr("backend.services.assistant.providers.ollama_provider.request.urlopen", _fake_urlopen)
    provider.complete(
        model_id="qwen3.5:27b",
        system_prompt="sys",
        conversation=[{"role": "user", "content": "short"}],
        max_output_tokens=256,
    )
    provider.complete(
        model_id="qwen3.5:27b",
        system_prompt="sys",
        conversation=[{"role": "user", "content": "x" * 32000}],
        max_output_tokens=1024,
    )
    provider.complete(
        model_id="qwen3.5:27b",
        system_prompt="sys",
        conversation=[{"role": "user", "content": "tiny"}],
        max_output_tokens=64,
    )
    assert len(payloads) == 3
    first_ctx = int(payloads[0]["options"]["num_ctx"])
    second_ctx = int(payloads[1]["options"]["num_ctx"])
    third_ctx = int(payloads[2]["options"]["num_ctx"])
    assert first_ctx >= 2048
    assert second_ctx >= first_ctx
    assert third_ctx == second_ctx


def test_list_model_metadata_reuses_sqlite_cache_across_instances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "assistant_sessions.db"
    calls: dict[str, int] = {"count": 0}

    def _fake_show_urlopen(req, timeout=0):  # noqa: ANN001
        if not req.full_url.endswith("/api/show"):
            raise AssertionError(f"Unexpected URL: {req.full_url}")
        calls["count"] += 1
        return _FakeResponse(
            {
                "capabilities": ["completion", "tools", "thinking"],
                "modelfile": "TEMPLATE {{ .Prompt }}",
            }
        )

    monkeypatch.setattr(
        "backend.services.assistant.providers.ollama_provider.request.urlopen",
        _fake_show_urlopen,
    )
    provider_1 = OllamaProvider(
        provider_id="ollama",
        base_url="http://127.0.0.1:11434",
        default_model="qwen3.5:27b",
        models=["qwen3.5:27b"],
        model_metadata_cache_db_path=str(db_path),
        discover_models=False,
    )
    metadata_1 = provider_1.list_model_metadata(models=["qwen3.5:27b"])
    assert metadata_1["qwen3.5:27b"]["capabilities"] == ["completion", "tools", "thinking"]
    assert calls["count"] == 1

    def _unexpected_urlopen(req, timeout=0):  # noqa: ANN001
        raise AssertionError(f"Unexpected network call for cached metadata: {req.full_url}")

    monkeypatch.setattr(
        "backend.services.assistant.providers.ollama_provider.request.urlopen",
        _unexpected_urlopen,
    )
    provider_2 = OllamaProvider(
        provider_id="ollama",
        base_url="http://127.0.0.1:11434",
        default_model="qwen3.5:27b",
        models=["qwen3.5:27b"],
        model_metadata_cache_db_path=str(db_path),
        discover_models=False,
    )
    metadata_2 = provider_2.list_model_metadata(models=["qwen3.5:27b"])
    assert metadata_2["qwen3.5:27b"]["thinking_mode"] == "boolean"
