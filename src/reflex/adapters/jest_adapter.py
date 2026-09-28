"""Jest adapter: collection via `jest --listTests`, static graph via
`jest --listTests` per-file dependency resolution (deps computed by madge,
see reflex.graph.js_graph)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from reflex.graph.js_graph import build_js_graph
from reflex.models import TestUnit


class JestAdapter:
    name = "jest"

    def __init__(self, root: str | Path, jest_bin: str = "npx jest"):
        self.root = Path(root).resolve()
        self.jest_bin = jest_bin

    def collect(self) -> list[TestUnit]:
        cmd = self.jest_bin.split() + ["--listTests", "--json"]
        result = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True)
        try:
            files = json.loads(result.stdout)
        except json.JSONDecodeError:
            files = [
                line.strip() for line in result.stdout.splitlines() if line.strip()
            ]

        units: list[TestUnit] = []
        for f in files:
            rel = str(Path(f).relative_to(self.root)) if Path(f).is_absolute() else f
            units.append(
                TestUnit(id=rel, file=rel, title=Path(rel).stem, framework="jest")
            )
        return units

    def dependency_graph(self) -> dict[str, set[str]] | None:
        tests = self.collect()
        return build_js_graph(self.root, tests)

    def can_analyze(self, file_path: str) -> bool:
        return file_path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"))

    def run_command(self, selected_ids: list[str]) -> list[str]:
        cmd = self.jest_bin.split() + ["--testPathPattern"]
        pattern = "|".join(selected_ids)
        return cmd + [pattern]

    def full_run_command(self, junit_path: str) -> list[str]:
        """Requires the `jest-junit` reporter package + config pointing its
        output at $JEST_JUNIT_OUTPUT_NAME (set by the orchestrator via env
        before invoking this command). Documented requirement, not bundled —
        Reflex doesn't install dependencies into the target repo."""
        return self.jest_bin.split() + ["--reporters=default", "--reporters=jest-junit"]
