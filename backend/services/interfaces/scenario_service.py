from __future__ import annotations

from abc import ABC, abstractmethod

from backend.contracts.decorators import contract
from backend.contracts.models import CreateScenarioRequest, Scenario


class ScenarioService(ABC):
    @contract(
        name="ScenarioService.create_scenario",
        request_type=CreateScenarioRequest,
        response_type=Scenario,
        description="Create a scenario directory + metadata record.",
    )
    @abstractmethod
    def create_scenario(self, request: CreateScenarioRequest) -> Scenario:
        raise NotImplementedError

    @contract(
        name="ScenarioService.get_scenario",
        request_type=None,
        response_type=Scenario,
        description="Get a scenario by identifier.",
    )
    @abstractmethod
    def get_scenario(self, scenario_id: str) -> Scenario:
        raise NotImplementedError

    @contract(
        name="ScenarioService.list_scenarios",
        request_type=None,
        response_type=list[Scenario],
        description="List all scenarios in the catalog.",
    )
    @abstractmethod
    def list_scenarios(self) -> list[Scenario]:
        raise NotImplementedError
