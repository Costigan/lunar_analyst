from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from backend.core.config import load_app_config


_DEFAULT_CATALOG_URL = "http://127.0.0.1:8000/api/v1/assistant/providers"
_PROVIDER_PATHS: dict[str, tuple[str, ...]] = {
    "ollama": ("ollama",),
    "local_subprocess": ("local_subprocess",),
    "codex_cli": ("codex_cli",),
    "gemini_cli": ("gemini_cli",),
    "openai": ("remote", "openai"),
    "anthropic": ("remote", "anthropic"),
    "google": ("remote", "google"),
}
_DEFAULT_BENCHMARK_BY_SUITE: dict[str, str | None] = {
    "functional": "backend/evals/assistant/functional_benchmark_v1.csv",
    "domain": None,
    "all": None,
}
_DEFAULT_EXCLUDED_CLI_PROVIDERS: tuple[str, ...] = ("codex_cli", "gemini_cli")


def _dedupe_targets(targets: list[ModelTarget]) -> list[ModelTarget]:
    by_key: dict[tuple[str, str], ModelTarget] = {}
    for target in targets:
        key = (target.provider_id, target.model_id)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = target
            continue
        # Prefer records that contain capability metadata.
        if existing.capabilities is None and target.capabilities is not None:
            by_key[key] = target
    out = list(by_key.values())
    out.sort(key=lambda item: (item.provider_id, item.model_id))
    return out


@dataclass(frozen=True)
class ModelTarget:
    provider_id: str
    model_id: str
    capabilities: tuple[str, ...] | None = None


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    duration_ms: int
    stdout: str
    stderr: str
    timed_out: bool = False
    exception: str | None = None


def _normalize_model_list(raw: Any) -> list[str]:
    models: list[str] = []
    if isinstance(raw, list):
        models = [str(item).strip() for item in raw if str(item).strip()]
    elif isinstance(raw, str) and raw.strip():
        models = [raw.strip()]
    seen: set[str] = set()
    deduped: list[str] = []
    for model in models:
        if model not in seen:
            seen.add(model)
            deduped.append(model)
    return deduped


def _get_nested_dict(root: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    node: Any = root
    for part in path:
        if not isinstance(node, dict):
            return {}
        node = node.get(part)
    return node if isinstance(node, dict) else {}


def discover_targets_from_config(
    *,
    include_providers: set[str] | None = None,
    exclude_providers: set[str] | None = None,
) -> list[ModelTarget]:
    config = load_app_config(strict=True)
    backend_cfg = config.get("backend", {})
    if not isinstance(backend_cfg, dict):
        raise RuntimeError("Invalid app config shape: expected [backend] table")
    llm_cfg = backend_cfg.get("llm", {})
    if not isinstance(llm_cfg, dict):
        raise RuntimeError("Invalid app config shape: expected [backend.llm] table")

    include = set(include_providers or set())
    exclude = set(exclude_providers or set())
    targets: list[ModelTarget] = []

    for provider_id, path in _PROVIDER_PATHS.items():
        if include and provider_id not in include:
            continue
        if provider_id in exclude:
            continue
        provider_cfg = _get_nested_dict(llm_cfg, path)
        if not provider_cfg:
            continue
        if provider_cfg.get("enabled") is False:
            continue

        models = _normalize_model_list(provider_cfg.get("models"))
        if not models:
            model = str(provider_cfg.get("model", "")).strip()
            if model:
                models = [model]
        for model in models:
            targets.append(ModelTarget(provider_id=provider_id, model_id=model, capabilities=None))

    return targets


def discover_targets_from_api(
    *,
    catalog_url: str,
    timeout_seconds: float,
    include_providers: set[str] | None = None,
    exclude_providers: set[str] | None = None,
) -> list[ModelTarget]:
    req = urlrequest.Request(catalog_url, method="GET")
    with urlrequest.urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))

    if not isinstance(payload, dict):
        raise RuntimeError("Provider catalog response must be an object")
    raw_providers = payload.get("providers")
    if not isinstance(raw_providers, list):
        raise RuntimeError("Provider catalog response missing providers[]")

    include = set(include_providers or set())
    exclude = set(exclude_providers or set())
    targets: list[ModelTarget] = []

    for item in raw_providers:
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("provider_id", "")).strip()
        if not provider_id:
            continue
        if include and provider_id not in include:
            continue
        if provider_id in exclude:
            continue
        if item.get("available") is False:
            continue

        models = _normalize_model_list(item.get("models"))
        if not models:
            default_model = str(item.get("default_model", "")).strip()
            if default_model:
                models = [default_model]
        raw_metadata = item.get("model_metadata")
        metadata_by_model = raw_metadata if isinstance(raw_metadata, dict) else {}
        for model in models:
            capabilities: tuple[str, ...] | None = None
            model_meta = metadata_by_model.get(model)
            if isinstance(model_meta, dict):
                raw_caps = model_meta.get("capabilities")
                if isinstance(raw_caps, list):
                    normalized = [str(entry).strip().lower() for entry in raw_caps if str(entry).strip()]
                    capabilities = tuple(normalized)
            targets.append(ModelTarget(provider_id=provider_id, model_id=model, capabilities=capabilities))

    return targets


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return text.strip("._-") or "value"


def _run_command(command: list[str], *, cwd: Path, timeout_seconds: int) -> CommandResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return CommandResult(
            command=command,
            returncode=int(completed.returncode),
            duration_ms=int((time.perf_counter() - started) * 1000),
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
        return CommandResult(
            command=command,
            returncode=124,
            duration_ms=int((time.perf_counter() - started) * 1000),
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
            exception=str(exc),
        )
    except Exception as exc:  # pragma: no cover - defensive
        return CommandResult(
            command=command,
            returncode=1,
            duration_ms=int((time.perf_counter() - started) * 1000),
            stdout="",
            stderr="",
            exception=str(exc),
        )


def _write_command_log(path: Path, result: CommandResult) -> None:
    payload = {
        "command": result.command,
        "returncode": result.returncode,
        "duration_ms": result.duration_ms,
        "timed_out": result.timed_out,
        "exception": result.exception,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"id": f"decode_error_{idx}", "error": f"invalid_json_line_{idx}"})
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return False
    return text in {"1", "true", "yes", "y", "on"}


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _prediction_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if total == 0:
        return {
            "cases": 0,
            "overall_success_rate": 0.0,
            "first_try_success_rate": 0.0,
            "hard_error_rate": 1.0,
            "tool_call_rate": 0.0,
            "source_reference_rate": 0.0,
            "prefilter_eligibility_rate": 0.0,
            "first_attempt_eligible_executed_success_rate": 0.0,
            "avg_duration_ms": None,
        }

    success_count = 0
    first_try_count = 0
    hard_error_count = 0
    tool_call_count = 0
    source_ref_count = 0
    prefilter_eligible_numerator = 0
    prefilter_eligible_denominator = 0
    eligible_success_numerator = 0
    eligible_success_denominator = 0
    durations: list[float] = []

    for row in rows:
        if _boolish(row.get("overall_success")):
            success_count += 1
        if _boolish(row.get("first_try_success")):
            first_try_count += 1
        if str(row.get("mode", "")).strip() == "tool_call":
            tool_call_count += 1
        if str(row.get("error", "")).strip():
            hard_error_count += 1
        prefilter_eligible = row.get("prefilter_eligible")
        if prefilter_eligible is not None:
            prefilter_eligible_denominator += 1
            if _boolish(prefilter_eligible):
                prefilter_eligible_numerator += 1
            eligible_success_denominator += 1
            if _boolish(prefilter_eligible) and _boolish(row.get("first_try_success")):
                eligible_success_numerator += 1
        try:
            if int(row.get("source_reference_count", 0) or 0) > 0:
                source_ref_count += 1
        except Exception:
            pass
        try:
            durations.append(float(row.get("duration_ms", 0) or 0))
        except Exception:
            pass

    return {
        "cases": total,
        "overall_success_rate": success_count / total,
        "first_try_success_rate": first_try_count / total,
        "hard_error_rate": hard_error_count / total,
        "tool_call_rate": tool_call_count / total,
        "source_reference_rate": source_ref_count / total,
        "prefilter_eligibility_rate": (
            prefilter_eligible_numerator / prefilter_eligible_denominator
            if prefilter_eligible_denominator > 0
            else 0.0
        ),
        "first_attempt_eligible_executed_success_rate": (
            eligible_success_numerator / eligible_success_denominator
            if eligible_success_denominator > 0
            else 0.0
        ),
        "avg_duration_ms": _mean(durations),
    }


def _score_metrics(score_json_path: Path) -> dict[str, Any]:
    if not score_json_path.exists():
        return {}
    payload = json.loads(score_json_path.read_text(encoding="utf-8"))
    metrics = payload.get("metrics", {}) if isinstance(payload, dict) else {}
    case_scores = payload.get("case_scores", []) if isinstance(payload, dict) else []
    weighted: float | None = None
    if isinstance(case_scores, list) and case_scores:
        scores: list[float] = []
        for item in case_scores:
            if not isinstance(item, dict):
                continue
            try:
                scores.append(float(item.get("score", 0.0)))
            except Exception:
                continue
        if scores:
            weighted = sum(scores) / len(scores)

    out: dict[str, Any] = {
        "weighted_score_100": weighted,
        "mode_accuracy": metrics.get("mode_accuracy"),
        "tool_selection_accuracy": metrics.get("tool_selection_accuracy"),
        "required_args_accuracy": metrics.get("required_args_accuracy"),
        "arg_schema_pass_rate": metrics.get("arg_schema_pass_rate"),
        "first_try_success_rate_scored": metrics.get("first_try_success_rate"),
        "unsafe_call_block_rate": metrics.get("unsafe_call_block_rate"),
        "prefilter_eligibility_rate_scored": metrics.get("prefilter_eligibility_rate"),
        "first_attempt_eligible_executed_success_rate_scored": metrics.get(
            "first_attempt_eligible_executed_success_rate"
        ),
        "one_repair_loop_recovery_rate_scored": metrics.get("one_repair_loop_recovery_rate"),
    }
    return out


def _write_case_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["# Case Details", ""]
    if not rows:
        lines.append("No prediction rows were recorded for this model.")
        lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    for row in rows:
        case_id = str(row.get("id", "") or "<unknown>")
        prompt = str(row.get("prompt", "") or "")
        mode = str(row.get("mode", "") or "")
        primary_tool = str(row.get("primary_tool", "") or "")
        turn_status = str(row.get("turn_status", "") or "")
        scenario_id = str(row.get("scenario_id_used", "") or "")
        duration_ms = row.get("duration_ms")
        first_try = _boolish(row.get("first_try_success"))
        overall_success = _boolish(row.get("overall_success"))
        unsafe_blocked = _boolish(row.get("unsafe_blocked"))
        quality_gate_applied = _boolish(row.get("quality_gate_applied"))
        quality_pass = _boolish(row.get("quality_pass"))
        quality_flags = row.get("quality_flags", [])
        error = str(row.get("error", "") or "").strip()
        response_text = str(row.get("response_text", "") or "")
        requested_provider_id = str(row.get("requested_provider_id", "") or "").strip()
        requested_model_id = str(row.get("requested_model_id", "") or "").strip()
        final_provider_id = str(row.get("final_provider_id", "") or "").strip()
        final_model_id = str(row.get("final_model_id", "") or "").strip()
        fallback_used = _boolish(row.get("fallback_used"))
        attempted_models = row.get("attempted_models", [])
        fallback_chain = row.get("fallback_chain", [])
        num_ctx = int(row.get("num_ctx", 0) or 0)
        num_ctx_capture_count = int(row.get("num_ctx_capture_count", 0) or 0)
        num_ctx_captures = row.get("num_ctx_captures", [])
        rag_context_text = str(row.get("rag_context_text", "") or "")
        rag_context_chars = int(row.get("rag_context_chars", 0) or 0)
        rag_context_capture_count = int(row.get("rag_context_capture_count", 0) or 0)
        rag_context_captures = row.get("rag_context_captures", [])
        tool_calls = row.get("tool_calls", [])
        source_refs = row.get("source_references", [])
        warnings = row.get("warnings", [])

        lines.append(f"## {case_id}")
        lines.append("")
        lines.append(f"- scenario_id_used: `{scenario_id}`")
        lines.append(f"- mode: `{mode}`")
        lines.append(f"- primary_tool: `{primary_tool or '-'}`")
        lines.append(f"- turn_status: `{turn_status or '-'}`")
        lines.append(f"- first_try_success: `{str(first_try).lower()}`")
        lines.append(f"- overall_success: `{str(overall_success).lower()}`")
        lines.append(f"- unsafe_blocked: `{str(unsafe_blocked).lower()}`")
        requested_model_label = f"{requested_provider_id}/{requested_model_id}".strip("/") or "-"
        final_model_label = f"{final_provider_id}/{final_model_id}".strip("/") or "-"
        lines.append(f"- requested_model: `{requested_model_label}`")
        lines.append(f"- final_model: `{final_model_label}`")
        lines.append(f"- fallback_used: `{str(fallback_used).lower()}`")
        if quality_gate_applied:
            lines.append(f"- quality: `{'pass' if quality_pass else 'fail'}`")
        else:
            lines.append("- quality: `n/a`")
        if isinstance(quality_flags, list) and quality_flags:
            lines.append(f"- quality_flags: `{'; '.join(str(item) for item in quality_flags if str(item).strip())}`")
        if isinstance(duration_ms, int):
            lines.append(f"- duration_ms: `{duration_ms}`")
        lines.append(f"- num_ctx: `{num_ctx}`")
        lines.append(f"- num_ctx_capture_count: `{num_ctx_capture_count}`")
        lines.append(f"- rag_context_chars: `{rag_context_chars}`")
        lines.append(f"- rag_context_capture_count: `{rag_context_capture_count}`")
        if error:
            lines.append(f"- error: `{error}`")
        lines.append("")
        lines.append("### Prompt")
        lines.append("")
        lines.append("```text")
        lines.append(prompt)
        lines.append("```")
        lines.append("")
        lines.append("### Tool Calls")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(tool_calls if isinstance(tool_calls, list) else [], indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")
        lines.append("### Response Text")
        lines.append("")
        lines.append("```text")
        lines.append(response_text)
        lines.append("```")
        lines.append("")
        lines.append("### Source References")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(source_refs if isinstance(source_refs, list) else [], indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")
        lines.append("### Injected RAG Context")
        lines.append("")
        lines.append("```text")
        lines.append(rag_context_text)
        lines.append("```")
        lines.append("")
        lines.append("### Model Attempts")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(attempted_models if isinstance(attempted_models, list) else [], indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")
        lines.append("### Fallback Chain")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(fallback_chain if isinstance(fallback_chain, list) else [], indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")
        if isinstance(num_ctx_captures, list) and num_ctx_captures:
            lines.append("### Num Ctx Captures")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(num_ctx_captures, indent=2, ensure_ascii=False))
            lines.append("```")
            lines.append("")
        if isinstance(rag_context_captures, list) and rag_context_captures:
            lines.append("### RAG Context Captures")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(rag_context_captures, indent=2, ensure_ascii=False))
            lines.append("```")
            lines.append("")
        if isinstance(warnings, list) and warnings:
            lines.append("### Warnings")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(warnings, indent=2, ensure_ascii=False))
            lines.append("```")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _format_rate(value: Any) -> str:
    try:
        numeric = float(value)
    except Exception:
        return "-"
    return f"{numeric:.3f}"


def _format_number(value: Any) -> str:
    try:
        numeric = float(value)
    except Exception:
        return "-"
    return f"{numeric:.1f}"


def _render_markdown_table(rows: list[dict[str, Any]]) -> str:
    headers = [
        "rank",
        "provider",
        "model",
        "status",
        "cases",
        "success",
        "errors",
        "avg_ms",
        "weighted",
        "mode_acc",
        "tool_acc",
        "prefilter_ok",
    ]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for idx, row in enumerate(rows, start=1):
        line = [
            str(idx),
            str(row.get("provider_id", "")),
            str(row.get("model_id", "")),
            str(row.get("status", "")),
            str(row.get("cases", "")),
            _format_rate(row.get("overall_success_rate")),
            _format_rate(row.get("hard_error_rate")),
            _format_number(row.get("avg_duration_ms")),
            _format_number(row.get("weighted_score_100")),
            _format_rate(row.get("mode_accuracy")),
            _format_rate(row.get("tool_selection_accuracy")),
            _format_rate(row.get("prefilter_eligibility_rate_scored")),
        ]
        lines.append("| " + " | ".join(line) + " |")
    return "\n".join(lines)


def _status_rank(status: str) -> int:
    order = {"ok": 3, "score_failed": 2, "run_failed": 1}
    return order.get(status, 0)


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            _status_rank(str(row.get("status", ""))),
            float(row.get("weighted_score_100", -1.0) or -1.0),
            float(row.get("overall_success_rate", 0.0) or 0.0),
            -float(row.get("hard_error_rate", 1.0) or 1.0),
            -float(row.get("avg_duration_ms", 10**12) or 10**12),
        ),
        reverse=True,
    )


def _parse_target(raw: str) -> ModelTarget:
    text = str(raw).strip()
    if ":" not in text:
        raise ValueError(f"Invalid --target '{raw}'. Expected format provider:model")
    provider, model = text.split(":", 1)
    provider_id = provider.strip()
    model_id = model.strip()
    if not provider_id or not model_id:
        raise ValueError(f"Invalid --target '{raw}'. Expected format provider:model")
    return ModelTarget(provider_id=provider_id, model_id=model_id, capabilities=None)


def _ensure_rag_db_parent_from_config() -> str | None:
    try:
        config = load_app_config(strict=True)
        backend_cfg = config.get("backend", {})
        llm_cfg = backend_cfg.get("llm", {}) if isinstance(backend_cfg, dict) else {}
        rag_cfg = llm_cfg.get("rag", {}) if isinstance(llm_cfg, dict) else {}
        if not isinstance(rag_cfg, dict):
            return None
        if rag_cfg.get("enabled") is False:
            return None

        workspace_raw = ""
        if isinstance(backend_cfg, dict):
            workspace_raw = str(backend_cfg.get("workspace_root", "")).strip()
        workspace_root = Path(workspace_raw).expanduser().resolve() if workspace_raw else (Path.cwd() / "scenarios").resolve()
        rag_rel = str(rag_cfg.get("global_index_relative_path", ".assistant/rag/global_rag.db")).strip()
        rag_db_path = (workspace_root / rag_rel).resolve()
        rag_db_path.parent.mkdir(parents=True, exist_ok=True)
        return None
    except Exception as exc:
        return str(exc)


def _resolve_ollama_base_url_from_config() -> str:
    try:
        config = load_app_config(strict=True)
    except Exception:
        return "http://127.0.0.1:11434"
    backend_cfg = config.get("backend", {})
    if not isinstance(backend_cfg, dict):
        return "http://127.0.0.1:11434"
    llm_cfg = backend_cfg.get("llm", {})
    if not isinstance(llm_cfg, dict):
        return "http://127.0.0.1:11434"
    ollama_cfg = llm_cfg.get("ollama", {})
    if not isinstance(ollama_cfg, dict):
        return "http://127.0.0.1:11434"
    url = str(ollama_cfg.get("base_url", "")).strip()
    return url or "http://127.0.0.1:11434"


def _probe_ollama_capabilities(model_id: str, *, timeout_seconds: float) -> tuple[str, ...] | None:
    base_url = _resolve_ollama_base_url_from_config().rstrip("/")
    req = urlrequest.Request(
        url=f"{base_url}/api/show",
        method="POST",
        data=json.dumps({"model": model_id}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    raw_caps = payload.get("capabilities")
    if not isinstance(raw_caps, list):
        return tuple()
    normalized = [str(item).strip().lower() for item in raw_caps if str(item).strip()]
    return tuple(normalized)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run assistant evals across provider/model targets and build a leaderboard.")
    parser.add_argument("--suite", default="functional", choices=["functional", "domain", "all"])
    parser.add_argument("--scenario", type=str, default=None, help="Optional scenario selector for isolated clone source.")
    parser.add_argument("--target", action="append", default=[], help="Explicit provider:model target (repeatable).")
    parser.add_argument("--catalog-source", default="auto", choices=["auto", "config", "api"])
    parser.add_argument("--catalog-url", default=_DEFAULT_CATALOG_URL, help="Provider catalog endpoint for API discovery.")
    parser.add_argument("--catalog-timeout", type=float, default=4.0, help="Seconds for catalog API request timeout.")
    parser.add_argument("--include-provider", action="append", default=[], help="Optional provider filter (repeatable).")
    parser.add_argument("--exclude-provider", action="append", default=[], help="Optional provider exclusion (repeatable).")
    parser.add_argument(
        "--allow-cli-providers",
        action="store_true",
        help="Include CLI providers (codex_cli/gemini_cli). Disabled by default.",
    )
    parser.add_argument(
        "--require-tool-capability",
        dest="require_tool_capability",
        action="store_true",
        default=None,
        help="Require model capability metadata/probe to indicate tool support.",
    )
    parser.add_argument(
        "--no-require-tool-capability",
        dest="require_tool_capability",
        action="store_false",
        help="Do not filter by tool capability.",
    )
    parser.add_argument(
        "--tool-capability-probe-timeout",
        type=float,
        default=3.0,
        help="Seconds for provider capability probe calls (used for some config-discovered models).",
    )
    parser.add_argument("--model-regex", type=str, default=None, help="Optional regex filter applied to model id.")
    parser.add_argument("--max-models", type=int, default=None, help="Optional cap after target filtering.")
    parser.add_argument("--python-exe", type=str, default=sys.executable, help="Python executable used for subprocess runs.")
    parser.add_argument("--benchmark", type=Path, default=None, help="Optional benchmark path passed to score.py.")
    parser.add_argument("--skip-score", action="store_true", help="Skip scoring and rank by prediction metrics only.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory for run artifacts and leaderboard files.")
    parser.add_argument("--confirmation-decision", default="allow_once", choices=["allow_once", "always_allow_action_type", "deny_once", "none"])
    parser.add_argument("--max-confirmation-resolves", type=int, default=8)
    parser.add_argument("--planner-only", action="store_true")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--case-id", action="append", default=[], help="Optional repeated case id filter.")
    parser.add_argument("--sleep-ms", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=7200, help="Per subprocess timeout.")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop matrix run after the first failed target.")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    include_providers = {str(item).strip() for item in args.include_provider if str(item).strip()}
    exclude_providers = {str(item).strip() for item in args.exclude_provider if str(item).strip()}
    if not bool(args.allow_cli_providers):
        exclude_providers.update(_DEFAULT_EXCLUDED_CLI_PROVIDERS)
    model_pattern = re.compile(args.model_regex) if args.model_regex else None

    explicit_targets = [(_parse_target(raw)) for raw in args.target]
    targets: list[ModelTarget]

    if explicit_targets:
        targets = _dedupe_targets(explicit_targets)
        discovery = "explicit_targets"
    else:
        discovery = args.catalog_source
        targets = []
        if args.catalog_source in {"auto", "api"}:
            try:
                targets = discover_targets_from_api(
                    catalog_url=str(args.catalog_url),
                    timeout_seconds=float(args.catalog_timeout),
                    include_providers=include_providers,
                    exclude_providers=exclude_providers,
                )
                targets = _dedupe_targets(targets)
                discovery = "api"
            except (urlerror.URLError, TimeoutError, RuntimeError, json.JSONDecodeError):
                if args.catalog_source == "api":
                    raise
        if not targets:
            targets = discover_targets_from_config(
                include_providers=include_providers,
                exclude_providers=exclude_providers,
            )
            targets = _dedupe_targets(targets)
            discovery = "config"

    if model_pattern is not None:
        targets = [target for target in targets if model_pattern.search(target.model_id)]

    require_tool_capability = args.require_tool_capability
    if require_tool_capability is None:
        require_tool_capability = str(args.suite) in {"functional", "all"}

    filtered_out_no_tools = 0
    unknown_tool_capability = 0
    if require_tool_capability:
        filtered_targets: list[ModelTarget] = []
        ollama_probe_cache: dict[str, tuple[str, ...] | None] = {}
        for target in targets:
            capabilities = target.capabilities
            if capabilities is None and target.provider_id == "ollama":
                if target.model_id not in ollama_probe_cache:
                    ollama_probe_cache[target.model_id] = _probe_ollama_capabilities(
                        target.model_id,
                        timeout_seconds=float(args.tool_capability_probe_timeout),
                    )
                capabilities = ollama_probe_cache[target.model_id]
            if capabilities is None:
                unknown_tool_capability += 1
                filtered_targets.append(target)
                continue
            cap_set = {entry.strip().lower() for entry in capabilities if entry.strip()}
            supports_tools = bool(cap_set & {"tools", "tool_use", "tool_calls", "function_calling", "functions"})
            if supports_tools:
                filtered_targets.append(target)
            else:
                filtered_out_no_tools += 1
        targets = filtered_targets

    if args.max_models is not None and args.max_models >= 0:
        targets = targets[: int(args.max_models)]

    if not targets:
        print("No targets discovered. Provide --target provider:model or adjust catalog/provider filters.")
        return 2

    rag_warning = _ensure_rag_db_parent_from_config()
    if rag_warning:
        print(f"Warning: unable to pre-create RAG DB parent path ({rag_warning})")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or Path("backend/evals/assistant/leaderboard_runs") / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    benchmark_path = args.benchmark
    if benchmark_path is None:
        default_benchmark = _DEFAULT_BENCHMARK_BY_SUITE.get(str(args.suite), None)
        benchmark_path = Path(default_benchmark) if default_benchmark else None
    do_score = not bool(args.skip_score) and benchmark_path is not None

    print(f"Target discovery: {discovery} ({len(targets)} targets)")
    if not bool(args.allow_cli_providers):
        print(f"CLI providers excluded by default: {', '.join(_DEFAULT_EXCLUDED_CLI_PROVIDERS)}")
    else:
        print("CLI providers: included")
    if require_tool_capability:
        print(
            "Tool capability filter: enabled"
            f" (excluded_no_tools={filtered_out_no_tools}, unknown_kept={unknown_tool_capability})"
        )
    else:
        print("Tool capability filter: disabled")
    print(f"Suite: {args.suite}")
    print(f"Output directory: {output_dir}")
    if do_score:
        print(f"Scoring benchmark: {benchmark_path}")
    else:
        print("Scoring: skipped")

    rows: list[dict[str, Any]] = []
    repo_root = Path(__file__).resolve().parents[3]

    for idx, target in enumerate(targets, start=1):
        print(f"[{idx}/{len(targets)}] provider={target.provider_id} model={target.model_id}")
        run_dir = output_dir / f"{_slug(target.provider_id)}__{_slug(target.model_id)}"
        run_dir.mkdir(parents=True, exist_ok=True)

        predictions_path = run_dir / "predictions.jsonl"
        predictions_csv_path = run_dir / "predictions.csv"
        predictions_human_path = run_dir / "predictions_human.txt"
        case_details_md_path = run_dir / "case_details.md"
        run_log_path = run_dir / "run_benchmark.log.json"
        score_log_path = run_dir / "score.log.json"
        score_json_path = run_dir / "score.json"

        run_cmd = [
            str(args.python_exe),
            "-m",
            "backend.evals.assistant.run_benchmark",
            "--suite",
            str(args.suite),
            "--provider",
            target.provider_id,
            "--model",
            target.model_id,
            "--output",
            str(predictions_path),
            "--csv-out",
            str(predictions_csv_path),
            "--human-readable-out",
            str(predictions_human_path),
            "--confirmation-decision",
            str(args.confirmation_decision),
            "--max-confirmation-resolves",
            str(args.max_confirmation_resolves),
            "--sleep-ms",
            str(args.sleep_ms),
        ]
        if args.scenario:
            run_cmd.extend(["--scenario", str(args.scenario)])
        if args.planner_only:
            run_cmd.append("--planner-only")
        if args.max_cases is not None:
            run_cmd.extend(["--max-cases", str(args.max_cases)])
        for case_id in args.case_id:
            run_cmd.extend(["--case-id", str(case_id)])

        run_result = _run_command(run_cmd, cwd=repo_root, timeout_seconds=int(args.timeout_seconds))
        _write_command_log(run_log_path, run_result)

        status = "ok"
        score_result: CommandResult | None = None
        if run_result.returncode != 0:
            status = "run_failed"
        elif do_score:
            score_cmd = [
                str(args.python_exe),
                "-m",
                "backend.evals.assistant.score",
                "--benchmark",
                str(benchmark_path),
                "--predictions",
                str(predictions_path),
                "--json-out",
                str(score_json_path),
            ]
            score_result = _run_command(score_cmd, cwd=repo_root, timeout_seconds=max(300, int(args.timeout_seconds)))
            _write_command_log(score_log_path, score_result)
            if score_result.returncode != 0:
                status = "score_failed"

        predictions = _read_jsonl(predictions_path)
        _write_case_markdown(case_details_md_path, predictions)
        metrics = _prediction_metrics(predictions)
        scored = _score_metrics(score_json_path) if status == "ok" and do_score else {}
        row: dict[str, Any] = {
            "provider_id": target.provider_id,
            "model_id": target.model_id,
            "status": status,
            "run_duration_ms": run_result.duration_ms,
            "score_duration_ms": score_result.duration_ms if score_result else None,
            "predictions_path": str(predictions_path),
            "predictions_csv_path": str(predictions_csv_path),
            "predictions_human_path": str(predictions_human_path),
            "case_details_md_path": str(case_details_md_path),
            "run_log_path": str(run_log_path),
            "score_log_path": str(score_log_path) if score_result else None,
            "score_json_path": str(score_json_path) if score_result else None,
        }
        row.update(metrics)
        row.update(scored)
        rows.append(row)

        if status != "ok" and args.stop_on_error:
            print(f"Stopping early due to status={status}")
            break

    ranked = _sort_rows(rows)
    leaderboard_md = _render_markdown_table(ranked)
    print("")
    print(leaderboard_md)

    leaderboard_json_path = output_dir / "leaderboard.json"
    leaderboard_csv_path = output_dir / "leaderboard.csv"
    leaderboard_md_path = output_dir / "leaderboard.md"

    leaderboard_json_payload = {
        "generated_at": datetime.now().isoformat(),
        "suite": args.suite,
        "discovery": discovery,
        "benchmark": str(benchmark_path) if benchmark_path else None,
        "scored": do_score,
        "targets": [asdict(item) for item in targets],
        "rows": ranked,
    }
    leaderboard_json_path.write_text(json.dumps(leaderboard_json_payload, indent=2), encoding="utf-8")

    fieldnames = sorted({key for row in ranked for key in row.keys()})
    with leaderboard_csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in ranked:
            writer.writerow(row)

    leaderboard_md_path.write_text(leaderboard_md + "\n", encoding="utf-8")
    print("")
    print(f"Wrote JSON: {leaderboard_json_path}")
    print(f"Wrote CSV:  {leaderboard_csv_path}")
    print(f"Wrote MD:   {leaderboard_md_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
