"""Core data types shared across adapters, graph builders, and the classifier.

Keeping these framework-agnostic is what lets Reflex support pytest, Jest,
Playwright BDD, Go test, etc. behind the same orchestration logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TestUnit:
    """A single runnable test, normalized across frameworks."""

    __test__ = False  # tell pytest this is not a test class despite the name

    id: str
    """Framework-native identifier the adapter can re-run directly,
    e.g. 'tests/test_billing.py::test_refund_duplicate' or
    'src/Billing.test.tsx'."""

    file: str
    """Path to the file this test lives in, relative to the component root."""

    title: str
    """Human-readable test name/description, used as Laya's semantic input.
    Falls back to `id` if the framework has no separate title."""

    framework: str
    """e.g. 'pytest', 'jest', 'playwright-bdd'."""


@dataclass
class Component:
    """A repo/monorepo subtree owned by one adapter (e.g. 'web-app', 'core')."""

    name: str
    path: str
    adapter: str
    foundational: bool = False
    adapter_options: dict = field(default_factory=dict)
    """Extra kwargs passed straight through to the adapter's constructor,
    e.g. {jest_bin: 'yarn jest'} or {features_dir: 'e2e/features',
    generated_dir: '.features-gen/e2e'}. Keeps adapters generic — repo-
    specific conventions live in config, not in adapter source."""

    dependents: list[str] = field(default_factory=list)
    """Other component names that must be force-run in full when THIS
    component changes (regardless of whether their own files changed).
    Typically used for foundational/shared packages: a 'core' component
    with dependents ['backend', 'web-app'] means any change to core forces
    a full run of backend and web-app too."""


@dataclass
class SelectionResult:
    component: str
    reason_by_test: dict[str, str]
    """test id -> short reason string, e.g. 'static-graph', 'laya:0.82',
    'changed-test-file', 'foundational-full-run'."""
    full_run: bool = False

    @property
    def selected_ids(self) -> list[str]:
        return list(self.reason_by_test.keys())
