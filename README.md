# ai-guidelines

`ai-guidelines` manages reusable Markdown guidelines for a project.

It allows users to declare dependencies on reference guidelines (on a reference git project)
and copy/update them.

## What are guidelines?

A **guideline** is reusable Markdown context containing rules or practices.
It is declared in the project context (usually in `AGENTS.md`),
and the agent identifies when it has to use it, especially during planning.

> [!IMPORTANT]
> Guidelines work like skills, as they are loaded by progressive disclosure.

## Any example?

Sure, look at this very project!
For instance, look at how
[`.github/guidelines/git-commit-message.guidelines.md`](.github/guidelines/git-commit-message.guidelines.md)
declares the rules for writing conventional Git commit messages in this project,
and how it is referenced in `AGENTS.md`.

This means the pointer to the rules for writing Git commit messages is always in the
start of the Context Windows, so it is in the zone of attention of the LLM.
Any agent working in this project will naturally follow this convention,
even if it does not have the same set of skills or plugins installed as the original author.

Also note the clear precedence rules in `AGENTS.md`:

```md
Precedence rule:

- CONSTITUTION > AGENTS.md > Guidelines > skills > other prompts
```

This means that a guideline that is meant to be reusable across several projects can conflict
with another rule.
The CONSTITUTION.md and AGENTS.md take precedence, giving the agent the correct guidance on
which rules to follow.

Note that for small projects, `CONSTITUTION.md` or equivalent is not really needed.
But since `AGENTS.md` is always in context it should not grow too much.
When it becomes too large, move the rules to modify your project
to a dedicated `CONSTITUTION.md` file.

They are written in this project.
They are actually declared in [`guidelines.yml`](guidelines.yml) and are recopied into the
project's guidelines folder when `guidelines update` is run (see the [`justfile`](justfile)).

They are maintained in the sister-project
[Guidelines Registry](https://github.com/gsemet/ai-guidelines-registry).

## Why not instructions or rules?

An **instruction file** is a GitHub Copilot specific file that is loaded
or injected by the Coding Agent according to its file-pattern rules.
During planning, when the agent is thinking about the files to edit, for instance, the coding
agent does not see any file of this type opened, and so may not have loaded the relevant
instruction files at this point.

> [!WARNING]
> Using progressive disclosure allows to **see** when the agent loads a given guideline (or skill),
> which is harder to detect using instruction files.

This means instructions are only good for encoding some low-level coding preferences,
not for moving elaborated preferences.

## Why not use skills?

You can definitely use skills to encode your coding standard preferences.
But they will all be placed in a single location in your project (for example, `.github/skills/`),
making them hard to distinguish from other skills for you and your agent.

> [!TIP]
> In a nutshell, if you declare guidelines in your project, you can just say
> "do XX respecting project guidelines" and even small models will follow the right ones.

A **skill** is a self-describing knowledge package
that declares activation or loading behavior.
A **Skill** can contain coding standards and act exactly like guidelines,
but it is good to place it in a separate location with a clear name.

> [!IMPORTANT]
> If you use skills to encode your coding standards, use [Microsoft APM](https://github.com/microsoft/apm)
> to do the same than this project does. Actually, this project started because
> APM does not support guidelines files; see the following Github issue
> [apm#2525](https://github.com/microsoft/apm/issues/2525).

This tool only installs explicitly selected guidelines:
Put project-owned loading rules in `AGENTS.md`, `CONSTITUTION.md`,
or another project convention.

> [!NOTE]
> TL;DR: Guidelines are like skills but placed in a different location within the
> source tree.

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
`guidelines outdated`, or `guidelines update --dry-run`.
Add sources with `guidelines add LOCATION [PATTERN] --ref REF --target-path PATH --alias NAME`
and remove declarations with `guidelines remove IDENTIFIER`; installed files are preserved.
`sync --dry-run` previews changes, while `sync --frozen` replays complete locked state without
resolution or writes.
Use `--refresh` and `--no-cache` with search, and `guidelines cache size` to inspect the cache.
Selectors match both `.guideline.md` and `.guidelines.md` source files, including when the suffix
is omitted.
Missing source paths are reported as concise CLI errors rather than Python tracebacks.
Installed files always use the canonical `.guidelines.md` suffix.

## Documentation

Full documentation (tutorials, how-to guides, CLI and format reference, and design rationale)
is at <https://ai-guidelines.readthedocs.io/>.

Start with the [getting-started tutorial](docs/source/tutorials/getting-started.md).
To publish guidelines for your own team, commit them to a Git repository and declare it as a
source: see [source grammar](docs/source/reference/source-grammar.md).
For how acquired sources are cached, see [how caching works](docs/source/explanation/caching.md).

See also [`SECURITY.md`](SECURITY.md) and the [`examples/`](examples/) directory.
