# Horizon Container (native/new_horizon/horizon)

This directory contains a standalone .NET 10 container build for the `horizon` CLI.

## What It Does

The `horizon` program supports two verbs:

- `make`: Generate horizon patch files from DEM inputs.
- `psr`: Generate a permanent shadow raster (GeoTIFF) from an existing horizons directory.

Both workflows assume all input/output paths are under a single mounted data root.

## Build Image

From anywhere in the repo:

```bash
bash native/new_horizon/horizon/scripts/build-image.sh
```

Optional custom tag:

```bash
bash native/new_horizon/horizon/scripts/build-image.sh lunar-horizon:dev
```

## Local Run

`run-local.sh` enforces a single-root model and GPU use.

- Mounts: `<host_data_root>:/workspace`
- Requires relative paths under `<host_data_root>`
- Always uses `docker run --gpus all`

### Generate Horizons (`make`)

`make` shards patch processing by zero-based patch-list index:

```text
patch_index % stride == offset
```

```bash
bash native/new_horizon/horizon/scripts/run-local.sh \
  /e/lunar_analyst_docker_test/haworth \
  make \
  horizons \
  0 \
  16 \
  dems/haworth.tif \
  dems/LDEM_80S_20M-2017-06-15-processed.tif
```

Equivalent in-container args:

```text
horizon make /workspace/horizons 0 16 /workspace/dems/haworth.tif /workspace/dems/LDEM_80S_20M-2017-06-15-processed.tif
```

### Generate PSR (`psr`)

```bash
bash native/new_horizon/horizon/scripts/run-local.sh \
  /e/lunar_analyst_docker_test/haworth \
  psr \
  horizons \
  dems/haworth.tif \
  lighting/psr.tif
```

Equivalent in-container args:

```text
horizon psr /workspace/horizons /workspace/dems/haworth.tif /workspace/lighting/psr.tif
```

## Prerequisites

- Docker with NVIDIA runtime support (`--gpus all` must work).
- Host GPU driver and toolkit configured (verify with `nvidia-smi` and a CUDA test container).
- DEM and horizon paths must exist under the single `host_data_root` tree.

## Nautilus Notes

For NRP Nautilus deployment, use a Kubernetes `Job` (or `CronJob`) instead of `docker run`.

- Publish image to a registry Nautilus can pull from.
- Request GPU explicitly, e.g. `resources.limits.nvidia.com/gpu: 1`.
- Mount one PVC at `/workspace`.
- Keep all DEM/horizons/output paths under that PVC tree.
- Pass args exactly as the CLI expects (`make` or `psr` with absolute `/workspace/...` paths).
- Set `restartPolicy: Never`, `backoffLimit`, and resource requests/limits appropriate for workload size.
- Add `nodeSelector` / affinity / tolerations required by your Nautilus GPU node policy.

Minimal container command shape in a Job:

```yaml
command: ["/app/horizon"]
args:
  - "make"
  - "/workspace/haworth/horizons"
  - "0"
  - "1000"
  - "/workspace/haworth/dems/haworth.tif"
  - "/workspace/haworth/dems/LDEM_80S_20M-2017-06-15-processed.tif"
```

or

```yaml
command: ["/app/horizon"]
args:
  - "psr"
  - "/workspace/haworth/horizons"
  - "/workspace/haworth/dems/haworth.tif"
  - "/workspace/haworth/lighting/psr.tif"
```

## Troubleshooting

- `Unable to load shared library 'cspice'`:
  Ensure image was rebuilt after Dockerfile updates and that `libcspice.so` is present in the image output.
- `You must install or update .NET`:
  Ensure `TargetFramework` and runtime image major versions match (this project is `net10.0`).
- Low/no GPU utilization:
  Confirm container has GPU access and check workload size (small patch counts may not saturate GPU).
