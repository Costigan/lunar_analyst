from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from backend.contracts.decorators import contract


@dataclass(frozen=True)
class HorizonComputeRequest:
    scenario_id: str
    dem_product_id: str
    azimuth_step_deg: float


@dataclass(frozen=True)
class HorizonComputeResult:
    # Internal/native payload can contain ndarray-like structures.
    horizon_bins: Any
    metadata: dict[str, Any]


class NativeComputeService(ABC):
    @contract(
        name="NativeComputeService.run_horizon",
        request_type=HorizonComputeRequest,
        response_type=HorizonComputeResult,
        description="Run horizon compute via pythonnet/.NET worker boundary.",
    )
    @abstractmethod
    def run_horizon(self, request: HorizonComputeRequest) -> HorizonComputeResult:
        raise NotImplementedError

    @contract(
        name="NativeComputeService.health_check",
        request_type=None,
        response_type=dict,
        description="Validate native runtime availability.",
    )
    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        raise NotImplementedError
