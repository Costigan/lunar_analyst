from __future__ import annotations

from backend.services.assistant.context_builder import (
    build_system_prompt,
    compact_tool_result_for_model_context,
    summarize_tool_result,
)


def test_summarize_tool_result_uses_artifact_summary_text() -> None:
    result = {
        "summary_text": "GeoTIFF `hillshade.tif` with 1 band(s), 512x512 pixels, dtype=uint8.",
        "key_stats": {"width": 512, "height": 512},
        "warnings": [],
    }
    text = summarize_tool_result("artifact.describe_geotiff", result)
    assert text == "GeoTIFF `hillshade.tif` with 1 band(s), 512x512 pixels, dtype=uint8."


def test_summarize_tool_result_uses_capabilities_text() -> None:
    result = {"text": "Lunar Analyst can manage scenarios and run jobs."}
    text = summarize_tool_result("capabilities.describe", result)
    assert text == "Lunar Analyst can manage scenarios and run jobs."


def test_compact_tool_result_strips_inline_render_payloads() -> None:
    result = {
        "summary_text": "Preview ready.",
        "key_stats": {"width": 256, "height": 256},
        "artifacts": [
            {
                "output_id": "preview",
                "kind": "image",
                "mime_type": "image/png",
                "storage": "file",
                "title": "preview.png",
                "file_id": "fil_preview",
                "data": {"base64": "a" * 1000},
                "metadata": {"alt": "Preview"},
            }
        ],
    }

    compact = compact_tool_result_for_model_context("artifact.preview_geotiff", result)

    assert compact["summary_text"] == "Preview ready."
    assert compact["artifacts"] == [
        {
            "output_id": "preview",
            "kind": "image",
            "mime_type": "image/png",
            "storage": "file",
            "title": "preview.png",
            "file_id": "fil_preview",
        }
    ]
    assert "base64" not in str(compact)


def test_system_prompt_enforces_script_intent_policy() -> None:
    prompt = build_system_prompt(
        scenario_id="scn_x",
        scenario_directory="/d/lunar_analyst_scenarios/scn_x",
        capabilities_text="capabilities",
        compacted_summary=None,
        persistent_constraints="Prefer byte rasters unless precision is required.",
    )
    assert "must use scenario.write_run_script" in prompt
    assert "For deictic confirmations like 'do that'" in prompt
    assert "Visibility/selection mask" in prompt
    assert "Persistent constraints:" in prompt
