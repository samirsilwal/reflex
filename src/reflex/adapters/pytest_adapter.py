"""pytest adapter: collection via `pytest --collect-only -q`, static graph via
AST import analysis (reflex.graph.python_graph)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from reflex.graph.python_graph import build_python_graph
from reflex.models import TestUnit


class PytestAdapter:
    name = "pytest"

    def __init__(self, root: str | Path, python_bin: str | None = None):
        self.root = Path(root)
        self.python_bin = python_bin or sys.executable

    def collect(self) -> list[TestUnit]:
        result = subprocess.run(
            [self.python_bin, "-m", "pytest", "--collect-only", "-q"],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        units: list[TestUnit] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or "::" not in line:
                continue
            file_part = line.split("::", 1)[0]
            units.append(
                TestUnit(
                    id=line,
                    file=file_part,
                    title=line.replace("::", " "),
                    framework="pytest",
                )
            )
        return units

    def dependency_graph(self) -> dict[str, set[str]] | None:
        tests = self.collect()
        return build_python_graph(self.root, tests)

    def can_analyze(self, file_path: str) -> bool:
        return file_path.endswith(".py")

    def run_command(self, selected_ids: list[str]) -> list[str]:
        return [self.python_bin, "-m", "pytest", *selected_ids]

    def full_run_command(self, junit_path: str) -> list[str]:
        """Run the entire suite, writing JUnit XML for shadow-mode
        reconciliation. Works out of the box — pytest's --junitxml is
        stdlib/builtin, no extra plugin required."""
        return [self.python_bin, "-m", "pytest", f"--junitxml={junit_path}"]
