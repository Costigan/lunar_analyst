from __future__ import annotations

import hashlib
import json
from typing import Any


def build_cache_context(
    *,
    provider_id: str,
    model_id: str,
    system_prompt: str,
    tool_schema: list[dict[str, Any]],
    scenario_id: str | None,
    compacted_summary: str | None,
) -> dict[str, str]:
    stable_payload = {
        "provider_id": provider_id,
        "model_id": model_id,
        "system_prompt": system_prompt,
        "tool_schema": tool_schema,
        "scenario_id": scenario_id,
        "compacted_summary": compacted_summary or "",
    }
    raw = json.dumps(stable_payload, sort_keys=True, separators=(",", ":"))
    return {"stable_prefix_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest()}
