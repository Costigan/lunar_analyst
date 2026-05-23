from __future__ import annotations

import json
from pathlib import Path

from backend.jobs.worker_protocol import (
    PROTOCOL_VERSION,
    build_worker_protocol_paths,
    is_cancel_requested,
    read_progress_events_since_line,
    request_cancel,
    worker_context_payload,
    write_json_file,
    write_progress_event,
)


def test_worker_protocol_paths_and_context_payload(tmp_path: Path) -> None:
    paths = build_worker_protocol_paths(tmp_path / "run")

    payload = worker_context_payload(
        implementation_name="generate_horizons",
        job_id="job-1",
        scenario_id="scenario-1",
        args={"value": 1},
        paths=paths,
    )

    assert payload["protocol_version"] == PROTOCOL_VERSION
    assert payload["implementation_name"] == "generate_horizons"
    assert payload["job_id"] == "job-1"
    assert payload["scenario_id"] == "scenario-1"
    assert payload["args"] == {"value": 1}
    assert payload["progress_path"] == str(paths.progress_path)
    assert payload["result_path"] == str(paths.result_path)
    assert payload["cancel_path"] == str(paths.cancel_path)


def test_progress_jsonl_round_trip_and_line_cursor(tmp_path: Path) -> None:
    progress_path = tmp_path / "progress.jsonl"

    write_progress_event(
        progress_path,
        {
            "percent": "12.5",
            "message": "Preparing inputs.",
            "stage": "prepare",
            "processed": 1,
        },
    )
    write_progress_event(
        progress_path,
        {
            "message": "Native worker reported status.",
            "event_kind": "native_status",
        },
    )

    next_index, events = read_progress_events_since_line(progress_path, 0)

    assert next_index == 2
    assert events == [
        {
            "percent": 12.5,
            "message": "Preparing inputs.",
            "stage": "prepare",
            "processed": 1,
        },
        {
            "message": "Native worker reported status.",
            "event_kind": "native_status",
        },
    ]

    next_index_2, events_2 = read_progress_events_since_line(progress_path, next_index)
    assert next_index_2 == 2
    assert events_2 == []


def test_progress_reader_skips_malformed_lines(tmp_path: Path) -> None:
    progress_path = tmp_path / "progress.jsonl"
    progress_path.write_text(
        "\n".join(
            [
                json.dumps({"percent": 5, "message": "Valid"}),
                "{not-json",
                json.dumps(["not", "an", "object"]),
                json.dumps({"percent": "nan", "message": "Invalid percent"}),
                json.dumps({"percent": 10}),
                json.dumps({"message": "Still valid"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    next_index, events = read_progress_events_since_line(progress_path, 0)

    assert next_index == 6
    assert events == [
        {"percent": 5.0, "message": "Valid"},
        {"message": "Still valid"},
    ]


def test_result_json_and_cancel_flag_helpers(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    cancel_path = tmp_path / "cancel.flag"

    write_json_file(result_path, {"ok": True, "result": {"answer": 42}})
    assert json.loads(result_path.read_text(encoding="utf-8")) == {
        "ok": True,
        "result": {"answer": 42},
    }

    assert not is_cancel_requested(cancel_path)
    request_cancel(cancel_path, reason="stop")
    assert is_cancel_requested(cancel_path)
    assert cancel_path.read_text(encoding="utf-8") == "stop"
