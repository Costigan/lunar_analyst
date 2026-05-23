from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.services.assistant.scenario_ref_normalization import (
    canonicalize_scenario_reference,
    normalize_scenario_reference,
)

if TYPE_CHECKING:
    from backend.api.dependencies import ServiceContainer


def match_scenario(
    services: "ServiceContainer",
    scenario_ref: str,
    *,
    dem_extent_reader: Any,
) -> dict[str, Any]:
    scenarios = list(services.scenario_service.list_scenarios())
    if not scenarios:
        return {
            "status": "not_found",
            "message": "No scenarios are available.",
            "candidates": [],
            "scenario_ref": scenario_ref,
        }

    normalized_ref = normalize_scenario_reference(scenario_ref)
    needle = canonicalize_scenario_reference(normalized_ref)
    scored: list[tuple[int, dict[str, Any]]] = []
    for scenario in scenarios:
        candidate = {
            "scenario_id": scenario.scenario_id,
            "name": scenario.name,
            "scenario_root": scenario.scenario_root,
            "directory": scenario.directory,
            "dem_extent": dem_extent_reader(scenario),
        }
        score = scenario_match_score(candidate, needle)
        if score > 0:
            scored.append((score, candidate))

    if not scored:
        return {
            "status": "not_found",
            "message": f"No scenario matched '{normalized_ref or scenario_ref}'.",
            "candidates": scenario_candidates_for_hint(scenarios),
            "scenario_ref": normalized_ref or scenario_ref,
        }

    scored.sort(key=lambda item: (-item[0], str(item[1]["scenario_id"])))
    top_score = scored[0][0]
    top = [item for item in scored if item[0] >= top_score - 40]
    if len(top) > 1 and top_score < 900:
        candidates = [item[1] for item in top[:5]]
        return {
            "status": "ambiguous",
            "message": f"Multiple scenarios match '{normalized_ref or scenario_ref}'.",
            "candidates": candidates,
            "scenario_ref": normalized_ref or scenario_ref,
        }

    winner = scored[0][1]
    return {
        "status": "selected",
        "message": f"Current scenario set to {winner['scenario_id']} ({winner['name']}).",
        "scenario": winner,
        "scenario_ref": normalized_ref or scenario_ref,
    }


def scenario_candidates_for_hint(scenarios: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for scenario in scenarios[:5]:
        items.append(
            {
                "scenario_id": scenario.scenario_id,
                "name": scenario.name,
                "scenario_root": scenario.scenario_root,
                "directory": scenario.directory,
            }
        )
    return items


def scenario_match_score(candidate: dict[str, Any], needle: str) -> int:
    if not needle:
        return 0
    scenario_id = canonicalize_scenario_reference(str(candidate.get("scenario_id", "")))
    name = canonicalize_scenario_reference(str(candidate.get("name", "")))
    scenario_root = canonicalize_scenario_reference(str(candidate.get("scenario_root", "")))
    directory_name = canonicalize_scenario_reference(
        Path(str(candidate.get("directory", "")).strip()).name
    )

    haystacks = [scenario_id, name, scenario_root, directory_name]
    haystack = " ".join(item for item in haystacks if item).strip()

    if needle == scenario_id:
        return 1000
    if needle == name:
        return 950
    if needle == scenario_root:
        return 900
    if needle == directory_name:
        return 880

    score = 0
    if needle in scenario_id:
        score = max(score, 700)
    if needle in name:
        score = max(score, 650)
    if needle in scenario_root:
        score = max(score, 620)
    if needle in directory_name:
        score = max(score, 610)

    if scenario_id.startswith(needle):
        score = max(score, 780)
    if name.startswith(needle):
        score = max(score, 740)
    if scenario_root.startswith(needle):
        score = max(score, 720)

    tokens = [token for token in needle.split() if token]
    if tokens and all(token in haystack for token in tokens):
        score = max(score, 600 + min(40, len(tokens) * 10))

    return score
