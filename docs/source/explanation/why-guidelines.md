# Why guidelines

## Guidelines and progressive disclosure

A guideline is a focused Markdown document containing rules, patterns, or practices. Teams can
use progressive disclosure: keep a short rule summary available first, then load the relevant
guideline only when a task needs it. This keeps agent context useful without requiring every
rule for every task.

`ai-guidelines` installs selected files. It does not inject them, does not activate them, and
does not execute them. The installed Markdown is agent context, so the project's own loading
policy remains authoritative.

## Why guidelines instead of instruction files or skills?

Guidelines are project guardrails. They should be versioned and reviewed like source code,
because an unwanted change to a guideline can significantly affect how agents work. The
project owner chooses when to update them, rather than accepting an automatic refresh.

Skills are useful, but placing reusable guidelines in `.agents/skills/` mixes project
guardrails with other skills and exposes them to skill auto-activation through frontmatter
descriptions. A project may need the same guideline in different situations across different
projects; the project harness in `AGENTS.md` should decide when it is loaded.

`*.instructions.md` files are activated by an agent based on the files currently opened or
being edited. They may therefore not be loaded while a planning agent is thinking about a
change. They can be registered in `AGENTS.md`, but their `applyTo` behavior must be disabled
or otherwise controlled to avoid unwanted activation.

If reusable domain rules are intentionally packaged as `SKILL.md` or `*.instructions.md`, use
a package manager designed for that — see {doc}`apm-comparison`. This project supports the
simpler alternative: copy selected guideline files into the consumer project, commit them, and
explicitly reference them from the project harness.

## Project-owned loading

After synchronization, add a project-owned instruction to `AGENTS.md`, `CONSTITUTION.md`, or an
equivalent file. For example:

> When changing Python tests, read `.github/guidelines/python-testing.guidelines.md`.
> When writing commits, read `.github/guidelines/commit-message.guidelines.md`.

This explicit relationship means each project can choose names, paths, and loading conditions.
The same source guideline can therefore be reused across many projects without embedding a
project-specific activation system in the guideline manager.

## Copy, don't reference

Installed guidelines are committed into the consuming repository. This is deliberate.

The alternative — resolving guidelines at agent runtime from a remote — would mean the rules
governing your code review could change between two runs with no diff, no review, and no
record. Copying makes every change to your guardrails a reviewable commit, keeps agents
working offline, and makes `git log` the audit trail.

The cost is that you must run `guidelines update` to receive improvements. That cost is the
feature.

## What this is not

Instruction files may be automatically selected by an agent. Skills are self-describing
packages that declare activation or loading behavior. Catalogs publish and organize items.

Guidelines are simply reusable Markdown files, and this tool manages their safe, reproducible
installation.

## See also

- {doc}`../reference/source-grammar` — how reusable guidelines are addressed and published.
- {doc}`apm-comparison` — when to use a different tool instead.
