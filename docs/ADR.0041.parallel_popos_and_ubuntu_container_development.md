# ADR.0041: Parallel Pop!_OS Host Development and Ubuntu Container Deployment

- Status: Accepted
- Date: 2026-04-01
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0002.scenario_filesystem_and_catalog.md`, `docs/ADR.0021.assistant_rag_wrapper_and_scenario_index.md`, `docs/ADR.0039.linux_port_popos_and_ubuntu_container.md`, `AGENTS.md`

## Implementation Status

ADR 0041 has been implemented through the C4 boundary as the current stopping point for this workstream.

- Repo and workflow changes for Phases A through C3 are in place.
- C4 is the current maturity gate for real NRP deployment validation.
- Remaining post-C4 steps are intentionally deferred until the application is more mature and the deployment surface is stable enough to justify further operational hardening.

## Context

Lunar Analyst is now being exercised on Linux host environments, specifically Pop!_OS, and also needs an Ubuntu-based container deployment path for the National Research Platform (NRP).

The active architecture already assumes:

- a configurable `workspace_root`,
- self-contained scenario directories under that workspace,
- a workspace-global `scenario_catalog.db`,
- workspace-global assistant state under `.assistant/`,
- FastAPI as the control plane,
- additional local processes for compute worker and Marimo,
- Linux/container work must remain additive and must not break the maintained Windows baseline.

The current repo also already reflects a Linux-oriented config example:

- `config/lunar_analyst.toml` uses `workspace_root = "/e/lunar_analyst_scenarios"`.

The main operational question is not only "how do we run in Docker?" but "how do we keep day-to-day development productive on Pop!_OS while converging toward an Ubuntu container that behaves the same way in NRP?"

## Problem

If host development and container deployment drift apart, we will repeatedly rediscover environment issues in packaging, path layout, native dependencies, and workspace persistence.

We need a strategy that answers all of the following:

1. What is the stable runtime boundary between host and container?
2. What should be mounted persistently in containerized deployments?
3. Is mounting only scenario directories sufficient?
4. Should there be a relatively static Ubuntu base image that plays the same role as the Windows/Linux Python environment (`env_311`)?
5. During development, should the git working tree be bind-mounted into the container so local editing tools and Codex continue to work normally?

## Decision

Adopt a **workspace-root-centered container strategy** with **three layers of runtime**:

1. **Host-native Pop!_OS development remains first-class** for day-to-day coding and debugging.
2. **A stable Ubuntu base image** becomes the container equivalent of `env_311`.
3. **Runtime and development containers** are built on that common base, with different mount policies for production vs development.

### Core Decision Summary

- The persistent container mount should be the **entire `workspace_root`**, not only individual scenario directories.
- Mounting only scenario directories is **not sufficient**, because the backend also owns workspace-global state such as:
  - `scenario_catalog.db`
  - `.assistant/rag/global_rag.db`
  - future workspace-global assistant metadata and caches
- The repo working tree should be **bind-mounted in development containers** so editing, tests, and Codex workflows remain normal.
- The repo working tree should **not** be mounted in production/NRP runtime containers; production images should carry the application code as an image artifact.
- We should introduce a relatively static **Ubuntu base image** containing the OS packages and language runtimes needed by Lunar Analyst. That image is the container analogue of `env_311`, even though it will also include non-Python dependencies such as .NET and GDAL/PROJ.

### Operating Policy

- Container-based development is **additive** and must not replace normal host-native Pop!_OS development.
- Host-native development remains the fastest default path for coding, debugging, and exploratory work.
- Container-based development exists to:
  - validate Ubuntu runtime parity continuously,
  - catch packaging/path/dependency regressions early,
  - support NRP-oriented deployment testing locally.
- Production deployment uses immutable runtime images plus mounted persistent workspace storage.

### Recommended Runtime Shape

Use a **single application container image family** with environment-specific launch modes.

Recommended first implementation:

- one dev image for local Docker/Compose use,
- one runtime image for deployment,
- both built from one shared base image.

Recommended process policy inside those environments:

- FastAPI remains the main service entrypoint,
- compute worker remains a separate Python process,
- Marimo remains a separate process when enabled,
- frontend assets are built into the runtime image rather than served from a bind-mounted node workspace in production.

The architecture invariant is process separation, not necessarily container separation. Therefore:

- in the first containerized slice, FastAPI may launch/manage the worker subprocess exactly as it does in host-native mode,
- Marimo may remain optional/disabled for initial container deployment,
- later, if operationally useful, Marimo can be moved to its own service/deployment without changing the app contract.

## Rationale

### Why mount the whole workspace root

The scenario model is intentionally workspace-root-centered, not scenario-directory-only.

Per existing ADRs and design docs:

- scenarios live under `{workspace_root}/{scenario_root}/`
- the global catalog lives at `{workspace_root}/scenario_catalog.db`
- assistant RAG state lives under `{workspace_root}/.assistant/`

If we mount only scenario directories:

- scenario discovery and catalog state become awkward or split-brain,
- assistant global state is either lost on restart or forced into a separate special-case mount,
- path-safety assumptions become harder to reason about,
- migrations toward new workspace-global features become operationally brittle.

The simpler and more correct contract is:

- one persistent volume mounted as the container `workspace_root`.

### Why a base image should exist

`env_311` is currently serving as a stable, prebuilt dependency environment. The containerized equivalent should do the same job.

The base image should absorb the slow-changing parts:

- Ubuntu base OS,
- Python 3.11 runtime and venv tooling,
- GDAL/PROJ and related native packages,
- .NET 9 SDK/runtime as needed,
- system libraries required by `pythonnet`, raster tools, and native interop,
- any standard CLI/runtime prerequisites used by backend startup and tests.

That gives us a stable substrate that is rebuilt infrequently, while app/runtime images rebuild more often as code changes.

### Why bind-mount the repo in development only

For active development on Pop!_OS, mounting the repo into the container gives the least friction:

- local editors operate on the same files,
- Codex can continue to work in the normal git working tree,
- backend/frontend reload loops can run against live source,
- container behavior stays close to NRP runtime behavior without forcing every edit through image rebuilds.

For production, bind-mounting the repo is the wrong boundary:

- it weakens reproducibility,
- it couples deploy behavior to arbitrary host filesystem state,
- it is unnecessary once an image is built.

## Target Container Model

### Image 1: `lunar-analyst-base`

Purpose:

- relatively static Ubuntu image, similar in role to `env_311`

Contents:

- Ubuntu LTS base
- Python 3.11
- pip/venv tooling
- .NET 9 runtime and likely SDK for Linux-native builds/tests
- GDAL/PROJ runtime and development packages required by Python wheels/native bindings
- native support libraries used by `pythonnet` and CSPICE loading
- optional Node toolchain only if frontend build must happen inside this image

Expected rebuild cadence:

- infrequent
- only when OS/runtime dependencies change

### Image 2: `lunar-analyst-runtime`

Purpose:

- production/runtime image for Docker and NRP

Built from:

- `lunar-analyst-base`

Contents:

- checked-out application code copied into image
- installed Python dependencies
- built frontend assets
- built/packaged native artifacts needed for Linux runtime
- default Linux container config

Runtime mount policy:

- mount persistent `workspace_root`
- inject config via env/config file/ConfigMap
- inject secrets separately

### Image 3: `lunar-analyst-dev`

Purpose:

- local containerized development on Pop!_OS

Built from:

- `lunar-analyst-base`

Expected mounts:

- bind-mounted git working tree
- bind-mounted persistent workspace root
- optional package/cache mounts for faster rebuilds

This image exists to mirror the runtime environment while preserving fast edit-test cycles.

### Image Build Responsibilities

#### `lunar-analyst-base`

Should contain only slow-moving dependencies and platform setup:

- Ubuntu LTS
- Python 3.11
- .NET 9 SDK/runtime
- Linux GDAL/PROJ runtime
- Linux build tools needed for Python packages/native checks
- any required shared libraries for `pythonnet`, CSPICE, and raster packages

Should not contain:

- repo source code
- scenario data
- environment-specific secrets

#### `lunar-analyst-dev`

Should add:

- developer conveniences,
- a default working directory such as `/workspace/lunar_analyst`,
- optional shell tooling for debugging,
- optional frontend dev dependencies if not already in the base image.

Should expect:

- bind-mounted repo checkout,
- bind-mounted workspace root,
- iterative command execution (`pytest`, backend startup, frontend dev server, native probes).

#### `lunar-analyst-runtime`

Should add:

- copied application source,
- installed Python dependencies,
- built frontend assets,
- runtime entrypoint,
- runtime config defaults for container use.

Should be:

- rebuildable from source at any time,
- runnable without a git checkout,
- immutable apart from mounted workspace/config/secrets.

## Container Files and Repo Additions

The implementation should add a dedicated containerization surface in the repo.

Expected files:

- `docker/Dockerfile.base`
- `docker/Dockerfile.dev`
- `docker/Dockerfile.runtime`
- `docker/entrypoints/run-backend.sh`
- `docker/entrypoints/run-dev.sh`
- `docker/compose.dev.yml`
- `config/lunar_analyst.container.toml`
- `config/lunar_analyst.devcontainer.toml`
- `deploy/nrp/` manifests or a Helm/Kustomize equivalent
- `.dockerignore`

Optional but likely useful:

- `docker/README.md`
- `scripts/docker-build.sh`
- `scripts/docker-run-dev.sh`
- `scripts/docker-smoke.sh`

## Container Runtime Topology

### Local Development Topology

Recommended initial local container development topology:

1. `app-dev` container
   - built from `lunar-analyst-dev`
   - bind-mounts repo root
   - bind-mounts workspace root
   - exposes backend port
   - can run tests and backend commands
2. optional `marimo-dev` container or host-native Marimo
   - enabled only if active notebook workflow requires it
3. optional provider-side services
   - for example local Ollama, if used

The first slice does not need a separate container for the worker if the backend already spawns a separate worker process correctly inside the container.

### Production / NRP Topology

Recommended initial NRP topology:

1. one `Deployment` for the main backend/runtime container
2. one PVC mounted as `workspace_root`
3. one `Service` for HTTP traffic
4. one `Ingress` only if external browser access is needed
5. Marimo disabled initially unless there is a clear requirement to expose it

The first NRP slice should avoid over-distribution. The main goal is reliable deployment of the control plane with persistent scenario storage.

First-slice scaling policy:

- exactly one application replica
- no HPA/autoscaling
- no concurrent writer pods against the same `workspace_root`
- no active/active deployment shape

This is a normative constraint for the first slice, not just an implementation suggestion. The current authoritative state includes multiple SQLite/SpatiaLite databases under the mounted workspace, so multi-writer deployment is not accepted until explicit storage and locking validation says otherwise.

Later evolution options:

- separate Marimo deployment
- separate worker deployment if the worker lifecycle eventually requires stronger isolation
- job queues or explicit worker pools if compute scale-out becomes necessary

## Development Modes and Expected Use

### Mode A: Host-Native Pop!_OS

Use for:

- most code editing,
- rapid backend iteration,
- native debugging,
- Python and .NET troubleshooting,
- Codex-assisted editing in the normal repo.

This remains the default development mode.

### Mode B: Dev Container

Use for:

- validating Ubuntu package/runtime assumptions,
- checking that the repo runs with image-based dependencies,
- reproducing deployment-like path and config behavior,
- onboarding contributors who want a more packaged Linux environment.

This mode complements host-native development rather than replacing it.

### Mode C: Runtime Container

Use for:

- pre-release image validation,
- deployment smoke tests,
- NRP compatibility checks,
- operations-oriented debugging.

This mode should not be the normal edit loop.

## Volume and Mount Policy

### Required Persistent Mount

Mount the entire workspace root, for example:

- host path: `/srv/lunar-analyst/workspace`
- container path: `/var/lib/lunar-analyst/workspace`

and configure:

- `backend.workspace_root = "/var/lib/lunar-analyst/workspace"`

This one mount should contain:

- scenario directories
- `scenario_catalog.db`
- `.assistant/`

### Scenario Directories

Scenario directories should remain subdirectories within the mounted workspace root, not separate first-class mounts by default.

That preserves the existing architecture:

- single allowlisted root
- consistent relative-path handling
- simpler backup/snapshot procedures
- easier scenario discovery

### Additional Mounts

Persistent additional mounts are optional, not mandatory.

Possible optional mounts:

- config file mount if we do not bake config into image
- secrets mount for API keys or provider credentials
- scratch/temp mount if NRP policy wants large temp IO off the container filesystem

Not required as persistent volumes by default:

- repo source tree
- frontend build artifacts
- native binaries
- Python virtual environment
- system package state

These belong in the image, not in persistent storage.

### Development-Only Mounts

For local container-based development, additional bind mounts are recommended:

- repo root mounted read-write,
- optional pip/npm cache mounts for faster rebuilds,
- optional local scratch mount if large transient raster jobs should avoid bloating the writable container layer.

Recommended development mount contract:

- repo root:
  - host: `/e/projects/lunar_analyst`
  - container: `/workspace/lunar_analyst`
- workspace root:
  - host: `/e/lunar_analyst_scenarios`
  - container: `/var/lib/lunar-analyst/workspace`

Recommended production mount contract:

- only the persistent workspace PVC, plus optional config/secrets mounts

### Storage Class Guidance for NRP

Because Lunar Analyst writes many scenario artifacts and catalog databases, the PVC backing `workspace_root` should be selected deliberately.

Operational guidance:

- prefer one durable PVC for `workspace_root`
- prefer initial access mode `ReadWriteOnce`
- use faster scratch/ephemeral storage only for transient temp files if later profiling shows benefit
- keep authoritative scenario data and SQLite/SpatiaLite files on the durable mounted workspace

This matches the current architecture:

- `scenario.db`
- `scenario_catalog.db`
- `.assistant/rag/global_rag.db`

are all authoritative persisted state.

Storage validation requirements:

- verify SQLite WAL/journal behavior on the chosen storage class
- verify file-lock semantics under real pod restart conditions
- verify that abrupt pod termination does not leave the workspace in a non-recoverable state
- do not assume `ReadWriteMany` semantics are safe for these databases without explicit proof

If NRP storage constraints force a different access mode or storage class, that change requires explicit validation evidence before adoption.

### Backup and Restore Policy

The current ADR discussed persistence but did not define operational recovery. That is not sufficient for deployment.

For the first slice, the mounted `workspace_root` must have an explicit backup and restore plan covering:

- backup scope:
  - scenario directories
  - `scenario_catalog.db`
  - `.assistant/`
- backup cadence
- retention window
- restore target location and procedure
- operator validation that a restored workspace can boot the backend and rediscover scenarios correctly

Minimum operational requirement:

- one documented backup procedure
- one documented restore procedure
- one tested restore drill against a non-production workspace snapshot

The first NRP deployment should not be considered operationally ready without restore evidence.

### Logs

Default policy should be:

- write logs to stdout/stderr for Docker/Kubernetes collection

A dedicated persistent log volume is not required unless NRP operations demand file-based retention.

## Development Strategy

### Recommended Parallel Workflow

Use two supported Linux execution modes in parallel:

1. **Host-native Pop!_OS mode**
   - fastest iteration for local debugging
   - easiest access to local editors, GPU/native troubleshooting, and ad hoc scripts
2. **Dev-container mode**
   - validates that the code also runs inside an Ubuntu container
   - catches packaging and dependency drift early

The rule should be:

- do most coding host-native,
- regularly verify in the dev container,
- treat the runtime container as a release artifact, not the primary editing environment.

### Daily Developer Workflow

Recommended day-to-day sequence:

1. edit code host-native in the normal Pop!_OS checkout
2. run fast host-native tests first
3. start or reuse the dev container against the same checkout
4. rerun the relevant smoke/integration checks in the dev container
5. periodically build the runtime image to verify production packaging assumptions

This keeps the fastest loop local while continuously preventing container drift.

### Working Tree Policy During Development

During development, yes, the git working tree should be mounted into the dev container.

That is the cleanest way to keep:

- host-side editing,
- git operations,
- Codex operation on the normal workspace,
- live code execution in the container

aligned on the same files.

Recommended mount shape:

- repo root -> `/workspace/lunar_analyst`
- workspace root -> `/var/lib/lunar-analyst/workspace`

This keeps code and scenario state clearly separated.

### Frontend Development Policy

For frontend work, there are two acceptable development patterns:

1. run frontend tooling host-native and point it at a host-native or containerized backend
2. run frontend tooling inside the dev container against the bind-mounted repo

The first is likely faster on Pop!_OS. The second is useful when validating Node/package behavior inside Ubuntu.

The production runtime image should not depend on a live frontend dev server.

### Config Policy

Do not rely on ad hoc path edits between host and container.

Instead:

- add a dedicated Linux container config profile, for example `config/lunar_analyst.container.toml`
- keep path differences limited to config/env
- keep code paths platform-neutral

Host example:

- repo root: `/e/projects/lunar_analyst`
- workspace root: `/e/lunar_analyst_scenarios`

Container example:

- repo root: `/workspace/lunar_analyst` in dev, image-local in runtime
- workspace root: `/var/lib/lunar-analyst/workspace`

### Environment Parity Policy

Keep parity centered on:

- Python version,
- .NET version,
- GDAL/PROJ presence,
- config profile names and semantics,
- workspace path semantics,
- native library resolution behavior.

It is acceptable for host-native and dev-container workflows to differ in:

- exact repo path,
- shell tooling,
- filesystem performance characteristics,
- whether frontend tooling runs inside or outside the container.

## NRP Deployment Strategy

For NRP, prefer a Kubernetes-style deployment shape:

- container image: `lunar-analyst-runtime`
- persistent volume claim mounted at container `workspace_root`
- ConfigMap or mounted config file for non-secret settings
- Secret for provider/API credentials
- Service/Ingress as needed

Operationally:

- the container should be replaceable without losing scenarios or workspace-global metadata
- all persistent scientific artifacts and assistant workspace state live under the mounted workspace root
- the image remains immutable across deployments

### Recommended First NRP Slice

The first production-quality NRP deployment should aim for the smallest viable shape:

- backend/runtime image only
- one replica only
- one mounted PVC as `workspace_root`
- one config profile
- one Kubernetes `Secret`
- one HTTP `Service`
- ingress only if browser access is required
- Marimo disabled unless explicitly needed
- local-only provider integrations disabled unless intentionally supported in-cluster

This minimizes operational variables while proving the main deployment model.

### External Access Policy

Decide explicitly which surfaces are externally reachable:

- main browser/API surface may be exposed
- MCP HTTP/SSE should be exposed only if there is a real integration need
- Marimo should remain internal or disabled by default

Every externally reachable surface must have an explicit auth policy.

### Secrets Management on NRP

NRP Nautilus is documented as a Kubernetes-based environment, and the public user docs show standard Kubernetes deployment patterns:

- namespace-scoped workloads and manifests
- PVC-backed storage options
- environment variables populated from Kubernetes `Secret` objects via `secretKeyRef`

Relevant NRP examples:

- the Nautilus Kubernetes docs show normal container/job patterns with mounted storage and Kubernetes-native resource definitions
- the NRP GUI Desktop docs explicitly show `kubectl create secret generic ...` and `valueFrom.secretKeyRef` for container credentials

That means Lunar Analyst should follow standard Kubernetes secret handling on NRP rather than inventing a custom secret distribution path.

### Secret Categories

Separate secrets into three categories:

1. **Application secrets**
   - consumed directly by Lunar Analyst
2. **Deployment/infrastructure secrets**
   - used by Kubernetes or the image pipeline, not by app code
3. **Data volumes**
   - scenario data and workspace files are persistent data, not secrets by mechanism

### Application Secrets Actually Used by This App

#### 1. `OPENAI_API_KEY`

This is the primary app secret today when the configured remote provider is OpenAI.

Evidence:

- `config/lunar_analyst.toml` sets `backend.llm.remote.openai.api_key_env = "OPENAI_API_KEY"`
- `backend/services/assistant/provider_registry.py` wires the OpenAI provider from that env var

This key is required only if:

- the OpenAI provider is enabled, and
- the deployment intends to use OpenAI-backed assistant flows

Given the current config defaults, this is the most likely required secret for NRP.

#### 2. `ANTHROPIC_API_KEY`

Optional, not currently enabled by default.

Evidence:

- `config/lunar_analyst.toml` sets `backend.llm.remote.anthropic.api_key_env = "ANTHROPIC_API_KEY"`
- `backend/services/assistant/provider_registry.py` supports Anthropic as an optional remote provider

Needed only if Anthropic is enabled in the deployment config.

#### 3. `GOOGLE_API_KEY`

Optional, not currently enabled by default.

Evidence:

- `config/lunar_analyst.toml` sets `backend.llm.remote.google.api_key_env = "GOOGLE_API_KEY"`
- `backend/services/assistant/provider_registry.py` supports Google as an optional remote provider

Needed only if Google/Gemini is enabled in the deployment config.

#### 4. `LUNAR_ANALYST_MCP_TOKEN`

Optional, but important if the MCP HTTP/SSE endpoint is exposed.

Evidence:

- `config/lunar_analyst.toml` sets both:
  - `backend.mcp.http_auth_token_env = "LUNAR_ANALYST_MCP_TOKEN"`
  - `backend.llm.codex_cli.mcp_auth_token_env = "LUNAR_ANALYST_MCP_TOKEN"`
  - `backend.llm.gemini_cli.mcp_auth_token_env = "LUNAR_ANALYST_MCP_TOKEN"`
- `backend/api/routers/mcp.py` enforces bearer/header auth when that env var resolves to a token
- `backend/services/assistant/providers/external_mcp_cli_provider.py` reads the same token for outbound MCP CLI integration

This token is required if:

- we expose MCP over HTTP/SSE beyond a trusted localhost-only path, or
- we want local/sidecar CLI providers to authenticate to that MCP endpoint consistently

For an NRP deployment, this should generally be treated as required whenever MCP is enabled over network-accessible endpoints.

### What Secrets Are Not Currently Needed by the App

There are no active codepaths showing a requirement for:

- PostgreSQL passwords
- Redis passwords
- S3/MinIO access keys
- broker credentials
- separate RAG database credentials

That is because the current architecture keeps authoritative state in:

- scenario-local `scenario.db`
- workspace-global `scenario_catalog.db`
- workspace-global `.assistant/rag/global_rag.db`

all stored on the mounted filesystem under `workspace_root`.

So the answer to "are there more secrets than the OpenAI key?" is:

- **yes**, potentially
- but the additional app secrets are currently limited to:
  - `LUNAR_ANALYST_MCP_TOKEN`
  - optional `ANTHROPIC_API_KEY`
  - optional `GOOGLE_API_KEY`

There is not currently evidence of a broader secret inventory for the app itself.

### Optional or Future Secrets

#### Marimo token/auth

`config/lunar_analyst.toml` includes `backend.marimo.use_token_auth`, currently set to `false`.

Implication:

- Marimo token auth is a possible future deployment concern
- it is not currently an established required deploy-time secret in the Linux/container profile

If Marimo is exposed in NRP:

- either keep it internal-only and avoid separate external auth material
- or front it with ingress auth and/or a dedicated secret-managed token strategy

#### Ingress or SSO proxy credentials

If NRP deployment adds:

- OAuth2 proxy
- basic auth at ingress
- institutional SSO integration

those credentials are deployment secrets, but they are not currently Lunar Analyst application secrets.

#### Image pull credentials

If the runtime image is stored in a private registry, `imagePullSecrets` may be required.

Those are Kubernetes deployment secrets, not app-consumed secrets.

### Recommended NRP Secret Handling

#### Primary rule

Use Kubernetes `Secret` objects and inject secrets into the container as environment variables, matching the app’s existing `*_env` config contract.

That aligns with:

- NRP’s Kubernetes deployment model
- NRP’s documented `secretKeyRef` usage
- the current Lunar Analyst config design

First-slice rule:

- env-var injection from Kubernetes `Secret` objects is the primary and preferred mechanism
- secret volume mounts are not the default mechanism for first-slice NRP deployment
- any later move to file-mounted secrets should happen only if a concrete provider or platform integration requires it

#### Recommended secret layout

Create one namespace-local secret for app runtime, for example:

- name: `lunar-analyst-secrets`

Suggested keys:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY` only if enabled
- `GOOGLE_API_KEY` only if enabled
- `LUNAR_ANALYST_MCP_TOKEN` if MCP HTTP/SSE is enabled

Then inject them with `env.valueFrom.secretKeyRef`.

#### Do not store secrets in:

- container images
- checked-in config files
- the mounted `workspace_root`
- scenario directories
- `scenario.db`
- `.assistant/`

The workspace mount is for persistent scientific data and assistant state, not for credentials.

#### Rotation policy

Because the secret surfaces are env-var based, rotation can be handled by:

- updating the Kubernetes `Secret`
- restarting the Deployment/Pod

For the current app architecture, restart-based rotation is acceptable and simpler than hot-reload machinery.

### Exposure Recommendations

#### Minimum-secret deployment

For the smallest operational footprint on NRP:

- enable only one remote provider
- if that is OpenAI, inject only `OPENAI_API_KEY`
- disable unused remote providers
- keep MCP HTTP/SSE disabled or internal-only unless there is a concrete integration need

That keeps the practical secret set to one item:

- `OPENAI_API_KEY`

#### Hardened deployment

If MCP is exposed outside the pod:

- require `LUNAR_ANALYST_MCP_TOKEN`
- pass it via Kubernetes `Secret`
- do not leave the MCP endpoint unauthenticated

If multiple model vendors are enabled:

- keep each vendor key as a separate key in the same secret or in separate secrets
- only inject the env vars actually needed by the enabled providers

### Proposed ADR Policy for Secrets

For Lunar Analyst on NRP:

- app secrets are injected exclusively through Kubernetes `Secret` objects
- the mounted workspace root must never contain credential material
- `OPENAI_API_KEY` is the default required secret when OpenAI is enabled
- `LUNAR_ANALYST_MCP_TOKEN` becomes required when MCP HTTP/SSE is externally reachable
- `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY` remain optional provider-specific secrets
- deployment-specific secrets such as `imagePullSecrets` are managed separately from app runtime secrets

### Sources

- NRP Nautilus Kubernetes docs: `https://nationalresearchplatform.org/documentation/userdocs/running/kubernetes/`
- NRP Nautilus GUI Desktop docs showing `kubectl create secret generic` and `secretKeyRef` usage: `https://nationalresearchplatform.org/documentation/userdocs/running/gui-desktop/`

## Implementation Strategy

### Phase A: Preparatory Alignment of Host and Container Development

This phase happens before adding the container-based development workflow. Its purpose is to make host-native development and future container execution converge on the same runtime contract.

#### A0: Contract and Scope Freeze

Before writing Docker/Kubernetes assets:

- ratify this ADR
- confirm the initial deployment excludes Tauri packaging
- confirm whether Marimo is in or out for the first container deployment
- confirm whether OpenAI is the only enabled remote provider
- confirm whether MCP HTTP/SSE needs to be exposed externally

Deliverables:

- approved ADR
- explicit first-slice scope note in the implementation ticket/plan

#### A1: Path and Config Normalization

Goals:

- ensure path behavior is fully config-driven
- eliminate hidden dependence on the repo checkout path
- define dedicated container config profiles

Required changes:

- add `config/lunar_analyst.container.toml`
- add `config/lunar_analyst.devcontainer.toml`
- ensure `workspace_root` is overridden cleanly by config/env
- ensure no active runtime path assumes `/e/...` or Windows drive paths
- verify assistant, RAG, and catalog paths remain rooted under configured `workspace_root`

Acceptance:

- backend can start against container config without code edits
- host-native Pop!_OS development still works as before
- a scenario created in container config writes only under container `workspace_root`

#### A2: Host-Native Development Alignment

Goals:

- align the existing Pop!_OS workflow with the container runtime contract before Docker is introduced into the daily loop

Required changes:

- make host-native launch paths use the same config semantics planned for containers
- document the host-native Linux workflow as the control baseline
- identify and remove any assumptions that code, temp files, logs, or generated state live under the repo checkout rather than under configured roots
- verify that frontend, backend, worker, and optional Marimo flows can all be described in platform-neutral path terms

Acceptance:

- host-native development remains the default path
- host-native documentation and config match the same runtime assumptions that containers will use
- there is no hidden requirement to colocate repo checkout and workspace root

#### A3: Base Image Construction

Goals:

- create the stable Ubuntu dependency image that serves as the container analogue of `env_311`

Required changes:

- add `docker/Dockerfile.base`
- pin Ubuntu version
- install Python 3.11
- install .NET 9 SDK/runtime
- install GDAL/PROJ runtime dependencies
- install other Linux shared libraries needed by the backend/native path

Verification:

- image build succeeds reproducibly
- probe commands succeed for:
  - `python --version`
  - `dotnet --info`
  - Python imports: `fastapi`, `rasterio`, `osgeo`, `pythonnet`

Acceptance:

- base image can serve as the parent for both dev and runtime images

#### Phase A Exit Criteria

Phase A is complete when:

- host-native Pop!_OS development is still intact
- path and config behavior are container-compatible
- the Ubuntu base image exists and passes runtime probes
- the repo is ready to add container-based development without first discovering more path/runtime drift

### Phase B: Add Container-Based Development Workflow

This phase adds the new development workflow on top of the aligned host/runtime contract established in Phase A. It does not replace host-native development.

#### B1: Dev Image and Local Compose Workflow

Goals:

- enable container-based development without replacing host-native development

Required changes:

- add `docker/Dockerfile.dev`
- add `docker/compose.dev.yml`
- add dev entrypoint/wrapper scripts
- mount repo root and workspace root
- expose backend port and optional frontend/Marimo ports

Recommended Compose behavior:

- service `app-dev`
- working directory `/workspace/lunar_analyst`
- repo bind-mounted read-write
- workspace root bind-mounted read-write
- optional named caches for pip/npm

Acceptance:

- a developer can start the dev container and run backend commands against the bind-mounted checkout
- Codex and normal git operations remain on the host checkout as usual
- scenario data persists outside the container lifecycle

#### B2: Dev Workflow Verification

Goals:

- prove that container-based development is a real, usable complement to host-native development

Required checks:

- backend starts from the bind-mounted checkout inside the dev container
- targeted pytest slice passes inside the dev container
- representative scenario job runs successfully inside the dev container
- developer can switch between host-native and dev-container workflows without changing the scenario storage contract

Acceptance:

- container-based development is documented and operational
- the dev container catches real Ubuntu/runtime issues without requiring a new primary development model

#### Phase B Exit Criteria

Phase B is complete when:

- container-based development works from a bind-mounted checkout
- host-native development remains the default and is not degraded
- both workflows are documented as parallel supported modes

### Downstream Delivery Work After Phase B

The following work depends on Phases A and B, but is not itself part of the two-phase host/container alignment effort.

#### C1: Runtime Image

Goals:

- produce an immutable production-style image

Required changes:

- add `docker/Dockerfile.runtime`
- copy source into image
- install Python dependencies in image
- build frontend assets in image or in an earlier build stage
- add runtime entrypoint
- set default config path for container runtime

Acceptance:

- runtime image starts without bind-mounted source
- runtime image serves the built frontend and backend APIs
- runtime image writes persistent state only to mounted `workspace_root`

#### C2: Local Runtime Smoke and Persistence Tests

Goals:

- verify production-style container behavior before touching NRP

Required checks:

- start runtime image locally with mounted workspace root
- verify scenario discovery/list/create
- verify `scenario_catalog.db` survives restart
- verify `.assistant/rag/global_rag.db` survives restart
- verify representative scenario job writes outputs under scenario root
- verify logs go to stdout/stderr

Acceptance:

- local runtime container behaves correctly across stop/start cycles

#### C3: NRP Kubernetes Assets

Goals:

- define a deployable first-slice NRP package

Required changes:

- add `deploy/nrp/namespace-notes.md` or equivalent instructions
- add deployment manifest(s)
- add service manifest
- add ingress manifest if needed
- add PVC manifest or PVC usage instructions
- add ConfigMap or mounted config-file pattern
- add Secret consumption pattern

Required manifest concerns:

- container image reference
- mounted `workspace_root`
- replica count fixed to `1`
- deployment strategy chosen to avoid accidental overlapping writers during rollout
- resource requests/limits
- liveness/readiness probes
- env vars for secrets
- optional node selectors if future GPU/native workloads require them

Acceptance:

- manifests are sufficient to deploy the backend/runtime image in an NRP namespace

#### C4: NRP Deployment Validation

Goals:

- prove the deployment model on the target platform

Required checks:

- pod starts successfully
- readiness probe passes
- workspace PVC is mounted correctly
- deployment runs with exactly one replica
- rollout behavior does not create overlapping writer pods against the same workspace
- scenario create/list/discovery works
- persistent state survives pod replacement
- externally exposed endpoints behave as expected
- configured secrets are visible only through env injection and not written into workspace storage
- backup procedure completes successfully
- restore drill succeeds against a snapshot of the workspace

Acceptance:

- one documented NRP deployment path works end-to-end

#### C5: CI Integration

Goals:

- prevent future drift between host and container workflows

Required additions:

- container build CI for `Dockerfile.base`
- container build CI for `Dockerfile.runtime`
- smoke test lane that starts the runtime image and runs a small API check set
- keep Windows and host-Linux lanes as baseline controls

Acceptance:

- image builds and runtime smoke tests run automatically

## Verification Matrix

### Host-Native Pop!_OS

Must verify:

- backend startup
- targeted pytest slice
- representative scenario job

### Dev Container

Must verify:

- backend startup from bind-mounted repo
- same targeted pytest slice
- same representative scenario job

### Runtime Container

Must verify:

- startup from immutable image
- workspace-root persistence across restart
- scenario and RAG/catalog persistence

### NRP Deployment

Must verify:

- pod readiness
- PVC mount behavior
- single-replica enforcement
- API reachability
- secret injection
- persistence across pod replacement
- backup/restore drill

## Definition of Done

This ADR’s plan is considered implemented when:

- host-native Pop!_OS development remains fully usable
- Phase A preparatory alignment is complete
- Phase B container-based development workflow is documented and operational
- runtime image can be built reproducibly
- one local production-style container smoke flow passes
- one NRP deployment path is documented and validated
- workspace-root persistence and secret handling follow the policies in this ADR
- single-writer deployment constraints are documented and enforced for the first slice
- backup and restore procedures are documented and exercised

## Rollout and Risk Control

### Rollout Order

1. Phase A: contract/scope freeze
2. Phase A: path and config normalization
3. Phase A: host-native development alignment
4. Phase A: base image
5. Phase B: dev image and local compose workflow
6. Phase B: dev workflow verification
7. downstream runtime image
8. downstream local runtime smoke
9. downstream NRP deployment
10. downstream CI automation

### Risks

- Linux native dependency mismatch in image vs host
- SQLite/SpatiaLite behavior differences on PVC-backed storage
- accidental multi-writer rollout or scaling against a shared workspace
- hidden repo-path assumptions
- overexposing MCP or Marimo without explicit auth posture
- container startup scripts drifting from host startup behavior
- backup/restore procedure not actually restoring a usable workspace

### Mitigations

- keep host-native development as the control baseline
- keep scope small in the first deployment slice
- validate persistence early with restart tests
- lock the first NRP slice to one replica and one writer
- validate storage locking semantics on the chosen PVC class before scaling assumptions change
- keep secrets env-driven and Kubernetes-native
- test restore from backup before declaring the deployment operational
- avoid introducing a separate container-only codepath unless required

## Out of Scope for First Slice

- replacing host-native Pop!_OS development
- Tauri packaging in containers
- distributed worker pools
- autoscaling compute architecture
- multi-tenant auth redesign
- high-availability database replacements for current filesystem-backed state

## Consequences

Positive:

- host and container workflows converge on the same path model
- production persistence matches the existing workspace-root architecture
- container images become reproducible and mostly immutable
- development remains fast because the git working tree stays editable on the host
- the repo gains a concrete path from local Ubuntu parity testing to NRP deployment

Tradeoffs:

- maintaining a base image adds some release/process overhead
- maintaining dev and runtime container workflows adds documentation and CI overhead
- initial deployment will likely exclude some optional surfaces such as Marimo until they are proven safe and necessary
- workspace-root mount means global assistant/catalog state is intentionally shared across all scenarios in one deployment
- dev and runtime images add one more layer of packaging discipline that the repo does not yet fully have
- operational scaling is intentionally constrained in the first slice because authoritative state remains SQLite/SpatiaLite-backed

## Explicit Answers

### Where should scenario directories be mounted?

Inside a single persistent mounted `workspace_root`, not as isolated mounts by default.

### Is that the only volume mount needed?

No. The required persistent mount is the full `workspace_root`, because it also contains workspace-global state such as `scenario_catalog.db` and `.assistant/`. Additional persistent mounts are optional for config/secrets/temp policy, but not required for core runtime behavior.

### Is there a base Ubuntu container that plays the same role as `env_311`?

There should be. We should create one. It should be a relatively static Ubuntu base image containing Python 3.11 plus the OS/runtime dependencies needed by Lunar Analyst.

### During development, should the git working tree be mounted so Codex can operate as usual?

Yes, for development containers. No, for production/NRP runtime containers.

## Out of Scope

- Exact Dockerfile syntax
- Exact NRP manifest syntax
- Final native-compute packaging details for every moonlib/CSPICE artifact
- Tauri packaging changes
