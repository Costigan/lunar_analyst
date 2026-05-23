#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${LUNAR_ANALYST_DEV_WORKDIR:-/workspace/lunar_analyst}"
CONFIG_PATH="${LUNAR_ANALYST_DEV_CONFIG:-$WORKDIR/config/lunar_analyst.devcontainer.toml}"
WORKSPACE_ROOT="${LUNAR_ANALYST_WORKSPACE_ROOT:-/var/lib/lunar-analyst/workspace}"
PIP_CACHE_DIR="${PIP_CACHE_DIR:-$WORKSPACE_ROOT/.container-cache/pip}"
NPM_CACHE_DIR="${NPM_CACHE_DIR:-$WORKSPACE_ROOT/.container-cache/npm}"
HOME_DIR="${HOME:-/tmp/lunar-analyst-home}"
DEV_UID="${LUNAR_ANALYST_DEV_UID:-1000}"
DEV_GID="${LUNAR_ANALYST_DEV_GID:-1000}"
DEV_USER="lunar"
DEV_GROUP="lunar"
VENV_DIR="${VIRTUAL_ENV:-/opt/lunar-analyst/.venv}"

mkdir -p "$WORKSPACE_ROOT" "$PIP_CACHE_DIR" "$NPM_CACHE_DIR" "$HOME_DIR"
export HOME="$HOME_DIR"
export PIP_CACHE_DIR
export npm_config_cache="$NPM_CACHE_DIR"
export LUNAR_ANALYST_CONFIG_TOML="$CONFIG_PATH"

if [[ "$(id -u)" == "0" ]]; then
  if ! getent group "$DEV_GID" >/dev/null 2>&1; then
    groupadd --gid "$DEV_GID" "$DEV_GROUP"
  else
    DEV_GROUP="$(getent group "$DEV_GID" | cut -d: -f1)"
  fi

  if ! getent passwd "$DEV_UID" >/dev/null 2>&1; then
    useradd --uid "$DEV_UID" --gid "$DEV_GID" --home-dir "$HOME_DIR" --shell /bin/bash --create-home "$DEV_USER"
  else
    DEV_USER="$(getent passwd "$DEV_UID" | cut -d: -f1)"
  fi

  # Optimize: only chown if the top-level directory isn't already owned by the target user.
  # This avoids 20s+ delays on startup when cache/venv are already correct.
  for target_dir in "$HOME_DIR" "$PIP_CACHE_DIR" "$NPM_CACHE_DIR" "$VENV_DIR"; do
    if [[ -d "$target_dir" ]]; then
        if [[ "$(stat -c '%u:%g' "$target_dir")" != "$DEV_UID:$DEV_GID" ]]; then
            echo "run-dev.sh: fixing ownership of $target_dir (this may take a moment)..."
            chown -R "$DEV_UID:$DEV_GID" "$target_dir"
        fi
    fi
  done
fi

cd "$WORKDIR"

export PYTHONPATH="$WORKDIR:$WORKDIR/moonlayers_pkg${PYTHONPATH:+:$PYTHONPATH}"

if [[ $# -eq 0 ]]; then
  if [[ "$(id -u)" == "0" ]]; then
    exec gosu "$DEV_UID:$DEV_GID" bash
  fi
  exec bash
fi

if [[ "$(id -u)" == "0" ]]; then
  exec gosu "$DEV_UID:$DEV_GID" "$@"
fi

exec "$@"
