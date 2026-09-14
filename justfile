# A plain POSIX shell, not zsh: the same recipes run on Linux, macOS, and
# Windows (under Git Bash or WSL).
set shell := ["sh", "-cu"]

uv := "uv"

_default:
    @just --list

# Install development and documentation dependencies.
[group('env')]
install:
    {{ uv }} sync --dev --group docs

# Alias for `install`.
[group('env')]
dev: install

# Upgrade the lockfile and resynchronize the environment.
[group('env')]
update:
    {{ uv }} lock --upgrade
    {{ uv }} sync --dev --group docs

# Format Python sources in place.
[group('style')]
style:
    {{ uv }} run ruff format .
    {{ uv }} run ruff check --fix .
    just --fmt

# Verify formatting without modifying files.
[group('style')]
style-check:
    {{ uv }} run ruff format --check .
    {{ uv }} run ruff format --check .github/skills/gh-release-notes/scripts

# Alias for `style`.
[group('style')]
fmt: style

# Alias for `style-check`.
[group('style')]
fmt-check: style-check

# Run Ruff linting.
[group('lint')]
lint:
    {{ uv }} run ruff check .
    {{ uv }} run ruff check .github/skills/gh-release-notes/scripts

# Alias for `lint`.
[group('lint')]
ruff-check: lint

# Run strict type checking.
[group('lint')]
typecheck:
    {{ uv }} run mypy src
    {{ uv }} run mypy .github/skills/gh-release-notes/scripts

# Alias for `typecheck`.
[group('lint')]
mypy: typecheck

# Run the test suite.
[group('test')]
test:
    {{ uv }} run pytest src/ai_guidelines/tests

# Run the test suite in parallel.
[group('test')]
test-fast:
    {{ uv }} run pytest -n auto src/ai_guidelines/tests

# Run the test suite with the coverage gate.
[group('test')]
tests-coverage:
    {{ uv }} run pytest -n auto --cov=ai_guidelines --cov-report=term-missing --cov-report=xml --cov-fail-under=90 src/ai_guidelines/tests

# Run the slow build-backend tests excluded from the default gate.
[group('test')]
test-slow:
    {{ uv }} run pytest -m slow src/ai_guidelines/tests

# Regenerate CHANGELOG.md from conventional commits.
[group('docs')]
changelog:
    {{ uv }} run cz changelog

# Regenerate the changelog and build the documentation.
[group('docs')]
docs: changelog
    {{ uv }} run sphinx-build -b html docs/source docs/_build/html

# Build the documentation with warnings as errors (non-mutating).
[group('docs')]
docs-check:
    {{ uv }} run sphinx-build -W -b html docs/source docs/_build/html
    {{ uv }} run sphinx-build -W -b doctest docs/source docs/_build/doctest

# Serve the documentation with live reload.
[group('docs')]
docs-serve:
    {{ uv }} run sphinx-autobuild --watch src docs/source docs/_build/html

# Open the built documentation.
[group('docs')]
[macos]
docs-open: docs
    open docs/_build/html/index.html

# Open the built documentation.
[group('docs')]
[linux]
docs-open: docs
    xdg-open docs/_build/html/index.html

# Build the wheel and source distribution.
[group('build')]
build:
    {{ uv }} build

# Update project-managed guidelines from their registries.
[group('guidelines')]
update-guidelines:
    {{ uv }} run guidelines update

# Main quality gate. Formatting checks, tests, docs, and package build all run here.
[group('gate')]
preflight:
    just style-check
    just lint
    just typecheck
    just tests-coverage
    just docs-check
    just build
    @echo "✅ Preflight passed (style, lint, types, coverage, docs, build)."

# CI gate. Identical to `preflight` by construction.
[group('gate')]
ci-check: preflight

# Alias for `preflight`.
[group('gate')]
check: preflight

# List all recipes.
[group('gate')]
help:
    @just --list
