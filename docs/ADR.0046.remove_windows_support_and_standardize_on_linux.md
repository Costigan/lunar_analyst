# ADR.0046: Remove Windows Support and Standardize on Linux

- Status: Accepted
- Date: 2026-04-08
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0001.process_model.md`, `docs/ADR.0012.python_net_native_bridge.md`, `docs/ADR.0037.script_runtime_mode_isolation_for_osgeo_and_moonlib.md`, `docs/ADR.0039.linux_port_popos_and_ubuntu_container.md`, `docs/ADR.0041.parallel_popos_and_ubuntu_container_development.md`, `AGENTS.md`

## Context

Lunar Analyst currently contains conflicting platform signals:

- project-level guidance still describes Windows 11 as the maintained runtime baseline,
- more recent implementation work has established host-native Linux and Ubuntu container workflows,
- setup docs, helper scripts, and examples still include Windows-specific paths and commands,
- native bootstrap and some tooling assumptions still carry Windows-first terminology and compatibility goals.

This ambiguity creates avoidable cost in several areas:

- environment management,
- documentation,
- testing,
- native bridge maintenance,
- packaging assumptions,
- assistant guidance and generated script quality.

The codebase has already accumulated Linux-oriented development paths:

- host-native Pop!_OS development,
- Ubuntu-container parity work,
- Linux-oriented bootstrap and runtime scripts,
- Linux troubleshooting around GDAL, Python, and Marimo workflows.

At the same time, maintaining Windows compatibility continues to impose friction:

- duplicate bootstrap and launch paths,
- Windows-path examples (`D:/...`, `.bat`, PowerShell),
- Windows-specific DLL naming and loader logic,
- Windows-specific guidance in tests and developer documentation,
- native portability work constrained by "must not break Windows" policy even when Linux is the actual target.

If Windows support is no longer a product or development requirement, the project should say so explicitly and remove the related maintenance burden in a controlled way.

## Problem

The current project state mixes:

- Linux as the effective active engineering environment,
- Windows as the documented supported baseline,
- and cross-platform compatibility work that is expensive but not strategically necessary.

Without an explicit architectural decision, the repo will continue to drift:

- developers will not know which platform assumptions are authoritative,
- bootstrap and test workflows will remain duplicated,
- native bridge code will keep carrying Windows-only branches and naming conventions,
- documentation and assistant guidance will continue to produce platform-inconsistent outputs.

## Decision

Lunar Analyst will remove support for running on Windows and standardize on Linux as the only supported development and runtime platform.

The supported platform contract becomes:

1. Host-native Linux is the only supported developer workstation/runtime environment.
2. Ubuntu-based containers remain the supported deployment/runtime packaging target.
3. Windows-specific runtime, bootstrap, test, and documentation paths will be removed rather than maintained in parallel.

For avoidance of doubt:

- this is not "Windows is untested";
- this is "Windows is unsupported and may stop working at any time."

## Scope

In scope:

- developer bootstrap and launch workflows,
- backend runtime assumptions,
- native bridge/bootstrap paths,
- notebook and Marimo execution assumptions,
- frontend/dev build documentation,
- test and CI matrix policy,
- deployment/container guidance,
- assistant and RAG guidance that currently embeds Windows-specific assumptions,
- removal of Windows-specific helper scripts and docs where no longer needed.

Out of scope:

- immediate replacement of Tauri packaging strategy unless it is currently Windows-only in active use,
- redesign of the core FastAPI/worker/Marimo/browser process model,
- changing job-handler authority or compute contract architecture,
- security/auth changes unrelated to platform support removal.

## Detailed Decision

### 1. Platform Baseline

The new platform baseline is:

- Linux host development,
- Linux runtime execution,
- Ubuntu-container deployment/runtime parity.

The required interpreter/runtime baseline remains:

- Python `3.11.x`,
- .NET `9.0`,
- Node/npm for frontend builds.

### 2. Documentation Policy

All repo documentation will be revised so Linux is the only supported operating environment.

This includes:

- removing Windows 11 as the maintained baseline,
- removing `D:\...` examples from active setup and test paths,
- removing PowerShell/`.bat` instructions from primary workflows,
- revising any language that implies parallel Windows support.

Historical Windows notes may be preserved only in archived or explicitly historical docs.

### 3. Bootstrap and Launch Policy

The Linux bootstrap and launch scripts become canonical.

Specifically:

- `scripts/bootstrap.sh` is the primary environment bootstrap path,
- Linux launch scripts are the only supported launch helpers,
- Windows bootstrap/launch helpers may be deleted once documentation and CI are updated,
- generated dependency workflows should assume Linux paths and Linux package/tool resolution only.

### 4. Native Bridge and Interop Policy

Native bridge code should be simplified for Linux support rather than preserving Windows compatibility branches.

This includes:

- `.so` shared-library loading as the supported native-artifact model,
- removal or de-prioritization of `.dll`-specific guidance and fallback logic where safe,
- Linux-native dependency resolution for CSPICE, GDAL, PROJ, and moonlib artifacts,
- Linux-only smoke/health expectations for native bootstrap.

This ADR does not require immediate deletion of every Windows code path in one change, but it does require that remaining Windows branches be treated as migration debt to remove, not compatibility commitments to preserve.

### 5. Notebook and Assistant Runtime Policy

Notebook jobs, scenario scripts, and assistant-generated Python code should assume Linux as the supported execution environment.

That means:

- no active guidance should reference Windows drive-letter paths,
- generated scripts should assume Linux filesystem semantics,
- setup and repair guidance should target Linux package and runtime behavior,
- assistant startup/RAG guidance should be revised so Linux is the authoritative environment story.

### 6. Test and CI Policy

Linux becomes the sole supported CI and local verification baseline.

This means:

- Linux CI lanes are required,
- Windows CI lanes should be removed once Linux lanes fully cover supported behavior,
- contract, integration, frontend, and native validation should all publish Linux-first instructions,
- manual verification procedures should be Linux-only in active docs.

### 7. Deployment Policy

Deployment and runtime packaging should assume Linux hosts and Ubuntu-compatible containers only.

If Tauri or any desktop packaging remains in scope later, that should be addressed by a separate ADR with explicit Linux desktop targets.

## Consequences

Positive:

- one authoritative platform story,
- simpler setup and troubleshooting,
- reduced native-interop maintenance burden,
- clearer assistant/runtime guidance,
- cleaner docs and CI policy,
- easier ownership of GDAL/PROJ/pythonnet issues.

Negative:

- loss of Windows workstation/runtime compatibility,
- removal of existing Windows developer workflows,
- potential migration cost for any remaining Windows-only users,
- required cleanup across docs, scripts, tests, and native bootstrap code.

Risks:

- hidden Windows assumptions may still exist in runtime code,
- native bridge simplification could expose Linux-only gaps that were previously masked by Windows-first design,
- stale docs or RAG content may continue to emit Windows-specific guidance unless comprehensively updated.

## Migration Plan (Checklist)

### Phase 0: Approval and Support Contract

- [x] Confirm product decision that Windows is no longer a supported runtime or development environment.
- [x] Record the new support statement in `docs/DESIGN.md`.
- [x] Update `AGENTS.md` so platform instructions no longer require Windows support.
- [x] Update top-level docs to state Linux-only support unambiguously.
- [x] Define the exact supported Linux baseline:
  - [x] host distro contract,
  - [x] Python `3.11.x`,
  - [x] .NET `9.0`,
  - [x] Node/npm baseline.

### Phase 1: Documentation and Guidance Cleanup

- [x] Remove Windows-first language from:
  - [x] `README.md`,
  - [x] `docs/DEVELOPER_GUIDE.md`,
  - [x] `docs/HOW_TO_TEST.md`,
  - [x] active setup docs,
  - [x] assistant guidance and RAG corpus files that describe runtime setup.
- [x] Replace `D:\...` and `.bat` examples in active docs with Linux examples.
- [x] Mark any remaining Windows references as historical only or remove them.
- [x] Audit `docs/DESIGN.md` for:
  - [x] Windows target statements,
  - [x] Windows-only process assumptions,
  - [x] platform support language that conflicts with Linux-only support.

### Phase 2: Bootstrap and Script Consolidation

- [x] Make Linux bootstrap and launch scripts the only supported entrypoints.
- [x] Remove or archive Windows bootstrap helpers such as:
  - [x] `scripts/bootstrap.ps1`,
  - [x] `.bat` launch wrappers,
  - [x] Windows-only environment activation guidance.
- [x] Ensure dependency compile/bootstrap/verify flows work Linux-only without Windows compatibility branches.
- [x] Add clear Linux prerequisite guidance for:
  - [x] Python,
  - [x] GDAL/PROJ,
  - [x] .NET SDK/runtime,
  - [x] Node/npm.

### Phase 3: Runtime Code Cleanup

- [x] Remove Windows-path fallback defaults and examples from active runtime code.
- [x] Audit backend code for drive-letter paths, `.dll` assumptions, Win32 API usage, and Windows-specific environment logic.
- [x] Simplify config defaults to Linux-native path and executable assumptions.
- [x] Remove Windows-only runtime branches where no longer needed.
- [x] Add tests that assert Linux-native defaults and path handling.

### Phase 4: Native Bridge and Interop Simplification

- [x] Refactor `pythonnet` and moonlib bootstrap to target Linux shared-library loading as the supported path.
- [x] Remove Windows-specific preload and search-path logic that is no longer needed.
- [x] Standardize CSPICE/GDAL/PROJ handling around Linux runtime expectations.
- [x] Validate native smoke tests on Linux-only supported configurations.
- [x] Document rollback steps for any native bootstrap regression during the cleanup.

### Phase 5: Notebook, Assistant, and RAG Alignment

- [x] Remove Windows-specific environment assumptions from notebook/job helpers.
- [x] Update assistant guidance corpus so generated code and repair advice assume Linux.
- [ ] Re-run assistant evals that cover script-writing, notebook execution, and environment repair.
- [ ] Verify Marimo and scenario-script flows under the Linux-only baseline.

### Phase 6: Test and CI Matrix Simplification

- [ ] Remove Windows lanes from active CI after Linux coverage is complete.
- [ ] Ensure Linux CI covers:
  - [ ] backend unit/integration/contract tests,
  - [ ] frontend tests/build,
  - [ ] native smoke checks,
  - [ ] representative notebook flows.
- [x] Update local verification docs so Linux is the only active test workflow.

### Phase 7: Cleanup and Removal

- [x] Delete obsolete Windows-specific scripts and docs after replacement is merged.
- [x] Remove stale comments, config examples, and compatibility notes that imply Windows support.
- [x] Update ADR/status tracking files to reflect the new platform support policy.
- [ ] Capture final manual verification evidence for Linux-only bootstrap, startup, notebooks, native smoke, and frontend workflows.

## Acceptance Criteria

This ADR is considered implemented when all of the following are true:

- active docs identify Linux as the only supported platform,
- Linux bootstrap from a clean machine is repo-managed and documented,
- Windows-specific bootstrap/launch guidance is removed from active workflows,
- runtime code no longer relies on Windows-only defaults,
- native bridge smoke checks pass on Linux,
- CI covers the supported Linux workflows,
- assistant guidance and environment-repair guidance no longer assume Windows support.

## Rollback Strategy

If Linux-only standardization reveals unacceptable gaps:

- retain the historical Windows-specific code in version control history,
- reintroduce only the minimum required Windows support paths under a new explicit ADR,
- avoid partially restoring Windows guidance without also restoring verification and ownership commitments.

Rollback should be treated as a new scoped architectural decision, not an ad hoc reversal in docs only.
