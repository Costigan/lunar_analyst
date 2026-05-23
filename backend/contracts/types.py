from typing import Annotated

from pydantic import StringConstraints

# Project-wide UTC timestamp format: YYYY-MM-DDTHH-MM-SS (no Z).
UtcTimestamp = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$"),
]

ScenarioRoot = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{2,31}$"),
]
