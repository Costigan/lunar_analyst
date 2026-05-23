# ADR.0037: Script Runtime Mode Isolation for `osgeo` and `moonlib`

## Status
Accepted

## Context
Lunar Analyst runs user-authored scenario Python scripts through the notebook/job runner path. Some scripts are pure Python geospatial workflows (`rasterio`, `osgeo.gdal`, `scipy`), while others require native `moonlib` (`pythonnet` bridge).

Recent runs exposed a deterministic failure mode:
- `osgeo.gdal` script execution failed with PROJ database layout mismatch errors (for example `DATABASE.LAYOUT.VERSION.MINOR ... expected ... comes from another PROJ installation`).
- Root cause: mixed runtime resolution between:
  - Python environment GDAL/PROJ stack (`env_311` + `osgeo` wheels),
  - native `moonlib` bundled GDAL/PROJ assets.

This class of failure is expected when two incompatible GDAL/PROJ stacks are visible in the same process and environment precedence is uncontrolled.

Current behavior is ambiguous for script authors:
- scripts that should be simple `osgeo` runs can fail depending on native-path contamination,
- scripts that need `moonlib` can fail if global fixes force `osgeo` paths only.

## Decision
Adopt explicit **script runtime modes** with process-scoped environment isolation:

1. `runtime_mode = "osgeo"` (default)
- Intended for `rasterio`/`osgeo`/`scipy` scripts.
- Runner pins GDAL/PROJ env vars to the active Python environment (`env_311`) data paths before any GDAL import.
- Runner does not bootstrap `pythonnet`/`moonlib` unless explicitly requested by script mode.

2. `runtime_mode = "moonlib"`
- Intended for scripts that require `pythonnet`/`moonlib`.
- Runner performs moonlib/native bootstrap first and uses moonlib-compatible path policy.
- Direct `osgeo.gdal` usage is discouraged in this mode unless explicitly supported by helper APIs.

3. `runtime_mode = "hybrid"` (deferred; not required for initial delivery)
- If needed later, implement as multi-subprocess orchestration (osgeo subprocess + moonlib subprocess exchanging files), not mixed ABI in one interpreter process.

## Rationale
- Process role isolation is the only robust way to avoid ABI/data-path collisions between two GDAL/PROJ stacks.
- A global one-size-fits-all environment policy will always break one class of scripts.
- Explicit mode selection makes behavior deterministic and debuggable.
- Defaulting to `osgeo` preserves expected behavior for most user-authored analysis scripts.

## Non-Goals
- Do not make mixed `osgeo` + `moonlib` imports in one process a supported baseline.
- Do not introduce silent auto-detection that guesses runtime mode without explicit policy; this increases ambiguity.

## API / Contract Changes
Add runtime mode to script-launch surfaces:

1. `scenario.run_script` (assistant/MCP tool arguments)
- Add optional `runtime_mode: "osgeo" | "moonlib"`; default `"osgeo"`.

2. Jobs/typed path used by script execution (`run_notebook_definition` integration)
- Add optional runtime mode in run params and propagate into runner environment setup.

3. UI Jobs Manager
- Add runtime mode selector for script jobs (default `osgeo`).
- Persist draft setting in existing parameter draft state.

4. Script metadata hint (optional additive convenience)
- Support pragma at top of script, e.g. `# lunar_runtime: moonlib`.
- Precedence: explicit run parameter > pragma hint > default `osgeo`.

## Runtime Policy

### `osgeo` mode policy
- Before importing user script:
  - set `GDAL_DATA` to Python `osgeo` data path,
  - set `PROJ_LIB` (and/or `PROJ_DATA`) to Python `osgeo` proj path,
  - sanitize/remove conflicting moonlib-provided GDAL/PROJ env vars for this process.
- No automatic `moonlib` bootstrap.
- If script imports `moonlib`/`pythonnet`, fail fast with explicit error code (`script_runtime_mode_conflict`) and actionable message.

### `moonlib` mode policy
- Bootstrap `pythonnet`/moonlib first.
- Apply moonlib-native runtime configuration path policy.
- If script imports `osgeo.gdal` directly, warn or fail based on strictness setting (initial default: warn + proceed only if import succeeds).

## Observability
Emit structured metadata/logging for every script run:
- `runtime_mode`,
- resolved `gdal_data_path`, `proj_path`,
- env normalization actions applied,
- conflict detection outcomes,
- explicit error codes for mode mismatch and runtime init failures.

## Detailed Implementation Plan

### Phase A: Contract plumbing
1. Add `runtime_mode` enum to tool schemas and relevant request models for script runs.
2. Update assistant tool registry descriptions and validation.
3. Regenerate OpenAPI/contracts.

### Phase B: Runner environment isolation
1. Add runner utility to resolve Python `osgeo` data directories from the active interpreter.
2. Add mode-specific environment normalizer executed in runner before user script import.
3. Ensure mode setting is passed through from API/tool invocation into runner context payload.

### Phase C: Mode conflict guardrails
1. Add lightweight import-scan preflight (AST parse or guarded runtime checks):
- detect `import moonlib`/`import pythonnet` in `osgeo` mode -> fast fail,
- detect direct `osgeo` imports in `moonlib` mode -> warning/fail by policy.
2. Return stable machine-readable errors:
- `script_runtime_mode_conflict`,
- `script_runtime_environment_init_failed`.

### Phase D: UI and UX integration
1. Jobs Manager parameter surface:
- show runtime mode for script jobs,
- default `osgeo`,
- keep existing launch flow unchanged otherwise.
2. Assistant prompt guidance:
- advise `runtime_mode="moonlib"` when script explicitly uses moonlib bridge.

### Phase E: Testing
1. Unit tests:
- env resolver and mode-specific env mutations,
- precedence logic (arg > pragma > default).
2. Worker/integration tests:
- `osgeo` script succeeds with viewshed/proj operations,
- `moonlib` script succeeds with bridge calls,
- wrong-mode import fails with explicit error code.
3. Contract tests:
- schema includes `runtime_mode`,
- launch endpoints/tool schemas validate enum and default.

### Phase F: Rollout and migration
1. Ship with default mode `osgeo` and additive optional parameter.
2. Add release note and docs examples for both modes.
3. Collect telemetry on mode conflicts and initialization failures for one release cycle.

### Phase G: Deferred hybrid mode evaluation
1. Evaluate demand for scripts that truly require both stacks in one logical workflow.
2. If needed, implement `hybrid` as subprocess choreography, not in-process mixing.

## Validation & Acceptance
Implementation is complete when:
1. `osgeo` script runs are deterministic and no longer fail from moonlib `proj.db` contamination.
2. `moonlib` script runs remain functional.
3. Mode-conflict errors are explicit and actionable.
4. Jobs Manager and assistant tool surfaces expose runtime mode without breaking existing script workflows.
5. New tests pass for both runtime modes and conflict cases.

## Consequences

### Expected Benefits
- Deterministic script execution behavior.
- Reduced time lost to opaque GDAL/PROJ runtime conflicts.
- Clear operator mental model: choose `osgeo` vs `moonlib` per script.

### Costs
- Additional parameter and UI complexity.
- Extra validation and environment management logic in runner path.

### Risks
- Some legacy scripts may rely on accidental mixed-runtime behavior.
- Incorrect mode choice by users can produce fast-fail errors (mitigated by guidance and defaults).

## Rollback Plan
If rollout causes unacceptable regressions:
1. Keep schema additive but temporarily ignore explicit runtime mode and run legacy path behind feature flag.
2. Disable strict conflict guardrails (warn-only mode).
3. Retain observability payloads to diagnose migration issues.
