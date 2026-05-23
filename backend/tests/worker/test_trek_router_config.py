from __future__ import annotations

from backend.api.routers import trek as trek_router


def test_load_trek_cache_ttl_seconds_reads_config_override(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "lunar_analyst.toml"
    cfg.write_text(
        "\n".join(
            [
                "[backend]",
                "[backend.trek]",
                "catalog_cache_ttl_seconds = 345600",
                "feature_cache_ttl_seconds = 604800",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(cfg))

    catalog_ttl, feature_ttl = trek_router._load_trek_cache_ttl_seconds()

    assert catalog_ttl == 345600
    assert feature_ttl == 604800


def test_load_trek_cache_ttl_seconds_clamps_invalid_values(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "lunar_analyst.toml"
    cfg.write_text(
        "\n".join(
            [
                "[backend]",
                "[backend.trek]",
                "catalog_cache_ttl_seconds = 10",
                "feature_cache_ttl_seconds = \"not-a-number\"",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(cfg))

    catalog_ttl, feature_ttl = trek_router._load_trek_cache_ttl_seconds()

    assert catalog_ttl == 60
    assert feature_ttl == 14 * 24 * 60 * 60
