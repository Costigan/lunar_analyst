# ADR.0052: Assistant Bug Report Capture and Offline Analysis

- Status: Accepted
- Date: 2026-04-14
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0033.assistant_observability_and_failure_taxonomy.md`, `docs/ADR.0035.typed_entity_memory_and_reference_resolution_v1.md`

## Context

The Lunar Analyst assistant is still under active development, and users need a simple way to report when the assistant did the wrong thing or failed to do something expected.

Debugging those reports is harder than it should be because the relevant information is currently spread across turn events, Python logs, UI state, and scenario/session state.

## Problem

1. Users need a low-friction way to report assistant failures at the point of use.
2. The report needs to be captured together with the relevant assistant turn context, Python log excerpt, and program state.
3. The investigation step is developer-only and should be separate from the user capture step.
4. We want a repeatable analysis workflow that can hand a captured report to Codex, Gemini, or Copilot along with the repository docs and code context.

## Decision

Introduce a two-step assistant bug-report workflow.

### Step 1: Capture

1. Add a menu operation labeled `Report Assistant Bug`.
2. The operation opens a modal dialog with a short free-text field for the user to describe what was wrong.
3. Submitting the form creates a structured bug-report bundle in a developer-facing `debugging/` area.
4. The bundle captures:
   - the user report text,
   - the most recent relevant assistant turn identifiers,
   - a bounded slice of Python log lines around that turn,
   - the current program state needed for diagnosis,
   - any relevant scenario/session identifiers and selected workspace state.
5. Capture should use explicit correlation identifiers from the assistant observability contract where available, rather than relying only on raw log tail matching.
6. Capture must redact secrets, tokens, and other unsafe values.
7. Capture must be bounded so the report stays small enough to inspect and archive.

### Step 2: Analyze

1. Add a developer-only analysis workflow that consumes one captured bug-report bundle at a time.
2. The analysis workflow may invoke Codex, Gemini, or Copilot, but only after the developer selects the captured report.
3. The analysis workflow should load the report together with relevant repository documentation and code context, so the model can reason over the actual implementation rather than just the symptom summary.
4. The analysis workflow should write its findings to a separate analysis artifact, for example a note, markdown summary, or issue draft under `debugging/`.
5. The analysis step is intentionally separate from the capture step so production users are not blocked on model analysis and the capture path stays lightweight.

## Rationale

1. The assistant already emits structured turn and stage telemetry, so bug capture should align with that model instead of inventing a new ad hoc trail.
2. Capturing a report at the moment of failure preserves user intent and reduces context loss.
3. Keeping analysis separate avoids coupling the UI capture path to external model availability.
4. A stored bundle gives developers a stable input for Codex, Gemini, or Copilot investigations.
5. The developer can choose which model to use based on the incident type, without changing the user-facing capture flow.

## Non-Goals

1. Automatically fixing assistant bugs in production.
2. Running a shell command from the user-facing backend request path.
3. Sending raw secrets, credentials, or unbounded logs into the analysis workflow.
4. Replacing the existing assistant observability contract.
5. Building a generic incident-management system for every application failure.

## Capture Contract

The captured bundle should include a structured payload with at least:

```json
{
  "bug_report_id": "timestamp-or-uuid",
  "created_at_utc": "2026-04-14T22:00:00Z",
  "report_text": "short user description",
  "assistant": {
    "session_id": "session-id",
    "turn_id": "turn-id",
    "segment_id": "segment-id-or-null",
    "provider_id": "codex_cli-or-gemini_cli-or-local",
    "model_id": "model-id"
  },
  "log_excerpt": [
    "bounded python log lines"
  ],
  "program_state": {
    "scenario_id": "scenario-id",
    "workspace_state": "compact summary",
    "active_panel": "assistant",
    "selected_inputs": []
  },
  "redactions_applied": true
}
```

The exact shape may grow, but the bundle must remain:

1. machine-readable,
2. correlation-friendly,
3. redacted by default,
4. small enough for manual review.

## Analysis Contract

The offline analysis workflow should:

1. accept a captured bug-report bundle as input,
2. load relevant docs and code context from the repository,
3. invoke one chosen analysis model or provider,
4. preserve the resulting notes as a separate artifact,
5. keep the original capture bundle immutable.

Preferred outputs from analysis:

1. likely root cause,
2. reproduction hypothesis,
3. relevant files or functions,
4. suggested fix strategy,
5. follow-up tests or instrumentation gaps.

## Risks and Mitigations

- Risk: capture includes too much sensitive state.
  - Mitigation: redact by default and keep the captured payload bounded.
- Risk: analysis gets coupled to runtime request handling.
  - Mitigation: separate capture from analysis and keep the analysis developer-initiated.
- Risk: shell invocation becomes brittle or unsafe.
  - Mitigation: prefer a structured local workflow or existing provider wrapper over ad hoc shell piping.
- Risk: reports become hard to correlate with actual assistant execution.
  - Mitigation: require session/turn identifiers and structured telemetry references in the bundle.

## Implementation Notes

The preferred shape is:

1. capture endpoint or service method that persists the bug bundle,
2. developer-facing analysis command or UI action that picks a bundle and runs a provider against it,
3. storage layout under `debugging/` with timestamped bundle and analysis artifacts.

If the project uses Codex, Gemini, or Copilot-specific analysis flows, those should be implemented as thin wrappers around the same captured bundle format so the workflow stays consistent across providers.

## Implementation Plan

### Phase 1: Capture Path

Frontend files:

- `backend/web/lunar_analyst/src/components/Toolbar.tsx`
- `backend/web/lunar_analyst/src/AppLayout.tsx`
- `backend/web/lunar_analyst/src/services/assistantService.ts`
- new modal component under `backend/web/lunar_analyst/src/components/assistant/`

Backend files:

- `backend/api/routers/assistant.py`
- `backend/services/assistant/assistant_service.py`
- `backend/contracts/assistant_models.py`
- `backend/services/assistant/session_store.py` or a sibling persistence helper

Tasks:

1. Add a `Report Assistant Bug` menu item to the existing application menu.
2. Open a modal dialog that accepts a short report summary and an optional severity hint.
3. Include the current assistant session, turn, and scenario identifiers in the submission payload when available.
4. On the backend, build a normalized bug-report bundle from the submitted text plus correlated assistant/session state.
5. Pull a bounded log excerpt using the assistant turn correlation identifiers first, then fall back to a narrow Python log tail only if needed.
6. Capture a compact program-state snapshot that includes active scenario, selected assistant provider/model, workspace panel context, and other bounded state useful for diagnosis.
7. Persist the bundle as an immutable artifact under `debugging/assistant-bug-reports/<timestamp-or-id>/`.
8. Redact secrets, credentials, and raw tokens before the bundle is written.

### Phase 2: Offline Analysis Path

Developer tooling files:

- new script or command under `backend/tools/` or `scripts/`
- optional provider adapters under `backend/services/assistant/`
- optional docs/index input list under `docs/`

Tasks:

1. Add a developer-only command or UI action that selects an existing bug-report bundle.
2. Feed the stored bundle, the relevant docs, and the current code context to a chosen analysis provider.
3. Keep the provider invocation out of the runtime request path so capture stays fast and reliable.
4. Allow the analysis layer to target Codex or Gemini immediately, with Copilot supported through the same bundle contract if a local adapter is available.
5. Write the analysis result to a separate artifact beside the original capture bundle.
6. Preserve the original report as immutable input so later investigations can be repeated or compared.

### Phase 3: Validation

Tests:

1. Unit tests for bundle construction, redaction, and correlation selection.
2. API tests for the new bug-report endpoint.
3. Frontend tests for modal open/close and payload submission.
4. Integration tests for storage layout and offline analysis artifact creation.

Acceptance criteria:

1. The user can file a bug report from the UI without leaving the assistant workflow.
2. The stored bundle includes the user text, a bounded log excerpt, and compact program state.
3. The capture path does not invoke an LLM or shell command directly.
4. Developers can run a separate offline analysis step against the stored bundle.
5. Codex, Gemini, and future Copilot adapters all consume the same bundle format.

## Testing Strategy

1. Unit tests for bundle construction and redaction.
2. Integration tests for turn-correlation and bounded log extraction.
3. Contract tests for the stored bundle schema.
4. Regression tests that ensure the analysis workflow does not mutate the original capture artifact.
