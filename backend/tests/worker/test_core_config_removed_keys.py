from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.config import APP_CONFIG_ENV, load_app_config


def test_removed_llm_keys_are_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg_path = tmp_path / "bad.toml"
    cfg_path.write_text(
        "\n".join(
            [
                "[backend.llm]",
                "default_provider = \"openai\"",
                "hybrid_command_router_enabled = true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(APP_CONFIG_ENV, str(cfg_path))
    with pytest.raises(ValueError, match="Removed/invalid config keys"):
        load_app_config(strict=True)


def test_removed_segment_classifier_table_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg_path = tmp_path / "bad-segment.toml"
    cfg_path.write_text(
        "\n".join(
            [
                "[backend.llm]",
                "default_provider = \"openai\"",
                "[backend.llm.segment_intent_classifier]",
                "model = \"any\"",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(APP_CONFIG_ENV, str(cfg_path))
    with pytest.raises(ValueError, match="Removed/invalid config keys"):
        load_app_config(strict=True)


def test_removed_keys_are_rejected_even_with_non_strict_load(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg_path = tmp_path / "bad-nonstrict.toml"
    cfg_path.write_text(
        "\n".join(
            [
                "[backend.llm.performance]",
                "prewarm_on_startup = false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(APP_CONFIG_ENV, str(cfg_path))
    with pytest.raises(ValueError, match="Removed/invalid config keys"):
        load_app_config(strict=False)
