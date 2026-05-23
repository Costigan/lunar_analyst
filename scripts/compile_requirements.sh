#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${LUNAR_ANALYST_REQUIREMENTS_PYTHON:-python3.11}"
FORCE=0
EPSILON_SECONDS="${LUNAR_ANALYST_REQUIREMENTS_EPSILON_SECONDS:-2}"
WORK_DIR="$(mktemp -d)"
TOOLS_VENV="$WORK_DIR/pip-tools-venv"
TOOLS_PYTHON=

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      FORCE=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ $FORCE -ne 1 && -f "$ROOT_DIR/requirements.txt" ]]; then
  if python3 - <<'PY' "$ROOT_DIR/requirements.in" "$ROOT_DIR/requirements.txt" "$EPSILON_SECONDS"
from pathlib import Path
import sys

requirements_in = Path(sys.argv[1])
requirements_txt = Path(sys.argv[2])
epsilon = float(sys.argv[3])
delta = requirements_in.stat().st_mtime - requirements_txt.stat().st_mtime
raise SystemExit(0 if delta <= epsilon else 1)
PY
  then
    echo "requirements.txt is up to date"
    exit 0
  fi
fi

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

"$PYTHON_BIN" -m venv "$TOOLS_VENV"
TOOLS_PYTHON="$TOOLS_VENV/bin/python"

"$TOOLS_PYTHON" -m pip install --upgrade pip wheel setuptools
"$TOOLS_PYTHON" -m pip install pip-tools
"$TOOLS_PYTHON" -m piptools compile \
  --resolver=backtracking \
  --strip-extras \
  --no-header \
  --no-emit-index-url \
  --output-file "$ROOT_DIR/requirements.txt" \
  "$ROOT_DIR/requirements.in"

python3 - <<'PY' "$ROOT_DIR/requirements.txt"
from pathlib import Path
import sys

path = Path(sys.argv[1])
content = path.read_text(encoding="utf-8")
header = (
    "# Generated from requirements.in by scripts/compile_requirements.sh.\n"
    "# Install from this file. Edit requirements.in, then regenerate.\n\n"
)
path.write_text(header + content, encoding="utf-8")
PY
