# Why guidelines?

## What is a guideline?

A guideline is a focused Markdown document containing rules, patterns, or practices
that a project wants an agent to use for a particular kind of work.
Examples include guidelines for writing commit messages, testing Python code, or documenting a
public API.

The important distinction is that a guideline is **project context**, not an executable extension.
It does not run code, activate itself, or require a particular agent host.
It is a plain document that a project can name, review, and put under version control.

This makes guidelines useful for organizations that maintain several projects.
A team can write a practice once in a reference repository, then select that guideline for each
project that needs it.
Each consuming project receives its own committed copy.
The organization therefore avoids rewriting the same standard everywhere, while each project
still controls which standards it adopts and when it accepts an update.

The relationship between the project pointer and the detailed document can be summarized like this:

```text
flowchart TD
	A[AGENTS.md] -->|names the applicable practice| P[Short pointer in initial context]
	T[Current task] -->|needs that practice| G[Load full guideline]
	P -->|progressive disclosure| G
	G --> R[Apply detailed rules]
	C[CONSTITUTION.md] -->|higher-priority constraints| R
```

## How a guideline is used

A guideline has two parts in practice:

1. The guideline document contains the detailed rules.
2. A project-owned file such as `AGENTS.md` or `CONSTITUTION.md` explains when that document
	 should be read.

For example, a project might declare:

```md
- `.github/guidelines/python-testing.guidelines.md`: Writing and organizing Python tests
```

The pointer keeps the project's main context concise while making the relationship visible.
When an agent plans or performs work on Python tests, the project can tell it to read the
referenced guideline.
The project also remains free to define precedence.
A constitution or an `AGENTS.md` file can override a general organizational recommendation when
the project has a legitimate local constraint.

`ai-guidelines` manages the declaration and synchronization of these documents.
Once a guideline is in the consuming repository, it is just Markdown.
Contributors and agents do not need the `guidelines` command, a plugin installation, or network
access in order to read and apply it.

## When rules disagree

Installing a guideline does not give it authority over the consuming project's existing rules.
The project should declare an authority order in `AGENTS.md` or an equivalent project-owned
harness file, so an agent can resolve conflicts before acting.
This repository uses the following order for its project guidance:

```text
CONSTITUTION > AGENTS.md > Guidelines > skills > other prompts
```

Read `>` as "takes precedence over", not as a loading sequence.
The order is a convention declared by the consuming project; `ai-guidelines` does not impose it
on every repository.
The layers have different jobs:

- `CONSTITUTION.md` contains foundational constraints and principles.
- `AGENTS.md` provides project-level operating instructions and points the agent to applicable
	guidelines.
- Guidelines provide reusable, detailed practices that the project has explicitly adopted.
- Skills and other supplemental prompts may provide useful domain knowledge or capabilities,
	but their rule-like advice does not override the project contract above them.

When two applicable rules conflict, the agent should identify their sources, follow the highest
applicable layer in the declared order, and keep any non-conflicting parts of the lower-priority
rule.
For example, if a reusable commit-message guideline requires a 72-character subject but
`AGENTS.md` requires 100 characters for this project, the local `AGENTS.md` rule wins for the
subject length; the guideline's other compatible requirements still apply.
If no project-owned file declares how to resolve a conflict, the agent should surface the
ambiguity and ask for a local decision rather than silently treating installation order or file
location as authority.

This project-defined order applies to project guidance and installed agent context.
It does not override the agent host's system or developer instructions, applicable safety
policies, or a direct user request.
Those higher-level constraints remain in force.

## The example: a commit-message guideline

This project uses `.github/guidelines/git-commit-message.guidelines.md`
to define its commit-message practice.
The file illustrates the complete pattern:

- Its metadata identifies the guideline and summarizes its subject.
- Its rules describe the Conventional Commit format, allowed types, line lengths, trailers,
	and the requirement to describe user impact.
- Its positive example shows the expected result in a form an agent can imitate.
- `AGENTS.md` points to the file and labels it as the project's commit-message guidance.

When an agent needs to write a commit message, it can discover the pointer in the project's
initial context, load the detailed document only for that task, and apply the rules while still
respecting the precedence order declared by the project.
The standard is therefore both reusable and local: it can originate in an organizational
repository, but the project has a visible, versioned copy, an explicit decision to use it, and a
clear answer if another rule disagrees.

## Progressive disclosure

Guidelines use the same progressive-disclosure idea that makes skills useful:

- Keep a short pointer or summary available in the project's initial context.
- Load the full guideline when the current task needs it.
- Keep unrelated rules out of the active context.

This approach gives an agent enough information to find the right practice without making every
task carry every organizational rule.
It also makes loading intentional and observable: the project says which document applies and
under what circumstances, rather than relying on a hidden activation rule.

Progressive disclosure describes how the information is introduced; it does not make a guideline a
skill.
The document remains project-owned Markdown, and the project harness decides when it is relevant.

## Why not instruction or rules files?

Instruction files are useful for host-specific, low-level preferences.
They are commonly selected according to the files currently open or being edited.
That selection can be useful during implementation, but it is not a dependable place for a
cross-cutting organizational standard: an agent planning a change may not have opened a matching
file yet, so the relevant instruction may not be loaded.

A guideline is a better fit for a detailed practice that should be considered during planning
and implementation.
The project names it from `AGENTS.md`, `CONSTITUTION.md`, or an equivalent harness file,
so the loading relationship is visible and does not depend on the current editor selection.

This does not make instruction files unnecessary.
Use them where automatic, path-specific behavior is exactly what is wanted.
Use project-owned constitution or rules files for high-priority local constraints.
Use guidelines for reusable, detailed practices that the project wants to adopt deliberately.

## Why not skills?

A skill can contain coding standards, but it is a self-describing knowledge package with its own
activation or loading behavior.
Skills are a good fit when the reusable unit also needs domain knowledge, scripts, references,
or an agent-facing capability.

Guidelines are a simpler fit when the reusable unit is a project guardrail: a Markdown document
that should be explicitly named in the repository and reviewed alongside the code.
Keeping it in a dedicated guidelines directory also separates organizational standards from the
other skills available to an agent and avoids depending on skill auto-activation.

The choice is therefore about ownership and behavior, not expressive power.
A coding standard could technically be packaged as either one.
Choose a guideline when the project should own a versioned copy and decide when to load it;
choose a skill when the package itself needs to describe and provide its activation or capability.

## Why copy guidelines into the project?

The reference repository is a convenient place to factorize and maintain organizational practice.
It should not be a runtime dependency of every contributor's agent session.

When a project synchronizes a guideline, the selected Markdown is copied into the project and
committed.
This gives the project:

- a stable snapshot that works offline;
- a reviewable diff when the standard changes;
- an audit trail in Git; and
- a consistent document for contributors and agents, even when they have different plugins or
	tools installed.

The trade-off is intentional: an update is not applied invisibly.
A maintainer runs `guidelines update` when the project is ready, reviews the resulting changes,
and merges them like any other change.
If the new practice requires code refactoring, that impact is visible in the same project
workflow rather than arriving unexpectedly during a later agent run.

In short, guidelines let an organization maintain reusable practices centrally while letting each
project adopt them explicitly, load them through progressive disclosure, and keep the result in
its own source tree.
`ai-guidelines` handles safe, reproducible synchronization; the project remains the authority over
what its agents should follow.

## See also

- {doc}`../reference/source-grammar` — how reusable guidelines are addressed and published.
- {doc}`apm-comparison` — when to use a different packaging approach instead.
