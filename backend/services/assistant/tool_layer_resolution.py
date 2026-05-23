from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.api.dependencies import ServiceContainer


def resolve_layer_id_by_name(
    services: "ServiceContainer",
    *,
    scenario_id: str,
    layer_name: str,
) -> str:
    target = str(layer_name).strip().lower()
    if not target:
        raise ValueError("layer_name is required")

    layers = services.layer_service.list_layers(scenario_id)
    if not layers:
        raise KeyError(f"No scenario layers found for scenario_id={scenario_id}")

    def candidates_for(layer: Any) -> set[str]:
        title = str(getattr(layer, "title", "") or "").strip()
        values = {title.lower(), Path(title).stem.lower()}
        try:
            record = services.product_service.get_file_record(str(getattr(layer, "source_file_id", "")))
            rel = str(getattr(record, "relative_path", "") or "").strip()
            if rel:
                values.add(rel.lower())
                values.add(Path(rel).name.lower())
                values.add(Path(rel).stem.lower())
        except Exception:
            pass
        return {item for item in values if item}

    exact: list[Any] = []
    partial: list[Any] = []
    for layer in layers:
        names = candidates_for(layer)
        if target in names:
            exact.append(layer)
            continue
        if any(target in name for name in names):
            partial.append(layer)

    if len(exact) == 1:
        return str(exact[0].layer_id)
    if len(exact) > 1:
        raise ValueError(
            "Ambiguous layer_name; matches: "
            + ", ".join(str(item.title) for item in exact[:5])
        )
    if len(partial) == 1:
        return str(partial[0].layer_id)
    if len(partial) > 1:
        raise ValueError(
            "Ambiguous layer_name; partial matches: "
            + ", ".join(str(item.title) for item in partial[:5])
        )
    raise KeyError(f"No layer matched layer_name={layer_name!r} in scenario_id={scenario_id}")
