from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.contracts.models import LayerState
from backend.services.migrations import ensure_schema


class LayerStateRepository:
    def _connect(self, db_path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.commit()
        return conn

    def list_layers_for_scenario(self, db_path: Path, scenario_id: str) -> list[tuple[Any, ...]]:
        ensure_schema(db_path)
        conn = self._connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT
                    layer_id, scenario_id, product_id, title, visible, opacity, z_index,
                    render_mode, source_file_id, style, updated_at_utc
                FROM layer_state
                WHERE scenario_id = ?
                ORDER BY z_index ASC, updated_at_utc ASC
                """,
                (scenario_id,),
            ).fetchall()
            return [tuple(row) for row in rows]
        finally:
            conn.close()

    def upsert_layer(self, db_path: Path, layer: LayerState) -> None:
        ensure_schema(db_path)
        conn = self._connect(db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO layer_state(
                    layer_id, scenario_id, product_id, title, visible, opacity, z_index,
                    render_mode, source_file_id, style, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    layer.layer_id,
                    layer.scenario_id,
                    layer.product_id,
                    layer.title,
                    1 if layer.visible else 0,
                    layer.opacity,
                    layer.z_index,
                    layer.render_mode.value,
                    layer.source_file_id,
                    json.dumps(layer.style),
                    layer.updated_at_utc,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_layer(self, db_path: Path, layer_id: str) -> None:
        ensure_schema(db_path)
        conn = self._connect(db_path)
        try:
            conn.execute("DELETE FROM layer_state WHERE layer_id = ?", (layer_id,))
            conn.commit()
        finally:
            conn.close()

    def delete_layers(self, db_path: Path, layer_ids: list[str]) -> None:
        if not layer_ids:
            return
        ensure_schema(db_path)
        conn = self._connect(db_path)
        try:
            conn.executemany(
                "DELETE FROM layer_state WHERE layer_id = ?",
                [(layer_id,) for layer_id in layer_ids],
            )
            conn.commit()
        finally:
            conn.close()

