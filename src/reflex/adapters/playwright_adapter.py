"""Plain Playwright adapter (no BDD layer) — used e.g. by backend/playwright
API tests, where specs are ordinary .spec.ts files with normal imports.
Unlike PlaywrightBDDAdapter, this can use the standard JS import graph
(madge) directly, since there's no feature-file/step-definition indirection
to reason about."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from reflex.graph.js_graph import build_js_graph
from reflex.models import TestUnit


class PlaywrightAdapter:
    name = "playwright"

    def __init__(self, root: str | Path, playwright_bin: str = "npx playwright"):
        self.root = Path(root).resolve()
        self.playwright_bin = playwright_bin

    def collect(self) -> list[TestUnit]:
        cmd = self.playwright_bin.split() + ["test", "--list", "--reporter=json"]
        result = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True)
        units: list[TestUnit] = []
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return units

        # Playwright's suite "file" is relative to the project's testDir
        # (e.g. 'api-tests/'), NOT the component root — resolve via
        # config.rootDir so file paths line up with the component-root-
        # relative keys the static graph (madge) produces.
        test_dir = data.get("config", {}).get("rootDir")
        prefix = ""
        if test_dir:
            try:
                prefix = str(Path(test_dir).resolve().relative_to(self.root))
                if prefix == ".":
                    prefix = ""
            except ValueError:
                prefix = ""

        def to_root_relative(file_rel: str) -> str:
            return f"{prefix}/{file_rel}" if prefix else file_rel

        def walk(suite: dict) -> None:
            file_rel = to_root_relative(suite.get("file", ""))
            for spec in suite.get("specs", []):
                title = spec.get("title", file_rel)
                units.append(
                    TestUnit(
                        id=f"{file_rel}::{title}",
                        file=file_rel,
                        title=title,
                        framework="playwright",
                    )
                )
            for child in suite.get("suites", []):
                walk(child)

        for suite in data.get("suites", []):
            walk(suite)
        return units

    def dependency_graph(self) -> dict[str, set[str]] | None:
        tests = self.collect()
        return build_js_graph(self.root, tests)

    def can_analyze(self, file_path: str) -> bool:
        return file_path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"))

    def run_command(self, selected_ids: list[str]) -> list[str]:
        files = sorted({tid.split("::", 1)[0] for tid in selected_ids})
        return self.playwright_bin.split() + ["test", *files]

    def full_run_command(self, junit_path: str) -> list[str]:
        """Uses Playwright's built-in junit reporter (no extra package
        needed), but requires PLAYWRIGHT_JUNIT_OUTPUT_NAME set by the
        orchestrator before invoking this command."""
        return self.playwright_bin.split() + ["test", "--reporter=junit"]
