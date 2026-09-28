"""Static import-graph builder for Python components.

Computes, for every test file, the transitive set of local modules it
imports, then inverts that into: source_file -> {test ids that depend on it}.
Pure stdlib (ast) — no extra dependency, works offline in CI.
"""

from __future__ import annotations

import ast
from pathlib import Path

from reflex.models import TestUnit


def _module_to_path(root: Path, module: str) -> Path | None:
    """Best-effort resolution of a dotted module name to a file under root."""
    candidate = root / Path(*module.split("."))
    if candidate.with_suffix(".py").exists():
        return candidate.with_suffix(".py")
    if (candidate / "__init__.py").exists():
        return candidate / "__init__.py"
    return None


def _local_imports(file_path: Path) -> set[str]:
    try:
        tree = ast.parse(file_path.read_text(), filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _transitive_closure(root: Path, start_file: Path, max_depth: int = 6) -> set[Path]:
    seen: set[Path] = set()
    frontier = {start_file}
    for _ in range(max_depth):
        next_frontier: set[Path] = set()
        for f in frontier:
            if f in seen:
                continue
            seen.add(f)
            for mod in _local_imports(f):
                resolved = _module_to_path(root, mod)
                if resolved is not None and resolved not in seen:
                    next_frontier.add(resolved)
        if not next_frontier:
            break
        frontier = next_frontier
    return seen


def build_python_graph(root: Path, tests: list[TestUnit]) -> dict[str, set[str]]:
    """Return source_file (relative to root) -> set of test ids depending on it."""
    graph: dict[str, set[str]] = {}

    by_file: dict[str, list[TestUnit]] = {}
    for t in tests:
        by_file.setdefault(t.file, []).append(t)

    for file_rel, units in by_file.items():
        abs_path = root / file_rel
        if not abs_path.exists():
            continue
        closure = _transitive_closure(root, abs_path)
        for dep in closure:
            try:
                dep_rel = str(dep.relative_to(root))
            except ValueError:
                continue
            graph.setdefault(dep_rel, set()).update(u.id for u in units)

    return graph
