from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core.config import load_app_config
from backend.services.assistant.bug_report_service import bug_report_dir, load_bug_report_bundle, list_bug_report_summaries


RELEVANT_FILES = [
    "docs/DESIGN.md",
    "docs/ADR.0033.assistant_observability_and_failure_taxonomy.md",
    "docs/ADR.0052.explain_assistant_behavior.md",
    "backend/services/assistant/assistant_service.py",
    "backend/api/routers/assistant.py",
    "backend/web/lunar_analyst/src/AppLayout.tsx",
    "backend/web/lunar_analyst/src/components/Toolbar.tsx",
    "backend/web/lunar_analyst/src/components/assistant/AssistantBugReportDialog.tsx",
    "backend/web/lunar_analyst/src/services/assistantService.ts",
]


def _workspace_root() -> Path:
    config = load_app_config()
    backend = config.get("backend", {})
    if isinstance(backend, dict):
        workspace_root = backend.get("workspace_root")
        if isinstance(workspace_root, str) and workspace_root.strip():
            return Path(workspace_root).expanduser().resolve()
    return Path(".").resolve()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_bug_report_reference(workspace_root: Path, selector: str | None) -> tuple[str, Path]:
    summaries = list_bug_report_summaries(workspace_root)
    if not summaries:
        raise FileNotFoundError(
            f"No assistant bug reports found under {workspace_root / 'debugging' / 'assistant-bug-reports'}"
        )
    raw_selector = (selector or "").strip()
    if not raw_selector or raw_selector.lower() == "latest":
        chosen = summaries[0]
        return chosen.bug_report_id, Path(chosen.bundle_path)
    if raw_selector.isdigit():
        sequence_number = int(raw_selector)
        if sequence_number < 1 or sequence_number > len(summaries):
            raise ValueError(f"Bug report sequence {sequence_number} is out of range (1-{len(summaries)})")
        chosen = summaries[sequence_number - 1]
        return chosen.bug_report_id, Path(chosen.bundle_path)
    return raw_selector, bug_report_dir(workspace_root, raw_selector) / "bug-report.json"


def _read_file(path: Path) -> str:
    if not path.exists():
        return f"[missing: {path.as_posix()}]"
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"[unable to read {path.as_posix()}: {exc}]"


def _copy_context_files(output_dir: Path, repo_root: Path) -> list[str]:
    copied: list[str] = []
    for relative_path in RELEVANT_FILES:
        source = (repo_root / relative_path).resolve()
        if not source.exists():
            continue
        if source.is_dir():
            continue
        destination = (output_dir / "context" / relative_path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_read_file(source), encoding="utf-8")
        copied.append(relative_path)
    return copied


def _build_prompt(provider: str, bug_report: dict[str, Any], repo_root: Path) -> str:
    return "\n".join(
        [
            f"# Assistant Bug Analysis ({provider})",
            "",
            "You are reviewing a captured Lunar Analyst assistant bug report.",
            "Use the repository docs and code to identify the most likely root cause,",
            "reproduction hypothesis, and a concrete fix/test plan.",
            "",
            "## Bug Report",
            "",
            "```json",
            json.dumps(bug_report, indent=2, sort_keys=True),
            "```",
            "",
            "## Relevant Files",
            "",
            *[f"- `{path}`" for path in RELEVANT_FILES],
            "",
            "A matching copy of each file is written under `context/` in the analysis directory.",
            "",
            "## Repo Root",
            "",
            f"`{repo_root.as_posix()}`",
            "",
            "## Instructions",
            "",
            "1. Inspect the cited docs and code paths in the repository.",
            "2. Explain what assistant behavior likely failed and why.",
            "3. Call out any missing telemetry or state needed for diagnosis.",
            "4. Suggest the smallest safe fix path and the regression tests to add.",
            "5. Keep the analysis grounded in the captured bundle; do not speculate beyond the evidence.",
            "",
        ]
    )


def _write_package(
    output_dir: Path,
    provider: str,
    prompt: str,
    bug_report: dict[str, Any],
    repo_root: Path,
    bug_report_path: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    copied_context_files = _copy_context_files(output_dir, repo_root)
    prompt_path = output_dir / "analysis_prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider": provider,
        "repo_root": repo_root.as_posix(),
        "bug_report_id": bug_report.get("bug_report_id"),
        "bug_report_path": bug_report_path.as_posix(),
        "relevant_files": RELEVANT_FILES,
        "copied_context_files": copied_context_files,
        "analysis_prompt_path": prompt_path.as_posix(),
    }
    (output_dir / "analysis_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def _run_provider_analysis(provider: str, prompt: str, repo_root: Path, output_dir: Path) -> None:
    response_path = output_dir / f"{provider}_analysis_response.md"
    provider_home = output_dir / f".{provider}_home"
    provider_env = os.environ.copy()
    provider_env.update(
        {
            "XDG_CONFIG_HOME": (provider_home / "config").as_posix(),
            "XDG_CACHE_HOME": (provider_home / "cache").as_posix(),
            "XDG_DATA_HOME": (provider_home / "data").as_posix(),
            "XDG_STATE_HOME": (provider_home / "state").as_posix(),
            "TMPDIR": (provider_home / "tmp").as_posix(),
        }
    )
    for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "TMPDIR"):
        Path(provider_env[key]).mkdir(parents=True, exist_ok=True)
    print(f"{provider.capitalize()} launch diagnostics:")
    print(f"  HOME={provider_env.get('HOME', '')}")
    print(f"  XDG_CONFIG_HOME={provider_env['XDG_CONFIG_HOME']}")
    print(f"  XDG_CACHE_HOME={provider_env['XDG_CACHE_HOME']}")
    print(f"  XDG_DATA_HOME={provider_env['XDG_DATA_HOME']}")
    print(f"  XDG_STATE_HOME={provider_env['XDG_STATE_HOME']}")
    print(f"  TMPDIR={provider_env['TMPDIR']}")
    if provider == "codex":
        cmd = [
            "codex",
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--cd",
            str(repo_root),
            "--add-dir",
            str(repo_root),
            "--add-dir",
            str(output_dir),
            "--output-last-message",
            str(response_path),
            "-",
        ]
        print(f"Running Codex command: {' '.join(cmd[:-1])} <prompt>")
        subprocess.run(cmd, input=prompt, text=True, check=True, env=provider_env)
        print(f"Saved Codex response to {response_path.as_posix()}")
        return
    if provider == "gemini":
        cmd = [
            "gemini",
            "-p",
            prompt,
            "--approval-mode",
            "plan",
            "--include-directories",
            str(repo_root),
            "--output-format",
            "text",
        ]
        print(f"Running Gemini command: {' '.join(cmd)}")
        subprocess.run(cmd, cwd=str(output_dir), check=True, env=provider_env)
        print(f"Gemini analysis run from {output_dir.as_posix()}")
        return
    if provider == "copilot":
        cmd = [
            "copilot",
            "-p",
            prompt,
            "--mode",
            "plan",
            "--allow-all-paths",
            "--allow-all-tools",
        ]
        print(f"Running Copilot command: {' '.join(cmd)}")
        subprocess.run(cmd, cwd=str(output_dir), check=True, env=provider_env)
        print(f"Copilot analysis run from {output_dir.as_posix()}")
        return
    raise ValueError(f"Unsupported provider: {provider}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an offline analysis package for an assistant bug report.")
    parser.add_argument(
        "bug_report_id_or_path",
        nargs="?",
        default=None,
        help="Bug report selector: sequence number, bug report id, or path to bug-report.json (defaults to latest)",
    )
    parser.add_argument("--provider", choices=["codex", "gemini", "copilot"], default="codex")
    parser.add_argument("--repo-root", default=None, help="Override the repository root containing docs and code")
    parser.add_argument("--workspace-root", default=None, help="Override the scenario workspace root containing bug reports")
    parser.add_argument("--output-dir", default=None, help="Override the output directory")
    parser.add_argument("--launch", action="store_true", help="Run the selected provider after writing the package")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else _repo_root()
    workspace_root = Path(args.workspace_root).expanduser().resolve() if args.workspace_root else _workspace_root()
    selector = str(args.bug_report_id_or_path or "").strip()
    bug_report_path = Path(selector).expanduser()
    if selector and bug_report_path.is_file():
        bundle_path = bug_report_path.resolve()
        report_id = bundle_path.parent.name
        bug_report = json.loads(bundle_path.read_text(encoding="utf-8"))
    else:
        report_id, bundle_path = _resolve_bug_report_reference(workspace_root, selector)
        if bundle_path.is_file():
            bug_report = json.loads(bundle_path.read_text(encoding="utf-8"))
        else:
            bundle = load_bug_report_bundle(workspace_root, report_id)
            bug_report = bundle.model_dump(mode="json")

    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else bug_report_dir(workspace_root, report_id) / "analysis" / f"{args.provider}_{_utc_stamp()}"
    )
    prompt = _build_prompt(args.provider, bug_report, repo_root)
    final_output_dir = output_dir
    try:
        manifest = _write_package(output_dir, args.provider, prompt, bug_report, repo_root, bundle_path)
    except OSError as exc:
        if args.output_dir is not None:
            raise
        final_output_dir = (
            Path(tempfile.gettempdir())
            / "lunar-analyst"
            / "assistant-bug-reports"
            / report_id
            / "analysis"
            / f"{args.provider}_{_utc_stamp()}"
        )
        print(f"Falling back to writable analysis directory: {final_output_dir.as_posix()}")
        manifest = _write_package(final_output_dir, args.provider, prompt, bug_report, repo_root, bundle_path)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    if args.launch:
        print(f"Launching {args.provider} analysis from {final_output_dir.as_posix()}")
        _run_provider_analysis(args.provider, prompt, repo_root, final_output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
