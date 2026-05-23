from __future__ import annotations

import argparse
from pathlib import Path

from backend.core.config import load_app_config
from backend.services.assistant.bug_report_service import bug_report_root, list_bug_report_summaries


def _workspace_root() -> Path:
    config = load_app_config()
    backend = config.get("backend", {})
    if isinstance(backend, dict):
        workspace_root = backend.get("workspace_root")
        if isinstance(workspace_root, str) and workspace_root.strip():
            return Path(workspace_root).expanduser().resolve()
    return Path(".").resolve()


def _report_snippet(report_text: str, *, max_length: int = 72) -> str:
    first_line = next((line.strip() for line in report_text.splitlines() if line.strip()), report_text.strip())
    snippet = " ".join(first_line.split())
    if len(snippet) <= max_length:
        return snippet
    return f"{snippet[: max_length - 3].rstrip()}..."


def format_bug_report_listing(workspace_root: Path) -> list[str]:
    summaries = list_bug_report_summaries(workspace_root)
    return [
        f"{index}. {summary.bug_report_id}  {_report_snippet(summary.report_text)}"
        for index, summary in enumerate(summaries, start=1)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="List assistant bug reports in the workspace.")
    parser.add_argument("--workspace-root", default=None, help="Override the scenario workspace root containing bug reports")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).expanduser().resolve() if args.workspace_root else _workspace_root()
    lines = format_bug_report_listing(workspace_root)
    if not lines:
        print(f"No assistant bug reports found under {bug_report_root(workspace_root).as_posix()}")
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
