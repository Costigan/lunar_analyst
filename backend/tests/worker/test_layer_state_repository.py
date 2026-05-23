from __future__ import annotations

from pathlib import Path

from backend.contracts.models import LayerState, RenderMode
from backend.services.repositories.layer_state_repository import LayerStateRepository
from backend.services.migrations import ensure_schema


def test_layer_state_repository_round_trip(tmp_path: Path) -> None:
    repo = LayerStateRepository()
    db_path = (tmp_path / "scenario.db").resolve()
    ensure_schema(db_path)

    layer = LayerState(
        layer_id="lyr_1",
        scenario_id="scn_1",
        product_id="prod_1",
        title="Layer 1",
        visible=True,
        opacity=0.85,
        z_index=10,
        render_mode=RenderMode.RASTER,
        source_file_id="file_1",
        style={"brightness": 0.1, "contrast": 1.2},
        updated_at_utc="2026-03-03T12-00-00",
    )

    repo.upsert_layer(db_path, layer)
    rows = repo.list_layers_for_scenario(db_path, "scn_1")
    assert len(rows) == 1
    assert str(rows[0][0]) == "lyr_1"
    assert str(rows[0][3]) == "Layer 1"

    repo.delete_layer(db_path, "lyr_1")
    assert repo.list_layers_for_scenario(db_path, "scn_1") == []

