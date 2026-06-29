# Contributing

Thanks for your interest in improving **kissget**. This guide covers local setup,
the test/lint workflow, and the conventions the CI enforces. For how the code is
organised, read [ARCHITECTURE.md](ARCHITECTURE.md) first.

## Prerequisites

- **Python 3.10+** (CI tests 3.10–3.13)
- **[uv](https://docs.astral.sh/uv/)** — the project uses `uv` for dependency
  management and the `uv_build` backend.
- A Chromium browser (only needed to run the browser-based auth paths, not for tests).

## Setup

```console
git clone https://github.com/Neon-Solitude/kisskh-dl.git
cd kisskh-dl
uv sync
```

`uv sync` installs both runtime and `dev` dependencies (from the
`[dependency-groups] dev` table in [`pyproject.toml`](../pyproject.toml)) into a
local `.venv`. To run any command in that environment, prefix it with `uv run`.

For the browser-driven features (Workflows B and C), also install the Chromium
binary Playwright drives:

```console
uv run playwright install chromium
```

## Running the CLI from source

```console
uv run kissget --help
uv run kissget -vv dl --from-manifest manifest.json -s en -o .
```

`-v` raises the log level to INFO, `-vv` to DEBUG — invaluable when debugging the
API client or kkey capture.

## Quality checks

The CI ([`.github/workflows/pull-request.yml`](../.github/workflows/pull-request.yml))
runs the following on every push across the full Python × OS matrix. Run them
locally before opening a PR:

```console
uv run ruff check src tests          # lint
uv run ruff format --check src tests  # format check
uv run mypy --ignore-missing-imports src  # type check
uv run pytest                          # tests
```

To auto-fix formatting and lint issues:

```console
uv run ruff format src tests
uv run ruff check --fix src tests
```

### Coverage

```console
uv run pytest --cov=kissget
```

## Code style

Enforced by Ruff (config in [`pyproject.toml`](../pyproject.toml)):

- **Line length:** 120
- **Quotes:** double; spaces for indentation
- **Target:** `py310`
- **Lint rule sets:** pyflakes, pycodestyle (E/W), bugbear (B), isort (I),
  pep8-naming (N), comprehensions (C4), bandit (S), pyupgrade (UP)
- **Type hints:** required on public functions; `mypy` runs over `src`. Modules
  use `from __future__ import annotations` for forward-compatible typing.

A few project conventions worth matching:

- New API responses get a **Pydantic model** in [`models/`](../src/kissget/models/),
  using `Field(alias=...)` to map the site's camelCase JSON.
- Episode loops should **catch per-episode exceptions and continue** rather than
  aborting a batch (see [`cli.py`](../src/kissget/cli.py)).
- Any user-supplied string used in an output path must pass through
  `_sanitize_path_component()`.

## Tests

Tests live in [`tests/`](../tests/) and mirror the `src/kissget/` layout
(`tests/models/`, `tests/helper/`). The suite is **fully offline** — the API
client's `_request` method is replaced with a `MagicMock` so no network calls are
made (see [`tests/test_kisskh_api.py`](../tests/test_kisskh_api.py) for the
pattern). When adding a feature:

1. Add or extend a test next to the module you changed.
2. Mock external I/O (`_request`, `subprocess`, `requests.get`) — never hit the
   live site.
3. Keep fixtures realistic: copy the shape of a real API response, as the
   existing `Drama`/`Sub`/`Search` tests do.

## Submitting changes

1. Branch off `main` (`git checkout -b feature/your-change`).
2. Make the change with matching tests and docs.
3. Run the full quality-check block above — green locally means green in CI.
4. Open a PR following [`.github/PULL_REQUEST_TEMPLATE.md`](../.github/PULL_REQUEST_TEMPLATE.md).

## A note on legality and scope

kissget automates downloads from a third-party streaming site. Keep contributions
focused on the tool's mechanics (CLI ergonomics, resilience, download backends,
docs). Don't add features whose only purpose is evading site protections beyond
what's already required to make the tool function.
```
