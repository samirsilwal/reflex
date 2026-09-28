"""Load and validate reflex.config.yml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from reflex.models import Component


class ConfigError(ValueError):
    pass


# Reflex ships with its own bundled Laya model at <package_root>/models/<name>
# so the tool is self-contained and pushable as a standalone repo — no
# external/absolute model path required. `settings.model_path` in
# reflex.config.yml (or --model-path) can still override this with any
# other local path or a different bundled model name.
BUNDLED_MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "models"
DEFAULT_BUNDLED_MODEL = "laya-english"


def default_model_path() -> str | None:
    """Path to Reflex's own bundled model, if present, else None.

    Returns None (not an error) so Reflex still works static-graph-only
    in environments where the model weights were intentionally stripped
    (e.g. a slim checkout, or Git LFS not pulled) — model_path is always
    optional everywhere it's consumed.
    """
    candidate = BUNDLED_MODELS_DIR / DEFAULT_BUNDLED_MODEL
    if candidate.is_dir():
        return str(candidate)
    return None


@dataclass
class ReflexConfig:
    components: list[Component]
    model_path: str | None = None
    threshold: float = 0.5
    """Repo-wide defaults, overridable per-invocation via CLI flags."""


def load_config(path: str | Path) -> ReflexConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Reflex config not found: {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    components_raw = raw.get("components")
    if not components_raw:
        raise ConfigError("reflex.config.yml must define at least one component")

    components: list[Component] = []
    for name, spec in components_raw.items():
        if "path" not in spec or "adapter" not in spec:
            raise ConfigError(
                f"Component '{name}' must define 'path' and 'adapter'"
            )
        adapter_options = spec.get("adapter_options", {}) or {}
        if not isinstance(adapter_options, dict):
            raise ConfigError(
                f"Component '{name}': 'adapter_options' must be a mapping"
            )
        components.append(
            Component(
                name=name,
                path=spec["path"],
                adapter=spec["adapter"],
                foundational=bool(spec.get("foundational", False)),
                dependents=list(spec.get("dependents", [])),
                adapter_options=dict(adapter_options),
            )
        )

    _validate_dependents(components)

    settings = raw.get("settings", {}) or {}
    return ReflexConfig(
        components=components,
        model_path=settings.get("model_path") or default_model_path(),
        threshold=float(settings.get("threshold", 0.5)),
    )


def _validate_dependents(components: list[Component]) -> None:
    names = {c.name for c in components}
    for c in components:
        unknown = [d for d in c.dependents if d not in names]
        if unknown:
            raise ConfigError(
                f"Component '{c.name}' declares unknown dependents: {unknown}"
            )


def component_for_path(components: list[Component], file_path: str) -> Component | None:
    """Return the most specific component whose path prefixes file_path."""
    best: Component | None = None
    for c in components:
        norm = c.path.rstrip("/") + "/"
        if file_path.startswith(norm) or file_path == c.path:
            if best is None or len(c.path) > len(best.path):
                best = c
    return best


def expand_dependents(components: list[Component], changed_names: set[str]) -> set[str]:
    """Transitive closure of components that must be forced to a full run
    because *something they depend on* changed — not because they changed
    themselves. E.g. if 'core' changes and declares dependents
    ['backend', 'lambda'], both get force-full-run even though no file
    under backend/ or lambda/ was touched."""
    by_name = {c.name: c for c in components}
    forced: set[str] = set()
    frontier = set(changed_names)
    seen_frontier: set[str] = set()
    while frontier:
        next_frontier: set[str] = set()
        for name in frontier:
            if name in seen_frontier:
                continue
            seen_frontier.add(name)
            comp = by_name.get(name)
            if comp is None:
                continue
            for dep in comp.dependents:
                if dep not in forced:
                    forced.add(dep)
                    next_frontier.add(dep)
        frontier = next_frontier
    return forced

