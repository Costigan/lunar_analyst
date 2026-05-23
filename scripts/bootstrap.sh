#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${LUNAR_ANALYST_VENV:-$ROOT_DIR/.venv}"
PYTHON_BIN="${LUNAR_ANALYST_BOOTSTRAP_PYTHON:-python3.11}"
REFRESH_REQUIREMENTS=0
SKIP_FRONTEND_BUILD=0
SKIP_SPACY_MODEL=0
SKIP_VERIFY=0
RECREATE_ENV=0

resolve_numpy_requirement() {
  local requirement
  requirement="$(grep -E '^numpy([[:space:]]|[<>=!~])' "$ROOT_DIR/requirements.txt" | head -n 1 || true)"
  if [[ -z "$requirement" ]]; then
    echo "numpy requirement not found in $ROOT_DIR/requirements.txt" >&2
    exit 1
  fi
  printf '%s\n' "$requirement"
}

install_python_numpy() {
  local numpy_requirement
  numpy_requirement="$(resolve_numpy_requirement)"
  "$ENV_PYTHON" -m pip install --no-cache-dir "$numpy_requirement"
}

install_python_gdal() {
  if ! command -v gdal-config >/dev/null 2>&1; then
    echo "gdal-config is required on Linux before running bootstrap.sh" >&2
    exit 1
  fi

  local gdal_version
  gdal_version="$(gdal-config --version)"
  local gdal_cflags
  gdal_cflags="$(gdal-config --cflags)"
  local gdal_include_dir="${gdal_cflags#-I}"

  CFLAGS="${CFLAGS:-} ${gdal_cflags}" \
  C_INCLUDE_PATH="${C_INCLUDE_PATH:-}${C_INCLUDE_PATH:+:}${gdal_include_dir}" \
  CPLUS_INCLUDE_PATH="${CPLUS_INCLUDE_PATH:-}${CPLUS_INCLUDE_PATH:+:}${gdal_include_dir}" \
    "$ENV_PYTHON" -m pip install --no-cache-dir --no-build-isolation "GDAL==${gdal_version}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --venv)
      VENV_DIR="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --recreate-env)
      RECREATE_ENV=1
      shift
      ;;
    --refresh-requirements)
      REFRESH_REQUIREMENTS=1
      shift
      ;;
    --skip-frontend-build)
      SKIP_FRONTEND_BUILD=1
      shift
      ;;
    --skip-spacy-model)
      SKIP_SPACY_MODEL=1
      shift
      ;;
    --skip-verify)
      SKIP_VERIFY=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ $RECREATE_ENV -eq 1 && -d "$VENV_DIR" ]]; then
  rm -rf "$VENV_DIR"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

ENV_PYTHON="$VENV_DIR/bin/python"

if [[ ! -f "$ROOT_DIR/requirements.txt" ]]; then
  LUNAR_ANALYST_REQUIREMENTS_PYTHON="$PYTHON_BIN" "$ROOT_DIR/scripts/compile_requirements.sh"
elif [[ $REFRESH_REQUIREMENTS -eq 1 ]]; then
  LUNAR_ANALYST_REQUIREMENTS_PYTHON="$PYTHON_BIN" "$ROOT_DIR/scripts/compile_requirements.sh" --force
fi

"$ENV_PYTHON" -m pip install --upgrade pip setuptools wheel
install_python_numpy
install_python_gdal
"$ENV_PYTHON" -m pip install -r "$ROOT_DIR/requirements.txt"
"$ENV_PYTHON" -m pip install -e "$ROOT_DIR/moonlayers_pkg"

if [[ $SKIP_SPACY_MODEL -ne 1 ]]; then
  "$ENV_PYTHON" -m spacy download en_core_web_sm
fi

if [[ $SKIP_FRONTEND_BUILD -ne 1 ]]; then
  (
    cd "$ROOT_DIR/backend/web/lunar_analyst"
    npm ci
    npm run build
  )
  (
    cd "$ROOT_DIR/moonlayers_pkg"
    npm ci
    npm run build
  )
fi

if [[ $SKIP_VERIFY -ne 1 ]]; then
  "$ENV_PYTHON" "$ROOT_DIR/scripts/verify_env.py"
fi
