from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.contracts.decorators import contract
from backend.contracts.models import Job, JobEvent


class JobService(ABC):
    @contract(
        name="JobService.run_typed_job",
        response_type=Job,
        description="Queue or start a discovered typed tool implementation by name.",
    )
    @abstractmethod
    def run_typed_job(self, handler_name: str, args: dict[str, Any]) -> Job:
        raise NotImplementedError

    @contract(
        name="JobService.get_job",
        request_type=None,
        response_type=Job,
        description="Get a job by ID.",
    )
    @abstractmethod
    def get_job(self, job_id: str) -> Job:
        raise NotImplementedError

    @contract(
        name="JobService.list_job_events",
        request_type=None,
        response_type=list[JobEvent],
        description="List events for a job.",
    )
    @abstractmethod
    def list_job_events(self, job_id: str) -> list[JobEvent]:
        raise NotImplementedError

    @contract(
        name="JobService.cancel_job",
        request_type=None,
        response_type=Job,
        description="Request job cancellation.",
    )
    @abstractmethod
    def cancel_job(self, job_id: str) -> Job:
        raise NotImplementedError
