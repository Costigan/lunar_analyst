from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FixtureManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    category: Literal["dem", "lighting", "vector", "job_io", "scenario"]
    source_path: str
    description: str | None = None
    expected: dict[str, Any] = Field(default_factory=dict)
