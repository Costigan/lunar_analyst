from __future__ import annotations

import argparse
from pathlib import Path

import pytest


def main() -> int:
    parser = argparse.ArgumentParser(description="Run assistant eval suites via pytest harness.")
    parser.add_argument(
        "--suite",
        type=str,
        default="functional",
        choices=["functional", "domain", "all"],
        help="Pytest case suite to run.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output predictions path. Defaults to backend/evals/assistant/predictions_<suite>.jsonl",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Base scenario selector for per-test isolated clones; accepts scenario_id or scenario root name.",
    )
    parser.add_argument("--provider", type=str, default=None, help="Override provider id.")
    parser.add_argument("--model", type=str, default=None, help="Override model id.")
    parser.add_argument("--planner-only", action="store_true", help="Use parser fast-path planning only (no provider calls).")
    parser.add_argument(
        "--confirmation-decision",
        type=str,
        default="allow_once",
        choices=["allow_once", "always_allow_action_type", "deny_once", "none"],
        help="How to resolve pending assistant confirmations during runs.",
    )
    parser.add_argument(
        "--max-confirmation-resolves",
        type=int,
        default=8,
        help="Safety cap on confirmation resolutions per case.",
    )
    parser.add_argument("--case-id", action="append", default=[], help="Optional repeated case id filter.")
    parser.add_argument("--max-cases", type=int, default=None, help="Optional case limit after filters.")
    parser.add_argument("--csv-out", type=Path, default=None, help="Optional CSV output path.")
    parser.add_argument("--xlsx-out", type=Path, default=None, help="Optional XLSX output path.")
    parser.add_argument("--human-readable", action="store_true", help="Print human-readable per-case summaries.")
    parser.add_argument(
        "--human-readable-out",
        type=Path,
        default=None,
        help="Optional text output for human-readable summaries.",
    )
    parser.add_argument("--sleep-ms", type=int, default=0, help="Delay between cases in milliseconds.")
    parser.add_argument(
        "--capture-rag-context",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Capture exact injected RAG context in eval records (enabled by default for eval runs).",
    )
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first failed test assertion or runtime error.")
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=None,
        help="Deprecated: benchmark file inputs are no longer used; tests are pure pytest functions.",
    )
    args = parser.parse_args()

    if args.benchmark is not None:
        print(f"Ignoring deprecated --benchmark={args.benchmark}; using Python test suites.")

    pytest_args = [
        "-q",
        "-s",
        "backend/tests/evals",
        "--suite",
        str(args.suite),
        "--confirmation-decision",
        str(args.confirmation_decision),
        "--max-confirmation-resolves",
        str(args.max_confirmation_resolves),
        "--sleep-ms",
        str(args.sleep_ms),
    ]

    if args.fail_fast:
        pytest_args.append("-x")
    if args.output is not None:
        pytest_args.extend(["--output", str(args.output)])
    if args.scenario is not None:
        pytest_args.extend(["--scenario", str(args.scenario)])
    if args.provider is not None:
        pytest_args.extend(["--provider", str(args.provider)])
    if args.model is not None:
        pytest_args.extend(["--model", str(args.model)])
    if args.planner_only:
        pytest_args.append("--planner-only")
    if args.max_cases is not None:
        pytest_args.extend(["--max-cases", str(args.max_cases)])
    for case_id in args.case_id:
        pytest_args.extend(["--case-id", str(case_id)])
    if args.csv_out is not None:
        pytest_args.extend(["--csv-out", str(args.csv_out)])
    if args.xlsx_out is not None:
        pytest_args.extend(["--xlsx-out", str(args.xlsx_out)])
    if args.human_readable:
        pytest_args.append("--human-readable")
    if args.human_readable_out is not None:
        pytest_args.extend(["--human-readable-out", str(args.human_readable_out)])
    pytest_args.extend(["--capture-rag-context" if args.capture_rag_context else "--no-capture-rag-context"])

    return int(pytest.main(pytest_args))


if __name__ == "__main__":
    raise SystemExit(main())
