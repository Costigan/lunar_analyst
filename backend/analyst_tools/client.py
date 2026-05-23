from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

import httpx

from backend.contracts.models import Job, ToolDefinition, ToolDefinitionsResponse, ToolRunResponse

if TYPE_CHECKING:
    from backend.api.dependencies import ServiceContainer


@dataclass(frozen=True)
class AnalystToolHttpClientConfig:
    base_url: str
    api_token: str | None = None
    timeout_seconds: float = 30.0


class AnalystToolHttpClient:
    def __init__(self, config: AnalystToolHttpClientConfig, client: httpx.Client | None = None) -> None:
        self._config = config
        self._owns_client = client is None
        headers: dict[str, str] = {}
        if config.api_token:
            headers["x-lunar-session-token"] = config.api_token
        self._client = client or httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            headers=headers,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def list_tools(self, *, include_drafts: bool = False, include_system: bool = True) -> ToolDefinitionsResponse:
        response = self._client.get(
            "/api/v1/tools",
            params={"include_drafts": include_drafts, "include_system": include_system},
        )
        response.raise_for_status()
        return ToolDefinitionsResponse.model_validate(response.json())

    def get_tool(self, tool_name: str, *, include_drafts: bool = False, include_system: bool = True) -> ToolDefinition:
        response = self._client.get(
            f"/api/v1/tools/{tool_name}",
            params={"include_drafts": include_drafts, "include_system": include_system},
        )
        response.raise_for_status()
        return ToolDefinition.model_validate(response.json())

    def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolRunResponse:
        response = self._client.post(f"/api/v1/tools/{tool_name}/runs", json={"arguments": arguments})
        response.raise_for_status()
        return ToolRunResponse.model_validate(response.json())

    def get_job(self, job_id: str) -> Job:
        response = self._client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        return Job.model_validate(response.json())

    def cancel_job(self, job_id: str) -> Job:
        response = self._client.post(f"/api/v1/jobs/{job_id}/cancel")
        response.raise_for_status()
        return Job.model_validate(response.json())

    # Backward-compatible aliases.
    def get_run(self, run_id: str) -> Job:
        return self.get_job(run_id)

    def cancel_run(self, run_id: str) -> Job:
        return self.cancel_job(run_id)


class LocalAnalystToolClient:
    def __init__(self, services: "ServiceContainer") -> None:
        self._services = services

    def list_tools(self, *, include_drafts: bool = False, include_system: bool = True) -> ToolDefinitionsResponse:
        from backend.analyst_tools.catalog import list_tool_definitions

        return list_tool_definitions(include_drafts=include_drafts, include_system=include_system)

    def get_tool(self, tool_name: str, *, include_drafts: bool = False, include_system: bool = True) -> ToolDefinition:
        from backend.analyst_tools.catalog import get_tool_definition

        return get_tool_definition(tool_name, include_drafts=include_drafts, include_system=include_system)

    def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolRunResponse:
        definition = self.get_tool(tool_name, include_drafts=True, include_system=True)
        implementation_name = definition.implementation_name or definition.handler_name
        job = self._services.job_service.run_typed_job(implementation_name, arguments)
        return ToolRunResponse(
            tool_name=definition.tool_name,
            job_id=job.job_id,
            run_id=job.job_id,
            job=job,
            result=_extract_completed_job_result(self._services, job.job_id),
        )


def _extract_completed_job_result(services: "ServiceContainer", job_id: str) -> dict[str, Any]:
    from backend.contracts.models import JobEventName

    events = services.job_service.list_job_events(job_id)
    for event in reversed(events):
        if event.event_name == JobEventName.JOB_COMPLETED:
            payload = event.data.get("result", {})
            return payload if isinstance(payload, dict) else {}
    return {}
