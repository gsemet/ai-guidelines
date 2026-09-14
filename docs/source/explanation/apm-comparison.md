# Relationship to APM

`ai-guidelines` and APM are conceptually aligned where both help teams share reusable guidance
for coding work.
A project may use both: APM-managed material and guideline files can coexist,
and an agent may load either according to the project's own instructions.

This project does not depend on an APM package, service, runtime, or file importer.
It implements its own provider-neutral manifest, lockfile, acquisition, and reconciliation
contracts.
The similarity is interoperability at the level of human-readable guidance, not copied
implementation.
Use ordinary Git or local sources to share files between systems when their formats are compatible.

## Using APM without `ai-guidelines`

If the guidance should be automatically associated with files opened by a Coding Agent,
use APM to distribute Copilot instruction files and do not use `ai-guidelines` for that material.
Create a file such as `.github/instructions/python.instructions.md` with YAML frontmatter and an
`applyTo` glob:

```markdown
---
description: Python testing rules
applyTo: "**/test_*.py,**/*_test.py"
---

Use the project's approved Python test patterns.
```

Package and install that instruction with APM according to its documentation.
The `applyTo` glob controls when the Coding Agent loads it.
This is the appropriate choice when automatic file-pattern activation is desired;
it also means the guidance is not guaranteed to be present for planning or reasoning that is
unrelated to an opened matching file.

For manually controlled, project-owned loading, keep the material as a guideline and reference it
from `AGENTS.md` instead.
Do not duplicate the same rules in both an APM instruction and a managed guideline unless the
project deliberately accepts the maintenance cost and possible divergence.
