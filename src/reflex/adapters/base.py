"""Adapter protocol — this is the only thing a new language/framework needs
to implement to plug into Reflex.

Adapters intentionally have a small surface:
  - collect(): list every runnable test as a normalized TestUnit
  - dependency_graph(): OPTIONAL static file->tests map. Return None if the
    language/framework has no cheap way to compute one; Reflex will fall
    back to Laya for that component instead of failing.
  - run_command(): the argv to actually execute a selected subset

Anything with a "list tests" mode and a "run these specific test ids" mode
can be adapted (pytest, Jest, Mocha, Vitest, Playwright, go test, RSpec...).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from reflex.models import TestUnit


class Adapter(Protocol):
    name: str
    root: Path

    def collect(self) -> list[TestUnit]:
        ...

    def dependency_graph(self) -> dict[str, set[str]] | None:
        """Map source file path -> set of test ids that transitively depend
        on it. Return None if unsupported for this component/language."""
        ...

    def can_analyze(self, file_path: str) -> bool:
        """Whether dependency_graph() is capable of reasoning about this
        specific changed file (by extension/convention), regardless of
        whether that file happens to have zero dependent tests.

        This matters because 'not in the graph' is ambiguous: it could mean
        'genuinely unrelated' (fine to skip) or 'this adapter can't model
        this file type at all' (must fall back to Laya, not silently
        drop it). E.g. a Playwright-BDD adapter can deterministically map
        `.feature` file changes to their generated specs, but can't
        statically map step-definition file changes to the many features
        that use them — those must go through the Laya fallback instead.
        """
        ...

    def run_command(self, selected_ids: list[str]) -> list[str]:
        """Return argv to run exactly the given test ids."""
        ...
