from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class ModuleMetrics:
    path: str
    total_lines: int
    non_empty_lines: int
    function_defs: int
    class_defs: int
    import_fan_out: int


def _module_metrics(path: Path) -> ModuleMetrics:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    function_defs = 0
    class_defs = 0
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_defs += 1
        elif isinstance(node, ast.ClassDef):
            class_defs += 1
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return ModuleMetrics(
        path=str(path),
        total_lines=len(lines),
        non_empty_lines=sum(1 for line in lines if line.strip()),
        function_defs=function_defs,
        class_defs=class_defs,
        import_fan_out=len(imports),
    )


def _default_targets(root: Path) -> list[Path]:
    return [
        root / "backend/api/dependencies.py",
        root / "backend/jobs/handlers.py",
        root / "backend/services/assistant/tool_registry.py",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect baseline modularization metrics for ADR.0049.")
    parser.add_argument("--repo-root", default=".", help="Repository root path.")
    parser.add_argument("--json-out", help="Optional path to write JSON metrics.")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    targets = _default_targets(root)
    metrics = [_module_metrics(target) for target in targets]
    payload = {"modules": [asdict(item) for item in metrics]}

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("| Module | Total Lines | Non-Empty Lines | Function Defs | Class Defs | Import Fan-Out |")
    print("|---|---:|---:|---:|---:|---:|")
    for item in metrics:
        print(
            f"| `{item.path}` | {item.total_lines} | {item.non_empty_lines} | "
            f"{item.function_defs} | {item.class_defs} | {item.import_fan_out} |"
        )


if __name__ == "__main__":
    main()
