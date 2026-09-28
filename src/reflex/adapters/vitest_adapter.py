"""Vitest adapter — used e.g. by nova (design system) and forms-app.

`vitest list --json` gives an exact [{name, file, location}] listing
(verified against vitest 4.x). Static graph reuses the shared JS import
graph builder (madge) since vitest specs are ordinary TS/JS files.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from reflex.graph.js_graph import build_js_graph
from reflex.models import TestUnit


class VitestAdapter:
    name = "vitest"

    def __init__(self, root: str | Path, vitest_bin: str = "npx vitest"):
        self.root = Path(root).resolve()
        self.vitest_bin = vitest_bin

    def collect(self) -> list[TestUnit]:
        cmd = self.vitest_bin.split() + ["list", "--json"]
        result = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True)
        units: list[TestUnit] = []
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return units

        for entry in data:
            file_abs = entry.get("file", "")
            try:
                file_rel = str(Path(file_abs).relative_to(self.root))
            except ValueError:
                file_rel = file_abs
            name = entry.get("name", file_rel)
            units.append(
                TestUnit(id=f"{file_rel}::{name}", file=file_rel, title=name, framework="vitest")
            )
        return units

    def dependency_graph(self) -> dict[str, set[str]] | None:
        tests = self.collect()
        return build_js_graph(self.root, tests)

    def can_analyze(self, file_path: str) -> bool:
        return file_path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"))

    def run_command(self, selected_ids: list[str]) -> list[str]:
        files = sorted({tid.split("::", 1)[0] for tid in selected_ids})
        return self.vitest_bin.split() + ["run", *files]

    def full_run_command(self, junit_path: str) -> list[str]:
        """Vitest's junit reporter is built in — no extra package needed."""
        return self.vitest_bin.split() + ["run", "--reporter=junit", f"--outputFile={junit_path}"]
