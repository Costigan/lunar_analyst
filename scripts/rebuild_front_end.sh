#!/usr/bin/env bash

# Rebuild Lunar Analyst front-end bundles (map + moonlayers)
# Usage: ./scripts/rebuild_front_end.sh
# Runs npm install (idempotent) then vite build in each frontend package.

set -euo pipefail

# Resolve repo root (script is in scripts/)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAP_DIR="$REPO_ROOT/backend/web/lunar_analyst"
MOONLAYERS_DIR="$REPO_ROOT/moonlayers_pkg"

echo "Rebuilding frontends from repo root: $REPO_ROOT"

echo "\n==> Rebuilding map frontend (lunar_analyst)..."
if [ -d "$MAP_DIR" ]; then
  echo "Installing map frontend dependencies..."
  npm --prefix "$MAP_DIR" install --no-audit --no-fund
  echo "Building map frontend..."
  npm --prefix "$MAP_DIR" run build
  echo "Map frontend build finished. Output: $MAP_DIR/dist"
else
  echo "Map frontend directory not found: $MAP_DIR" >&2
fi

echo "\n==> Rebuilding MoonLayers frontend (moonlayers_pkg)..."
if [ -d "$MOONLAYERS_DIR" ]; then
  echo "Installing moonlayers frontend dependencies..."
  npm --prefix "$MOONLAYERS_DIR" install --no-audit --no-fund
  echo "Building moonlayers frontend..."
  npm --prefix "$MOONLAYERS_DIR" run build
  echo "MoonLayers frontend build finished. Output: $MOONLAYERS_DIR/dist"
else
  echo "MoonLayers frontend directory not found: $MOONLAYERS_DIR" >&2
fi

echo "\nAll frontend builds complete."
