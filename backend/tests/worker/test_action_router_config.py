from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.assistant.action_router_config import load_action_router_specs


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_load_action_router_specs_valid_minimal(tmp_path: Path) -> None:
    spec = _write(
        tmp_path / "router.yaml",
        """
version: 1
actions:
  - action_id: test.one
    priority: 1
    patterns:
      - '^hello$'
    steps:
      - kind: tool_call
        tool_name: capabilities.describe
        arguments: {}
""".strip(),
    )
    loaded = load_action_router_specs(spec_path=spec)
    assert len(loaded) == 1
    assert loaded[0].action_id == "test.one"


def test_load_action_router_specs_rejects_invalid_regex(tmp_path: Path) -> None:
    spec = _write(
        tmp_path / "router.yaml",
        """
version: 1
actions:
  - action_id: bad.regex
    priority: 1
    patterns:
      - '^(unclosed$'
    steps:
      - kind: tool_call
        tool_name: capabilities.describe
        arguments: {}
""".strip(),
    )
    with pytest.raises(ValueError, match="invalid regex"):
        load_action_router_specs(spec_path=spec)


def test_load_action_router_specs_rejects_unknown_tool(tmp_path: Path) -> None:
    spec = _write(
        tmp_path / "router.yaml",
        """
version: 1
actions:
  - action_id: bad.tool
    priority: 1
    patterns:
      - '^hello$'
    steps:
      - kind: tool_call
        tool_name: no.such.tool
        arguments: {}
""".strip(),
    )
    with pytest.raises(ValueError, match="unknown tool"):
        load_action_router_specs(spec_path=spec)


def test_load_action_router_specs_rejects_unknown_placeholder(tmp_path: Path) -> None:
    spec = _write(
        tmp_path / "router.yaml",
        """
version: 1
actions:
  - action_id: bad.placeholder
    priority: 1
    patterns:
      - '^hello$'
    steps:
      - kind: tool_call
        tool_name: capabilities.describe
        arguments:
          foo: "${does_not_exist}"
""".strip(),
    )
    with pytest.raises(ValueError, match="Unknown placeholder"):
        load_action_router_specs(spec_path=spec)


def test_load_action_router_specs_rejects_mutating_agent_allowed_tool(tmp_path: Path) -> None:
    spec = _write(
        tmp_path / "router.yaml",
        """
version: 1
actions:
  - action_id: bad.agent
    priority: 1
    patterns:
      - '^hello$'
    steps:
      - kind: agent_call
        objective: "resolve"
        allowed_tools:
          - layer.update_state
        output_schema:
          type: object
        max_iterations: 1
        max_output_tokens: 64
        timeout_ms: 2000
""".strip(),
    )
    with pytest.raises(ValueError, match="mutating tool not allowed"):
        load_action_router_specs(spec_path=spec)
