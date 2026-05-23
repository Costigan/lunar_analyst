from __future__ import annotations

import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from backend.api.dependencies import _load_llm_config, build_service_container
from backend.contracts.assistant_models import (
    AssistantConfirmationDecision,
    AssistantConfirmationDecisionRequest,
    CreateAssistantSessionRequest,
    CreateAssistantTurnRequest,
)
from backend.contracts.models import CreateScenarioRequest
from backend.evals.assistant import benchmark_core as core

DEFAULT_EVAL_SCENARIO_SELECTOR = "test_scenario"
CASE_SCENARIO_SELECTOR_OVERRIDES: dict[str, str] = {}
SCENARIO_SELECTOR_ALIASES: dict[str, str] = {
    "scn_mons-malapert": DEFAULT_EVAL_SCENARIO_SELECTOR,
    "mons-malapert": DEFAULT_EVAL_SCENARIO_SELECTOR,
}


@dataclass
class ScenarioClone:
    case_id: str
    scenario_id: str
    source_scenario_id: str
    root: Path
    source_root: Path


@dataclass
class EvalRuntime:
    suite_name: str
    output_path: Path
    services: Any
    effective_provider_id: str | None
    effective_model_id: str | None
    confirmation_decision: AssistantConfirmationDecision | None
    max_confirmation_resolves: int
    planner_only: bool
    capture_rag_context: bool
    sleep_ms: int
    forced_scenario_selector: str | None
    predictions: list[dict[str, Any]] = field(default_factory=list)
    created_roots: list[Path] = field(default_factory=list)
    pytest_failures: set[str] = field(default_factory=set)


def _resolve_eval_provider_defaults() -> tuple[str | None, str | None]:
    llm_cfg = _load_llm_config()
    eval_cfg = llm_cfg.get("evals", {})
    provider = None
    model = None
    if isinstance(eval_cfg, dict):
        provider = str(eval_cfg.get("default_provider", "")).strip() or None
        model = str(eval_cfg.get("default_model", "")).strip() or None
    if provider is None:
        provider = str(llm_cfg.get("eval_default_provider", "")).strip() or None
    if model is None:
        model = str(llm_cfg.get("eval_default_model", "")).strip() or None
    return provider, model


def _parse_confirmation_decision(value: str) -> AssistantConfirmationDecision | None:
    text = str(value).strip().lower()
    if not text or text == "none":
        return None
    mapping = {
        "allow_once": AssistantConfirmationDecision.ALLOW_ONCE,
        "always_allow_action_type": AssistantConfirmationDecision.ALWAYS_ALLOW_ACTION_TYPE,
        "deny_once": AssistantConfirmationDecision.DENY_ONCE,
    }
    return mapping.get(text)


def _case_id_from_name(name: str) -> str:
    base = name.split("[", 1)[0]
    if base.startswith("test_"):
        return base[5:]
    return base


def _case_id_from_item(item: pytest.Item) -> str:
    return _case_id_from_name(item.name)


def _case_id_from_nodeid(nodeid: str) -> str:
    tail = nodeid.split("::")[-1]
    return _case_id_from_name(tail)


def _normalize_slug(text: str, *, limit: int) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")
    return slug[:limit] if slug else "case"


def _make_eval_root_name(case_id: str) -> str:
    slug = _normalize_slug(case_id, limit=14)
    token = uuid.uuid4().hex[:10]
    return f"ev_{slug}_{token}"


def _normalize_eval_scenario_selector(selector: str) -> str:
    normalized = str(selector or "").strip()
    if not normalized:
        return DEFAULT_EVAL_SCENARIO_SELECTOR
    return SCENARIO_SELECTOR_ALIASES.get(normalized, normalized)


def _try_link_dir(source_dir: Path, target_dir: Path) -> bool:
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(str(source_dir), str(target_dir), target_is_directory=True)
        return True
    except OSError:
        return False


def _copy_or_link_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.unlink()

    prefers_copy = source.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".wal", ".shm"}
    if not prefers_copy:
        try:
            os.link(str(source), str(target))
            return
        except OSError:
            pass

    for attempt in range(3):
        try:
            shutil.copy2(source, target)
            return
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.2)


def _populate_scenario_clone(source_root: Path, target_root: Path) -> None:
    linked_dirs: list[Path] = []
    for rel in (Path("lighting") / "horizons", Path("horizons")):
        src_dir = (source_root / rel).resolve()
        dst_dir = (target_root / rel).resolve()
        if src_dir.exists() and src_dir.is_dir() and _try_link_dir(src_dir, dst_dir):
            linked_dirs.append(rel)

    def _is_under_linked(rel_path: Path) -> bool:
        rel_norm = rel_path.as_posix().strip("/")
        for linked in linked_dirs:
            linked_norm = linked.as_posix().strip("/")
            if rel_norm == linked_norm or rel_norm.startswith(f"{linked_norm}/"):
                return True
        return False

    for dirpath, dirnames, filenames in os.walk(source_root):
        src_dir = Path(dirpath)
        rel_dir = src_dir.relative_to(source_root)
        if _is_under_linked(rel_dir):
            dirnames[:] = []
            continue

        dst_dir = (target_root / rel_dir).resolve()
        dst_dir.mkdir(parents=True, exist_ok=True)

        for filename in filenames:
            src_file = (src_dir / filename).resolve()
            rel_file = src_file.relative_to(source_root)
            if _is_under_linked(rel_file.parent):
                continue
            dst_file = (target_root / rel_file).resolve()
            _copy_or_link_file(src_file, dst_file)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("assistant-evals")
    group.addoption("--suite", action="store", default="functional", choices=["functional", "domain", "all"])
    group.addoption("--output", action="store", default=None)
    group.addoption("--scenario", action="store", default=None)
    group.addoption("--provider", action="store", default=None)
    group.addoption("--model", action="store", default=None)
    group.addoption("--planner-only", action="store_true", default=False)
    group.addoption(
        "--confirmation-decision",
        action="store",
        default="allow_once",
        choices=["allow_once", "always_allow_action_type", "deny_once", "none"],
    )
    group.addoption("--max-confirmation-resolves", action="store", type=int, default=8)
    group.addoption("--csv-out", action="store", default=None)
    group.addoption("--xlsx-out", action="store", default=None)
    group.addoption("--human-readable", action="store_true", default=False)
    group.addoption("--human-readable-out", action="store", default=None)
    group.addoption("--sleep-ms", action="store", type=int, default=0)
    group.addoption("--case-id", action="append", default=[])
    group.addoption("--max-cases", action="store", type=int, default=None)
    group.addoption("--capture-rag-context", action="store_true", dest="capture_rag_context", default=True)
    group.addoption("--no-capture-rag-context", action="store_false", dest="capture_rag_context")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    suite = str(config.getoption("suite") or "functional").strip().lower()
    selected_ids = {str(item).strip() for item in list(config.getoption("case_id") or []) if str(item).strip()}
    max_cases = config.getoption("max_cases")

    filtered: list[pytest.Item] = []
    deselected: list[pytest.Item] = []

    for item in items:
        case_id = _case_id_from_item(item)
        is_func = case_id.startswith("func_")
        is_dom = case_id.startswith("dom_")

        suite_ok = suite == "all" or (suite == "functional" and is_func) or (suite == "domain" and is_dom)
        id_ok = not selected_ids or case_id in selected_ids
        if suite_ok and id_ok:
            filtered.append(item)
        else:
            deselected.append(item)

    if max_cases is not None and int(max_cases) >= 0 and len(filtered) > int(max_cases):
        deselected.extend(filtered[int(max_cases) :])
        filtered = filtered[: int(max_cases)]

    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = filtered


@pytest.fixture(scope="session")
def eval_runtime(pytestconfig: pytest.Config) -> EvalRuntime:
    suite_name = str(pytestconfig.getoption("suite") or "functional").strip().lower()
    output_opt = pytestconfig.getoption("output")
    output_path = Path(output_opt) if output_opt else Path(f"backend/evals/assistant/predictions_{suite_name}.jsonl")

    eval_default_provider, eval_default_model = _resolve_eval_provider_defaults()
    effective_provider_id = pytestconfig.getoption("provider") or eval_default_provider
    effective_model_id = pytestconfig.getoption("model") or eval_default_model
    confirmation_decision = _parse_confirmation_decision(str(pytestconfig.getoption("confirmation_decision")))

    capture_rag_context = bool(pytestconfig.getoption("capture_rag_context"))
    previous_capture_value = os.environ.get("ASSISTANT_EVAL_CAPTURE_RAG_CONTEXT")
    os.environ["ASSISTANT_EVAL_CAPTURE_RAG_CONTEXT"] = "1" if capture_rag_context else "0"

    services = build_service_container()
    runtime = EvalRuntime(
        suite_name=suite_name,
        output_path=output_path,
        services=services,
        effective_provider_id=effective_provider_id,
        effective_model_id=effective_model_id,
        confirmation_decision=confirmation_decision,
        max_confirmation_resolves=int(pytestconfig.getoption("max_confirmation_resolves")),
        planner_only=bool(pytestconfig.getoption("planner_only")),
        capture_rag_context=capture_rag_context,
        sleep_ms=int(pytestconfig.getoption("sleep_ms")),
        forced_scenario_selector=(
            _normalize_eval_scenario_selector(str(pytestconfig.getoption("scenario") or "").strip())
            if str(pytestconfig.getoption("scenario") or "").strip()
            else None
        ),
    )

    pytestconfig._assistant_eval_runtime = runtime  # type: ignore[attr-defined]

    try:
        providers = services.assistant_service._providers  # noqa: SLF001
        non_command = providers.select_for_prompt(
            provider_id=effective_provider_id,
            model_id=effective_model_id,
            is_command_turn=False,
        )
        command = providers.select_for_prompt(
            provider_id=effective_provider_id,
            model_id=effective_model_id,
            is_command_turn=True,
        )
        print(
            "Resolved provider/model:"
            f" non-command={non_command.provider_id}/{non_command.model_id}"
            f" command={command.provider_id}/{command.model_id}"
        )
        print(
            "Benchmark model source:"
            f" provider={'cli' if pytestconfig.getoption('provider') else ('backend.llm.evals.default_provider' if eval_default_provider else 'app default')}"
            f", model={'cli' if pytestconfig.getoption('model') else ('backend.llm.evals.default_model' if eval_default_model else 'app default')}"
        )
        print(f"Benchmark confirmation mode: {pytestconfig.getoption('confirmation_decision')}")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Resolved provider/model: unavailable ({exc})")

    if runtime.forced_scenario_selector:
        forced_id = core._resolve_scenario_selector(services=services, selector=runtime.forced_scenario_selector)
        print(
            f"Scenario context: forcing --scenario={runtime.forced_scenario_selector} "
            f"(resolved scenario_id={forced_id}) for case isolation clones"
        )
    else:
        print(
            "Scenario context: per-case scenario selectors (with isolated clones), "
            f"default={DEFAULT_EVAL_SCENARIO_SELECTOR}"
        )
    print(f"Eval RAG context capture: {'enabled' if runtime.capture_rag_context else 'disabled'}")

    yield runtime

    for root in reversed(runtime.created_roots):
        try:
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
        except Exception:
            pass

    for fn in (
        lambda: services.job_service.shutdown(),
        lambda: services.notebook_job_service.terminate_all_running(reason="assistant eval shutdown"),
        lambda: services.assistant_service.shutdown(),
        lambda: services.marimo_service.stop_if_running(),
    ):
        try:
            fn()
        except Exception:
            pass
    if previous_capture_value is None:
        os.environ.pop("ASSISTANT_EVAL_CAPTURE_RAG_CONTEXT", None)
    else:
        os.environ["ASSISTANT_EVAL_CAPTURE_RAG_CONTEXT"] = previous_capture_value


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]) -> Any:
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed:
        return
    runtime: EvalRuntime | None = getattr(item.config, "_assistant_eval_runtime", None)
    if runtime is None:
        return
    runtime.pytest_failures.add(_case_id_from_nodeid(item.nodeid))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    runtime: EvalRuntime | None = getattr(session.config, "_assistant_eval_runtime", None)
    if runtime is None:
        return

    core._write_predictions(runtime.output_path, runtime.predictions)
    print(f"\nWrote {len(runtime.predictions)} predictions to {runtime.output_path}")

    csv_out = session.config.getoption("csv_out")
    if csv_out:
        core._write_csv(Path(str(csv_out)), runtime.predictions)
        print(f"Wrote CSV report to {csv_out}")

    xlsx_out = session.config.getoption("xlsx_out")
    if xlsx_out:
        core._write_xlsx(Path(str(xlsx_out)), runtime.predictions)
        print(f"Wrote XLSX report to {xlsx_out}")

    if bool(session.config.getoption("human_readable")):
        print("\nHuman-readable summary:")
        for row in runtime.predictions:
            print(f"- {core._human_summary_line(row)}")

    human_out = session.config.getoption("human_readable_out")
    if human_out:
        core._write_human_report(Path(str(human_out)), runtime.predictions)
        print(f"Wrote human-readable report to {human_out}")

    hard_errors = [row for row in runtime.predictions if str(row.get("error", "") or "").strip()]
    non_success = [
        row
        for row in runtime.predictions
        if str(row.get("overall_success", False)).strip().lower() in {"false", "0", "no"}
    ]

    if hard_errors:
        print("\nFailed cases (hard errors):")
        for row in hard_errors:
            print(f"- {row.get('id', '')}: {row.get('error', '')}")

    non_success_without_hard_error = [row for row in non_success if not str(row.get("error", "") or "").strip()]
    if non_success_without_hard_error:
        print("\nFailed cases (non-success outcomes without hard error):")
        for row in non_success_without_hard_error:
            print(
                f"- {row.get('id', '')}:"
                f" mode={row.get('mode', '')}"
                f", primary_tool={row.get('primary_tool', '') or '-'}"
                f", turn_status={row.get('turn_status', '') or '-'}"
            )

    warning_rows = [row for row in runtime.predictions if isinstance(row.get("warnings"), list) and row.get("warnings")]
    if warning_rows:
        print("\nCase warnings (non-fatal):")
        for row in warning_rows:
            warnings = row.get("warnings", [])
            count = len(warnings) if isinstance(warnings, list) else 0
            print(f"- {row.get('id', '')}: warning_count={count}")

    if runtime.pytest_failures:
        print("\nFailed cases (pytest assertion failures):")
        for case_id in sorted(runtime.pytest_failures):
            print(f"- {case_id}")


@pytest.fixture
def isolated_scenario(eval_runtime: EvalRuntime, request: pytest.FixtureRequest) -> Callable[[str, str], ScenarioClone]:
    def _create(case_id: str, default_selector: str) -> ScenarioClone:
        selector = (
            eval_runtime.forced_scenario_selector
            or CASE_SCENARIO_SELECTOR_OVERRIDES.get(case_id)
            or _normalize_eval_scenario_selector(default_selector)
            or DEFAULT_EVAL_SCENARIO_SELECTOR
        )
        source_id = core._resolve_scenario_selector(services=eval_runtime.services, selector=selector)
        source_scenario = eval_runtime.services.scenario_service.get_scenario(source_id)
        source_root = Path(str(source_scenario.directory)).resolve()

        for _ in range(4):
            scenario_root = _make_eval_root_name(case_id)
            try:
                clone = eval_runtime.services.scenario_service.create_scenario(
                    CreateScenarioRequest(scenario_root=scenario_root, name=f"Eval {case_id}", owner="assistant-evals")
                )
            except Exception:
                continue
            clone_root = Path(str(clone.directory)).resolve()
            if clone_root.exists():
                _populate_scenario_clone(source_root, clone_root)
                eval_runtime.services.scenario_service.reconcile_scenario_filesystem(clone.scenario_id, force=True)
                eval_runtime.created_roots.append(clone_root)

                def _cleanup(path: Path = clone_root) -> None:
                    try:
                        if path.exists():
                            shutil.rmtree(path, ignore_errors=True)
                    except Exception:
                        pass

                request.addfinalizer(_cleanup)
                return ScenarioClone(
                    case_id=case_id,
                    scenario_id=clone.scenario_id,
                    source_scenario_id=source_id,
                    root=clone_root,
                    source_root=source_root,
                )

        raise RuntimeError(f"Unable to allocate isolated scenario clone for case: {case_id}")

    return _create


@pytest.fixture
def assistant_session(eval_runtime: EvalRuntime, request: pytest.FixtureRequest) -> str:
    case_id = _case_id_from_item(request.node)
    session = eval_runtime.services.assistant_service.create_session(
        CreateAssistantSessionRequest(title=f"assistant-eval-{case_id}")
    )
    return str(session.session_id)


@pytest.fixture
def run_turn(
    eval_runtime: EvalRuntime,
    request: pytest.FixtureRequest,
    assistant_session: str,
) -> Callable[..., dict[str, Any]]:
    case_id = _case_id_from_item(request.node)
    turn_index = 0

    def _run(*, prompt: str, scenario_id: str, turn_label: str | None = None) -> dict[str, Any]:
        nonlocal turn_index
        turn_index += 1
        prediction_id = (
            turn_label.strip()
            if isinstance(turn_label, str) and turn_label.strip()
            else (case_id if turn_index == 1 else f"{case_id}.turn{turn_index}")
        )
        started = time.perf_counter()
        case_payload = {
            "id": prediction_id,
            "prompt": prompt,
            "scenario_id_used": scenario_id,
        }
        try:
            if eval_runtime.planner_only:
                prediction = core._build_prediction_from_planner_only(
                    case_payload,
                    assistant_service=eval_runtime.services.assistant_service,
                    scenario_id=scenario_id,
                )
            else:
                request = CreateAssistantTurnRequest(
                    prompt=prompt,
                    scenario_id=scenario_id,
                    provider_id=eval_runtime.effective_provider_id,
                    model_id=eval_runtime.effective_model_id,
                )
                response = eval_runtime.services.assistant_service.create_turn(assistant_session, request)
                if eval_runtime.confirmation_decision is not None:
                    resolve_count = 0
                    while (
                        response.confirmation is not None
                        and str(getattr(response.confirmation, "status", "")).strip().lower() == "pending"
                        and resolve_count < max(1, int(eval_runtime.max_confirmation_resolves))
                    ):
                        decision_response = eval_runtime.services.assistant_service.resolve_confirmation(
                            assistant_session,
                            response.confirmation.confirmation_id,
                            AssistantConfirmationDecisionRequest(decision=eval_runtime.confirmation_decision),
                        )
                        resolve_count += 1
                        response = SimpleNamespace(
                            turn=decision_response.turn,
                            assistant_message=decision_response.assistant_message,
                            tool_calls=decision_response.tool_calls,
                            confirmation=None,
                        )
                prediction = core._build_prediction_from_live_response(case_payload, response)

            prediction["duration_ms"] = int((time.perf_counter() - started) * 1000)
            eval_runtime.predictions.append(prediction)
            if eval_runtime.sleep_ms > 0:
                time.sleep(max(0, eval_runtime.sleep_ms) / 1000.0)
            return prediction
        except Exception as exc:
            failure = {
                "id": prediction_id,
                "prompt": prompt,
                "scenario_id_used": scenario_id,
                "mode": "respond",
                "primary_tool": None,
                "tool_calls": [],
                "repair_applied": False,
                "first_try_success": False,
                "overall_success": False,
                "answer_generated": False,
                "unsafe_blocked": False,
                "quality_gate_applied": False,
                "quality_pass": True,
                "quality_flags": [],
                "quality_issue_count": 0,
                "turn_status": "",
                "response_text": "",
                "source_references": [],
                "source_reference_count": 0,
                "rag_context_text": "",
                "rag_context_chars": 0,
                "rag_context_capture_count": 0,
                "rag_context_captures": [],
                "requested_provider_id": "",
                "requested_model_id": "",
                "final_provider_id": "",
                "final_model_id": "",
                "fallback_used": False,
                "attempted_models": [],
                "attempted_model_count": 0,
                "fallback_chain": [],
                "fallback_chain_count": 0,
                "prefilter_eligible": None,
                "prefilter_failure_stage": None,
                "prefilter_error_code": None,
                "error": str(exc),
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
            eval_runtime.predictions.append(failure)
            print(f"  error: {exc}")
            raise

    return _run
