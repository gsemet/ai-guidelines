---
name: Python Unit Test Guideline
version: 3.0
description: Guidelines for Python unit testing
metadata:
  owner: Gaetan Semet <gaetan.semet@ampere.cars>
  keywords: [python, testing, unit-test, pytest, fixtures]
  guideline-id: 0b2bac4f-c5f2-4c24-a3cb-aecf5df02dbf
---

# Python Unit Test Guidelines

Use pytest, function-based tests, and pytest fixtures.

## Rules

1. Write standalone `def test_*():` functions, not test classes.
2. For multiple inputs with the same logic, use `@Parametrization` from the
   `pytest-parametrization` dev dependency, not `pytest.mark.parametrize`.
3. Use pytest fixtures instead of manual setup/teardown, including
   `tmp_path`, `monkeypatch`, `caplog`, and `mocker`.
4. Keep test code at most 120 characters wide. Preserve indentation in inline
   code and strings, using `textwrap.dedent()` when needed.
5. Keep tests as close as possible to the module under test. Put
   `test_<modulename>.py` in the `tests/` subdirectory parallel to its source
   module; for example, tests for `src/mypackage/models.py` go in
   `src/mypackage/tests/test_models.py`.
6. Name every test file `test_<modulename>.py`, matching the module it covers.
   One test module per source module.
7. Put test data, fixtures, and golden files in a `vectors/` subfolder
   relative to the tests that use them, never in a shared top-level data
   directory and never inline as large literals.
8. Never depend on a private service, an external repository, a network
   endpoint, or a developer's home directory. Use `tmp_path` for filesystem
   work and `monkeypatch` to isolate the environment.
9. Discover repository paths through a session-scoped `conftest.py` fixture
   that walks upward to a marker file such as `pyproject.toml`. Never compute
   a root with a fixed `Path(__file__).parents[N]` index, which silently
   breaks whenever the test tree moves.
10. Mark tests that shell out to a build backend, or that require network
    access, with a dedicated marker and deselect them from the default gate.

## Tools

- Framework: pytest.
- Parametrization: `pytest-parametrization`, not `pytest.mark.parametrize`.
- Fixtures: `tmp_path`, `monkeypatch`, `mocker`, and `caplog`.
- Set appropriate function, module, or session fixture scopes.

## Validation

Find the project's own test gate and run the full test suite with coverage
reporting. Do not assume a command name; read the project's task runner or
contributor documentation to discover it.

## Positive example

Layout:

```text
src/mypackage/
├── models.py
└── tests/
    ├── conftest.py
    ├── test_models.py
    └── vectors/
        └── sample_manifest.yml
```

Root discovery in `conftest.py`:

```python
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Return the repository root, discovered rather than computed by index."""
    for candidate in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("no pyproject.toml found above the test tree")
```

Filesystem work:

```python
def test_file_operations(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("key: value")

    assert config_file.exists()
    assert "key:" in config_file.read_text()
```

Using pytest-parametrization:

```python
from parametrization import Parametrization

@Parametrization.autodetect_parameters()
@Parametrization.case(name="valid_input", input_val=1, expected=2)
@Parametrization.case("zero_input", input_val=0, expected=1)
@Parametrization.case("negative_input", input_val=-1, expected=0)
def test_increment_function(input_val, expected):
    assert increment(input_val) == expected
```
