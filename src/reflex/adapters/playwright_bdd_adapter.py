"""Playwright-BDD adapter.

playwright-bdd compiles `.feature` files (+ step definitions) into generated
`.spec.ts` files under a generated directory (commonly `.features-gen/`),
which `playwright test` then runs normally.

This gives us a genuinely mixed static/semantic component:
  - `.feature` file changes CAN be mapped deterministically: the generated
    spec path is a naming-convention transform of the feature path, so we
    build a real (if narrow) static graph entry for it.
  - step-definition file changes CANNOT be mapped statically without a
    step-pattern matcher: a single step file backs scenarios across many
    unrelated features. `can_analyze()` returns False for these files, so
    the orchestrator routes them to the Laya semantic fallback instead of
    silently dropping them or forcing a full run.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from reflex.models import TestUnit


class PlaywrightBDDAdapter:
    name = "playwright-bdd"

    def __init__(
        self,
        root: str | Path,
        playwright_bin: str = "npx playwright",
        features_dir: str = "features",
        generated_dir: str = ".features-gen",
    ):
        self.root = Path(root).resolve()
        self.playwright_bin = playwright_bin
        self.features_dir = features_dir.rstrip("/")
        self.generated_dir = generated_dir.rstrip("/")

    def _feature_for_generated_spec(self, spec_file: str) -> str | None:
        """Reverse the bddgen naming convention:
        '.features-gen/features/login.feature.spec.js' -> 'features/login.feature'
        """
        prefix = self.generated_dir + "/"
        if not spec_file.startswith(prefix):
            return None
        tail = spec_file[len(prefix):]
        for suffix in (".spec.js", ".spec.ts"):
            if tail.endswith(suffix):
                return tail[: -len(suffix)]
        return None

    def collect(self) -> list[TestUnit]:
        cmd = self.playwright_bin.split() + ["test", "--list", "--reporter=json"]
        result = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True)
        units: list[TestUnit] = []
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return units

        # Same root-relative resolution issue as PlaywrightAdapter: "file"
        # is relative to testDir, not the component root.
        test_dir = data.get("config", {}).get("rootDir")
        prefix = ""
        if test_dir:
            try:
                prefix = str(Path(test_dir).resolve().relative_to(self.root))
                if prefix == ".":
                    prefix = ""
            except ValueError:
                prefix = ""

        for suite in data.get("suites", []):
            self._walk_suite(suite, units, prefix)
        return units

    def _walk_suite(self, suite: dict, units: list[TestUnit], prefix: str = "") -> None:
        file_rel = suite.get("file", "")
        if prefix:
            file_rel = f"{prefix}/{file_rel}"
        for spec in suite.get("specs", []):
            title = spec.get("title", file_rel)
            units.append(
                TestUnit(
                    id=f"{file_rel}::{title}",
                    file=file_rel,
                    title=title,
                    framework="playwright-bdd",
                )
            )
        for child in suite.get("suites", []):
            self._walk_suite(child, units, prefix)

    def dependency_graph(self) -> dict[str, set[str]] | None:
        tests = self.collect()
        graph: dict[str, set[str]] = {}
        for t in tests:
            feature = self._feature_for_generated_spec(t.file)
            if feature is not None:
                graph.setdefault(feature, set()).add(t.id)
        return graph

    def can_analyze(self, file_path: str) -> bool:
        # We can only deterministically map .feature file changes (via the
        # generated-spec naming convention). Step-definition files, page
        # objects, fixtures, etc. are not statically mappable here and must
        # go through the Laya semantic fallback instead.
        return file_path.endswith(".feature")

    def run_command(self, selected_ids: list[str]) -> list[str]:
        files = sorted({tid.split("::", 1)[0] for tid in selected_ids})
        return self.playwright_bin.split() + ["test", *files]

    def full_run_command(self, junit_path: str) -> list[str]:
        """Uses Playwright's built-in junit reporter — no extra package
        needed, but the orchestrator must set PLAYWRIGHT_JUNIT_OUTPUT_NAME."""
        return self.playwright_bin.split() + ["test", "--reporter=junit"]
