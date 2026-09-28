"""Git diff extraction and component mapping."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from reflex.config import component_for_path
from reflex.models import Component


@dataclass
class DiffSummary:
    changed_files: list[str]
    """All files changed in the PR, relative to repo root."""

    by_component: dict[str, list[str]]
    """component name -> changed files within that component."""

    hunks_by_file: dict[str, str]
    """file -> short unified-diff hunk-header context, used as Laya input text."""


def get_changed_files(base_ref: str, head_ref: str = "HEAD") -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...{head_ref}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_hunk_context(base_ref: str, file_path: str, head_ref: str = "HEAD", max_chars: int = 400) -> str:
    """Short diff context for a single file, used as semantic text for Laya.

    Only hunk headers (@@ ... @@) plus a few surrounding lines are kept —
    Laya's input budget (max_len=512 tokens) doesn't need the full patch,
    just enough to describe *what kind* of change happened.
    """
    result = subprocess.run(
        ["git", "diff", "--unified=1", f"{base_ref}...{head_ref}", "--", file_path],
        capture_output=True,
        text=True,
    )
    text = result.stdout
    return text[:max_chars]


def summarize_diff(components: list[Component], base_ref: str, head_ref: str = "HEAD") -> DiffSummary:
    changed_files = get_changed_files(base_ref, head_ref)

    by_component: dict[str, list[str]] = {c.name: [] for c in components}
    hunks_by_file: dict[str, str] = {}

    for f in changed_files:
        comp = component_for_path(components, f)
        if comp is not None:
            by_component[comp.name].append(f)
        hunks_by_file[f] = get_hunk_context(base_ref, f, head_ref)

    return DiffSummary(
        changed_files=changed_files,
        by_component=by_component,
        hunks_by_file=hunks_by_file,
    )
