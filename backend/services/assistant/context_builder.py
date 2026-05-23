from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.contracts.assistant_models import AssistantMessage
from backend.core.config import load_app_config, resolve_config_relative_path


_DEFAULT_SYSTEM_PROMPT_FILE = Path(__file__).with_name("system_prompt.txt")


def _resolve_system_prompt_file() -> Path:
    config = load_app_config(strict=False)
    backend_cfg = config.get("backend", {}) if isinstance(config, dict) else {}
    llm_cfg = backend_cfg.get("llm", {}) if isinstance(backend_cfg, dict) else {}
    raw = ""
    if isinstance(llm_cfg, dict):
        raw = str(llm_cfg.get("system_prompt_path", "")).strip()
    if raw:
        try:
            return resolve_config_relative_path(raw)
        except Exception:
            return _DEFAULT_SYSTEM_PROMPT_FILE
    return _DEFAULT_SYSTEM_PROMPT_FILE


SYSTEM_PROMPT_PATH = _resolve_system_prompt_file()


def _load_base_system_prompt_lines() -> list[str]:
    try:
        text = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        text = _DEFAULT_SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line]


BASE_SYSTEM_PROMPT_LINES = _load_base_system_prompt_lines()


def build_conversation(
    messages: list[AssistantMessage],
    *,
    max_messages: int = 40,
) -> list[dict[str, str]]:
    selected = messages[-max_messages:]
    conversation: list[dict[str, str]] = []
    for msg in selected:
        if msg.role.value not in {"user", "assistant"}:
            continue
        conversation.append({"role": msg.role.value, "content": msg.content})
    return conversation


def build_system_prompt(
    *,
    scenario_id: str | None,
    scenario_directory: str | None = None,
    capabilities_text: str,
    compacted_summary: str | None = None,
    persistent_constraints: str | None = None,
) -> str:
    lines: list[str] = [*BASE_SYSTEM_PROMPT_LINES, capabilities_text.strip()]
    if scenario_id:
        lines.append(f"Active scenario_id: {scenario_id}")
    if scenario_directory:
        lines.append(f"Active scenario_directory: {scenario_directory}")
    if persistent_constraints and persistent_constraints.strip():
        lines.append("Persistent constraints:")
        lines.append(persistent_constraints.strip())
    if compacted_summary and compacted_summary.strip():
        lines.append("Session summary:")
        lines.append(compacted_summary.strip())
    return "\n".join(lines)


def summarize_tool_result(tool_name: str, result: dict[str, Any]) -> str:
    if tool_name == "layer.list_visible":
        items = result.get("items")
        if isinstance(items, list):
            titles = [str(item.get("title", "")).strip() for item in items if isinstance(item, dict)]
            titles = [title for title in titles if title]
            if titles:
                return f"Visible layers: {', '.join(titles)}."
            return "No layers are currently visible."
        return "No layers are currently visible."
    if tool_name == "capabilities.describe":
        text = str(result.get("text", "")).strip()
        if text:
            return text
    if tool_name in {
        "artifact.describe_geotiff",
        "artifact.preview_geotiff",
        "artifact.stats_geotiff",
        "artifact.describe_table",
        "artifact.describe_plot",
    }:
        summary = str(result.get("summary_text", "")).strip()
        warnings_raw = result.get("warnings", [])
        warnings: list[str] = []
        if isinstance(warnings_raw, list):
            warnings = [str(item).strip() for item in warnings_raw if str(item).strip()]
        if summary and warnings:
            return f"{summary} Warnings: {'; '.join(warnings)}."
        if summary:
            return summary
    job_payload = result.get("job")
    job: dict[str, Any] = job_payload if isinstance(job_payload, dict) else {}
    job_id = str(result.get("job_id", "")).strip() or str(job.get("job_id", "")).strip()
    job_status = str(job.get("status", result.get("status", ""))).strip().lower()
    if job_status == "queued":
        if job_id:
            return f"Tool `{tool_name}` submitted job `{job_id}` (queued)."
        return f"Tool `{tool_name}` submitted a queued job."
    if job_status == "running":
        if job_id:
            return f"Tool `{tool_name}` started job `{job_id}` (running)."
        return f"Tool `{tool_name}` started a running job."
    if job_status == "completed" and job_id:
        return f"Tool `{tool_name}` completed job `{job_id}`."
    if job_status == "failed" and job_id:
        return f"Tool `{tool_name}` reported failed job `{job_id}`."
    if job_status == "cancelled" and job_id:
        return f"Tool `{tool_name}` reported cancelled job `{job_id}`."
    if not result:
        return f"Tool `{tool_name}` completed without output."
    keys = sorted(result.keys())
    preview_items: list[str] = []
    for key in keys[:6]:
        preview_items.append(f"{key}={result.get(key)!r}")
    preview = ", ".join(preview_items)
    return f"Tool `{tool_name}` completed: {preview}"


def compact_tool_result_for_model_context(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "product.list":
        raw_items = result.get("items")
        compact_items: list[dict[str, Any]] = []
        if isinstance(raw_items, list):
            for item in raw_items[:20]:
                if not isinstance(item, dict):
                    continue
                lineage = item.get("lineage", {})
                relative_path = ""
                if isinstance(lineage, dict):
                    relative_path = str(lineage.get("relative_path", "")).strip()
                compact_items.append(
                    {
                        "product_id": item.get("product_id"),
                        "kind": item.get("kind"),
                        "subkind": item.get("subkind"),
                        "created_at_utc": item.get("created_at_utc"),
                        "relative_path": relative_path,
                    }
                )
        return {
            "count": result.get("count", len(compact_items)),
            "items": compact_items,
            "truncated": bool(isinstance(raw_items, list) and len(raw_items) > len(compact_items)),
        }
    if tool_name == "product.files":
        raw_items = result.get("items")
        compact_items: list[dict[str, Any]] = []
        if isinstance(raw_items, list):
            for item in raw_items[:20]:
                if not isinstance(item, dict):
                    continue
                compact_items.append(
                    {
                        "file_id": item.get("file_id"),
                        "relative_path": item.get("relative_path"),
                        "role": item.get("role"),
                    }
                )
        return {
            "count": result.get("count", len(compact_items)),
            "items": compact_items,
            "truncated": bool(isinstance(raw_items, list) and len(raw_items) > len(compact_items)),
        }
    if tool_name == "layer.list_visible":
        raw_items = result.get("items")
        compact_items: list[dict[str, Any]] = []
        if isinstance(raw_items, list):
            for item in raw_items[:20]:
                if not isinstance(item, dict):
                    continue
                compact_items.append(
                    {
                        "layer_id": item.get("layer_id"),
                        "title": item.get("title"),
                        "visible": item.get("visible"),
                        "z_index": item.get("z_index"),
                    }
                )
        return {
            "scenario_id": result.get("scenario_id"),
            "count": result.get("count", len(compact_items)),
            "items": compact_items,
            "truncated": bool(isinstance(raw_items, list) and len(raw_items) > len(compact_items)),
        }
    compact: dict[str, Any] = {}
    summary_text = str(result.get("summary_text", "")).strip()
    if summary_text:
        compact["summary_text"] = summary_text
    key_stats = result.get("key_stats")
    if isinstance(key_stats, dict):
        compact["key_stats"] = dict(key_stats)
    warnings_raw = result.get("warnings")
    if isinstance(warnings_raw, list):
        compact["warnings"] = [str(item) for item in warnings_raw[:10]]
    source_files = result.get("source_files")
    if isinstance(source_files, list):
        compact["source_files"] = [str(item) for item in source_files[:5]]
    artifact_file_id = str(result.get("artifact_file_id", "")).strip()
    if artifact_file_id:
        compact["artifact_file_id"] = artifact_file_id
    generated_file_id = str(result.get("generated_file_id", "")).strip()
    if generated_file_id:
        compact["generated_file_id"] = generated_file_id
    generated_relative_path = str(result.get("generated_relative_path", "")).strip()
    if generated_relative_path:
        compact["generated_relative_path"] = generated_relative_path
    raw_artifacts = result.get("artifacts")
    if isinstance(raw_artifacts, list):
        compact["artifacts"] = [
            {
                "output_id": str(item.get("output_id", "")).strip(),
                "kind": str(item.get("kind", "")).strip(),
                "mime_type": str(item.get("mime_type", "")).strip(),
                "storage": str(item.get("storage", "")).strip(),
                "title": item.get("title"),
                "file_id": item.get("file_id"),
            }
            for item in raw_artifacts[:10]
            if isinstance(item, dict)
        ]
    if compact:
        return compact
    if tool_name in {"scenario.write_script", "scenario.write_run_script"}:
        return {
            "scenario_id": result.get("scenario_id"),
            "relative_path": result.get("relative_path"),
            "bytes_written": result.get("bytes_written"),
            "job_id": result.get("job_id"),
            "status": result.get("status"),
        }
    if tool_name in {"scenario.run_script", "scenario.run_marimo_notebook"}:
        return {
            "scenario_id": result.get("scenario_id"),
            "relative_path": result.get("relative_path"),
            "job_id": result.get("job_id"),
            "status": result.get("status"),
        }
    return dict(result)
