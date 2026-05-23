from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Any

import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from backend.evals.assistant import leaderboard as lb

_GEOMETRY_RE = re.compile(r"^(?P<w>\d+)x(?P<h>\d+)(?P<offsets>[+-]\d+[+-]\d+)?$")
_MIN_WINDOW_WIDTH = 900
_MIN_WINDOW_HEIGHT = 700


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _ui_state_path() -> Path:
    return Path(__file__).resolve().with_name("leaderboard_ui_state.json")


def _parse_bool(text: str) -> bool:
    value = str(text or "").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def _case_files_for_suite(suite: str) -> list[Path]:
    root = _repo_root()
    functional = root / "backend/tests/evals/test_assistant_functional.py"
    domain = root / "backend/tests/evals/test_assistant_domain.py"
    normalized = str(suite or "").strip().lower()
    if normalized == "functional":
        return [functional]
    if normalized == "domain":
        return [domain]
    return [functional, domain]


def _discover_case_ids(suite: str) -> list[str]:
    pattern = re.compile(r"^\s*def\s+test_([A-Za-z0-9_]+)\s*\(")
    case_ids: list[str] = []
    for path in _case_files_for_suite(suite):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = pattern.match(line)
            if match:
                case_ids.append(match.group(1))
    return sorted(set(case_ids))


def _discover_targets(
    *,
    catalog_source: str,
    catalog_url: str,
    catalog_timeout: float,
    allow_cli_providers: bool,
    require_tool_capability: bool,
    suite: str,
) -> tuple[list[lb.ModelTarget], str, int, int]:
    include_providers: set[str] = set()
    exclude_providers: set[str] = set()
    if not allow_cli_providers:
        exclude_providers.update(lb._DEFAULT_EXCLUDED_CLI_PROVIDERS)

    discovery = str(catalog_source or "auto").strip().lower()
    targets: list[lb.ModelTarget] = []
    if discovery in {"auto", "api"}:
        try:
            targets = lb.discover_targets_from_api(
                catalog_url=catalog_url,
                timeout_seconds=float(catalog_timeout),
                include_providers=include_providers,
                exclude_providers=exclude_providers,
            )
            targets = lb._dedupe_targets(targets)
            discovery = "api"
        except Exception:
            if discovery == "api":
                raise
    if not targets:
        targets = lb.discover_targets_from_config(
            include_providers=include_providers,
            exclude_providers=exclude_providers,
        )
        targets = lb._dedupe_targets(targets)
        discovery = "config"

    # Match leaderboard.py behavior: require tool capability by default for functional/all.
    require_tools = require_tool_capability
    if str(suite).strip().lower() in {"functional", "all"} and require_tool_capability is False:
        require_tools = False

    filtered_out_no_tools = 0
    unknown_tool_capability = 0
    if require_tools:
        filtered_targets: list[lb.ModelTarget] = []
        ollama_probe_cache: dict[str, tuple[str, ...] | None] = {}
        for target in targets:
            capabilities = target.capabilities
            if capabilities is None and target.provider_id == "ollama":
                if target.model_id not in ollama_probe_cache:
                    ollama_probe_cache[target.model_id] = lb._probe_ollama_capabilities(
                        target.model_id,
                        timeout_seconds=3.0,
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

    return targets, discovery, filtered_out_no_tools, unknown_tool_capability


class LeaderboardUi(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Lunar Analyst Leaderboard UI")
        self.geometry("1500x950")
        self.minsize(_MIN_WINDOW_WIDTH, _MIN_WINDOW_HEIGHT)

        self._event_queue: Queue[tuple[str, Any]] = Queue()
        self._run_thread: threading.Thread | None = None
        self._targets: list[lb.ModelTarget] = []
        self._result_rows: dict[str, dict[str, Any]] = {}
        self._predictions_by_result: dict[str, list[dict[str, Any]]] = {}
        self._case_row_by_iid: dict[str, dict[str, Any]] = {}
        self._past_runs_by_label: dict[str, Path] = {}
        self._latest_run_dir: Path | None = None

        self._suite_var = tk.StringVar(value="functional")
        self._scenario_var = tk.StringVar(value="test_scenario")
        self._catalog_source_var = tk.StringVar(value="auto")
        self._catalog_url_var = tk.StringVar(value="http://127.0.0.1:8000/api/v1/assistant/providers")
        self._catalog_timeout_var = tk.StringVar(value="4.0")
        self._allow_cli_var = tk.BooleanVar(value=False)
        self._require_tools_var = tk.BooleanVar(value=True)
        self._skip_score_var = tk.BooleanVar(value=False)
        self._planner_only_var = tk.BooleanVar(value=False)
        self._confirmation_var = tk.StringVar(value="allow_once")
        self._max_confirm_var = tk.StringVar(value="8")
        self._max_cases_var = tk.StringVar(value="")
        self._python_exe_var = tk.StringVar(value=sys.executable)
        self._output_base_var = tk.StringVar(value="backend/evals/assistant/leaderboard_runs/ui")
        self._status_var = tk.StringVar(value="Idle")

        self._run_button: ttk.Button | None = None
        self._refresh_models_button: ttk.Button | None = None
        self._refresh_cases_button: ttk.Button | None = None
        self._progressbar: ttk.Progressbar | None = None
        self._refresh_past_runs_button: ttk.Button | None = None
        self._state_file = _ui_state_path()
        self._pending_selected_models: list[str] = []
        self._pending_selected_cases: list[str] = []
        self._pending_selected_past_run: str = ""
        self._suppress_past_run_select_event = False

        loaded_state = self._load_ui_state()
        self._apply_loaded_state(loaded_state)

        self._build_ui()
        self._refresh_cases()
        self._refresh_targets()
        self._refresh_past_runs()
        self._restore_listbox_selection(self._model_list, self._pending_selected_models)
        self._restore_listbox_selection(self._case_list, self._pending_selected_cases)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_events)

    def _build_ui(self) -> None:
        root = ttk.Frame(self)
        root.pack(fill=tk.BOTH, expand=True)

        controls = ttk.LabelFrame(root, text="Run Controls")
        controls.pack(fill=tk.X, padx=8, pady=6)
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(3, weight=1)

        ttk.Label(controls, text="Suite").grid(row=0, column=0, sticky=tk.W, padx=4, pady=4)
        suite_box = ttk.Combobox(
            controls,
            textvariable=self._suite_var,
            values=["functional", "domain", "all"],
            state="readonly",
            width=14,
        )
        suite_box.grid(row=0, column=1, sticky=tk.W, padx=4, pady=4)
        suite_box.bind("<<ComboboxSelected>>", lambda _e: self._on_suite_changed())

        ttk.Label(controls, text="Scenario").grid(row=0, column=2, sticky=tk.W, padx=4, pady=4)
        ttk.Entry(controls, textvariable=self._scenario_var).grid(row=0, column=3, sticky=tk.EW, padx=4, pady=4)

        ttk.Label(controls, text="Catalog Source").grid(row=1, column=0, sticky=tk.W, padx=4, pady=4)
        ttk.Combobox(
            controls,
            textvariable=self._catalog_source_var,
            values=["auto", "api", "config"],
            state="readonly",
            width=14,
        ).grid(row=1, column=1, sticky=tk.W, padx=4, pady=4)

        ttk.Label(controls, text="Catalog URL").grid(row=1, column=2, sticky=tk.W, padx=4, pady=4)
        ttk.Entry(controls, textvariable=self._catalog_url_var).grid(row=1, column=3, sticky=tk.EW, padx=4, pady=4)

        ttk.Label(controls, text="Catalog Timeout").grid(row=2, column=0, sticky=tk.W, padx=4, pady=4)
        ttk.Entry(controls, textvariable=self._catalog_timeout_var, width=14).grid(row=2, column=1, sticky=tk.W, padx=4, pady=4)

        ttk.Label(controls, text="Python").grid(row=2, column=2, sticky=tk.W, padx=4, pady=4)
        ttk.Entry(controls, textvariable=self._python_exe_var).grid(row=2, column=3, sticky=tk.EW, padx=4, pady=4)

        ttk.Label(controls, text="Output Base").grid(row=3, column=0, sticky=tk.W, padx=4, pady=4)
        ttk.Entry(controls, textvariable=self._output_base_var).grid(row=3, column=1, columnspan=3, sticky=tk.EW, padx=4, pady=4)

        flag_row = ttk.Frame(controls)
        flag_row.grid(row=4, column=0, columnspan=4, sticky=tk.W, padx=4, pady=4)
        ttk.Checkbutton(flag_row, text="Allow CLI Providers", variable=self._allow_cli_var).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(flag_row, text="Require Tool Capability", variable=self._require_tools_var).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(flag_row, text="Skip Score", variable=self._skip_score_var).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(flag_row, text="Planner Only", variable=self._planner_only_var).pack(side=tk.LEFT, padx=4)

        opts_row = ttk.Frame(controls)
        opts_row.grid(row=5, column=0, columnspan=4, sticky=tk.W, padx=4, pady=4)
        ttk.Label(opts_row, text="Confirmation").pack(side=tk.LEFT, padx=4)
        ttk.Combobox(
            opts_row,
            textvariable=self._confirmation_var,
            values=["allow_once", "always_allow_action_type", "deny_once", "none"],
            state="readonly",
            width=24,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(opts_row, text="Max Confirms").pack(side=tk.LEFT, padx=4)
        ttk.Entry(opts_row, textvariable=self._max_confirm_var, width=6).pack(side=tk.LEFT, padx=4)
        ttk.Label(opts_row, text="Max Cases").pack(side=tk.LEFT, padx=4)
        ttk.Entry(opts_row, textvariable=self._max_cases_var, width=8).pack(side=tk.LEFT, padx=4)

        button_row = ttk.Frame(controls)
        button_row.grid(row=6, column=0, columnspan=4, sticky=tk.W, padx=4, pady=6)
        self._refresh_models_button = ttk.Button(button_row, text="Refresh Models", command=self._refresh_targets)
        self._refresh_models_button.pack(side=tk.LEFT, padx=4)
        self._refresh_cases_button = ttk.Button(button_row, text="Refresh Cases", command=self._refresh_cases)
        self._refresh_cases_button.pack(side=tk.LEFT, padx=4)
        self._run_button = ttk.Button(button_row, text="Run Selected", command=self._run_selected)
        self._run_button.pack(side=tk.LEFT, padx=4)
        ttk.Label(button_row, text="Status:").pack(side=tk.LEFT, padx=(12, 2))
        ttk.Label(button_row, textvariable=self._status_var).pack(side=tk.LEFT, padx=(2, 8))
        self._progressbar = ttk.Progressbar(button_row, mode="indeterminate", length=180)
        self._progressbar.pack(side=tk.LEFT, padx=4)

        content = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        content.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        left = ttk.Frame(content)
        right = ttk.Frame(content)
        content.add(left, weight=1)
        content.add(right, weight=2)

        model_box = ttk.LabelFrame(left, text="Models")
        model_box.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._model_list = tk.Listbox(model_box, selectmode=tk.EXTENDED, exportselection=False)
        self._model_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        case_box = ttk.LabelFrame(left, text="Cases")
        case_box.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        case_btns = ttk.Frame(case_box)
        case_btns.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(case_btns, text="Select All", command=self._select_all_cases).pack(side=tk.LEFT, padx=2)
        ttk.Button(case_btns, text="Clear", command=self._clear_case_selection).pack(side=tk.LEFT, padx=2)
        self._case_list = tk.Listbox(case_box, selectmode=tk.EXTENDED, exportselection=False)
        self._case_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        past_runs_box = ttk.LabelFrame(left, text="Past Runs")
        past_runs_box.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        past_btns = ttk.Frame(past_runs_box)
        past_btns.pack(fill=tk.X, padx=4, pady=4)
        self._refresh_past_runs_button = ttk.Button(past_btns, text="Refresh", command=self._refresh_past_runs)
        self._refresh_past_runs_button.pack(side=tk.LEFT, padx=2)
        self._past_runs_list = tk.Listbox(past_runs_box, selectmode=tk.SINGLE, exportselection=False)
        self._past_runs_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._past_runs_list.bind("<<ListboxSelect>>", self._on_past_run_selected)

        right_pane = ttk.Panedwindow(right, orient=tk.VERTICAL)
        right_pane.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        results_box = ttk.LabelFrame(right_pane, text="Model Results")
        columns = ("provider", "model", "status", "cases", "success", "errors", "weighted")
        self._results_tree = ttk.Treeview(results_box, columns=columns, show="headings", height=8)
        for col, title, width in (
            ("provider", "Provider", 120),
            ("model", "Model", 250),
            ("status", "Status", 100),
            ("cases", "Cases", 70),
            ("success", "Success", 80),
            ("errors", "Errors", 80),
            ("weighted", "Weighted", 90),
        ):
            self._results_tree.heading(col, text=title)
            self._results_tree.column(col, width=width, anchor=tk.W)
        self._results_tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._results_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_result_selected())

        detail_pane = ttk.Panedwindow(right_pane, orient=tk.VERTICAL)

        case_result_box = ttk.LabelFrame(detail_pane, text="Case Results")
        detail_box = ttk.LabelFrame(detail_pane, text="Case Detail")
        detail_pane.add(case_result_box, weight=1)
        detail_pane.add(detail_box, weight=2)

        case_columns = (
            "case_id",
            "success",
            "fallback",
            "final_model",
            "quality",
            "quality_flags",
            "turn_status",
            "mode",
            "duration_ms",
            "primary_tool",
            "error",
        )
        self._result_case_tree = ttk.Treeview(case_result_box, columns=case_columns, show="headings", height=10)
        for col, title, width in (
            ("case_id", "Case", 210),
            ("success", "Success", 70),
            ("fallback", "Fallback", 70),
            ("final_model", "Final Model", 200),
            ("quality", "Quality", 70),
            ("quality_flags", "Quality Flags", 180),
            ("turn_status", "Turn Status", 100),
            ("mode", "Mode", 80),
            ("duration_ms", "Duration ms", 90),
            ("primary_tool", "Primary Tool", 180),
            ("error", "Error", 220),
        ):
            self._result_case_tree.heading(col, text=title)
            self._result_case_tree.column(col, width=width, anchor=tk.W)
        self._result_case_tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._result_case_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_case_result_selected())

        self._case_detail_text = ScrolledText(detail_box, wrap=tk.NONE, font=("Consolas", 10))
        self._case_detail_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        log_box = ttk.LabelFrame(right_pane, text="Run Log")
        self._log_text = ScrolledText(log_box, wrap=tk.NONE, font=("Consolas", 10), height=10)
        self._log_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        right_pane.add(results_box, weight=1)
        right_pane.add(detail_pane, weight=3)
        right_pane.add(log_box, weight=1)

    def _load_ui_state(self) -> dict[str, Any]:
        try:
            if not self._state_file.exists():
                return {}
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _apply_loaded_state(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        mapping = {
            "suite": self._suite_var,
            "scenario": self._scenario_var,
            "catalog_source": self._catalog_source_var,
            "catalog_url": self._catalog_url_var,
            "catalog_timeout": self._catalog_timeout_var,
            "confirmation": self._confirmation_var,
            "max_confirm": self._max_confirm_var,
            "max_cases": self._max_cases_var,
            "python_exe": self._python_exe_var,
            "output_base": self._output_base_var,
        }
        for key, var in mapping.items():
            value = state.get(key)
            if isinstance(value, str):
                var.set(value)

        self._allow_cli_var.set(bool(state.get("allow_cli_providers", self._allow_cli_var.get())))
        self._require_tools_var.set(bool(state.get("require_tool_capability", self._require_tools_var.get())))
        self._skip_score_var.set(bool(state.get("skip_score", self._skip_score_var.get())))
        self._planner_only_var.set(bool(state.get("planner_only", self._planner_only_var.get())))

        selected_models = state.get("selected_models", [])
        selected_cases = state.get("selected_cases", [])
        selected_past_run = state.get("selected_past_run", "")
        if isinstance(selected_models, list):
            self._pending_selected_models = [str(item) for item in selected_models if str(item).strip()]
        if isinstance(selected_cases, list):
            self._pending_selected_cases = [str(item) for item in selected_cases if str(item).strip()]
        if isinstance(selected_past_run, str):
            self._pending_selected_past_run = selected_past_run.strip()

        geometry = state.get("geometry")
        if isinstance(geometry, str) and geometry.strip():
            try:
                if self._is_reasonable_geometry(geometry.strip()):
                    self.geometry(geometry.strip())
            except Exception:
                pass

    @staticmethod
    def _is_reasonable_geometry(value: str) -> bool:
        match = _GEOMETRY_RE.match(str(value or "").strip())
        if not match:
            return False
        width = int(match.group("w"))
        height = int(match.group("h"))
        if width < _MIN_WINDOW_WIDTH or height < _MIN_WINDOW_HEIGHT:
            return False
        return True

    def _restore_listbox_selection(self, listbox: tk.Listbox, desired_values: list[str]) -> None:
        if not desired_values:
            return
        wanted = {str(item) for item in desired_values if str(item).strip()}
        if not wanted:
            return
        listbox.select_clear(0, tk.END)
        for idx in range(listbox.size()):
            value = str(listbox.get(idx))
            if value in wanted:
                listbox.select_set(idx)

    def _resolve_output_base_dir(self) -> Path:
        output_base = self._output_base_var.get().strip() or "backend/evals/assistant/leaderboard_runs/ui"
        base_path = Path(output_base)
        return (_repo_root() / base_path).resolve() if not base_path.is_absolute() else base_path.resolve()

    def _selected_past_run_label(self) -> str:
        if not hasattr(self, "_past_runs_list"):
            return ""
        selection = self._past_runs_list.curselection()
        if not selection:
            return ""
        return str(self._past_runs_list.get(selection[0]))

    def _refresh_past_runs(self) -> None:
        if not hasattr(self, "_past_runs_list"):
            return
        previous_label = self._selected_past_run_label() or self._pending_selected_past_run
        base_dir = self._resolve_output_base_dir()
        self._past_runs_by_label.clear()
        self._suppress_past_run_select_event = True
        self._past_runs_list.delete(0, tk.END)

        if not base_dir.exists() or not base_dir.is_dir():
            self._append_log(f"Past runs base not found: {base_dir}")
            self._pending_selected_past_run = ""
            self._suppress_past_run_select_event = False
            return

        run_dirs: list[Path] = []
        for child in base_dir.iterdir():
            if not child.is_dir():
                continue
            if (child / "leaderboard.json").exists():
                run_dirs.append(child)
        run_dirs.sort(key=lambda item: item.name, reverse=True)

        for run_dir in run_dirs:
            label = run_dir.name
            self._past_runs_by_label[label] = run_dir
            self._past_runs_list.insert(tk.END, label)

        self._suppress_past_run_select_event = False
        selected = False
        if previous_label:
            selected = self._select_past_run_label(previous_label, load=True)
        if not selected and self._past_runs_list.size() > 0 and not self._selected_past_run_label():
            self._select_past_run_label(str(self._past_runs_list.get(0)), load=True)
        self._pending_selected_past_run = ""
        self._append_log(f"Found {len(run_dirs)} past runs under {base_dir}")

    def _on_past_run_selected(self, _event: tk.Event | None = None) -> None:
        if self._suppress_past_run_select_event:
            return
        self._load_selected_past_run(show_empty_message=False)

    def _select_past_run_label(self, label: str, *, load: bool) -> bool:
        if not hasattr(self, "_past_runs_list"):
            return False
        target = str(label or "").strip()
        if not target:
            return False
        for idx in range(self._past_runs_list.size()):
            value = str(self._past_runs_list.get(idx))
            if value != target:
                continue
            self._suppress_past_run_select_event = True
            self._past_runs_list.selection_clear(0, tk.END)
            self._past_runs_list.selection_set(idx)
            self._past_runs_list.activate(idx)
            self._past_runs_list.see(idx)
            self._suppress_past_run_select_event = False
            if load:
                self._load_selected_past_run(show_empty_message=False)
            return True
        return False

    def _load_selected_past_run(self, *, show_empty_message: bool = True) -> None:
        if not hasattr(self, "_past_runs_list"):
            return
        selection = self._past_runs_list.curselection()
        if not selection:
            if show_empty_message:
                messagebox.showinfo("Past Runs", "Select a past run to load.")
            return
        label = str(self._past_runs_list.get(selection[0]))
        run_dir = self._past_runs_by_label.get(label)
        if run_dir is None:
            if show_empty_message:
                messagebox.showerror("Past Runs", f"Selected run directory not found for label: {label}")
            return
        self._latest_run_dir = run_dir
        self._append_log(f"Loading past run: {run_dir}")
        self._load_results(run_dir)
        self._save_ui_state()

    def _save_ui_state(self) -> None:
        geometry = self.geometry()
        if not self._is_reasonable_geometry(geometry):
            geometry = "1500x950"
        payload = {
            "suite": self._suite_var.get(),
            "scenario": self._scenario_var.get(),
            "catalog_source": self._catalog_source_var.get(),
            "catalog_url": self._catalog_url_var.get(),
            "catalog_timeout": self._catalog_timeout_var.get(),
            "allow_cli_providers": bool(self._allow_cli_var.get()),
            "require_tool_capability": bool(self._require_tools_var.get()),
            "skip_score": bool(self._skip_score_var.get()),
            "planner_only": bool(self._planner_only_var.get()),
            "confirmation": self._confirmation_var.get(),
            "max_confirm": self._max_confirm_var.get(),
            "max_cases": self._max_cases_var.get(),
            "python_exe": self._python_exe_var.get(),
            "output_base": self._output_base_var.get(),
            "selected_models": self._selected_models(),
            "selected_cases": self._selected_cases(),
            "selected_past_run": self._selected_past_run_label(),
            "geometry": geometry,
        }
        try:
            self._state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _on_close(self) -> None:
        if self._run_thread is not None and self._run_thread.is_alive():
            proceed = messagebox.askyesno(
                "Run In Progress",
                "A run is still in progress. Close the UI anyway?",
            )
            if not proceed:
                return
        self._save_ui_state()
        self.destroy()

    def _on_suite_changed(self) -> None:
        suite = self._suite_var.get().strip().lower()
        if suite in {"functional", "all"}:
            self._require_tools_var.set(True)
        if suite == "domain":
            self._skip_score_var.set(True)
        self._refresh_cases()
        self._refresh_targets()
        self._save_ui_state()

    def _refresh_targets(self) -> None:
        previous_selected = self._selected_models()
        try:
            timeout = float(self._catalog_timeout_var.get().strip() or "4.0")
        except Exception:
            timeout = 4.0
        try:
            targets, discovery, excluded_no_tools, unknown_kept = _discover_targets(
                catalog_source=self._catalog_source_var.get(),
                catalog_url=self._catalog_url_var.get().strip(),
                catalog_timeout=timeout,
                allow_cli_providers=bool(self._allow_cli_var.get()),
                require_tool_capability=bool(self._require_tools_var.get()),
                suite=self._suite_var.get().strip().lower(),
            )
        except Exception as exc:
            messagebox.showerror("Model Discovery Failed", str(exc))
            return

        self._targets = targets
        self._model_list.delete(0, tk.END)
        for target in targets:
            self._model_list.insert(tk.END, f"{target.provider_id}:{target.model_id}")
        desired_selection = previous_selected or self._pending_selected_models
        self._restore_listbox_selection(self._model_list, desired_selection)
        self._pending_selected_models = []
        self._append_log(
            f"Discovered {len(targets)} models via {discovery}; "
            f"excluded_no_tools={excluded_no_tools}, unknown_capability_kept={unknown_kept}"
        )

    def _refresh_cases(self) -> None:
        previous_selected = self._selected_cases()
        suite = self._suite_var.get().strip().lower()
        cases = _discover_case_ids(suite)
        self._case_list.delete(0, tk.END)
        for case_id in cases:
            self._case_list.insert(tk.END, case_id)
        desired_selection = previous_selected or self._pending_selected_cases
        self._restore_listbox_selection(self._case_list, desired_selection)
        self._pending_selected_cases = []
        self._append_log(f"Loaded {len(cases)} cases for suite={suite}")

    def _select_all_cases(self) -> None:
        self._case_list.select_set(0, tk.END)

    def _clear_case_selection(self) -> None:
        self._case_list.select_clear(0, tk.END)

    def _selected_models(self) -> list[str]:
        indices = list(self._model_list.curselection())
        if not indices:
            return []
        return [str(self._model_list.get(index)) for index in indices]

    def _selected_cases(self) -> list[str]:
        return [str(self._case_list.get(index)) for index in self._case_list.curselection()]

    def _set_running_state(self, running: bool, *, status: str) -> None:
        self._status_var.set(status)
        run_state = tk.DISABLED if running else tk.NORMAL
        for widget in (
            self._run_button,
            self._refresh_models_button,
            self._refresh_cases_button,
            self._refresh_past_runs_button,
        ):
            if widget is not None:
                widget.configure(state=run_state)
        if self._progressbar is not None:
            if running:
                self._progressbar.start(12)
            else:
                self._progressbar.stop()

    def _run_selected(self) -> None:
        if self._run_thread is not None and self._run_thread.is_alive():
            messagebox.showinfo("Run In Progress", "A run is already in progress.")
            return
        targets = self._selected_models()
        if not targets:
            messagebox.showwarning("No Models Selected", "Select one or more models to run.")
            return

        run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_base = self._output_base_var.get().strip() or "backend/evals/assistant/leaderboard_runs/ui"
        run_dir = Path(output_base) / run_stamp
        run_dir_abs = (_repo_root() / run_dir).resolve() if not Path(output_base).is_absolute() else run_dir.resolve()

        cmd = [
            self._python_exe_var.get().strip() or sys.executable,
            "-u",
            "-m",
            "backend.evals.assistant.leaderboard",
            "--suite",
            self._suite_var.get().strip().lower(),
            "--catalog-source",
            "config",
            "--output-dir",
            str(run_dir_abs),
            "--confirmation-decision",
            self._confirmation_var.get().strip(),
            "--max-confirmation-resolves",
            self._max_confirm_var.get().strip() or "8",
        ]

        scenario = self._scenario_var.get().strip()
        if scenario:
            cmd.extend(["--scenario", scenario])
        if self._allow_cli_var.get():
            cmd.append("--allow-cli-providers")
        if self._require_tools_var.get():
            cmd.append("--require-tool-capability")
        else:
            cmd.append("--no-require-tool-capability")
        if self._skip_score_var.get():
            cmd.append("--skip-score")
        if self._planner_only_var.get():
            cmd.append("--planner-only")

        max_cases = self._max_cases_var.get().strip()
        if max_cases:
            cmd.extend(["--max-cases", max_cases])

        for target in targets:
            cmd.extend(["--target", target])
        for case_id in self._selected_cases():
            cmd.extend(["--case-id", case_id])

        self._latest_run_dir = run_dir_abs
        self._pending_selected_past_run = run_dir_abs.name
        self._save_ui_state()
        self._set_running_state(True, status="Running...")
        self._append_log("")
        self._append_log(f"Starting run in {run_dir_abs}")
        self._append_log(" ".join(cmd))

        self._run_thread = threading.Thread(target=self._run_worker, args=(cmd, run_dir_abs), daemon=True)
        self._run_thread.start()

    def _run_worker(self, command: list[str], run_dir: Path) -> None:
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(_repo_root()),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            self._event_queue.put(("run_error", str(exc)))
            return

        assert proc.stdout is not None
        for line in proc.stdout:
            self._event_queue.put(("log", line.rstrip("\n")))
        proc.wait()
        self._event_queue.put(("run_finished", {"returncode": int(proc.returncode), "run_dir": str(run_dir)}))

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self._event_queue.get_nowait()
            except Empty:
                break
            if kind == "log":
                line = str(payload)
                self._append_log(line)
                if line.startswith("[") and "]" in line and "provider=" in line and "model=" in line:
                    self._status_var.set(f"Running {line}")
            elif kind == "run_error":
                self._set_running_state(False, status="Failed to start")
                messagebox.showerror("Run Failed", str(payload))
            elif kind == "run_finished":
                returncode = int(payload.get("returncode", 1))
                run_dir = Path(str(payload.get("run_dir", ""))).resolve()
                if returncode != 0:
                    self._append_log(f"Run finished with errors (returncode={returncode}).")
                    self._set_running_state(False, status=f"Finished with errors ({returncode})")
                else:
                    self._append_log("Run finished successfully.")
                    self._set_running_state(False, status="Finished successfully")
                self._pending_selected_past_run = run_dir.name
                self._refresh_past_runs()
                if not self._select_past_run_label(run_dir.name, load=True):
                    self._load_results(run_dir)
        self.after(100, self._poll_events)

    def _load_results(self, run_dir: Path) -> None:
        leaderboard_path = run_dir / "leaderboard.json"
        if not leaderboard_path.exists():
            self._append_log(f"Missing leaderboard.json: {leaderboard_path}")
            return
        payload = json.loads(leaderboard_path.read_text(encoding="utf-8"))
        rows = payload.get("rows", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            rows = []

        self._result_rows.clear()
        self._predictions_by_result.clear()
        self._case_row_by_iid.clear()
        self._results_tree.delete(*self._results_tree.get_children())
        self._result_case_tree.delete(*self._result_case_tree.get_children())

        for row in rows:
            if not isinstance(row, dict):
                continue
            key = f"{row.get('provider_id', '')}:{row.get('model_id', '')}"
            self._result_rows[key] = row
            self._results_tree.insert(
                "",
                tk.END,
                iid=key,
                values=(
                    row.get("provider_id", ""),
                    row.get("model_id", ""),
                    row.get("status", ""),
                    row.get("cases", ""),
                    f"{float(row.get('overall_success_rate', 0.0) or 0.0):.3f}" if row.get("cases", 0) else "0.000",
                    f"{float(row.get('hard_error_rate', 0.0) or 0.0):.3f}" if row.get("cases", 0) else "0.000",
                    f"{float(row.get('weighted_score_100', 0.0) or 0.0):.1f}" if row.get("weighted_score_100") is not None else "-",
                ),
            )
            pred_path_text = str(row.get("predictions_path", "") or "").strip()
            pred_path = Path(pred_path_text)
            if pred_path_text and pred_path.exists():
                self._predictions_by_result[key] = self._read_predictions(pred_path)
        self._append_log(f"Loaded {len(rows)} result rows from {leaderboard_path}")
        # Convenience: auto-select first result row so users immediately see details.
        children = self._results_tree.get_children()
        if children:
            first = str(children[0])
            self._results_tree.selection_set(first)
            self._results_tree.focus(first)
            self._on_result_selected()

    def _read_predictions(self, path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows

    def _on_result_selected(self) -> None:
        selected = self._results_tree.selection()
        if not selected:
            return
        key = str(selected[0])
        predictions = self._predictions_by_result.get(key, [])
        self._case_row_by_iid.clear()
        self._result_case_tree.delete(*self._result_case_tree.get_children())
        for idx, row in enumerate(predictions):
            case_id = str(row.get("id", "")).strip() or f"case_{idx + 1}"
            success = "yes" if _parse_bool(str(row.get("overall_success", False))) else "no"
            fallback = "yes" if _parse_bool(str(row.get("fallback_used", False))) else "no"
            final_provider_id = str(row.get("final_provider_id", "") or "").strip()
            final_model_id = str(row.get("final_model_id", "") or "").strip()
            final_model = f"{final_provider_id}/{final_model_id}".strip("/")
            final_model = final_model or "-"
            quality_gate_applied = _parse_bool(str(row.get("quality_gate_applied", False)))
            quality_pass = _parse_bool(str(row.get("quality_pass", False)))
            quality = ("pass" if quality_pass else "fail") if quality_gate_applied else "n/a"
            quality_flags = row.get("quality_flags", [])
            quality_flag_text = "-"
            if isinstance(quality_flags, list):
                labels = [str(item).strip() for item in quality_flags if str(item).strip()]
                if labels:
                    quality_flag_text = ", ".join(labels[:2])
                    if len(labels) > 2:
                        quality_flag_text += f" +{len(labels) - 2}"
            turn_status = str(row.get("turn_status", "")).strip() or "-"
            mode = str(row.get("mode", "")).strip() or "-"
            duration_raw = row.get("duration_ms")
            duration_text = str(duration_raw) if isinstance(duration_raw, int) else "-"
            primary_tool = str(row.get("primary_tool", "")).strip() or "-"
            error = str(row.get("error", "")).strip()
            error_short = (error[:120] + "...") if len(error) > 123 else (error or "-")
            iid = f"case_{idx}"
            self._case_row_by_iid[iid] = row
            self._result_case_tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    case_id,
                    success,
                    fallback,
                    final_model,
                    quality,
                    quality_flag_text,
                    turn_status,
                    mode,
                    duration_text,
                    primary_tool,
                    error_short,
                ),
            )
        if predictions:
            # Convenience: auto-select first case and render full details.
            children = self._result_case_tree.get_children()
            first_iid = str(children[0]) if children else ""
            if first_iid:
                self._result_case_tree.selection_set(first_iid)
                self._result_case_tree.focus(first_iid)
                first_row = self._case_row_by_iid.get(first_iid, predictions[0])
            else:
                first_row = predictions[0]
            self._case_detail_text.delete("1.0", tk.END)
            self._case_detail_text.insert("1.0", self._format_case_detail(first_row))
        else:
            self._case_detail_text.delete("1.0", tk.END)
            self._case_detail_text.insert(
                "1.0",
                (
                    f"Selected result: {key}\n"
                    "No case prediction rows were loaded.\n"
                    "Check run_benchmark.log.json for execution/setup errors.\n"
                ),
            )

    def _on_case_result_selected(self) -> None:
        selected_result = self._results_tree.selection()
        if not selected_result:
            return
        selection = self._result_case_tree.selection()
        if not selection:
            return
        iid = str(selection[0])
        row = self._case_row_by_iid.get(iid)
        if row is None:
            return
        self._case_detail_text.delete("1.0", tk.END)
        self._case_detail_text.insert("1.0", self._format_case_detail(row))

    def _format_case_detail(self, row: dict[str, Any]) -> str:
        lines: list[str] = []
        lines.append(f"id: {row.get('id', '')}")
        lines.append(f"scenario_id_used: {row.get('scenario_id_used', '')}")
        lines.append(f"mode: {row.get('mode', '')}")
        lines.append(f"primary_tool: {row.get('primary_tool', '')}")
        lines.append(f"turn_status: {row.get('turn_status', '')}")
        lines.append(f"first_try_success: {str(_parse_bool(str(row.get('first_try_success', False)))).lower()}")
        lines.append(f"overall_success: {str(_parse_bool(str(row.get('overall_success', False)))).lower()}")
        lines.append(f"unsafe_blocked: {str(_parse_bool(str(row.get('unsafe_blocked', False)))).lower()}")
        requested_provider_id = str(row.get("requested_provider_id", "") or "").strip()
        requested_model_id = str(row.get("requested_model_id", "") or "").strip()
        final_provider_id = str(row.get("final_provider_id", "") or "").strip()
        final_model_id = str(row.get("final_model_id", "") or "").strip()
        requested_model_label = f"{requested_provider_id}/{requested_model_id}".strip("/")
        final_model_label = f"{final_provider_id}/{final_model_id}".strip("/")
        attempted_models = row.get("attempted_models", [])
        attempted_model_label = ""
        if isinstance(attempted_models, list):
            for item in attempted_models:
                if not isinstance(item, dict):
                    continue
                attempt_provider = str(item.get("provider_id", "") or "").strip()
                attempt_model = str(item.get("model_id", "") or "").strip()
                candidate = f"{attempt_provider}/{attempt_model}".strip("/")
                if candidate:
                    attempted_model_label = candidate
                    break
        model_used_label = final_model_label or requested_model_label or attempted_model_label or "-"
        lines.append(f"requested_model: {requested_model_label or '-'}")
        lines.append(f"final_model: {final_model_label or '-'}")
        lines.append(f"model_used: {model_used_label}")
        lines.append(f"fallback_used: {str(_parse_bool(str(row.get('fallback_used', False)))).lower()}")
        quality_gate_applied = _parse_bool(str(row.get("quality_gate_applied", False)))
        quality_pass = _parse_bool(str(row.get("quality_pass", False)))
        lines.append(f"quality_gate_applied: {str(quality_gate_applied).lower()}")
        lines.append(f"quality_pass: {'n/a' if not quality_gate_applied else str(quality_pass).lower()}")
        quality_flags = row.get("quality_flags", [])
        if isinstance(quality_flags, list) and quality_flags:
            labels = [str(item).strip() for item in quality_flags if str(item).strip()]
            lines.append(f"quality_flags: {', '.join(labels) if labels else '-'}")
        else:
            lines.append("quality_flags: -")
        lines.append(f"duration_ms: {row.get('duration_ms', '')}")
        lines.append(f"num_ctx: {int(row.get('num_ctx', 0) or 0)}")
        lines.append(f"num_ctx_capture_count: {int(row.get('num_ctx_capture_count', 0) or 0)}")
        lines.append(f"rag_context_chars: {row.get('rag_context_chars', 0)}")
        lines.append(f"rag_context_capture_count: {row.get('rag_context_capture_count', 0)}")
        error = str(row.get("error", "") or "").strip()
        if error:
            lines.append(f"error: {error}")
        lines.append("")
        lines.append("PROMPT")
        lines.append("-" * 80)
        lines.append(str(row.get("prompt", "")))
        lines.append("")
        lines.append("TOOL CALLS")
        lines.append("-" * 80)
        lines.append(json.dumps(row.get("tool_calls", []), indent=2, ensure_ascii=False))
        lines.append("")
        lines.append("RESPONSE")
        lines.append("-" * 80)
        lines.append(str(row.get("response_text", "")))
        lines.append("")
        lines.append("SOURCE REFERENCES")
        lines.append("-" * 80)
        lines.append(json.dumps(row.get("source_references", []), indent=2, ensure_ascii=False))
        lines.append("")
        lines.append("INJECTED RAG CONTEXT")
        lines.append("-" * 80)
        lines.append(str(row.get("rag_context_text", "")))
        lines.append("")
        lines.append("MODEL ATTEMPTS")
        lines.append("-" * 80)
        lines.append(json.dumps(row.get("attempted_models", []), indent=2, ensure_ascii=False))
        lines.append("")
        lines.append("FALLBACK CHAIN")
        lines.append("-" * 80)
        lines.append(json.dumps(row.get("fallback_chain", []), indent=2, ensure_ascii=False))
        num_ctx_captures = row.get("num_ctx_captures", [])
        if isinstance(num_ctx_captures, list) and num_ctx_captures:
            lines.append("")
            lines.append("NUM_CTX CAPTURES")
            lines.append("-" * 80)
            lines.append(json.dumps(num_ctx_captures, indent=2, ensure_ascii=False))
        rag_context_captures = row.get("rag_context_captures", [])
        if isinstance(rag_context_captures, list) and rag_context_captures:
            lines.append("")
            lines.append("RAG CONTEXT CAPTURES")
            lines.append("-" * 80)
            lines.append(json.dumps(rag_context_captures, indent=2, ensure_ascii=False))
        warnings = row.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            lines.append("")
            lines.append("WARNINGS")
            lines.append("-" * 80)
            lines.append(json.dumps(warnings, indent=2, ensure_ascii=False))
        return "\n".join(lines)

    def _append_log(self, text: str) -> None:
        self._log_text.insert(tk.END, text + "\n")
        self._log_text.see(tk.END)


def main() -> int:
    app = LeaderboardUi()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
