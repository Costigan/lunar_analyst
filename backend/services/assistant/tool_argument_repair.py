from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RepairOutcome:
    repair_attempted: bool
    repair_applied: bool
    repair_rules: list[str] = field(default_factory=list)
    repair_status: str = "not_needed"
    repair_warning_codes: list[str] = field(default_factory=list)


class ToolArgumentRepairer:
    _ENUMS: dict[str, set[str]] = {
        "overwrite_mode": {"ask", "always", "never"},
        "mode": {"queued", "immediate"},
        "resampling": {"nearest", "bilinear", "cubic"},
    }

    def __init__(self, *, enabled: bool = False, max_repairs_per_call: int = 1) -> None:
        self._enabled = bool(enabled)
        self._max_repairs_per_call = max(0, int(max_repairs_per_call))

    def repair(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        scenario_id: str | None,
        schema: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], RepairOutcome]:
        if not self._enabled:
            return dict(arguments), RepairOutcome(repair_attempted=False, repair_applied=False, repair_status="not_needed")

        repaired = dict(arguments)
        applied: list[str] = []

        if "handler_name" in repaired and "implementation_name" not in repaired:
            repaired["implementation_name"] = repaired.pop("handler_name")
            applied.append("alias_handler_name")
        if "run_id" in repaired and "job_id" not in repaired:
            repaired["job_id"] = repaired.pop("run_id")
            applied.append("alias_run_id")

        if tool_name in {"raster.calculate", "raster.transform"} and "overwrite_mode" not in repaired:
            repaired["overwrite_mode"] = "ask"
            applied.append("default_overwrite_mode")

        path_keys = {
            "relative_path",
            "source_relative_path",
            "target_relative_path",
            "output_relative_path",
        }
        for key in path_keys:
            if key not in repaired:
                continue
            value = str(repaired.get(key, "") or "").strip().replace("\\", "/")
            value = value.lstrip("/")
            candidate = Path(value)
            if str(candidate).strip() and ".." in candidate.parts:
                return repaired, RepairOutcome(
                    repair_attempted=True,
                    repair_applied=bool(applied),
                    repair_rules=list(applied),
                    repair_status="blocked_requires_clarification",
                    repair_warning_codes=["policy_out_of_root_path"],
                )
            if value != repaired.get(key):
                repaired[key] = value
                applied.append(f"normalize_{key}")

        for key, allowed in self._ENUMS.items():
            if key not in repaired:
                continue
            value = str(repaired.get(key, "") or "").strip().lower()
            if value and value in allowed:
                if value != repaired.get(key):
                    repaired[key] = value
                    applied.append(f"normalize_enum_{key}")

        if (
            "time_start_utc" in repaired
            and "duration_hours" in repaired
            and "time_stop_utc" not in repaired
            and "time_step_hours" in repaired
        ):
            # Conservative completion: marker-only, actual timestamp math stays in tool layer.
            repaired["time_stop_utc"] = str(repaired["time_start_utc"])
            applied.append("temporal_stop_placeholder")

        if self._max_repairs_per_call <= 0:
            applied = []
        elif len(applied) > self._max_repairs_per_call:
            applied = applied[: self._max_repairs_per_call]

        repair_applied = bool(applied)
        return repaired, RepairOutcome(
            repair_attempted=True,
            repair_applied=repair_applied,
            repair_rules=applied,
            repair_status="revalidated" if repair_applied else "not_needed",
            repair_warning_codes=[],
        )
