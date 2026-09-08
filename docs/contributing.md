# Contributing

## Setup

```bash
git clone https://github.com/gabrielfrasantos/port-bridge.git
cd port-bridge
pip install -e ".[dev,gui]"
```

## Running tests

```bash
pytest tests/ -v
```

Tests stub all hardware dependencies — no physical serial port or CAN adapter required.

## Linting and formatting

```bash
ruff check .          # lint
ruff format .         # format
mypy                  # type-check
```

CI enforces all three. Fix linting errors before opening a PR.

## Commit style

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add GUI dark mode toggle
fix: prevent CAN bridge hang on adapter removal
docs: clarify gs_usb Windows driver steps
chore: update python-can to 4.4
ci: add Windows test matrix
```

`release-please` reads commit messages to determine the next version and generate the changelog automatically.

## Adding a new CAN backend

1. Add detection logic to `list_can_interfaces.detect_python_can_configs` (if python-can supports it) or add a new `detect_*` function.
2. If a custom bus class is needed (like `CandleBus`), add it as `portbridge/<name>_bus.py`.
3. Wire the factory in `CanBusOverTcpServer.start()`.
4. Add a test in `tests/` that stubs the new backend.
5. Document the adapter in `docs/adapters.md`.

## Project structure

```
portbridge/          Core package (CLI + bridge logic)
portbridge/gui/      Optional GUI (requires PySide6)
tests/               Unit tests (no hardware required)
docs/                Documentation
.github/workflows/   CI, release, CodeQL
```

## Release process

Releases are fully automated via `release-please`:

1. Merge PRs with Conventional Commit messages to `main`.
2. `release-please` opens a release PR updating the version in `pyproject.toml` and `CHANGELOG.md`.
3. Merging that PR creates a GitHub release and triggers PyPI publish via OIDC.

Manual release is possible via `workflow_dispatch` on the `release-please` workflow.
