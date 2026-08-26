# Update guidelines

**Goal:** bring installed guidelines up to date without losing control of what changes.

## 1. Look, without touching anything

```console
$ guidelines outdated
```

`outdated` is strictly read-only. It resolves references and compares them against the
lockfile. It writes nothing.

## 2. Review the plan

```console
$ guidelines update --dry-run
```

This prints the plan — which declarations would move, from which revision to which — and
applies nothing.

## 3. Apply

```console
$ guidelines update
```

`update` applies exactly the revision selected in the plan.

## What can and cannot refresh

| Reference kind | Refreshes on update? |
|---|---|
| Moving branch (`main`, `master`) | Yes |
| Semantic version range (`^1.0.0`) | Yes, within the declared bound |
| Ambiguous name | Yes, conservatively |
| Exact tag (`v1.2.0`) | **No** |
| Commit SHA | **No** |

To move a pinned declaration, edit `ref` in `guidelines.yml` and run `guidelines sync`.

## Handling local edits

If you edited a managed file, its SHA-256 no longer matches the lockfile. The tool will not
pretend otherwise:

- **Ordinary sync** warns and overwrites. Your edit is reported before it is replaced.
- **A stale locally edited file is never automatically deleted.** If a declaration stops
  selecting a file you had modified, the file stays and is reported.

If you want an edit to survive, it does not belong in a managed file. Put it in a
project-owned guideline that no declaration selects.

## Refreshing the resolution cache

```console
$ guidelines update --refresh
```

Forces revalidation against the remote rather than trusting cached resolution. Use
`--no-cache` to bypass the cache entirely.

## Removing a declaration

```console
$ guidelines remove IDENTIFIER
```

This removes the *declaration*. **Installed files are preserved.** Delete them yourself if
you want them gone — the tool does not destroy content it did not just write.
