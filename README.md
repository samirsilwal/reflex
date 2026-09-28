# Reflex

**Reflex** is a language-agnostic PR test-selection layer for CI. It uses
[Laya](../README.md) (a small "System-1" text classifier) as a *fast,
intuitive gut-check* on top of deterministic static analysis, to decide
which test suites are actually relevant to a pull request's diff — so CI
only runs those, instead of the full suite every time.

No feedback loop, no persisted state across runs. Every run recomputes its
decision from the diff + repo structure, and prints its reasoning to the
job summary for that run only.

## Why "Reflex"

Laya is described as a System-1 (fast, reflexive) decision model, as
opposed to a slow, deliberate System-2 reasoner. Reflex uses it the same
way: a fast pre-filter that runs *before* the deliberate, expensive full
test run — not a replacement for it.

## How it works

```
diff (git) ──▶ component mapping ──▶ per component:
                                        │
                         ┌──────────────┴───────────────┐
                         │                               │
                 static dependency graph          Laya semantic fallback
                 (deterministic, free)            (fuzzy / cross-boundary)
                         │                               │
                         └──────────────┬────────────────┘
                                        ▼
                              selected test ids
                                        │
                                        ▼
                          adapter.run(selected_ids)
```

1. **Diff extraction** — `git diff <base>...<head>` → changed files.
2. **Component mapping** — each changed file is assigned to a component
   (`web-app`, `backend`, `core`, `util`, …) via `reflex.config.yml`.
3. **Adapters** — each component declares an adapter (`pytest`, `jest`,
   `mocha`, `vitest`, `playwright`, `playwright-bdd`, or a custom one).
   Adapters only need to
   implement:
   - `collect()` → list of test units (id, file, title)
   - `dependency_graph()` → optional static import graph (file → tests
     that transitively depend on it). Return `None` if unsupported.
   - `can_analyze(file_path)` → whether the graph can reason about *this
     specific* changed file. Resolution is **per-file, not per-component**:
     e.g. Playwright-BDD deterministically maps `.feature` file changes
     (via the generated-spec naming convention) while step-definition file
     changes fall back to Laya — both signals combine within one component.
   - `run_command(selected_ids)` → the actual filtered test-run invocation.
   - `full_run_command(junit_path)` *(optional)* → run everything, writing
     JUnit XML — used only by shadow mode (see below).
4. **Selection**:
   - **Foundational components** (`core`, `util`, anything else marked
     `foundational: true` in config) → when *that* component itself has
     changed files, run its own full suite **and** force a full run of
     every component listed in its `dependents: [...]` (transitively — see
     `expand_dependents()` in `reflex/config.py`). A foundational component
     with zero changed files behaves like any other component: it does not
     force anything. Blast radius is too high to trust semantic filtering
     once a foundational component *has* changed, and this list is a
     small, manually curated, cheap-to-maintain config — not a learned
     feedback loop.
   - **Files a component's adapter can statically analyze** → hard-include
     any test whose transitive import closure touches that file.
     Deterministic, free, and the primary signal.
   - **Files it can't** (cross-language/cross-repo effects, config,
     migrations, BDD step-definition files, components without a graph
     adapter at all) → Laya `noul` ("is this suite affected?")
     classification, run **once per test file/suite**, not per individual
     test, to keep model calls bounded regardless of repo size.
   - Tests inside changed test files themselves are always included.
5. **Fail-open safety nets**: no adapter registered for a changed path, or
   an unresolvable file with no classifier configured → run that
   component's full suite. Selection empty → run full suite. PR label
   `run-all-tests` forces full suite for every component.
6. **Execution** — orchestrator calls each affected adapter's
   `run_command()` with its selected ids.
7. **Shadow mode** (`--shadow`, safe rollout) — always runs the *full*
   suite (ground truth for that run), but also computes what the selector
   would have picked, and diffs them: any file that actually failed but
   wasn't in the selection is flagged `⚠ would have MISSED`. This
   comparison is done at file granularity via each test framework's JUnit
   XML output (pytest's `--junitxml` works out of the box; Jest/Mocha/
   Playwright need their respective junit reporter configured). Nothing is
   persisted between runs — it's printed to that run's job summary only.

## Configuration

Reflex is intentionally **not** coupled to any single repo layout, language,
or test framework — everything repo-specific lives in `reflex.config.yml`,
not in Reflex's source.

```yaml
settings:
  # Optional. Reflex auto-detects its own bundled model at
  # reflex/models/laya-english/ when omitted. Set model_path only to
  # point at a different/external model, or leave both unset (no bundled
  # model present) to run static-graph-only.
  threshold: 0.5

components:
  web-app:
    path: web-app/
    adapter: jest              # built-in short name (see ADAPTER_REGISTRY)
    dependents: []

  e2e:
    path: web-app/playwright/
    adapter: playwright-bdd
    # Forwarded verbatim as kwargs to the adapter's constructor. Lets you
    # express repo-specific conventions (custom bin, non-default folder
    # names, ...) from config instead of editing adapter source.
    adapter_options:
      features_dir: e2e/features
      generated_dir: .features-gen/e2e

  custom-service:
    path: services/custom/
    # Not a Reflex built-in: a fully custom/plugin adapter, dynamically
    # imported at run time. Must be importable in the Action's Python env
    # (e.g. installed via requirements.txt or a local path on PYTHONPATH).
    adapter: myorg.reflex_adapters.gotest:GoTestAdapter
    adapter_options:
      go_bin: go

  core:
    path: core/
    adapter: pytest
    foundational: true
    dependents: [backend, web-app]   # forced full-run when core changes
```

### Config reference

| Key | Scope | Required | Default | Description |
|---|---|---|---|---|
| `settings.model_path` | global | no | auto-detected bundled model (`models/laya-english/`), or `None` | Path to a local Laya model directory. Overridden by `--model-path` CLI flag. Static-graph-only (no semantic fallback) if left unset and no bundled model is present. |
| `settings.threshold` | global | no | `0.5` | Laya `noul` probability threshold above which a suite is considered "affected". Overridden by `--threshold` CLI flag. |
| `components.<name>` | component | yes (≥1) | — | Component key; used as the display name and as the target of other components' `dependents:` lists. |
| `components.<name>.path` | component | **yes** | — | Path to the component's root, relative to the repo root. Diff files are mapped to components by longest-prefix match on this. |
| `components.<name>.adapter` | component | **yes** | — | Either a built-in short name (`pytest`, `jest`, `mocha`, `vitest`, `playwright`, `playwright-bdd`) from `ADAPTER_REGISTRY`, or a `"module.path:ClassName"` string for a dynamically-imported custom adapter. |
| `components.<name>.adapter_options` | component | no | `{}` | Mapping forwarded as kwargs to the adapter's constructor (e.g. `jest_bin`, `features_dir`, `generated_dir`) — how repo-specific conventions are configured without touching adapter source. |
| `components.<name>.foundational` | component | no | `false` | When `true` **and** this component itself has changed files, forces a full run of this component's own suite (semantic/static filtering is skipped as too risky). Has no effect if the component has zero changed files. |
| `components.<name>.dependents` | component | no | `[]` | Other component names to force a full run on when *this* component changes (regardless of whether the dependent itself changed). Propagates transitively via `expand_dependents()`. Every name listed must match another component key in the same file, or config loading fails validation. |

- **Built-in adapters** (`pytest`, `jest`, `mocha`, `vitest`, `playwright`,
  `playwright-bdd`) live in `ADAPTER_REGISTRY` (`reflex/select.py`) as
  short-name conveniences — they are defaults, not a hard dependency.
- **Any other framework** (Go, RSpec, JUnit/Gradle, a bespoke in-house
  runner, ...) is supported the same way, without touching Reflex's
  source: implement the `Adapter` protocol (`reflex/adapters/base.py`) in
  your own package and reference it as `adapter: "your.module:YourClass"`.
- **`adapter_options`** are per-component kwargs passed straight through to
  the adapter's constructor — this is how naming conventions, binaries, or
  directory layouts that differ per repo get configured without forking
  Reflex or hardcoding a specific layout into adapter source.
- **`settings.model_path` / `settings.threshold`** are config-file defaults
  for the classifier; CLI flags (`--model-path`, `--threshold`) always
  override them, so a CI job can e.g. point at a different model without
  editing the checked-in config. If `model_path` is unset entirely, Reflex
  auto-detects its own bundled model at `models/laya-english/` (see
  `reflex/config.py::default_model_path()`) — the repo is self-contained
  and works out of the box with no external model path required.
- Config validation (`reflex/config.py::load_config`) rejects components
  missing `path`/`adapter`, and any `dependents` entry that doesn't name a
  real component in the same file, at parse time — before any git/CI work
  runs.

## Layout

```
reflex/
├── action.yml                       # composite GitHub Action
├── reflex.config.example.yml        # minimal component → adapter config
├── reflex.config.laudio-example.yml # modeled on the actual ~/laudio repos (see below)
├── requirements.txt
├── models/
│   └── laya-english/          # bundled Laya model (Git LFS, see .gitattributes)
├── reflex/
│   ├── models.py              # TestUnit, Component dataclasses
│   ├── config.py              # load reflex.config.yml, bundled-model auto-detect
│   ├── diffutil.py            # git diff + component mapping
│   ├── classifier.py          # Laya noul wrapper, suite-level batching
│   ├── junit.py                # JUnit XML reader, used by shadow mode
│   ├── select.py               # CLI entrypoint / orchestrator + shadow mode
│   ├── adapters/
│   │   ├── base.py            # Adapter protocol
│   │   ├── pytest_adapter.py
│   │   ├── jest_adapter.py
│   │   ├── mocha_adapter.py
│   │   ├── vitest_adapter.py
│   │   ├── playwright_adapter.py       # plain @playwright/test
│   │   └── playwright_bdd_adapter.py   # .feature + playwright-bdd
│   └── graph/
│       ├── python_graph.py    # ast-based import graph
│       └── js_graph.py        # madge-based import graph
└── tests/
    ├── test_select.py         # selection-logic unit tests (Laya mocked)
    └── test_config.py         # config parsing, dependents-propagation tests
```

Everything under this one `reflex/` folder is self-contained and can be
pushed to its own repo as-is — `models/laya-english/` (~820MB) is tracked
via Git LFS (`.gitattributes`), so a normal `git clone` still pulls it
without any external/absolute path configuration.

## Usage

```yaml
- uses: ./reflex
  with:
    base-ref: ${{ github.event.pull_request.base.sha }}
    config: reflex.config.yml
```

See `reflex.config.example.yml` for the config format.

## Performance

Measured locally (Apple Silicon, `laya==0.3.20`, ModernBERT-large,
Reflex's bundled `models/laya-english`):

| | MPS (GPU) | forced CPU |
|---|---|---|
| Model load | ~4.9s | ~3.7s |
| Per `predict()` call (warm) | ~29ms | ~76ms |

Laya is called **once per affected test suite/file**, not per test, so
even on a CPU-only GitHub-hosted runner (older/shared cores than the
above — expect this to land somewhere in the ~100–250ms/call range),
total classification time for ~50 suites is a few seconds. The dominant
cost in the pipeline is the static dependency graph build, not Laya —
cache it (keyed on lockfile hash) and only rebuild when manifests change.
