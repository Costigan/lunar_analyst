# Message Pane: Goals, Current Architecture, and Known Bugs

## Goals

The Message Pane is intended to be the single, durable operator-facing output stream for run activity and messages in a scenario.

Desired behavior:

- One pane, one transcript.
- Script/notebook output should be visible in near real time.
- A script/notebook run should behave like one logical message block:
  - block begins when the run starts
  - stdout/stderr content grows over time
  - block ends when the run reaches terminal status
- While a run block is open, unrelated messages must not be interleaved inside that block.
- Message ordering should follow arrival/start order:
  - non-run messages are atomic
  - run blocks reserve a position and then grow in place
- Scroll behavior:
  - if user is at bottom, new output should keep the pane at bottom
  - if user scrolls up, pane should not force-jump to bottom


## Current Architecture

### Backend

There are two separate backend message/log channels used by the frontend:

1. Workspace messages (scenario-scoped transcript entries)
   - Endpoint:
     - `GET /api/v1/scenarios/{scenario_id}/messages`
     - `DELETE /api/v1/scenarios/{scenario_id}/messages`
   - Code pointers:
     - [list/clear workspace messages router](/e/projects/lunar_analyst/backend/api/routers/v1.py#L656)
     - [workspace message append/read/clear helpers](/e/projects/lunar_analyst/backend/api/dependencies.py#L5351)
   - Storage:
     - `<workspace_root>/.lunar_analyst/messages/<scenario_id>.jsonl`
   - Writers:
     - job lifecycle message conversion (`job_queued`, `job_started`, `job_progress`, etc.)
     - Python lint endpoint (`/python-files:lint`) appends `source="python"` entries
   - Code pointers:
     - [lint endpoint writes workspace message](/e/projects/lunar_analyst/backend/api/routers/v1.py#L502)
     - [job-event -> workspace-message conversion](/e/projects/lunar_analyst/backend/api/dependencies.py#L5398)

2. Notebook/script run stdout/stderr logs
   - Endpoint:
     - `GET /api/v1/jobs/{job_id}/logs?stream=combined`
   - Code pointers:
     - [job logs router endpoint](/e/projects/lunar_analyst/backend/api/routers/v1.py#L866)
     - [notebook run log read service](/e/projects/lunar_analyst/backend/api/dependencies.py#L3772)
   - Storage:
     - `<scenario>/.notebook_jobs/runs/<run_id>/runner_stdout.log`
     - `<scenario>/.notebook_jobs/runs/<run_id>/runner_stderr.log`
   - Produced by:
     - `NotebookJobService` process pipe capture in `backend/api/dependencies.py`
   - Code pointers:
     - [notebook job subprocess + pipe capture](/e/projects/lunar_analyst/backend/api/dependencies.py#L3597)
     - [stdout/stderr log file paths](/e/projects/lunar_analyst/backend/api/dependencies.py#L3661)
     - [stream pipe to log implementation](/e/projects/lunar_analyst/backend/api/dependencies.py#L4034)

### Frontend (`JobsManagerPane`)

Primary file:
- `backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx`

Current strategy:

- Maintains an in-memory transcript model (`transcriptItems`) with two item types:
  - workspace message item
  - run block item (contains ordered lines: markers/stdout/stderr)
  - Code pointers:
    - [transcript types and stream entries](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L46)
    - [state holders (`transcriptItems`, cursors, scroll refs)](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L252)
- Uses websocket job events + polling (`/jobs/{id}`) to detect run state transitions.
  - Code pointers:
    - [websocket job event handling](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L511)
    - [status poll fallback](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L559)
- Uses polling of `/jobs/{id}/logs` to append stdout/stderr deltas into the active run block.
  - Code pointers:
    - [active run log polling loop](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L688)
    - [append stdout/stderr deltas](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L423)
    - [final drain/retry on run completion](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L461)
- Computes a visible prefix of transcript items up to the first unfinished run block, so later items are effectively queued behind it.
  - Code pointers:
    - [active unfinished run detection](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L632)
    - [visible transcript prefix computation](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L637)
- Renders `visibleTranscriptLines` into the Messages pane.
  - Code pointer:
    - [Messages pane render path](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L1225)
- Includes bottom-stick scrolling logic:
  - tracks whether user is near bottom
  - auto-scrolls only when sticky mode is active
  - Code pointers:
    - [sticky auto-scroll effect](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L652)
    - [onScroll stickiness toggle](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L1241)


## Known Bugs / Current Gaps

1. **Quick-run output reliability is still inconsistent**
   - Very short scripts can still occasionally produce start/end markers without expected stdout/stderr lines.
   - Symptoms observed:
     - repeated fast runs show intermittent missing script output
     - adding sleep makes output consistently visible
   - Likely root cause class:
     - timing races between:
       - event arrival (`job_started` / terminal events)
       - creation/finalization of run transcript block
       - first/last log poll snapshots
   - Code hotspots:
     - [start block on queued/running](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L388)
     - [finalize block + final drain](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L461)
     - [active log poll cadence](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L688)

2. **Complex event ordering remains fragile**
   - Overlapping quick runs and mixed message traffic are handled by custom in-memory logic with several async update paths.
   - There is still risk of ordering anomalies under bursty event timing.

3. **Classification of script/notebook runs depends on metadata reconciliation**
   - Run identity currently infers “script/notebook block” from reconciled job metadata (`job_type`, `notebook_job_id`, title/definition).
   - If metadata arrives late or inconsistently, run-block state can still become temporarily incorrect.
   - Code pointer:
     - [reconcile run metadata (`job_type`, `notebook_job_id`)](/e/projects/lunar_analyst/backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx#L169)

4. **No dedicated integration tests yet for fast-run race scenarios**
   - There are frontend tests for existing jobs utilities, but current Message Pane race behavior is not fully locked by targeted tests for:
     - extremely short run completion
     - overlapping runs
     - mixed workspace-message and run-log arrival timing


## Non-goals of this document

- This document does not prescribe an immediate implementation rewrite.
- This document does not include UI polish tasks unrelated to message ordering/reliability.


## Immediate stabilization direction (summary)

- Keep the one-pane transcript model.
- Add deterministic sequencing for run-block lifecycle transitions.
- Add explicit fast-run regression tests that simulate:
  - start+finish before first log poll
  - final-log availability delay
  - concurrent run starts with interleaved workspace messages.

## Deterministic Handshake Plan (Implementation)

This section is the concrete implementation plan for the current fast-run reliability bug.

### Scope

- In scope:
  - Add backend log-completion signaling to `/api/v1/jobs/{job_id}/logs`.
  - Replace frontend terminal-run handling with an explicit `OPEN -> DRAINING -> CLOSED` state machine.
  - Seed run metadata earlier in websocket queued/started events for stable classification.
  - Add focused regression coverage for quick-run completion races.
- Out of scope:
  - Full rewrite of message transport architecture.
  - Changing the single-pane transcript UX contract.

### Plan Checklist

- [x] Backend: add deterministic `is_final` in job log payloads.
  - [x] Track per-run process exit and stdout/stderr pump completion in notebook run state.
  - [x] Return `is_final` for `stdout`, `stderr`, and `combined` responses.
  - [x] Keep `is_final` monotonic (once true, always true for the run).
- [x] Frontend: run-block lifecycle state machine.
  - [x] Add run block phase enum: `open`, `draining`, `closed`.
  - [x] On terminal run event, transition to `draining` and keep transcript prefix blocking active.
  - [x] Continue log polling until backend `is_final=true`, then append end marker and close block.
  - [x] Guarantee at least one drain poll even if terminal arrives before first active poll.
- [x] Frontend: deterministic sequencing latch.
  - [x] Preserve per-run line cursor (`stdout`, `stderr`) and always drain from latest totals.
  - [x] Avoid duplicate drain loops for the same run with per-run in-flight guards.
- [x] Backend/WS metadata seeding.
  - [x] Include `job_type`, `handler_name`, and `title` in `job_queued` + `job_started` websocket payloads.
  - [x] Include `notebook_job_id` when available in queued/started payloads.
- [ ] Regression tests.
  - [x] Contract/backend test: `/jobs/{id}/logs` reports `is_final=true` after terminal completion for notebook runs.
  - [ ] Frontend test: quick run path keeps block in draining until `is_final`, and includes fast stdout line.

## Note on recent changes

- Runner bootstrap logging was moved off user-visible stdout in:
  - [backend/notebook/job_runner.py](/e/projects/lunar_analyst/backend/notebook/job_runner.py#L96)
- This reduced transcript noise but did not resolve the core quick-run race.
