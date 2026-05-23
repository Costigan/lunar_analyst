# Container Notes

This file is the Phase A/B/C containerization surface guide for Lunar Analyst.

## First-Slice Scope Assumptions

These are the implementation assumptions captured from `docs/ADR.0041.parallel_popos_and_ubuntu_container_development.md` so Phase A can land as code:

- Tauri packaging is out of scope.
- Marimo is disabled by default in container configs.
- OpenAI is the enabled remote provider in the container-oriented configs.
- MCP stdio remains available, while HTTP/SSE are disabled by default in the runtime-oriented container profile.

## Host-Native Linux Baseline

Host-native Pop!_OS development remains the control baseline.

- Use the normal git checkout on the host.
- Keep `workspace_root` outside the repo checkout.
- Use `config/lunar_analyst.toml` for host-native Linux.
- Use `config/lunar_analyst.devcontainer.toml` only when validating the container-shaped path contract locally.

Start the host-native backend with:

```bash
./scripts/run-host-dev.sh
```

## Base Image

`docker/Dockerfile.base` is the Phase A Ubuntu dependency image. It is the container analogue of the project Python environment and includes:

- Ubuntu 22.04
- Python 3.11
- .NET 9 SDK
- GDAL/PROJ native packages
- Python probe imports used by Lunar Analyst (`fastapi`, `rasterio`, `osgeo`, `pythonnet`)

Build:

```bash
docker build -f docker/Dockerfile.base -t lunar-analyst-base .
```

Probe:

```bash
docker run --rm lunar-analyst-base
```

## Dev Container Workflow

Phase B adds a bind-mounted development container that preserves the host-native checkout and the external workspace-root contract.

Added Phase B files:

- `docker/Dockerfile.dev`
- `docker/compose.dev.yml`
- `docker/entrypoints/run-dev.sh`
- `docker/entrypoints/docker-smoke.sh`
- `scripts/docker-build.sh`
- `scripts/docker-down.sh`
- `scripts/docker-run-dev.sh`
- `scripts/docker-smoke.sh`

Mount contract:

- repo checkout: host checkout -> `/workspace/lunar_analyst`
- workspace root: `${LUNAR_ANALYST_HOST_WORKSPACE_ROOT:-/e/lunar_analyst_scenarios}` -> `/var/lib/lunar-analyst/workspace`
- container entrypoint creates a matching in-container user from `${LUNAR_ANALYST_DEV_UID}` / `${LUNAR_ANALYST_DEV_GID}` and then drops privileges to it
- dev caches: `/var/lib/lunar-analyst/workspace/.container-cache/`

Build both images:

```bash
./scripts/docker-build.sh
```

Open a dev shell:

```bash
./scripts/docker-run-dev.sh
```

The wrapper exports the host UID/GID before invoking Compose so files created in bind-mounted repo/workspace paths remain editable on the host.
`docker-run-dev.sh` launches a one-off interactive dev container with service ports exposed via `docker compose run --rm --service-ports`.
It drops you directly into a shell inside that container. You should see a prompt like:

```bash
lunar@<container-id>:/workspace/lunar_analyst$
```

When you exit that shell, the one-off container is removed.
It does not mutate the checkout on startup; dependency preparation is performed by the smoke path or manually from inside the shell.

Run the Phase B smoke checks in the container:

```bash
./scripts/docker-smoke.sh
```

Or, after `./scripts/docker-run-dev.sh`, run this directly inside the container shell:

```bash
/usr/local/bin/docker-smoke.sh
```

Tear down the compose-managed resources:

```bash
./scripts/docker-down.sh
```

To remove named Docker resources as well:

```bash
./scripts/docker-down.sh --volumes
```

The smoke script performs the in-container dependency preparation explicitly:

- `pip install -e ./moonlayers_pkg`
- `npm ci` in `backend/web/lunar_analyst`
- `npm ci` in `moonlayers_pkg`

Inside the dev container, the normal backend path is:

```bash
python -m uvicorn backend.api.app:app --host 0.0.0.0 --port 8000 --reload
```

The dev container keeps:

- the git working tree on the host
- scenario data outside container lifecycle
- the same `workspace_root` contract used by Phase A container configs

## Runtime Container Workflow

Phase C adds the immutable runtime image and the local production-style smoke path.

Added files:

- `docker/Dockerfile.runtime`
- `docker/entrypoints/run-backend.sh`
- `scripts/docker-run-runtime.sh`
- `scripts/docker-runtime-smoke.sh`
- `deploy/nrp/`

Build all three images:

```bash
./scripts/docker-build.sh
```

Run the runtime image locally with only the persistent workspace mounted:

```bash
./scripts/docker-run-runtime.sh
```

Default runtime contract:

- image: `lunar-analyst-runtime`
- config path in image: `/opt/lunar-analyst/config/lunar_analyst.container.toml`
- mounted workspace root: `${LUNAR_ANALYST_RUNTIME_WORKSPACE_ROOT:-${LUNAR_ANALYST_HOST_WORKSPACE_ROOT:-/e/lunar_analyst_scenarios}}` -> `/var/lib/lunar-analyst/workspace`
- backend entrypoint: `python -m uvicorn backend.api.app:app --host 0.0.0.0 --port 8000`

Run the Phase C local production-style smoke checks:

```bash
./scripts/docker-runtime-smoke.sh
```

That smoke flow verifies:

- immutable-image startup without a bind-mounted repo
- frontend HTML served from built Vite assets
- backend API health
- scenario creation and restart-time rediscovery from `scenario_catalog.db`
- RAG DB creation and persistence at `.assistant/rag/global_rag.db`
- one representative raster job writing under the scenario root
- logs emitted to container stdout/stderr

## NRP Manifests

Phase C also adds first-slice NRP deployment assets under `deploy/nrp/`:

- `runtime-pvc.yaml`
- `runtime-configmap.yaml`
- `runtime-deployment.yaml`
- `runtime-service.yaml`
- `runtime-ingress.optional.yaml`
- `namespace-notes.md`

The first-slice NRP policy is intentionally conservative:

- one replica only
- `Recreate` rollout strategy
- one PVC mounted as `/var/lib/lunar-analyst/workspace`
- ConfigMap-mounted runtime config
- secret injection through env refs (`OPENAI_API_KEY`, `LUNAR_ANALYST_MCP_TOKEN`)
