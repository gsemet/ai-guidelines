# Source grammar and selectors

Every accepted form of the `source` field, plus the rules for selecting files, naming
targets, and resolving references.

## Source expressions

| Form | Example |
|---|---|
| Local folder | `../shared-guidelines` |
| Local file | `./team/quality.guideline.md` |
| GitHub short form | `owner/repository` |
| GitHub short form with path | `owner/repository/SWE/Python` |
| Generic Git URL | `https://git.example.com/team/guidelines.git` |
| SCP-style SSH | `git@github.com:owner/repository.git` |
| SSH URL | `ssh://git@gitlab.example.com/platform/guidelines.git` |
| GitHub web URL | `https://github.com/owner/repo/tree/main/SWE/Python` |
| GitLab web URL | `https://gitlab.example.com/g/p/-/blob/main/rules.guideline.md` |

### The GitHub short form

Recognized when the expression has no scheme, does not begin with `.`, `/`, or `~`, and
contains at least one `/`. Expanded to a GitHub Git URL. This is the feature that makes a
plain Git repository read like a registry.

### The `#ref:path` fragment

```text
https://github.com/example/guidelines.git#v2.1.0:SWE/Python
https://github.com/example/guidelines.git#main
```

The fragment carries `ref`, or `ref:path`. Do not combine an inline fragment path with the
`repository:path` compact form, and do not combine an inline `#ref` with a separate `ref` key.

## Revision syntax

`ref` accepts:

Branch or tag name
: `main`, `v1.2.0`. A name that could be either resolves to `reference_kind: ambiguous` and is
  treated conservatively.

Commit SHA
: 7 to 64 hexadecimal characters. Fully pinned.

Semantic version range
: Resolved against the tags of the source repository, selecting the highest match.

### Semantic version ranges

| Range | Meaning | Matches |
|---|---|---|
| `^1.2.3` | Compatible within the major | `1.2.3`, `1.9.0`; not `2.0.0` |
| `~1.2.3` | Compatible within the minor | `1.2.3`, `1.2.9`; not `1.3.0` |
| `>=1.2.0 <2.0.0` | Explicit bounds | any tag in the interval |
| `1.2.3` | Exact | only `1.2.3` |

A leading `v` on tags is tolerated: `^1.0.0` matches a tag named `v1.4.2`.

A range is a **moving** reference. `guidelines update` refreshes it when a newer matching tag
appears, but never crosses the declared bound. Ranges require the source to publish tags —
verify with `git ls-remote --tags <url>`.

## File selection

Discovery is recursive and recognizes only the **case-sensitive** suffixes `.guideline.md` and
`.guidelines.md`.

Selectors may use either suffix, or omit it entirely:

```yaml
pattern: "*.guideline.md"     # singular
pattern: "*.guidelines.md"    # plural
pattern: "*.guideline?.md"    # both
paths: [SWE/Python/rules]     # suffix omitted
```

Synchronization always writes the canonical `.guidelines.md` suffix, regardless of the source
filename. Singular/plural collisions resolve deterministically.

Optional YAML frontmatter is display and search metadata only. It is never required and never
needs provider-specific fields.

Recognized frontmatter keys:

`name`
: Human-readable name. Conventionally includes a version number.

`description`
: One sentence, shown by `guidelines list` and `guidelines search`.

`metadata`
: Free-form mapping. `owner`, `keywords`, and a stable `guideline-id` UUID are conventional.

## Registries

A **registry** is just a Git repository where you store guidelines. There is no index file, no
catalog, and no registry abstraction in the tool — everything resolves through Git.

To publish guidelines for reuse, commit them to a repository and point a declaration at it:

```console
$ guidelines add your-org/your-guidelines SWE/Python/Python_Unit_Test
$ guidelines sync
```

Two conventions matter because the tool depends on them:

- **The unit of distribution is the folder you declare.** The lock entry `name` is that leaf
  folder's name; the installed filename comes from the source filename.
- **A leaf `README.md` is not vendored.** It documents the guideline for people browsing the
  repository, so content that lives only there never reaches a consuming project.

Tag your releases. Without tags, consumers can only reference a branch, which is a moving
reference — see {doc}`../how-to/pin-a-version`.

## Target resolution

Precedence, highest first:

1. Declaration `target_path`
2. Existing lock target for that entry
3. Manifest `default_guidelines_path`
4. An existing `.agents/guidelines` layout in the project
5. `.github/guidelines`

Every target is project-relative and must remain inside the project. Source folders are
flattened according to the declaration.

## Reference resolution and caching

Remote sources require Git. Cache identity excludes credentials and query data.

`--frozen`
: Strict replay. No resolution, no acquisition, no writes. Fails if replay data is missing.
  This is the CI command.

`--dry-run`
: Plans without writing.

`--refresh`
: Revalidates against the remote rather than trusting cached resolution.

`--no-cache`
: Bypasses the cache entirely.

Exact tags and SHAs remain pinned. Provider reference metadata takes precedence over
name-based guesses.

## Safety rules

Rejected: path traversal, glob metacharacters where a literal path is required, control
characters and NUL bytes, embedded credentials, and option-like revisions beginning with `-`.
Source and target symlink escapes are rejected. All destinations and cross-source collisions
are validated **before** any mutation.

Missing sources produce concise CLI errors, not Python tracebacks.

## Ownership semantics

Managed files use SHA-256 ownership:

- Ordinary sync warns about, then overwrites, an altered managed file.
- A stale locally edited file is **never** automatically deleted.
- Removing a declaration does not delete installed files.

Reports identify paths with expected and current hashes.
