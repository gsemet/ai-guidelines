# Contributing

Thanks for helping improve `ai-guidelines`. Contributions should preserve the provider-neutral,
copy-only safety boundary and include focused tests for public behavior or security regressions.

Agent will read `CONSTITUTION.md` before starting any development.

## Local development

Install the development environment with `just install`. Run the exact quality gate before
opening a pull request:

```console
just preflight
```

The gate runs Ruff format checking, Ruff linting, strict mypy, pytest with an 85% coverage
gate, and a Sphinx build with warnings as errors. It is provably non-mutating: it invokes only
`-check` recipe variants. `just ci-check` is a one-line alias to `preflight`, so CI and local
runs are identical by construction.

Tests must use local temporary repositories or fakes and must not require network services.

## Versioning

The version is **SCM-derived** via `hatch-vcs`. It is not written in `pyproject.toml`, in
`__init__.py`, or in any test. Never hardcode it. Release by dispatching the `Release`
workflow, which tags with Commitizen and publishes to PyPI through Trusted Publishing.

## Test layout

Tests live in `src/ai_guidelines/tests/`, adjacent to the modules they cover, and are excluded
from the wheel.

This is an **intentional divergence** from the common top-level `tests/` convention. It
follows the project-consumed `Python_Unit_Test` guideline, which requires tests to sit as close
as possible to the module under test.

Conventions:

- One `test_<modulename>.py` per source module.
- Test data goes in a `vectors/` subfolder next to the tests that use it.
- Discover the repository root through the session-scoped `project_root` fixture in
  `conftest.py`. Never use a fixed `Path(__file__).parents[N]` index.
- Tests that shell out to a build backend are marked `slow` and deselected from the default
  gate. Run them with `just test-slow`.

## Documentation

Documentation is Sphinx with MyST under `docs/source/`, organized by Diátaxis: `tutorials/`,
`how-to/`, `reference/`, `explanation/`. Build it with `just docs`, preview with
`just docs-serve`. The CLI and API reference pages are generated from the source, so keep
docstrings and Click help accurate rather than duplicating them by hand.

Use Conventional Commits with a concise, user-focused subject; `CHANGELOG.md` is generated
from them by Commitizen.
