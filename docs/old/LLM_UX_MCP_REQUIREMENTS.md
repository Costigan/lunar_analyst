# LLM UX and MCP Requirements (Post-Phase-1 Additions)

## 1. Purpose
This document captures additional UX and MCP requirements discussed after initial LLM integration, before further implementation work.

## 2. Scope
Applies to:
1. Built-in Lunar Analyst assistant UX.
2. Assistant-driven scenario control.
3. MCP operations for jobs/scripts/notebooks.
4. Confirmation and policy behavior for script execution and script generation.
5. Log visibility and artifact registration behavior.

## 3. Scenario Selection UX Requirements

### RS-01 Single Current Scenario
1. There is exactly one current scenario per user session.
2. Current scenario state is shared across assistant and Scenario Explorer in that user session.
3. Current implementation environment has one user session, but requirements remain session-scoped.

### RS-02 Assistant Can Change Scenario
1. Assistant can set current scenario via prose.
2. Scenario can be identified by `scenario_id` or flexible name match.
3. If a unique reasonable match exists, switch immediately without confirmation.

### RS-03 Ambiguous and Missing Matches
1. If multiple reasonable matches exist, assistant must request disambiguation.
2. Disambiguation response must include top candidates with IDs and names.
3. If no match exists, current scenario remains unchanged and assistant returns suggestions.

### RS-04 UI and Map Synchronization
1. If assistant changes scenario, Scenario Explorer selection updates.
2. If Scenario Explorer changes scenario, assistant context updates.
3. Scenario change auto-zooms map to scenario default DEM extent.

### RS-05 Auditability
1. Scenario change events are recorded in assistant transcript/audit history as explicit system/audit entries.

### RS-06 MCP and Scenario Switching
1. Model-driven external MCP usage is allowed to switch current scenario.
2. Fine-grained MCP client restrictions are deferred to a future security design pass.

## 4. MCP Operations Requirements

### RM-01 Job Listing and Execution
1. MCP provides an operation to list predefined scenario-independent job definitions.
2. MCP provides an operation to execute one of those predefined jobs.

### RM-02 Scenario Script and Notebook Discovery
1. MCP provides operations to list runnable scenario-local Python scripts.
2. MCP provides operations to list runnable scenario-local Marimo notebooks.
3. Script discovery is recursive under scenario root (`**/*.py`).
4. Scenario directories are expected to remain shallow in practice, but discovery remains recursive.

### RM-03 Script and Notebook Execution
1. MCP provides operations to execute a listed script/notebook.
2. For v1 simplicity, script execution assumes no user-provided script arguments.
3. Script argument inference by LLM or backend local model is out of scope for v1.

### RM-04 Run Logs Access
1. LLM can access execution logs via MCP polling operations.
2. Log retrieval supports configurable `head N` and `tail M` line slices.
3. Log retrieval returns log size metadata (at minimum byte size and line count).
4. If logs are large, model may create temporary helper scripts to inspect/summarize.

### RM-05 Run Lifecycle
1. MCP exposes run status and run cancellation operations for executed jobs/scripts/notebooks.

## 5. Confirmation and Policy Requirements

### RC-01 Script Run Confirmation Scope
1. Script execution follows existing command confirmation model.
2. Approval for running a script is scoped to that specific script (not all scripts).

### RC-02 Script Generation and Write
1. LLM can generate new scripts.
2. Default write location for generated scripts is scenario root.
3. Initial script creation does not require confirmation.

### RC-03 Overwrite Policy
1. Overwrite approvals are session-scoped only (no cross-session persistence).
2. If the script was created by the LLM in the current session, overwrite is auto-approved.
3. If the script pre-existed before the session, first overwrite requires user confirmation.
4. Once approved for that pre-existing script, approval is remembered for the rest of the session.
5. User must be able to revoke/change overwrite approval decisions during the session.

## 6. Artifact Registration Requirements

### RA-01 Registration on Execution
1. Execution of scripts/notebooks should register produced artifacts.
2. Scripts should register artifacts directly when possible.
3. If scripts do not register outputs, backend must auto-register outputs.
4. Registration behavior must be idempotent.

## 7. Deferred/Out-of-Scope Items
1. MCP client authorization/restriction model beyond current baseline.
2. Script argument inference and dynamic parameter extraction.
3. Local-model-assisted script signature discovery.

## 8. Implementation Notes for Next Pass
1. Add explicit policy state for:
   1. per-session script-run approvals keyed by script path;
   2. per-session overwrite approvals keyed by script path;
   3. per-session set of scripts created by assistant.
2. Provide UI affordance to inspect/revoke session approvals.
3. Keep scenario selection as a single source of truth in session state, not duplicated per subsystem.
