---
name: Dependency Management Guidelines
description: Dependency and reproducibility rules for ai-guidelines
---

# Dependency Management Guidelines

Use `uv` with the repository's `uv.toml` and commit intentional `uv.lock`
changes. Keep runtime dependencies minimal, public, and compatible with Python
3.10+.

Use `just update` for dependency upgrades. Do not rely on inherited user-level
package indexes or hidden environment configuration. Do not add a dependency
when a small standard-library implementation is sufficient.
