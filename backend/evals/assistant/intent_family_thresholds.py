from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.evals.assistant.intent_family_readiness import compute_intent_family_readiness


DEFAULT_THRESHOLDS: dict[str, float] = {
    "validation_rate": 0.70,
    "mapping_success_rate": 0.60,
    "clarification_rate_max": 0.40,
    "fallback_to_model_rate_max": 0.60,
}


def evaluate_intent_family_thresholds(
    report: dict[str, Any],
    *,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    limits = dict(DEFAULT_THRESHOLDS)
    if isinstance(thresholds, dict):
        for key, value in thresholds.items():
            try:
                limits[str(key)] = float(value)
            except Exception:
                continue
    families_payload = report.get("families")
    families = families_payload if isinstance(families_payload, dict) else {}
    per_family: dict[str, dict[str, Any]] = {}
    all_pass = True
    for family, payload in sorted(families.items()):
        if not isinstance(payload, dict):
            continue
        validation_rate = float(payload.get("validation_rate", 0.0) or 0.0)
        mapping_success_rate = float(payload.get("mapping_success_rate", 0.0) or 0.0)
        clarification_rate = float(payload.get("clarification_rate", 0.0) or 0.0)
        fallback_to_model_rate = float(payload.get("fallback_to_model_rate", 0.0) or 0.0)
        checks = {
            "validation_rate": validation_rate >= limits["validation_rate"],
            "mapping_success_rate": mapping_success_rate >= limits["mapping_success_rate"],
            "clarification_rate_max": clarification_rate <= limits["clarification_rate_max"],
            "fallback_to_model_rate_max": fallback_to_model_rate <= limits["fallback_to_model_rate_max"],
        }
        family_pass = all(checks.values())
        all_pass = all_pass and family_pass
        per_family[family] = {
            "pass": family_pass,
            "checks": checks,
            "rates": {
                "validation_rate": validation_rate,
                "mapping_success_rate": mapping_success_rate,
                "clarification_rate": clarification_rate,
                "fallback_to_model_rate": fallback_to_model_rate,
            },
        }
    return {
        "pass": all_pass,
        "thresholds": limits,
        "families": per_family,
    }


def _load_json(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate per-family readiness thresholds.")
    parser.add_argument("--readiness-json", type=Path, default=None, help="Existing readiness JSON report.")
    parser.add_argument("--predictions", type=Path, default=None, help="Predictions JSONL to compute readiness from.")
    parser.add_argument("--json-out", type=Path, default=None, help="Optional output JSON path.")
    args = parser.parse_args()

    if args.readiness_json is None and args.predictions is None:
        raise SystemExit("Provide --readiness-json or --predictions.")
    if args.readiness_json is not None:
        report = _load_json(args.readiness_json)
    else:
        rows: list[dict[str, Any]] = []
        assert args.predictions is not None
        for raw in args.predictions.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                rows.append(parsed)
        report = compute_intent_family_readiness(rows)

    result = evaluate_intent_family_thresholds(report)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if bool(result.get("pass", False)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
