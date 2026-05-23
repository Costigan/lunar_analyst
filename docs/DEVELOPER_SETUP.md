# Developer Setup

This document is the canonical repo-managed installation recipe for Lunar Analyst development.

## Baseline

- Linux is the maintained runtime baseline.
- Python `3.11.x` is required.
- .NET `9.0` is required for native `moonlib` development and tests.
- Node.js and npm are required for frontend builds.
- `spaCy` is required. The assistant segmenter depends on it, and the managed setup installs the `en_core_web_sm` model.

## Canonical Dependency Files

- `requirements.in`
- `requirements.txt`

Do not treat `moonlayers_pkg/requirements.txt` as an independent dependency list. It is only a compatibility wrapper back to the repo-root manifest.

Roles:

- `requirements.in` is the human-edited source of truth for direct Python dependencies and version bounds.
- `requirements.txt` is the generated install manifest used by the bootstrap scripts for the repo-managed Python packages.
- Linux `GDAL` is installed separately by `bootstrap.sh` as `GDAL==$(gdal-config --version)` so the Python binding matches the host `libgdal`.

Regenerate `requirements.txt` after editing `requirements.in`:

```bash
./scripts/compile_requirements.sh
```

Behavior:

- trusts the checked-in `requirements.txt` when its timestamp is the same as, or only slightly older than, `requirements.in`
- uses a small timestamp epsilon to avoid false recompiles after clone/checkouts
- use `./scripts/compile_requirements.sh --force` to regenerate unconditionally

Why these files live at the repo root:

- they describe repo-wide Python dependencies, not one specific script
- Python tools conventionally look for dependency manifests at the project root
- keeping them at the top level makes them easy to discover next to `README.md`, `package.json`, and the main source trees
- `scripts/` is for executable helpers; the dependency manifests are project data consumed by those helpers

## Linux Bootstrap

For host-native Linux development:

```bash
./scripts/bootstrap.sh
```

Default behavior:

- creates or reuses `.venv` under the repo root
- trusts the checked-in `requirements.txt` and installs from it
- installs Python `GDAL` first using the system `gdal-config` version
- regenerates `requirements.txt` only if it is missing
- installs the repo-managed Python dependency set
- installs `moonlayers_pkg` editable
- installs the required spaCy model `en_core_web_sm`
- builds both frontend bundles
- runs the same verification script

Useful flags:

- `./scripts/bootstrap.sh --recreate-env`
- `./scripts/bootstrap.sh --venv /path/to/venv`
- `./scripts/bootstrap.sh --python python3.11`
- `./scripts/bootstrap.sh --refresh-requirements`
- `./scripts/bootstrap.sh --skip-frontend-build`
- `./scripts/bootstrap.sh --skip-spacy-model`
- `./scripts/bootstrap.sh --skip-verify`

## Verification

The managed verification script checks:

- Python is `3.11.x`
- required packages import successfully
- the required spaCy model loads and produces sentence boundaries
- `osgeo.gdal_array` works via an in-memory `ReadAsArray()` roundtrip
- `moonlayers` imports from the editable package install

Run it directly with the environment’s Python if needed:

```bash
.venv/bin/python scripts/verify_env.py
```

## Frontend Only

When you only need to rebuild frontend assets:

```bash
npm run build:frontends
```
