---
name: gh-release-notes
description: Generate end-user-friendly GitHub release notes from the actual diff between releases, including user impact, examples, breaking changes, and public documentation links.
argument-hint: "from_ref=... to_ref=... repo_path=..."
user-invocable: true
---

# Release Notes Generator

Generate clean, end-user-friendly release notes from the actual Git diff between
an exclusive starting ref and an inclusive ending ref. The output is written
for use as a GitHub Release body and must contain release-note sections only.
GitHub supplies the release title, so never add a version heading, title,
preamble, file summary, commit summary, or closing explanation.

## Output contract

- Start with one of: `## New Features`, `## Enhancements`, `## Bug Fixes`,
  `## Breaking Changes`, `## Examples`, `## Documentation`, or `## Maintenance`.
- Include only changes that ordinary users of the published `ai-guidelines`
  package can do, observe, configure, rely on, or learn differently.
- Exclude CI, release automation, tests, Git evidence, contributor guidance,
  agent instructions, governance, internal process, and repository housekeeping.
- Translate implementation details into user outcomes. Never list files,
  functions, commit hashes, authors, or internal workflow mechanics.
- Use one concise line per bullet and omit empty sections.
- Include `## Maintenance` only when no user-facing change qualifies. It must be
  the only section and say that the release contains maintenance and internal
  improvements without inventing a benefit.
- Never combine `## Maintenance` with a user-facing section.
- Include `## Breaking Changes` only when the diff proves a public API, format,
  configuration, default, or supported-workflow break.
- Add a concrete `## Examples` section for changed commands, APIs, configuration,
  or before/after workflows when public usage evidence exists.
- Link relevant public documentation with descriptive inline Markdown, for example
  `See the [installation guide](https://ai-guidelines.readthedocs.io/en/stable/).`
  Never emit a bare URL or a repository-relative documentation path.
- Do not use code fences, a `#` title heading, or headings outside the permitted
  seven section names.

## Analysis workflow

1. Read the exact `from_ref..to_ref` commit log and diff.
2. Inspect public README, documentation, CLI help, and API references for evidence
   of user-visible behavior and the closest trustworthy documentation links.
3. Apply the normal-user audience test to every candidate change.
4. Detect breaking changes from commit markers and actual public contract changes.
5. Consolidate multiple commits that describe one final user outcome.
6. Categorize the remaining outcomes and write concise Markdown.
7. Review every bullet for user impact, documentation-link correctness, and the
   output contract before writing the requested file.

## Automated invocation

When the generator is invoked by a CI job with an explicit output path:

- Treat the requested file as mandatory and write the final Markdown directly to it.
- Do not use the Copilot response stream as the output artifact.
- Do not modify any other repository files.
- The first line must be a permitted `##` heading.
- The file must contain only release-note Markdown, without a title, preamble,
  code fence, or explanatory text.

For an empty or intentionally maintenance-only range, write exactly:

```markdown
## Maintenance

This release contains maintenance and internal improvements. No user-facing behavior changed.
```

The bundled generator precomputes Git evidence, invokes this skill through the
Copilot CLI, and validates the resulting file before the workflow creates the
GitHub Release.
