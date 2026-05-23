from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"

ALLOWED_MOONLIB_ATTRIBUTES = {
    "MoonlibBridge",
    "BridgeSmoke",
}


def _iter_backend_python_sources() -> list[Path]:
    return sorted(
        path
        for path in BACKEND_ROOT.rglob("*.py")
        if "tests" not in path.parts
    )


def test_production_backend_uses_moonlib_bridge_entry_surface_only() -> None:
    violations: list[str] = []

    for source_path in _iter_backend_python_sources():
        relative = source_path.relative_to(REPO_ROOT)
        module = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(module):
            if not isinstance(node, ast.Attribute):
                continue
            if not isinstance(node.value, ast.Name):
                continue
            if node.value.id != "moonlib":
                continue
            if node.attr in ALLOWED_MOONLIB_ATTRIBUTES:
                continue
            violations.append(f"{relative}:{node.lineno} uses moonlib.{node.attr}")

    if violations:
        detail = "\n".join(violations)
        raise AssertionError(
            "Production backend code must use moonlib via MoonlibBridge only. "
            "Allowed direct moonlib attributes are: "
            f"{', '.join(sorted(ALLOWED_MOONLIB_ATTRIBUTES))}. "
            "If a new exception is truly required, document it in docs/DESIGN.md "
            "and update this test with explicit rationale.\n"
            f"{detail}"
        )
