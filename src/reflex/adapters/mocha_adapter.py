"""Mocha adapter: collection via `mocha --dry-run --reporter json`, static
graph reused from js_graph (madge) same as Jest — Mocha has no bundled
bundler-aware dependency analysis of its own, but the import graph is a
language property, not a test-runner property, so it's shared."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from reflex.graph.js_graph import build_js_graph
from reflex.models import TestUnit


class MochaAdapter:
    name = "mocha"

    def __init__(self, root: str | Path, mocha_bin: str = "npx mocha", spec_glob: str = "test/**/*.js"):
        self.root = Path(root).resolve()
        self.mocha_bin = mocha_bin
        self.spec_glob = spec_glob

    def collect(self) -> list[TestUnit]:
        cmd = self.mocha_bin.split() + [
            self.spec_glob,
            "--dry-run",
            "--reporter",
            "json",
        ]
        result = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True)
        units: list[TestUnit] = []
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return units

        for test in data.get("tests", []) + data.get("pending", []):
            file_rel = test.get("file", "")
            if Path(file_rel).is_absolute():
                try:
                    file_rel = str(Path(file_rel).relative_to(self.root))
                except ValueError:
                    pass
            title = test.get("fullTitle") or test.get("title") or file_rel
            units.append(
                TestUnit(
                    id=f"{file_rel}::{title}",
                    file=file_rel,
                    title=title,
                    framework="mocha",
                )
            )
        return units

    def dependency_graph(self) -> dict[str, set[str]] | None:
        tests = self.collect()
        return build_js_graph(self.root, tests)

    def can_analyze(self, file_path: str) -> bool:
        return file_path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"))

    def run_command(self, selected_ids: list[str]) -> list[str]:
        files = sorted({tid.split("::", 1)[0] for tid in selected_ids})
        return self.mocha_bin.split() + files

    def full_run_command(self, junit_path: str) -> list[str]:
        """Requires the `mocha-junit-reporter` package installed."""
        return self.mocha_bin.split() + [
            self.spec_glob,
            "--reporter",
            "mocha-junit-reporter",
            "--reporter-options",
            f"mochaFile={junit_path}",
        ]
