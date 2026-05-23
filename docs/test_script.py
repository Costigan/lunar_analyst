from __future__ import annotations

import json
from pathlib import Path

from backend.notebook.runtime import get_context, register_output, report_progress


ctx = get_context()
report_progress(percent=5.0, message="test script started", stage="init")

output_rel = "outputs/test_script_output.geojson"
output_path = Path(ctx.scenario_root_dir) / output_rel
output_path.parent.mkdir(parents=True, exist_ok=True)

feature_collection = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-100.0, -100.0],
                        [100.0, -100.0],
                        [100.0, 100.0],
                        [-100.0, 100.0],
                        [-100.0, -100.0],
                    ]
                ],
            },
            "properties": {
                "name": "test_script_polygon",
                "scenario_id": ctx.scenario_id,
                "job_id": ctx.job_id,
                "params": ctx.params,
            },
        }
    ],
}
output_path.write_text(json.dumps(feature_collection, indent=2), encoding="utf-8")

register_output(
    relative_path=output_rel,
    kind="vector",
    subkind="geojson",
    render_mode="vector",
    metadata={"source": "docs/test_script.py"},
)
report_progress(percent=95.0, message="test script output registered", stage="finalize")
