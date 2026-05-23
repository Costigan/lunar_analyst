from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.contracts.assistant_models import AssistantBugReport, AssistantBugReportProgramState
from backend.services.assistant.bug_report_service import list_bug_report_summaries
from backend.tools.analyze_assistant_bug_report import _resolve_bug_report_reference
from backend.tools.list_assistant_bugs import format_bug_report_listing


def _write_bug_report(report_dir: Path, *, bug_report_id: str, created_at_utc: str, report_text: str) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = report_dir / "bug-report.json"
    bug_report = AssistantBugReport(
        bug_report_id=bug_report_id,
        created_at_utc=created_at_utc,
        report_text=report_text,
        assistant_context={},
        program_state=AssistantBugReportProgramState(),
        log_excerpt=[],
        redactions_applied=True,
    )
    bundle_path.write_text(json.dumps(bug_report.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8")


def test_list_bug_report_summaries_orders_reports_by_created_at_desc(tmp_path: Path) -> None:
    reports_root = tmp_path / "debugging" / "assistant-bug-reports"
    _write_bug_report(
        reports_root / "zzz-old",
        bug_report_id="br_old",
        created_at_utc="2026-04-14T09-00-00",
        report_text="Older bug report",
    )
    _write_bug_report(
        reports_root / "aaa-new",
        bug_report_id="br_new",
        created_at_utc="2026-04-15T09-00-00",
        report_text="Newer bug report",
    )

    summaries = list_bug_report_summaries(tmp_path)

    assert [summary.bug_report_id for summary in summaries] == ["br_new", "br_old"]


def test_resolve_bug_report_reference_supports_latest_sequence_and_bug_id(tmp_path: Path) -> None:
    reports_root = tmp_path / "debugging" / "assistant-bug-reports"
    _write_bug_report(
        reports_root / "first",
        bug_report_id="br_first",
        created_at_utc="2026-04-14T09-00-00",
        report_text="First bug report",
    )
    _write_bug_report(
        reports_root / "second",
        bug_report_id="br_second",
        created_at_utc="2026-04-15T09-00-00",
        report_text="Second bug report",
    )

    report_id, bundle_path = _resolve_bug_report_reference(tmp_path, None)
    assert report_id == "br_second"
    assert bundle_path == reports_root / "second" / "bug-report.json"

    report_id, bundle_path = _resolve_bug_report_reference(tmp_path, "latest")
    assert report_id == "br_second"
    assert bundle_path == reports_root / "second" / "bug-report.json"

    report_id, bundle_path = _resolve_bug_report_reference(tmp_path, "1")
    assert report_id == "br_second"
    assert bundle_path == reports_root / "second" / "bug-report.json"

    report_id, bundle_path = _resolve_bug_report_reference(tmp_path, "2")
    assert report_id == "br_first"
    assert bundle_path == reports_root / "first" / "bug-report.json"

    report_id, bundle_path = _resolve_bug_report_reference(tmp_path, "br_first")
    assert report_id == "br_first"
    assert bundle_path == reports_root / "br_first" / "bug-report.json"


def test_resolve_bug_report_reference_rejects_out_of_range_sequence(tmp_path: Path) -> None:
    reports_root = tmp_path / "debugging" / "assistant-bug-reports"
    _write_bug_report(
        reports_root / "only",
        bug_report_id="br_only",
        created_at_utc="2026-04-15T09-00-00",
        report_text="Only bug report",
    )

    with pytest.raises(ValueError, match="out of range"):
        _resolve_bug_report_reference(tmp_path, "2")


def test_format_bug_report_listing_uses_first_line_and_truncates(tmp_path: Path) -> None:
    reports_root = tmp_path / "debugging" / "assistant-bug-reports"
    _write_bug_report(
        reports_root / "only",
        bug_report_id="br_only",
        created_at_utc="2026-04-15T09-00-00",
        report_text="This first line is intentionally long enough to require truncation in the listing output.\nMore detail here.",
    )

    lines = format_bug_report_listing(tmp_path)

    assert len(lines) == 1
    assert lines[0].startswith("1. br_only  This first line is intentionally long enough to require ")
    assert lines[0].endswith("...")
    assert "\n" not in lines[0]
