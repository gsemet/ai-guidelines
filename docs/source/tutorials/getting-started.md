# Getting started

By the end of this tutorial you will have a project that declares one external guideline,
installs it reproducibly, and can prove where every managed byte came from.

## 1. Install the tool

```console
$ uv tool install ai-guidelines
```

## 2. Find a guideline

```console
$ guidelines search gsemet/ai-guidelines-registry "*unittest*"
python-unittest.guidelines.md   https://github.com/gsemet/ai-guidelines-registry   SWE/Python/Python_Unit_Test/python-unittest.guidelines.md   no
```

Search is recursive by default: it walks every folder of the source.
The third column is the repository-relative path to give to `guidelines add`.

## 3. Declare your first source

From the root of any project:

```console
$ guidelines add gsemet/ai-guidelines-registry SWE/Python/Python_Unit_Test
```

`SWE/Python/Python_Unit_Test` is a folder, so every guideline file below it is selected.
You can also select a single file, with or without its suffix:

```console
$ guidelines add gsemet/ai-guidelines-registry SWE/Python/Python_Unit_Test/python-unittest
```

`add` writes `guidelines.yml` and synchronizes immediately.

## 4. Inspect the manifest

```yaml
version: 1
guidelines:
  - source: gsemet/ai-guidelines-registry
    path: SWE/Python/Python_Unit_Test
```

The `owner/repository` short form is expanded to a real Git URL at resolution time.
A registry is just a Git repository where guidelines are stored; see
{doc}`../reference/source-grammar` for every accepted source form.

## 5. Synchronize

```console
$ guidelines sync
```

`add` already ran this once; run it again whenever `guidelines.yml` changes.
Two things happen:

1. The selected files are copied into `.github/guidelines/`, always with the canonical
   `.guidelines.md` suffix.
2. `guidelines.lock.json` is written, recording the canonical source, the resolved commit,
   the reference kind, and a SHA-256 hash per managed file.

## 6. Prove reproducibility

```console
$ guidelines sync --frozen
```

`--frozen` performs no resolution, no acquisition, and no writes.
It fails if the lockfile is missing replay data.
This is the command to use in CI.

## 7. See what changed upstream

```console
$ guidelines outdated
$ guidelines update --dry-run
```

`outdated` is read-only.
`update` reviews a plan and applies exactly the revision you select.
Moving branches and semantic ranges may refresh; exact tags and commit SHAs never do.

## 8. Commit the result

Commit `guidelines.yml`, `guidelines.lock.json`, **and** the installed files.
The installed files are project-owned: your repository is the source of truth for what your
agents read.

## Where next

- {doc}`../how-to/pin-a-version` — stop following a moving branch.
- {doc}`../reference/source-grammar` — source forms, selectors, and publishing your own.
- {doc}`../explanation/why-guidelines` — the reasoning behind this model.
