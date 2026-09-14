# ai-guidelines

`ai-guidelines` manages reusable Markdown guidelines for a project.

It allows to declare dependencies on reference guidelines (on a reference git project)
and copy/update them.

## What is Guidelines?

A **guideline** is reusable Markdown context containing rules or practices.
It is declared in the project context (usually in `AGENTS.md`) and the
agent idenfies when it will have to use it, especially during planning.

> [!IMPORTANT]
> Guidelines work like skills, as they are loaded by progressive disclosure.

## Why not Intruction or rules?

An **instruction file** is a GitHub Copilot specific file that is loaded
or injected by the Coding Agent according to its file-pattern rules.
**During planning, when the agent is thinking about the files** to edit for instance,
the coding agent does not see any file of this type opened,
and so **MAY not have loaded the relevant instruction files at this point**.

> [!WARNING]
> Using progressive disclosure allows to **see** when the agent loads a given guideline (or skill),
> which is harder to detect using instruction files.

This means instructions are only good to encode some low level coding
preference, not move elaborated preferences.

## Why not using skills?

You can definitively use skills to encore your coding standard preferences.
But they will all be placed at a single place in your project (ex: `.github/skills/` folders),
making them hard to distinguish from other skills, for you and your agent.

> [!TIP]
> In a nutshell, if you declare guidelines in your project, you can just say
> "do XX respecting project guidelines" and even small models will follow the right ones.

A **skill** is a self-describing knowledge package that declares activation
or loading behavior.
A **Skill** can contain coding standards, and act exactely like guidelines,
but it is good to place them in a separate location with a clear name.

This tool only installs explicitly selected guidelines:
Put project-owned loading rules in `AGENTS.md`, `CONSTITUTION.md`,
or another project convention.

> [!NOTE]
> TL;DR: Guidelines are like skills but placed in a different location within the
> source tree.

## Guideline, instruction, and skill

A **guideline** is reusable Markdown context containing rules or practices.
It is declared in the project context (usually in `AGENTS.md`) and the
agent idenfies when it will have to use it, especially during planning.

An **instruction file** is a GitHub Copilot specific file that is loaded
or injected by the Coding Agent according to its file-pattern rules.
During planning, when the agent is thinking about the files to edit for instance, the coding agent does not see any file of this type opened,
and so MAY not have loaded the relevant instruction files at this point.
This means instructions are only good to encode some low level coding
preference, not move elaborated preferences.

A **skill** is a self-describing knowledge package that declares activation or loading behavior. A Skill can contain coding standards, and act exactely
like guidelines, but it is good to place them in a separate location
with a clear name.

This tool only installs explicitly selected guidelines:
Put project-owned loading rules in `AGENTS.md`, `CONSTITUTION.md`,
or another project convention.


## Install

```bash
uv tool install ai-guidelines

guidelines --version
```

Git is required for remote sources; local folders need no Git.

## Quick start

Create `guidelines.yml`:

```yaml
version: 1
default_guidelines_path: .github/guidelines
guidelines:
  - source: https://github.com/example/team-guidelines.git
    ref: main
    pattern: "*.guideline.md"
```

Run `guidelines sync`, then `guidelines list`, `guidelines search LOCATION QUERY`,
`guidelines outdated`, or `guidelines update --dry-run`. Add sources with
`guidelines add LOCATION [PATTERN] --ref REF --target-path PATH --alias NAME` and remove
declarations with `guidelines remove IDENTIFIER`; installed files are preserved. `sync --dry-run`
previews changes, while `sync --frozen` replays complete locked state without resolution or writes.
Use `--refresh` and `--no-cache` with search, and `guidelines cache size` to inspect the cache.
Selectors match both `.guideline.md` and `.guidelines.md` source files, including when the suffix is
omitted. Missing source paths are reported as concise CLI errors rather than
Python tracebacks. Installed files always use the canonical `.guidelines.md` suffix.

## Documentation

Full documentation — tutorials, how-to guides, CLI and format reference, and design
rationale — is at <https://ai-guidelines.readthedocs.io/>.

Start with the [getting-started tutorial](docs/source/tutorials/getting-started.md). To
publish guidelines for your own team, commit them to a Git repository and declare it as a
source — see [source grammar](docs/source/reference/source-grammar.md). For how acquired
sources are cached, see [how caching works](docs/source/explanation/caching.md).

See also [`SECURITY.md`](SECURITY.md) and the [`examples/`](examples/) directory.
