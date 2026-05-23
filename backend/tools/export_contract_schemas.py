from __future__ import annotations

import json
from pathlib import Path

from backend.contracts import models
from backend.contracts.assistant_events import AssistantWsEnvelope
from backend.contracts.assistant_models import (
    AssistantBugReport,
    AssistantBugReportProgramState,
    AssistantBugReportRequest,
    AssistantBugReportResponse,
    AssistantBugReportSummary,
    AssistantConfirmation,
    AssistantMessage,
    AssistantOutput,
    AssistantSession,
    AssistantTurn,
    CreateAssistantTurnRequest,
    CreateAssistantTurnResponse,
)
from backend.contracts.events import WsEnvelope
from backend.jobs.handlers import (
    GenerateHillshadeResult,
    GenerateHorizonsResult,
    GenerateLightmapReductionRasterResult,
)

MODEL_EXPORTS = {
    "scenario.schema.json": models.Scenario,
    "product.schema.json": models.Product,
    "layer_state.schema.json": models.LayerState,
    "job.schema.json": models.Job,
    "job_event.schema.json": models.JobEvent,
    "error_envelope.schema.json": models.ErrorEnvelope,
    "create_scenario_request.schema.json": models.CreateScenarioRequest,
    "register_product_request.schema.json": models.RegisterProductRequest,
    "generate_horizons_result.schema.json": GenerateHorizonsResult,
    "generate_hillshade_result.schema.json": GenerateHillshadeResult,
    "generate_lightmap_reduction_raster_result.schema.json": GenerateLightmapReductionRasterResult,
    "ws_event_envelope.schema.json": WsEnvelope,
    "assistant_session.schema.json": AssistantSession,
    "assistant_output.schema.json": AssistantOutput,
    "assistant_message.schema.json": AssistantMessage,
    "assistant_turn.schema.json": AssistantTurn,
    "assistant_confirmation.schema.json": AssistantConfirmation,
    "assistant_bug_report_program_state.schema.json": AssistantBugReportProgramState,
    "assistant_bug_report_request.schema.json": AssistantBugReportRequest,
    "assistant_bug_report.schema.json": AssistantBugReport,
    "assistant_bug_report_response.schema.json": AssistantBugReportResponse,
    "assistant_bug_report_summary.schema.json": AssistantBugReportSummary,
    "assistant_create_turn_request.schema.json": CreateAssistantTurnRequest,
    "assistant_create_turn_response.schema.json": CreateAssistantTurnResponse,
    "assistant_ws_event_envelope.schema.json": AssistantWsEnvelope,
}


def export_json_schemas(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, model in MODEL_EXPORTS.items():
        schema_path = output_dir / filename
        schema_path.write_text(
            json.dumps(model.model_json_schema(), indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    export_json_schemas(Path("docs/contracts/generated/v1"))
