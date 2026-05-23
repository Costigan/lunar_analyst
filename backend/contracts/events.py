from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .models import JobEventName
from .types import UtcTimestamp


STAGE1_WS_EVENT_NAMES: tuple[str, ...] = tuple(e.value for e in JobEventName)


class WsEnvelope(BaseModel):
    """Stage 1 frozen WebSocket event envelope."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    event: JobEventName
    scenario_id: str
    timestamp_utc: UtcTimestamp
    data: dict[str, Any]
