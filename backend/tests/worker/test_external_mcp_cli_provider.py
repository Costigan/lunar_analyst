from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.services.assistant.providers.external_mcp_cli_provider import ExternalMcpCliProvider


def _completed(stdout_payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["mock"],
        returncode=0,
        stdout=json.dumps(stdout_payload),
        stderr="",
    )


def _interactive_cli_script() -> str:
    return """
import json
import sys

for raw in sys.stdin:
    msg = raw.strip()
    if not msg:
        continue
    if msg == "emit_delta":
        sys.stdout.write(json.dumps({"event": "delta", "text": "hel"}) + "\\n")
        sys.stdout.flush()
        sys.stdout.write(json.dumps({"text": "hello"}) + "\\n")
        sys.stdout.flush()
        continue
    sys.stdout.write(json.dumps({"text": "echo:" + msg}) + "\\n")
    sys.stdout.flush()
""".strip()


def _persistent_test_provider(*, timeout_seconds: float = 2.0, idle_timeout_seconds: float = 600.0) -> ExternalMcpCliProvider:
    return ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=[sys.executable, "-u", "-c", _interactive_cli_script()],
        default_model="gpt-5-codex",
        models=["gpt-5-codex", "gpt-5-codex-next"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        access_mode="mcp_only",
        mcp_registration_mode="none",
        persistent=True,
        timeout_seconds=timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
    )


def test_external_provider_strips_ansi_artifacts_from_text(monkeypatch) -> None:
    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        del args, kwargs
        return _completed({"text": "\u001b[31;1merror\u001b[0m [36;1mtool[0m"})

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=["codex", "exec"],
        default_model="gpt-5-codex",
        models=["gpt-5-codex"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
    )
    result = provider.complete(
        model_id="gpt-5-codex",
        system_prompt="system",
        conversation=[{"role": "user", "content": "hello"}],
    )
    assert result.text == "error tool"


def test_external_provider_mcp_only_uses_safe_mode_flags_and_temp_cwd(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        seen["cmd"] = args[0]
        seen["cwd"] = kwargs.get("cwd")
        seen["env"] = kwargs.get("env")
        return _completed({"text": "ok"})

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=["codex", "exec"],
        default_model="gpt-5-codex",
        models=["gpt-5-codex"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        args=["--model", "{model_id}"],
        access_mode="mcp_only",
        mcp_only_args=["--sandbox", "read-only"],
        scenario_root_args=["--sandbox", "workspace-write"],
        scenario_root="/d/lunar_analyst_scenarios",
    )
    result = provider.complete(
        model_id="gpt-5-codex",
        system_prompt="system",
        conversation=[{"role": "user", "content": "hello"}],
    )
    assert result.text == "ok"
    cmd = seen["cmd"]
    assert isinstance(cmd, list)
    assert "--sandbox" in cmd
    assert "read-only" in cmd
    assert seen["cwd"] is not None
    env = seen["env"]
    assert isinstance(env, dict)
    assert env["LUNAR_ANALYST_ACCESS_MODE"] == "mcp_only"
    assert "LUNAR_ANALYST_SCENARIO_ROOT" not in env


def test_external_provider_scenario_root_mode_uses_workspace_cwd(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    scenario_root = str((tmp_path / "workspace").resolve())
    Path(scenario_root).mkdir(parents=True, exist_ok=True)

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        seen["cmd"] = args[0]
        seen["cwd"] = kwargs.get("cwd")
        seen["env"] = kwargs.get("env")
        return _completed({"text": "ok"})

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="gemini_cli",
        command=["gemini"],
        default_model="gemini-2.5-pro",
        models=["gemini-2.5-pro"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        args=["--model", "{model_id}"],
        access_mode="scenario_root",
        mcp_only_args=["--approval-mode", "plan"],
        scenario_root_args=["--include-directories", "{scenario_root}"],
        scenario_root=scenario_root,
    )
    result = provider.complete(
        model_id="gemini-2.5-pro",
        system_prompt="system",
        conversation=[{"role": "user", "content": "hello"}],
    )
    assert result.text == "ok"
    assert seen["cwd"] == scenario_root
    cmd = seen["cmd"]
    assert isinstance(cmd, list)
    assert "--include-directories" in cmd
    assert scenario_root in cmd
    env = seen["env"]
    assert isinstance(env, dict)
    assert env["LUNAR_ANALYST_ACCESS_MODE"] == "scenario_root"
    assert env["LUNAR_ANALYST_SCENARIO_ROOT"] == scenario_root


def test_external_provider_turn_override_can_force_scenario_root_mode(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    scenario_root = str((tmp_path / "workspace").resolve())
    Path(scenario_root).mkdir(parents=True, exist_ok=True)

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        seen["cmd"] = args[0]
        seen["cwd"] = kwargs.get("cwd")
        seen["env"] = kwargs.get("env")
        return _completed({"text": "ok"})

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=["codex", "exec"],
        default_model="gpt-5-codex",
        models=["gpt-5-codex"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        access_mode="mcp_only",
        mcp_only_args=["--sandbox", "read-only"],
        scenario_root_args=["--sandbox", "workspace-write"],
        scenario_root=scenario_root,
    )
    result = provider.complete(
        model_id="gpt-5-codex",
        system_prompt="system",
        conversation=[{"role": "user", "content": "hello"}],
        access_mode="scenario_root",
    )
    assert result.text == "ok"
    assert seen["cwd"] == scenario_root
    env = seen["env"]
    assert isinstance(env, dict)
    assert env["LUNAR_ANALYST_ACCESS_MODE"] == "scenario_root"
    assert env["LUNAR_ANALYST_SCENARIO_ROOT"] == scenario_root


def test_external_provider_scenario_working_directory_override_uses_active_scenario_dir(
    monkeypatch, tmp_path: Path
) -> None:
    seen: dict[str, object] = {}
    scenario_root = tmp_path / "workspace"
    scenario_dir = scenario_root / "scn_a"
    scenario_dir.mkdir(parents=True, exist_ok=True)

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        seen["cmd"] = args[0]
        seen["cwd"] = kwargs.get("cwd")
        seen["env"] = kwargs.get("env")
        return _completed({"text": "ok"})

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=["codex", "exec"],
        default_model="gpt-5-codex",
        models=["gpt-5-codex"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        access_mode="scenario_root",
        scenario_root=str(scenario_root),
    )
    result = provider.complete(
        model_id="gpt-5-codex",
        system_prompt="system",
        conversation=[{"role": "user", "content": "hello"}],
        scenario_working_directory=str(scenario_dir),
    )
    assert result.text == "ok"
    assert seen["cwd"] == str(scenario_dir.resolve())


def test_external_provider_scenario_root_mode_requires_workspace_root() -> None:
    provider = ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=["codex", "exec"],
        default_model="gpt-5-codex",
        models=["gpt-5-codex"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        access_mode="scenario_root",
    )
    with pytest.raises(RuntimeError, match="scenario_root access mode requires configured workspace root"):
        provider.complete(
            model_id="gpt-5-codex",
            system_prompt="system",
            conversation=[{"role": "user", "content": "hello"}],
        )


def test_external_provider_scenario_root_mode_rejects_cwd_outside_root(tmp_path: Path) -> None:
    scenario_root = tmp_path / "scenario-root"
    scenario_root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    provider = ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=["codex", "exec"],
        default_model="gpt-5-codex",
        models=["gpt-5-codex"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        access_mode="scenario_root",
        scenario_root=str(scenario_root),
        working_directory=str(outside),
    )
    with pytest.raises(RuntimeError, match="Working directory escapes scenario root"):
        provider.complete(
            model_id="gpt-5-codex",
            system_prompt="system",
            conversation=[{"role": "user", "content": "hello"}],
        )


def test_external_provider_codex_configures_mcp_server_via_exec_overrides(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("LUNAR_ANALYST_MCP_TOKEN", "abc123")

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        del kwargs
        cmd = [str(item) for item in args[0]]
        calls.append(cmd)
        return _completed({"text": "ok"})

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=["codex", "exec"],
        default_model="gpt-5-codex",
        models=["gpt-5-codex"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        args=["--model", "{model_id}"],
        mcp_registration_mode="codex",
        mcp_server_name="lunar_analyst",
        mcp_auth_token_env="LUNAR_ANALYST_MCP_TOKEN",
    )
    result = provider.complete(
        model_id="gpt-5-codex",
        system_prompt="system",
        conversation=[{"role": "user", "content": "hello"}],
    )
    assert result.text == "ok"
    assert len(calls) == 1
    cmd = calls[0]
    assert "-c" in cmd
    assert 'mcp_servers.lunar_analyst.url="http://127.0.0.1:8000/api/v1/mcp/sse"' in cmd
    assert 'mcp_servers.lunar_analyst.bearer_token_env_var="LUNAR_ANALYST_MCP_TOKEN"' in cmd


def test_external_provider_codex_omits_bearer_env_override_when_token_missing(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.delenv("LUNAR_ANALYST_MCP_TOKEN", raising=False)

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        del kwargs
        cmd = [str(item) for item in args[0]]
        calls.append(cmd)
        return _completed({"text": "ok"})

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=["codex", "exec"],
        default_model="gpt-5-codex",
        models=["gpt-5-codex"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        args=["--model", "{model_id}"],
        mcp_registration_mode="codex",
        mcp_server_name="lunar_analyst",
        mcp_auth_token_env="LUNAR_ANALYST_MCP_TOKEN",
    )
    result = provider.complete(
        model_id="gpt-5-codex",
        system_prompt="system",
        conversation=[{"role": "user", "content": "hello"}],
    )
    assert result.text == "ok"
    assert len(calls) == 1
    cmd = calls[0]
    assert "-c" in cmd
    assert 'mcp_servers.lunar_analyst.url="http://127.0.0.1:8000/api/v1/mcp/sse"' in cmd
    assert 'mcp_servers.lunar_analyst.bearer_token_env_var="LUNAR_ANALYST_MCP_TOKEN"' not in cmd


def test_external_provider_gemini_configures_mcp_server_with_auth_header(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("LUNAR_ANALYST_MCP_TOKEN", "abc123")

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        del kwargs
        cmd = [str(item) for item in args[0]]
        calls.append(cmd)
        if len(cmd) >= 1 and "gemini" in cmd[0] and "mcp" not in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="gemini_cli",
        command=["gemini.cmd"],
        default_model="gemini-2.5-pro",
        models=["gemini-2.5-pro"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        args=["--model", "{model_id}", "--prompt", "{prompt_text}"],
        mcp_registration_mode="gemini",
        mcp_server_name="lunar_analyst",
        mcp_auth_token_env="LUNAR_ANALYST_MCP_TOKEN",
    )
    result = provider.complete(
        model_id="gemini-2.5-pro",
        system_prompt="system",
        conversation=[{"role": "user", "content": "hello"}],
    )
    assert result.text == "ok"
    flattened = [" ".join(cmd) for cmd in calls]
    assert any("mcp add lunar_analyst http://127.0.0.1:8000/api/v1/mcp/sse --transport sse --scope project" in item for item in flattened)
    assert any("--header Authorization: Bearer abc123" in item for item in flattened)


def test_external_provider_persistent_requires_session_id() -> None:
    provider = _persistent_test_provider()
    try:
        with pytest.raises(RuntimeError, match="persistent mode requires assistant session_id"):
            provider.complete(
                model_id="gpt-5-codex",
                system_prompt="system",
                conversation=[{"role": "user", "content": "hello"}],
            )
    finally:
        provider.shutdown()


def test_external_provider_persistent_reuses_process_and_streams_delta(monkeypatch) -> None:
    starts = 0
    real_popen = subprocess.Popen

    def _counting_popen(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal starts
        starts += 1
        return real_popen(*args, **kwargs)

    monkeypatch.setattr("subprocess.Popen", _counting_popen)
    provider = _persistent_test_provider()
    deltas: list[str] = []
    try:
        provider.complete(
            model_id="gpt-5-codex",
            system_prompt="system",
            conversation=[{"role": "user", "content": "seed"}],
            session_id="as_1",
        )
        result = provider.complete(
            model_id="gpt-5-codex",
            system_prompt="system",
            conversation=[
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "emit_delta"},
            ],
            session_id="as_1",
            on_delta=deltas.append,
        )
        assert result.text == "hello"
        assert starts == 1
        assert deltas == ["hel"]
    finally:
        provider.shutdown()


def test_external_provider_persistent_restarts_on_fingerprint_change(monkeypatch) -> None:
    starts = 0
    real_popen = subprocess.Popen

    def _counting_popen(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal starts
        starts += 1
        return real_popen(*args, **kwargs)

    monkeypatch.setattr("subprocess.Popen", _counting_popen)
    provider = _persistent_test_provider()
    try:
        provider.complete(
            model_id="gpt-5-codex",
            system_prompt="system",
            conversation=[{"role": "user", "content": "first"}],
            session_id="as_1",
        )
        provider.complete(
            model_id="gpt-5-codex-next",
            system_prompt="system",
            conversation=[{"role": "user", "content": "second"}],
            session_id="as_1",
        )
        assert starts == 2
    finally:
        provider.shutdown()


def test_external_provider_persistent_reset_session_restarts_process(monkeypatch) -> None:
    starts = 0
    real_popen = subprocess.Popen

    def _counting_popen(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal starts
        starts += 1
        return real_popen(*args, **kwargs)

    monkeypatch.setattr("subprocess.Popen", _counting_popen)
    provider = _persistent_test_provider()
    try:
        provider.complete(
            model_id="gpt-5-codex",
            system_prompt="system",
            conversation=[{"role": "user", "content": "first"}],
            session_id="as_1",
        )
        provider.reset_session("as_1")
        provider.complete(
            model_id="gpt-5-codex",
            system_prompt="system",
            conversation=[{"role": "user", "content": "second"}],
            session_id="as_1",
        )
        assert starts == 2
    finally:
        provider.shutdown()


def test_external_provider_persistent_idle_cleanup_stops_process() -> None:
    provider = _persistent_test_provider(idle_timeout_seconds=1.0)
    try:
        provider.complete(
            model_id="gpt-5-codex",
            system_prompt="system",
            conversation=[{"role": "user", "content": "hello"}],
            session_id="as_1",
        )
        assert "as_1" in provider._processes
        time.sleep(1.1)
        provider.cleanup_idle_processes()
        assert "as_1" not in provider._processes
    finally:
        provider.shutdown()


def test_external_provider_persistent_rejects_prompt_arg_templates() -> None:
    provider = ExternalMcpCliProvider(
        provider_id="gemini_cli",
        command=[sys.executable, "-u", "-c", _interactive_cli_script()],
        default_model="gemini-2.5-pro",
        models=["gemini-2.5-pro"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        args=["--prompt", "{prompt_text}"],
        persistent=True,
    )
    try:
        with pytest.raises(RuntimeError, match="stdin prompt delivery"):
            provider.complete(
                model_id="gemini-2.5-pro",
                system_prompt="system",
                conversation=[{"role": "user", "content": "hello"}],
                session_id="as_1",
            )
    finally:
        provider.shutdown()


def test_external_provider_persistent_turn_eof_uses_oneshot_prompt_delivery(monkeypatch) -> None:
    captured_inputs: list[str] = []

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        del args
        captured_inputs.append(str(kwargs.get("input", "")))
        return _completed({"text": "ok"})

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=["codex", "exec"],
        default_model="gpt-5-codex",
        models=["gpt-5-codex"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        persistent=True,
        stdin_mode="turn_eof",
    )
    try:
        provider.complete(
            model_id="gpt-5-codex",
            system_prompt="system",
            conversation=[{"role": "user", "content": "first"}],
            session_id="as_1",
        )
        provider.complete(
            model_id="gpt-5-codex",
            system_prompt="system",
            conversation=[
                {"role": "assistant", "content": "prev"},
                {"role": "user", "content": "second"},
            ],
            session_id="as_1",
        )
        assert len(captured_inputs) == 2
        assert "SYSTEM:" in captured_inputs[0]
        assert "USER:\nfirst" in captured_inputs[0]
        assert "ASSISTANT:\nprev" in captured_inputs[1]
        assert "USER:\nsecond" in captured_inputs[1]
        assert provider._processes == {}
    finally:
        provider.shutdown()


def test_external_provider_turn_eof_allows_prompt_arg_template(monkeypatch) -> None:
    seen_cmd: list[str] = []
    seen_input: list[str] = []

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        cmd = [str(item) for item in args[0]]
        seen_cmd[:] = cmd
        seen_input.append(str(kwargs.get("input", "")))
        return _completed({"text": "ok"})

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="gemini_cli",
        command=["gemini.cmd"],
        default_model="gemini-2.5-pro",
        models=["gemini-2.5-pro"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        args=["--model", "{model_id}", "--prompt", "{prompt_text}", "--output-format", "json"],
        persistent=True,
        stdin_mode="turn_eof",
    )
    try:
        provider.complete(
            model_id="gemini-2.5-pro",
            system_prompt="system",
            conversation=[{"role": "user", "content": "describe dem"}],
            session_id="as_1",
        )
        assert "--prompt" in seen_cmd
        assert any("SYSTEM:" in arg for arg in seen_cmd)
        assert seen_input == [""]
    finally:
        provider.shutdown()


def test_external_provider_parses_jsonl_completion_and_deltas(monkeypatch) -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "response.output_text.delta", "delta": "Hello"}),
            json.dumps({"type": "response.output_text.delta", "delta": " world"}),
            json.dumps({"type": "turn.completed"}),
        ]
    )

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        del args, kwargs
        return subprocess.CompletedProcess(args=["mock"], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=["codex", "exec"],
        default_model="gpt-5.3-codex",
        models=["gpt-5.3-codex"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        persistent=True,
        stdin_mode="turn_eof",
    )
    deltas: list[str] = []
    try:
        result = provider.complete(
            model_id="gpt-5.3-codex",
            system_prompt="system",
            conversation=[{"role": "user", "content": "hello"}],
            session_id="as_1",
            on_delta=deltas.append,
        )
        assert result.text == "Hello world"
        assert deltas == ["Hello", " world"]
    finally:
        provider.shutdown()


def test_external_provider_parses_gemini_response_field_and_stats_usage(monkeypatch) -> None:
    payload = {
        "session_id": "s1",
        "response": "DEM description",
        "stats": {
            "models": {
                "gemini-2.5-pro": {
                    "tokens": {"prompt": 11, "candidates": 7, "cached": 3},
                }
            }
        },
    }

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        del args, kwargs
        return _completed(payload)

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="gemini_cli",
        command=["gemini.cmd"],
        default_model="gemini-2.5-pro",
        models=["gemini-2.5-pro"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        args=["--model", "{model_id}", "--output-format", "json"],
        persistent=True,
        stdin_mode="turn_eof",
    )
    try:
        result = provider.complete(
            model_id="gemini-2.5-pro",
            system_prompt="system",
            conversation=[{"role": "user", "content": "describe dem"}],
            session_id="as_1",
        )
        assert result.text == "DEM description"
        assert result.usage == {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "cached_prompt_tokens": 3,
        }
    finally:
        provider.shutdown()


def test_external_provider_raises_when_gemini_reports_tool_failures_without_response(monkeypatch) -> None:
    payload = {
        "session_id": "s1",
        "response": "",
        "stats": {
            "tools": {
                "totalFail": 1,
                "byName": {"run_shell_command": {"fail": 1}},
            }
        },
    }

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        del args, kwargs
        return _completed(payload)

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="gemini_cli",
        command=["gemini.cmd"],
        default_model="gemini-2.5-pro",
        models=["gemini-2.5-pro"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        args=["--model", "{model_id}", "--output-format", "json"],
        persistent=True,
        stdin_mode="turn_eof",
    )
    try:
        with pytest.raises(RuntimeError, match="tool failures"):
            provider.complete(
                model_id="gemini-2.5-pro",
                system_prompt="system",
                conversation=[{"role": "user", "content": "describe dem"}],
                session_id="as_1",
            )
    finally:
        provider.shutdown()


def test_external_provider_raises_on_jsonl_error_events(monkeypatch) -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "error", "message": "model rejected request"}),
            json.dumps({"type": "turn.failed", "error": {"message": "request failed"}}),
        ]
    )

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        del args, kwargs
        return subprocess.CompletedProcess(args=["mock"], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=["codex", "exec"],
        default_model="gpt-5.3-codex",
        models=["gpt-5.3-codex"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        persistent=True,
        stdin_mode="turn_eof",
    )
    try:
        with pytest.raises(RuntimeError, match="request failed"):
            provider.complete(
                model_id="gpt-5.3-codex",
                system_prompt="system",
                conversation=[{"role": "user", "content": "hello"}],
                session_id="as_1",
            )
    finally:
        provider.shutdown()


def test_external_provider_prefers_text_bearing_completion_over_empty_terminal_event(monkeypatch) -> None:
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "Nested answer"}],
                            }
                        ]
                    },
                }
            ),
            json.dumps({"type": "turn.completed"}),
        ]
    )

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        del args, kwargs
        return subprocess.CompletedProcess(args=["mock"], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=["codex", "exec"],
        default_model="gpt-5.3-codex",
        models=["gpt-5.3-codex"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        persistent=True,
        stdin_mode="turn_eof",
    )
    try:
        result = provider.complete(
            model_id="gpt-5.3-codex",
            system_prompt="system",
            conversation=[{"role": "user", "content": "hello"}],
            session_id="as_1",
        )
        assert result.text == "Nested answer"
    finally:
        provider.shutdown()


def test_external_provider_extracts_codex_item_completed_agent_message(monkeypatch) -> None:
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_1", "type": "agent_message", "text": "Codex item message"},
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 9}}),
        ]
    )

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        del args, kwargs
        return subprocess.CompletedProcess(args=["mock"], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)
    provider = ExternalMcpCliProvider(
        provider_id="codex_cli",
        command=["codex", "exec"],
        default_model="gpt-5.3-codex",
        models=["gpt-5.3-codex"],
        mcp_sse_url="http://127.0.0.1:8000/api/v1/mcp/sse",
        persistent=True,
        stdin_mode="turn_eof",
    )
    try:
        result = provider.complete(
            model_id="gpt-5.3-codex",
            system_prompt="system",
            conversation=[{"role": "user", "content": "hello"}],
            session_id="as_1",
        )
        assert result.text == "Codex item message"
    finally:
        provider.shutdown()
