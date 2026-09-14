# AGENTS.md — ai-guidelines project map

Read `CONSTITUTION.md` before changing the project.

## Technology stacks

- `uv`
- `just`

## Where to Find Rules

- Development, security, API, dependency, testing, and commit rules:
  `CONSTITUTION.md`.
- Domain-specific rules and reusable guidance: `.github/guidelines/`.
  Read only the guideline relevant to the current change.
- Exact commands and quality gates: `justfile --list`.

## Guidelines

- `.github/guidelines/git-commit-message.guidelines.md`: Writing good Git commit messages
- `.github/guidelines/python-module-documentation.guidelines.md`: Writing Python module/function
  docstring and documentation
- `.github/guidelines/python-unittest.guidelines.md`: Writing and organizing Python unit tests
- `.github/guidelines/markdown-line-wrap-by-clause.guidelines.md`: how to wrap lines in user-facing Markdown file

Precedence rule:

- CONSTITUTION > AGENTS.md > Guidelines > skills > other prompts

## Quality Gate

You HAVE TO ensure the main quality gate (`just preflight`) always pass
at end of your changes.
Always ensure to fix all issues reported in the best possible way,
to ensure the upmost quality of the project.

## Main Commands

```text
just install      # install development dependencies
just fmt          # format Python code
just fmt-check    # verify formatting
just ruff-check   # run Ruff linting
just mypy         # run strict type checking
just test         # run pytest with coverage and xdist
just build        # build sdist and wheel
just preflight    # main quality gate
just update       # update locked dependencies
```
