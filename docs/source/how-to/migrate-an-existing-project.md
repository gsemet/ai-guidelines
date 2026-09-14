# Migrate an existing project

**Goal:** bring guideline files that are already committed in your repository under managed,
reproducible control.

The situation: you have `.github/guidelines/` (or `.agents/guidelines/`) full of Markdown that
someone copied in by hand months ago.
Nobody knows which upstream version it came from.

## 1. Decide what is actually shared

Split your existing files into two piles:

**Project-owned.** Rules that only make sense in this repository.
These stay exactly where they are and must **not** be declared.
The tool never touches a file no declaration selects.

**Shared.** Rules that came from somewhere else, or that should be reused elsewhere.
Only these become managed.

Getting this split wrong in the direction of "manage everything" is the common mistake:
a managed file is one that `sync` may overwrite.

## 2. Publish the shared ones to a registry

If the upstream source still exists, skip to step 3.
If the files are orphaned copies, they need a home first: commit them to a Git repository and
declare that repository as the source.
See {doc}`../reference/source-grammar`.

## 3. Declare them

```console
$ guidelines add your-org/your-guidelines SWE/Python/Python_Unit_Test
```

Point `target_path` at the location the file already occupies, so nothing moves:

```yaml
version: 1
default_guidelines_path: .github/guidelines
guidelines:
  - source: your-org/your-guidelines
    ref: v1.0.0
    paths:
      - SWE/Python/Python_Unit_Test
    target_path: .github/guidelines
```

Target precedence, highest first: declaration `target_path`, existing lock target, manifest
`default_guidelines_path`, an existing `.agents/guidelines` layout, then `.github/guidelines`.
So if your project already uses `.agents/guidelines`, it is detected and preserved.

## 4. Preview before you touch anything

```console
$ guidelines sync --dry-run
```

Read this output carefully.
It is the moment to catch a `target_path` that would overwrite a project-owned file.

## 5. Diff, then commit

```console
$ guidelines sync
$ git diff
```

A clean diff means your hand-copied content matched the declared upstream revision exactly.

A non-empty diff is informative rather than alarming: it is the accumulated local drift you did
not know you had.
Inspect it and choose:

- The upstream version is right → keep the sync result.
- Your local changes are right → they are project-owned. Move them into a separate,
  undeclared file and re-sync.

## 6. Lock it down in CI

```console
$ guidelines sync --frozen
```

Add this to your pipeline.
From now on, drift is caught at review time instead of discovered months later.

## Note on foreign lockfiles

There is no importer for lockfiles produced by other tools.
Any unrecognized lockfile format is rejected with an instruction to regenerate it.
Declare your sources in `guidelines.yml` and run `guidelines sync`: regenerating is fast,
and it produces a lockfile whose provenance you can actually trust.
