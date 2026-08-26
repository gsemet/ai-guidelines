---
name: Python Development Guidelines
description: Python implementation and API rules for ai-guidelines
---

# Python Development Guidelines

Use Python 3.10+ syntax and type every maintained function signature. Keep
modules focused and use absolute `ai_guidelines.*` imports; relative imports are
not permitted.

Use Pydantic v2 for persisted domain models and validate external input before
filesystem or Git side effects. Keep public exports deliberate and preserve
sanitized errors at public boundaries.

Add useful Google-style docstrings to public APIs. Include `Args`, `Returns`,
and `Raises` sections when they clarify non-obvious behavior.
