# ai-guidelines Constitution

## Purpose

`ai-guidelines` is an independent, MIT-licensed Python package for declaring,
acquiring, discovering, and synchronizing reusable Markdown guidelines.

The project must remain independent of any agent host, plugin marketplace,
private service, catalog system, or vendor-specific deployment environment.

## Inviolable Principles

### Public standalone product

- The distribution name is `ai-guidelines`.
- The import package is `ai_guidelines`.
- The command-line entry point is `guidelines`.
- Public contracts must not name or expose compatibility with any specific
  agent host, plugin system, dashboard, catalog, or requirements runtime — not
  even to declare it unsupported.
- Do not add private infrastructure URLs, proxy settings, credentials, or
  vendor-specific deployment configuration.

### Safety before side effects

- Validate manifest, lockfile, source, selector, revision, and target inputs
  before invoking Git or writing project files.
- Never execute Markdown or source content as code.
- Keep all project destinations contained within the consumer project.
- Reject traversal, wildcard, control-character, credential-bearing, and
  option-like inputs where they are not explicitly supported.
- Preserve hash ownership and use conservative deletion for managed files.
- Build and validate complete destination plans before mutating the project.

### Reproducibility

- Normalize equivalent declarations into deterministic representations.
- Preserve credential-free source identity and resolved revision provenance.
- Treat lockfiles as versioned public contracts.
- Frozen operation must be strict, resolution-free, and write-free when exact
  replay state is unavailable.
- Publish lock state only after reconciliation succeeds.

### Small, typed modules

- Keep domain behavior in focused modules rather than duplicating it in CLI
  callbacks.
- Use Pydantic v2 for validated data models.
- Use absolute `ai_guidelines.*` imports; relative imports are not permitted.
- Add type hints to maintained Python code and keep mypy clean.
- Preserve a small, deliberate public facade. Internal helpers remain private.

## Development Standards

- Supported Python versions: 3.10 and newer.
- Use `uv` for dependency management and the repository's `uv.toml` for index
  configuration.
- Use `just preflight` as the main local quality gate.
- Keep `uv.lock` reproducible and update it when dependency declarations change.
- Use pytest function-based tests for behavior and security regressions.
- Add focused tests for every new public contract and failure mode.
- Use Ruff formatting and linting, and strict mypy configuration.
- Use Conventional Commits with concise, user-impact-oriented subjects.
- Do not weaken checks to make a change pass.

Follow the applicable guidelines in `.github/guidelines/`.

## Quality Gate

Run the complete gate before considering a change complete:

```text
just preflight
```

The gate checks formatting, linting, typing, tests with coverage, and package
building. If a check fails, fix the underlying issue and rerun the gate.

## Change Boundaries

- Do not modify another repository as part of an `ai-guidelines` change.
- Do not copy implementation-specific comments, namespaces, or compatibility
  models from unrelated host products.
- Prefer local temporary Git repositories and injected fakes in tests; tests
  must not require private services or network access.
- Update documentation when a public command, option, file format, or API
  behavior changes.

## Decision Authority

This document defines the foundational project rules. `AGENTS.md` explains how
coding agents apply them. Existing public behavior, tests, and the package
contracts are evidence; when they conflict with these principles, stop and
clarify the intended contract before implementing a risky change.
