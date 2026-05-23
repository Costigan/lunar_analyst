from __future__ import annotations

import json
from pathlib import Path

from backend.api.app import create_app


def export_openapi(output_path: Path) -> None:
    app = create_app()
    schema = app.openapi()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")


if __name__ == "__main__":
    export_openapi(Path("docs/contracts/generated/v1/openapi.json"))
