"""Reflex orchestrator: ties diff extraction, adapters, static graphs, and
the Laya fallback together into a per-component selection, then (optionally)
executes each adapter's run command.

Usage:
    python -m reflex.select --config reflex.config.yml --base-ref origin/main \
        [--model-path ../models/laya-english] [--shadow] [--run-all] \
        [--execute]
"""

from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from reflex.adapters.jest_adapter import JestAdapter
from reflex.adapters.mocha_adapter import MochaAdapter
from reflex.adapters.playwright_adapter import PlaywrightAdapter
from reflex.adapters.playwright_bdd_adapter import PlaywrightBDDAdapter
from reflex.adapters.pytest_adapter import PytestAdapter
from reflex.adapters.vitest_adapter import VitestAdapter
from reflex.classifier import LayaClassifier
from reflex.config import ReflexConfig, expand_dependents, load_config
from reflex.diffutil import summarize_diff
from reflex.junit import failed_files_from_junit
from reflex.models import Component, SelectionResult

# Built-in adapters, selectable by short name in reflex.config.yml. This is
# a set of conveniences, not a closed list: any component can instead set
# `adapter: "some.module.path:ClassName"` to plug in a custom adapter
# (RSpec, Go test, k6, an internal framework, ...) without touching Reflex's
# source at all — see build_adapter() below.
ADAPTER_REGISTRY = {
    "pytest": PytestAdapter,
    "jest": JestAdapter,
    "mocha": MochaAdapter,
    "vitest": VitestAdapter,
    "playwright": PlaywrightAdapter,
    "playwright-bdd": PlaywrightBDDAdapter,
}


class AdapterLoadError(RuntimeError):
    pass


def resolve_adapter_class(adapter_name: str):
    """Resolve a config 'adapter:' value to a class.

    - A short name ('pytest', 'jest', ...) looks up ADAPTER_REGISTRY.
    - A 'module.path:ClassName' string dynamically imports a custom
      adapter, so new frameworks/languages can be supported entirely from
      user code — Reflex's built-ins are defaults, not a hard dependency.
    """
    if adapter_name in ADAPTER_REGISTRY:
        return ADAPTER_REGISTRY[adapter_name]

    if ":" in adapter_name:
        module_name, _, class_name = adapter_name.partition(":")
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            raise AdapterLoadError(
                f"Could not import adapter module '{module_name}': {e}"
            ) from e
        cls = getattr(module, class_name, None)
        if cls is None:
            raise AdapterLoadError(
                f"Module '{module_name}' has no attribute '{class_name}'"
            )
        return cls

    return None


def build_adapter(component: Component):
    cls = resolve_adapter_class(component.adapter)
    if cls is None:
        return None
    return cls(root=component.path, **component.adapter_options)


def select_for_component(
    component: Component,
    changed_files: list[str],
    diff_text: str,
    classifier: LayaClassifier | None,
    run_all: bool,
    forced_full_run: bool = False,
) -> SelectionResult:
    adapter = build_adapter(component)

    # Unknown adapter -> fail open, run everything for this component.
    if adapter is None:
        return SelectionResult(component.name, {}, full_run=True)

    if run_all:
        return SelectionResult(component.name, {}, full_run=True)

    if forced_full_run:
        # A component this one lists as a 'dependent' changed (see
        # reflex.config.expand_dependents) — this component didn't
        # necessarily change itself, but its foundational dependency did,
        # so blast radius is too high to trust semantic filtering.
        return SelectionResult(component.name, {}, full_run=True)

    if not changed_files:
        return SelectionResult(component.name, {})

    if component.foundational:
        # This component itself changed and is foundational -> full run,
        # not just for it but (via expand_dependents in main()) for every
        # component that declares it as a dependency too.
        return SelectionResult(component.name, {}, full_run=True)

    tests = adapter.collect()
    # diff paths are repo-relative (e.g. 'backend/billing/service.py'), but
    # adapters/graphs operate relative to their own component root (e.g.
    # 'billing/service.py') — normalize before any lookup.
    prefix = component.path.rstrip("/") + "/"
    changed_files = [
        f[len(prefix):] if f.startswith(prefix) else f for f in changed_files
    ]
    changed_set = set(changed_files)

    reasons: dict[str, str] = {}

    # Always include tests inside changed test files themselves.
    for t in tests:
        if t.file in changed_set:
            reasons[t.id] = "changed-test-file"

    graph = adapter.dependency_graph()

    # Split changed files per-file, not per-component: a file the adapter
    # can statically analyze (e.g. a .py/.js source file, or for BDD a
    # .feature file) is resolved deterministically. A file it can't reason
    # about (e.g. a BDD step-definition file) falls back to Laya instead of
    # being silently dropped or forcing an all-or-nothing decision for the
    # whole component.
    unresolved_files: list[str] = []
    for changed_file in changed_files:
        if graph is not None and adapter.can_analyze(changed_file):
            for test_id in graph.get(changed_file, set()):
                reasons.setdefault(test_id, "static-graph")
        else:
            unresolved_files.append(changed_file)

    if unresolved_files:
        if classifier is None:
            # No semantic fallback configured and some changed files can't
            # be statically resolved -> fail open rather than risk a false
            # negative.
            return SelectionResult(component.name, {}, full_run=True)

        suites = {t.file: t.title for t in tests if t.file not in changed_set}
        results = classifier.filter_suites(diff_text, suites)
        for suite_file, res in results.items():
            for t in tests:
                if t.file == suite_file:
                    reasons.setdefault(t.id, f"laya:{res.probability:.2f}")

    # Empty selection with a non-trivial diff is suspicious -> fail open.
    if not reasons:
        return SelectionResult(component.name, {}, full_run=True)

    return SelectionResult(component.name, reasons)


def write_summary(
    results: list[SelectionResult], shadow: bool, shadow_reports: dict[str, "ShadowReport"] | None = None
) -> None:
    lines = ["# Reflex test selection" + (" (shadow mode)" if shadow else "")]
    shadow_reports = shadow_reports or {}
    for r in results:
        if r.full_run:
            lines.append(f"\n## {r.component}: FULL RUN")
        else:
            lines.append(f"\n## {r.component}: {len(r.selected_ids)} test(s) selected")
            for test_id, reason in sorted(r.reason_by_test.items()):
                lines.append(f"- `{test_id}` — {reason}")

        sr = shadow_reports.get(r.component)
        if sr is not None:
            if sr.unsupported:
                lines.append(
                    "\n_shadow comparison unavailable: adapter has no `full_run_command`_"
                )
            elif sr.missed_files:
                lines.append(
                    f"\n**⚠ would have MISSED {len(sr.missed_files)} failing file(s) "
                    f"if selector had gated execution:**"
                )
                for f in sorted(sr.missed_files):
                    lines.append(f"- `{f}`")
            else:
                lines.append(
                    "\n✅ shadow check: every file with failures was in the selection"
                )
    summary = "\n".join(lines)

    print(summary)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(summary + "\n")


@dataclass
class ShadowReport:
    missed_files: set[str]
    """Files that had a failing test in the real full run, but weren't
    included in this component's selection — the dangerous false-negative
    case shadow mode exists to catch."""
    unsupported: bool = False


def run_shadow_check(component: Component, result: SelectionResult) -> ShadowReport | None:
    """Run the FULL suite for this component (regardless of selection),
    capture JUnit XML, and compare which files actually failed against
    which files the selector would have picked — all within this one CI
    run, nothing persisted afterward."""
    if result.full_run:
        return None  # nothing to compare; selector already chose full run

    adapter = build_adapter(component)
    if adapter is None or not hasattr(adapter, "full_run_command"):
        return ShadowReport(missed_files=set(), unsupported=True)

    with tempfile.TemporaryDirectory() as tmp:
        junit_path = str(Path(tmp) / "junit.xml")
        cmd = adapter.full_run_command(junit_path)
        env = dict(os.environ)
        env["JEST_JUNIT_OUTPUT_DIR"] = tmp
        env["JEST_JUNIT_OUTPUT_NAME"] = "junit.xml"
        env["PLAYWRIGHT_JUNIT_OUTPUT_NAME"] = junit_path
        print(f"[reflex:shadow] running full suite for {component.name}: {' '.join(cmd)}")
        subprocess.run(cmd, cwd=component.path, env=env, check=False)

        failed_files = failed_files_from_junit(junit_path)

    selected_files = {tid.split("::", 1)[0] for tid in result.selected_ids}
    missed = failed_files - selected_files
    return ShadowReport(missed_files=missed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="reflex.config.yml")
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument(
        "--model-path",
        default=None,
        help="overrides settings.model_path from the config file",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="overrides settings.threshold from the config file",
    )
    parser.add_argument(
        "--shadow",
        action="store_true",
        help="always run the full suite, but also compute+report what the "
        "selector would have picked, and flag any failing file it would "
        "have skipped. Safe rollout mode: use before trusting --execute.",
    )
    parser.add_argument("--run-all", action="store_true", help="force full suite everywhere")
    parser.add_argument("--execute", action="store_true", help="actually run selected tests")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    components = cfg.components
    model_path = args.model_path if args.model_path is not None else cfg.model_path
    threshold = args.threshold if args.threshold is not None else cfg.threshold

    diff = summarize_diff(components, args.base_ref, args.head_ref)

    classifier = None
    if model_path:
        classifier = LayaClassifier(model_path, threshold=threshold)

    diff_text_all = "\n".join(diff.hunks_by_file.values())

    changed_component_names = {
        name for name, files in diff.by_component.items() if files
    }
    forced_names = expand_dependents(components, changed_component_names)

    results: list[SelectionResult] = []
    for component in components:
        changed = diff.by_component.get(component.name, [])
        result = select_for_component(
            component,
            changed,
            diff_text_all,
            classifier,
            args.run_all,
            forced_full_run=component.name in forced_names,
        )
        results.append(result)

    shadow_reports: dict[str, ShadowReport] = {}
    if args.shadow:
        for component, result in zip(components, results):
            report = run_shadow_check(component, result)
            if report is not None:
                shadow_reports[component.name] = report

    write_summary(results, shadow=args.shadow, shadow_reports=shadow_reports)

    if args.execute and not args.shadow:
        for component, result in zip(components, results):
            adapter = build_adapter(component)
            if adapter is None:
                continue
            if result.full_run:
                cmd = adapter.run_command([])
            elif result.selected_ids:
                cmd = adapter.run_command(result.selected_ids)
            else:
                continue
            print(f"[reflex] running: {' '.join(cmd)}")
            subprocess.run(cmd, cwd=component.path, check=False)
    elif args.shadow:
        # Shadow mode already ran the full suite above (that IS the real
        # signal of record for this CI run); nothing further to execute.
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
