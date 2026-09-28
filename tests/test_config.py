"""Tests for reflex.config: parsing, validation, and dependents propagation."""

from __future__ import annotations

import pytest

from reflex.config import ConfigError, expand_dependents, load_config
from reflex.models import Component


def write_config(tmp_path, text: str) -> Path:
    p = tmp_path / "reflex.config.yml"
    p.write_text(text)
    return p


def test_load_config_parses_adapter_options_and_settings(tmp_path):
    path = write_config(
        tmp_path,
        """
        settings:
          model_path: /models/laya-english
          threshold: 0.7
        components:
          web-app:
            path: web-app/
            adapter: playwright-bdd
            adapter_options:
              features_dir: e2e/features
              generated_dir: .features-gen/e2e
        """,
    )
    cfg = load_config(path)
    assert cfg.model_path == "/models/laya-english"
    assert cfg.threshold == 0.7
    assert cfg.components[0].adapter_options == {
        "features_dir": "e2e/features",
        "generated_dir": ".features-gen/e2e",
    }


def test_load_config_defaults_adapter_options_to_empty_dict(tmp_path):
    path = write_config(
        tmp_path,
        """
        components:
          backend:
            path: backend/
            adapter: pytest
        """,
    )
    cfg = load_config(path)
    assert cfg.components[0].adapter_options == {}
    assert cfg.threshold == 0.5  # library default when settings: is absent


def test_load_config_rejects_unknown_dependents(tmp_path):
    path = write_config(
        tmp_path,
        """
        components:
          core:
            path: core/
            adapter: pytest
            dependents: [nonexistent]
        """,
    )
    with pytest.raises(ConfigError, match="unknown dependents"):
        load_config(path)


def test_load_config_requires_path_and_adapter(tmp_path):
    path = write_config(
        tmp_path,
        """
        components:
          core:
            path: core/
        """,
    )
    with pytest.raises(ConfigError):
        load_config(path)


def make(name, dependents=None):
    return Component(name=name, path=f"{name}/", adapter="pytest", dependents=dependents or [])


def test_expand_dependents_direct():
    components = [make("core", dependents=["backend", "web-app"]), make("backend"), make("web-app")]
    forced = expand_dependents(components, {"core"})
    assert forced == {"backend", "web-app"}


def test_expand_dependents_transitive():
    # util -> core -> backend: changing util should force both core and backend
    components = [
        make("util", dependents=["core"]),
        make("core", dependents=["backend"]),
        make("backend"),
    ]
    forced = expand_dependents(components, {"util"})
    assert forced == {"core", "backend"}


def test_expand_dependents_no_changes_forces_nothing():
    components = [make("core", dependents=["backend"]), make("backend")]
    forced = expand_dependents(components, set())
    assert forced == set()


def test_expand_dependents_unrelated_change_forces_nothing():
    components = [make("core", dependents=["backend"]), make("backend"), make("util")]
    forced = expand_dependents(components, {"util"})
    assert forced == set()
