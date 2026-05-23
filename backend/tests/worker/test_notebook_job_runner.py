from __future__ import annotations

import json
import os
from pathlib import Path

from backend.notebook.job_runner import run_from_context
from backend.notebook.runtime import CONTEXT_PATH_ENV, get_context


def _write_context(
    *,
    context_path: Path,
    notebook_path: Path,
    scenario_root: Path,
) -> Path:
    result_path = context_path.parent / "result.json"
    progress_path = context_path.parent / "progress.jsonl"
    cancel_path = context_path.parent / "cancel.flag"
    payload = {
        "scenario_id": "scn_test",
        "job_id": "nbr_test",
        "scenario_root_dir": str(scenario_root),
        "notebook_path": str(notebook_path),
        "params": {},
        "result_path": str(result_path),
        "progress_path": str(progress_path),
        "cancel_path": str(cancel_path),
    }
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return result_path


def test_run_from_context_executes_script_style_main_guard(tmp_path: Path) -> None:
    scenario_root = (tmp_path / "scenario").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "script_main_only.py"
    script.write_text(
        "\n".join(
            [
                "from backend.notebook.runtime import get_context, register_output",
                "",
                "if __name__ == '__main__':",
                "    ctx = get_context()",
                "    out = ctx.scenario_root_dir / 'outputs' / 'main_only_output.tif'",
                "    out.parent.mkdir(parents=True, exist_ok=True)",
                "    out.write_bytes(b'MAIN-ONLY')",
                "    register_output(",
                "        relative_path='outputs/main_only_output.tif',",
                "        kind='analysis',",
                "        subkind='main_only',",
                "    )",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    context_path = tmp_path / "run_main_only" / "context.json"
    result_path = _write_context(
        context_path=context_path,
        notebook_path=script.resolve(),
        scenario_root=scenario_root,
    )

    code = run_from_context(context_path)

    assert code == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["result"] == {}
    assert len(result["outputs"]) == 1
    assert result["outputs"][0]["relative_path"] == "outputs/main_only_output.tif"
    assert (scenario_root / "outputs" / "main_only_output.tif").exists()


def test_run_from_context_executes_run_function_when_present(tmp_path: Path) -> None:
    scenario_root = (tmp_path / "scenario").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "script_run_func.py"
    script.write_text(
        "\n".join(
            [
                "def run(context):",
                "    out = context.scenario_root_dir / 'outputs' / 'run_func_output.tif'",
                "    out.parent.mkdir(parents=True, exist_ok=True)",
                "    out.write_bytes(b'RUN-FUNC')",
                "    context.register_output(",
                "        relative_path='outputs/run_func_output.tif',",
                "        kind='analysis',",
                "        subkind='run_func',",
                "    )",
                "    return {'status': 'ok'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    context_path = tmp_path / "run_run_func" / "context.json"
    result_path = _write_context(
        context_path=context_path,
        notebook_path=script.resolve(),
        scenario_root=scenario_root,
    )

    code = run_from_context(context_path)

    assert code == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["result"] == {"status": "ok"}
    assert len(result["outputs"]) == 1
    assert result["outputs"][0]["relative_path"] == "outputs/run_func_output.tif"
    assert (scenario_root / "outputs" / "run_func_output.tif").exists()


def test_run_from_context_runtime_progress_uses_shared_jsonl_writer(tmp_path: Path) -> None:
    scenario_root = (tmp_path / "scenario").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "script_runtime_progress.py"
    script.write_text(
        "\n".join(
            [
                "from backend.jobs.runtime_context import emit_job_progress",
                "",
                "def run(context):",
                "    emit_job_progress({'message': 'status without percent', 'event_kind': 'native_status'})",
                "    emit_job_progress({'percent': 'bad', 'message': 'bad percent is skipped'})",
                "    context.report_progress(percent=25, message='normal progress', stage='work')",
                "    return {'status': 'ok'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    context_path = tmp_path / "run_runtime_progress" / "context.json"
    result_path = _write_context(
        context_path=context_path,
        notebook_path=script.resolve(),
        scenario_root=scenario_root,
    )
    progress_path = context_path.parent / "progress.jsonl"

    code = run_from_context(context_path)

    assert code == 0
    assert json.loads(result_path.read_text(encoding="utf-8"))["ok"] is True
    progress = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert progress == [
        {"event_kind": "native_status", "message": "status without percent"},
        {"message": "normal progress", "percent": 25.0, "stage": "work"},
    ]


def test_run_from_context_rejects_moonlib_import_in_osgeo_mode(tmp_path: Path) -> None:
    scenario_root = (tmp_path / "scenario").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "script_imports_moonlib.py"
    script.write_text(
        "\n".join(
            [
                "import moonlib",
                "",
                "def run(context):",
                "    return {'status': 'ok'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    context_path = tmp_path / "run_import_conflict" / "context.json"
    result_path = _write_context(
        context_path=context_path,
        notebook_path=script.resolve(),
        scenario_root=scenario_root,
    )
    payload = json.loads(context_path.read_text(encoding="utf-8"))
    payload["runtime_mode"] = "osgeo"
    context_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    code = run_from_context(context_path)

    assert code == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert "script_runtime_mode_conflict" in str(result.get("error", ""))


def test_run_from_context_treats_system_exit_zero_as_success(tmp_path: Path) -> None:
    scenario_root = (tmp_path / "scenario").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "script_exit_zero.py"
    script.write_text(
        "\n".join(
            [
                "if __name__ == '__main__':",
                "    raise SystemExit(0)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    context_path = tmp_path / "run_exit_zero" / "context.json"
    result_path = _write_context(
        context_path=context_path,
        notebook_path=script.resolve(),
        scenario_root=scenario_root,
    )

    code = run_from_context(context_path)

    assert code == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["result"] == {}


def test_run_from_context_treats_system_exit_nonzero_as_failure(tmp_path: Path) -> None:
    scenario_root = (tmp_path / "scenario").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "script_exit_nonzero.py"
    script.write_text(
        "\n".join(
            [
                "if __name__ == '__main__':",
                "    raise SystemExit('script failed')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    context_path = tmp_path / "run_exit_nonzero" / "context.json"
    result_path = _write_context(
        context_path=context_path,
        notebook_path=script.resolve(),
        scenario_root=scenario_root,
    )

    code = run_from_context(context_path)

    assert code == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert "script failed" in str(result.get("error", ""))


def test_run_from_context_clears_runtime_context_after_execution(tmp_path: Path) -> None:
    scenario_root = (tmp_path / "scenario_a").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "script_noop.py"
    script.write_text("def run(context):\n    return {'status': 'ok'}\n", encoding="utf-8")
    context_path = tmp_path / "run_cleanup" / "context.json"
    result_path = _write_context(
        context_path=context_path,
        notebook_path=script.resolve(),
        scenario_root=scenario_root,
    )

    code = run_from_context(context_path)

    assert code == 0
    assert json.loads(result_path.read_text(encoding="utf-8"))["ok"] is True
    assert CONTEXT_PATH_ENV not in os.environ
    try:
        get_context()
    except RuntimeError:
        pass
    else:
        raise AssertionError("Notebook runtime context leaked after run_from_context().")
