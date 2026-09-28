"""Static import-graph builder for JS/TS components, via `madge`.

Requires `madge` on PATH (npx madge). If it's not available or the call
fails, returns None so the orchestrator falls back to Laya for this
component instead of hard-failing the whole run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from reflex.models import TestUnit


def _run_madge(root: Path) -> dict[str, list[str]] | None:
    if shutil.which("npx") is None:
        return None
    result = subprocess.run(
        ["npx", "--yes", "madge", "--json", "--extensions", "ts,tsx,js,jsx,mjs,cjs", "."],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def build_js_graph(root: Path, tests: list[TestUnit]) -> dict[str, set[str]] | None:
    """Return source_file (relative to root) -> set of test ids depending on
    it, using madge's dependency graph. `madge --json .` returns
    file -> [files it imports]; we invert + transitively close it, then
    intersect with test files."""
    raw = _run_madge(root)
    if raw is None:
        return None

    # raw: importer -> [imported files]. Build reverse edges: imported -> [importers]
    reverse: dict[str, set[str]] = {}
    for importer, imported_list in raw.items():
        for imported in imported_list:
            reverse.setdefault(imported, set()).add(importer)

    test_files = {t.file for t in tests}

    def transitive_dependents(f: str, max_depth: int = 8) -> set[str]:
        seen: set[str] = set()
        frontier = {f}
        for _ in range(max_depth):
            nxt: set[str] = set()
            for cur in frontier:
                for dependent in reverse.get(cur, set()):
                    if dependent not in seen:
                        nxt.add(dependent)
            seen |= nxt
            if not nxt:
                break
            frontier = nxt
        return seen

    graph: dict[str, set[str]] = {}
    all_files = set(raw.keys())
    for src in all_files:
        dependents = transitive_dependents(src)
        affected_tests = {
            t.id for t in tests if t.file in dependents or t.file == src
        }
        if affected_tests:
            graph[src] = affected_tests

    return graph
