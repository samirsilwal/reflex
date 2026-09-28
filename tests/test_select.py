"""Selection-logic tests. No real adapters, no Laya calls — pure orchestration
logic under reflex.select.select_for_component.
"""

from __future__ import annotations

import pytest

from reflex.classifier import ClassificationResult
from reflex.models import Component, TestUnit
from reflex import select as select_mod


class FakeAdapter:
    """Adapter double with a fixed test list and a fixed graph.

    `unanalyzable_files` lets tests simulate files the adapter's static
    graph can't reason about (e.g. BDD step-definition files), which must
    route to the Laya fallback instead of being resolved (or dropped) via
    the graph.
    """

    def __init__(
        self,
        tests: list[TestUnit],
        graph: dict[str, set[str]] | None,
        unanalyzable_files: set[str] | None = None,
    ):
        self._tests = tests
        self._graph = graph
        self._unanalyzable_files = unanalyzable_files or set()

    def collect(self) -> list[TestUnit]:
        return self._tests

    def dependency_graph(self):
        return self._graph

    def can_analyze(self, file_path: str) -> bool:
        return file_path not in self._unanalyzable_files

    def run_command(self, selected_ids):
        return ["echo", *selected_ids]


class FakeClassifier:
    def __init__(self, affected_files: set[str]):
        self.affected_files = affected_files
        self.threshold = 0.5

    def filter_suites(self, diff_text, suites):
        return {
            suite_id: ClassificationResult(suite_id, 0.9)
            for suite_id in suites
            if suite_id in self.affected_files
        }


def make_component(name="backend", adapter="pytest", foundational=False):
    return Component(name=name, path=f"{name}/", adapter=adapter, foundational=foundational)


def test_static_graph_hard_include(monkeypatch):
    tests = [
        TestUnit("t1.py::test_a", "t1.py", "test a", "pytest"),
        TestUnit("t2.py::test_b", "t2.py", "test b", "pytest"),
    ]
    graph = {"billing.py": {"t1.py::test_a"}}
    adapter = FakeAdapter(tests, graph)
    monkeypatch.setattr(select_mod, "build_adapter", lambda c: adapter)

    result = select_mod.select_for_component(
        make_component(), ["billing.py"], "diff text", classifier=None, run_all=False
    )

    assert not result.full_run
    assert result.selected_ids == ["t1.py::test_a"]
    assert result.reason_by_test["t1.py::test_a"] == "static-graph"


def test_foundational_component_forces_full_run_when_it_has_changes(monkeypatch):
    adapter = FakeAdapter([], {})
    monkeypatch.setattr(select_mod, "build_adapter", lambda c: adapter)

    result = select_mod.select_for_component(
        make_component(foundational=True),
        ["core/utils.py"],
        "diff text",
        classifier=None,
        run_all=False,
    )

    assert result.full_run


def test_foundational_component_does_not_force_full_run_when_unchanged(monkeypatch):
    """Regression test: a foundational component must NOT force a full run
    on every PR — only when it actually has changed files (or is forced via
    dependents propagation). Previously this check ran unconditionally,
    before the 'no changed files' check, wasting the full suite on every
    PR that never touched the foundational component."""
    adapter = FakeAdapter([], {})
    monkeypatch.setattr(select_mod, "build_adapter", lambda c: adapter)

    result = select_mod.select_for_component(
        make_component(foundational=True),
        [],  # no changes to this component at all
        "diff text",
        classifier=None,
        run_all=False,
    )

    assert not result.full_run
    assert result.selected_ids == []


def test_forced_full_run_from_dependents_propagation(monkeypatch):
    """A component with no local changes still gets full_run=True when the
    orchestrator determined (via reflex.config.expand_dependents) that a
    foundational dependency it relies on changed elsewhere."""
    adapter = FakeAdapter([], {})
    monkeypatch.setattr(select_mod, "build_adapter", lambda c: adapter)

    result = select_mod.select_for_component(
        make_component(),  # not foundational itself, no local changes
        [],
        "diff text",
        classifier=None,
        run_all=False,
        forced_full_run=True,
    )

    assert result.full_run


def test_dynamic_adapter_loading_via_module_class_string():
    """A custom adapter can be plugged in via 'module.path:ClassName'
    without touching Reflex's built-in ADAPTER_REGISTRY at all."""
    cls = select_mod.resolve_adapter_class("reflex.adapters.pytest_adapter:PytestAdapter")
    from reflex.adapters.pytest_adapter import PytestAdapter

    assert cls is PytestAdapter


def test_dynamic_adapter_loading_unknown_module_raises():
    with pytest.raises(select_mod.AdapterLoadError):
        select_mod.resolve_adapter_class("nonexistent.module:Foo")


def test_build_adapter_passes_through_adapter_options(monkeypatch):
    captured = {}

    class RecordingAdapter:
        def __init__(self, root, **kwargs):
            captured["root"] = root
            captured["kwargs"] = kwargs

    monkeypatch.setitem(select_mod.ADAPTER_REGISTRY, "recording", RecordingAdapter)
    component = Component(
        name="web-app",
        path="web-app/",
        adapter="recording",
        adapter_options={"features_dir": "e2e/features"},
    )

    select_mod.build_adapter(component)

    assert captured["root"] == "web-app/"
    assert captured["kwargs"] == {"features_dir": "e2e/features"}


def test_no_graph_falls_back_to_laya(monkeypatch):
    tests = [
        TestUnit("t1.py::test_a", "t1.py", "affected test", "pytest"),
        TestUnit("t2.py::test_b", "t2.py", "unaffected test", "pytest"),
    ]
    adapter = FakeAdapter(tests, graph=None)
    monkeypatch.setattr(select_mod, "build_adapter", lambda c: adapter)
    classifier = FakeClassifier(affected_files={"t1.py"})

    result = select_mod.select_for_component(
        make_component(), ["config.yaml"], "diff text", classifier=classifier, run_all=False
    )

    assert not result.full_run
    assert result.selected_ids == ["t1.py::test_a"]
    assert result.reason_by_test["t1.py::test_a"].startswith("laya:")


def test_changed_test_file_always_included(monkeypatch):
    tests = [TestUnit("t1.py::test_a", "t1.py", "test a", "pytest")]
    adapter = FakeAdapter(tests, graph={})
    monkeypatch.setattr(select_mod, "build_adapter", lambda c: adapter)

    result = select_mod.select_for_component(
        make_component(), ["t1.py"], "diff text", classifier=None, run_all=False
    )

    assert result.reason_by_test["t1.py::test_a"] == "changed-test-file"


def test_run_all_forces_full_run(monkeypatch):
    adapter = FakeAdapter([], {})
    monkeypatch.setattr(select_mod, "build_adapter", lambda c: adapter)

    result = select_mod.select_for_component(
        make_component(), ["anything.py"], "diff text", classifier=None, run_all=True
    )

    assert result.full_run


def test_no_changed_files_selects_nothing(monkeypatch):
    adapter = FakeAdapter([], {})
    monkeypatch.setattr(select_mod, "build_adapter", lambda c: adapter)

    result = select_mod.select_for_component(
        make_component(), [], "diff text", classifier=None, run_all=False
    )

    assert not result.full_run
    assert result.selected_ids == []


def test_repo_relative_changed_files_are_normalized_to_component_root(monkeypatch):
    """Regression test: diffutil.summarize_diff returns repo-relative paths
    (e.g. 'backend/billing/service.py'), but adapters/graphs are keyed
    relative to the component root ('billing/service.py'). Selection must
    strip the component path prefix before doing graph lookups."""
    tests = [TestUnit("test_billing.py::test_refund", "test_billing.py", "test refund", "pytest")]
    graph = {"billing/service.py": {"test_billing.py::test_refund"}}
    adapter = FakeAdapter(tests, graph)
    monkeypatch.setattr(select_mod, "build_adapter", lambda c: adapter)

    result = select_mod.select_for_component(
        make_component(name="backend"),
        ["backend/billing/service.py"],  # repo-relative, as diffutil produces
        "diff text",
        classifier=None,
        run_all=False,
    )

    assert not result.full_run
    assert result.selected_ids == ["test_billing.py::test_refund"]


def test_unknown_adapter_fails_open(monkeypatch):
    monkeypatch.setattr(select_mod, "build_adapter", lambda c: None)

    result = select_mod.select_for_component(
        make_component(adapter="rspec"), ["x.rb"], "diff text", classifier=None, run_all=False
    )

    assert result.full_run


def test_mixed_static_and_laya_within_same_component(monkeypatch):
    """BDD-style scenario: a .feature file change is statically resolved via
    the graph, while a step-definition file change (which the adapter can't
    analyze) falls back to Laya — both signals combine in one component."""
    tests = [
        TestUnit("login.feature.spec.js::logs in", "login.feature.spec.js", "logs in", "playwright-bdd"),
        TestUnit("checkout.feature.spec.js::checks out", "checkout.feature.spec.js", "checks out", "playwright-bdd"),
        TestUnit("unrelated.feature.spec.js::does other thing", "unrelated.feature.spec.js", "does other thing", "playwright-bdd"),
    ]
    graph = {"features/login.feature": {"login.feature.spec.js::logs in"}}
    adapter = FakeAdapter(
        tests, graph, unanalyzable_files={"steps/checkout_steps.js"}
    )
    monkeypatch.setattr(select_mod, "build_adapter", lambda c: adapter)
    classifier = FakeClassifier(affected_files={"checkout.feature.spec.js"})

    result = select_mod.select_for_component(
        make_component(adapter="playwright-bdd"),
        ["features/login.feature", "steps/checkout_steps.js"],
        "diff text",
        classifier=classifier,
        run_all=False,
    )

    assert not result.full_run
    assert result.reason_by_test["login.feature.spec.js::logs in"] == "static-graph"
    assert result.reason_by_test["checkout.feature.spec.js::checks out"].startswith("laya:")
    assert "unrelated.feature.spec.js::does other thing" not in result.reason_by_test


def test_unanalyzable_file_with_no_classifier_fails_open(monkeypatch):
    tests = [TestUnit("a.spec.js::t", "a.spec.js", "t", "playwright-bdd")]
    adapter = FakeAdapter(tests, graph={}, unanalyzable_files={"steps/foo.js"})
    monkeypatch.setattr(select_mod, "build_adapter", lambda c: adapter)

    result = select_mod.select_for_component(
        make_component(adapter="playwright-bdd"),
        ["steps/foo.js"],
        "diff text",
        classifier=None,
        run_all=False,
    )

    assert result.full_run
