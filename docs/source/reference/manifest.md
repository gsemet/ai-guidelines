# Manifest format (`guidelines.yml`)

`guidelines.yml` is the hand-edited declaration of intent.
It records what you *want*; the lockfile records what you *got*.

## Top level

```yaml
version: 1
default_guidelines_path: .github/guidelines
guidelines: []
```

`version`
: Required.
  Only `1` is supported.

`default_guidelines_path`
: Optional.
  Default install directory for declarations that do not set `target_path`.

`guidelines`
: A list of declarations.

## Declaration keys

`source`
: **Required.**
  A local path, Git URL, SSH location, GitHub or GitLab form, or
  `owner/repository` short form.
  See {doc}`source-grammar`.

`ref`
: Optional revision: branch, tag, commit SHA, or semantic version range.

`paths`
: Optional list of exact source-relative paths to select.

`path`
: Optional shorthand for a single exact path.
  Equivalent to a one-element `paths`.

`pattern`
: Optional glob selecting files by filename stem, for example `"*.guideline.md"`.

`target_path`
: Optional project-relative install directory.
  Must remain inside the project.

`alias`
: Optional stable display name.

## Selector precedence

Use **one** selector per declaration.
`paths` (and its `path` shorthand) select exact locations; `pattern` selects by glob.
Combining them is not meaningful: pick the one that expresses your intent.

## Scalar shorthand

A declaration may be a bare string:

```yaml
guidelines:
  - ./shared/guidelines/
```

This is normalized to the object form on save, so the file you get back is always explicit.

## Full example

```yaml
version: 1
default_guidelines_path: .github/guidelines
guidelines:
  # Pinned registry leaf
  - source: gsemet/ai-guidelines-registry
    ref: v1.2.0
    paths:
      - SWE/Python/Python_Unit_Test

  # Glob over a private repository, installed elsewhere
  - source: git@gitlab.example.com:platform/guidelines.git
    ref: "^2.0.0"
    pattern: "*.guideline.md"
    target_path: .agents/guidelines
    alias: platform

  # Local working copy
  - source: ../shared-guidelines
```

## Constraints

Rejected in any path or revision field: absolute paths, `..` traversal, control characters,
NUL bytes, glob metacharacters where a literal path is required, option-like values starting
with `-`, and embedded credentials.
Symlink escape from the source or the target is rejected.

## See also

- {doc}`lockfile` — the generated companion file.
- {doc}`source-grammar` — every accepted `source` expression.
