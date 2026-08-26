# Pin a version

**Goal:** stop a declaration from silently following a moving branch.

## Check what you have now

```console
$ guidelines list
```

Look at `reference_kind` in `guidelines.lock.json`. If it is `branch` or `ambiguous`, the
declaration is *moving*.

## Pin to a tag

The recommended default.

```yaml
guidelines:
  - source: gsemet/ai-guidelines-registry
    ref: v1.2.0
    paths:
      - SWE/Python/Python_Unit_Test
```

An exact tag never refreshes on `guidelines update`.

## Pin to a commit

Maximum strictness. Immune even to a moved tag.

```yaml
guidelines:
  - source: gsemet/ai-guidelines-registry
    ref: 3da7cf8a1b2c3d4e5f60718293a4b5c6d7e8f901
```

Accepted as 7 to 64 hexadecimal characters.

## Accept a semantic version range

When you want patch and minor fixes but not breaking changes, `ref` accepts a semantic
version range. The tool resolves it against the tags in the source repository and picks the
highest match.

```yaml
guidelines:
    # highest 1.x.y tag
  - source: your-org/your-guidelines
    ref: "^1.0.0"

    # highest 1.2.x tag
  - source: your-org/your-guidelines
    ref: "~1.2.0"

    # explicit bounds
  - source: your-org/your-guidelines
    ref: ">=1.2.0 <2.0.0"
```

A range is a *moving* reference by design: `guidelines update` will refresh it when a new
matching tag appears. That is the point. What it will not do is cross the bound you declared.

:::{note}
Ranges only work if the source repository actually publishes tags. Against a registry with no
tags, a range has nothing to match. Check with `git ls-remote --tags <url>`.
:::

## Pin inline in the source expression

Instead of a separate `ref` key:

```yaml
guidelines:
  - source: "https://github.com/example/team-guidelines.git#v2.1.0:SWE/Python"
```

Do not set both an inline `#ref:` fragment and a `ref` key on the same declaration.

## Apply and verify

```console
$ guidelines sync
$ guidelines outdated
```

After pinning to an exact tag or commit, `outdated` should report nothing to do for that
declaration.

## Enforce it in CI

```console
$ guidelines sync --frozen
```

`--frozen` performs no resolution, no acquisition, and no writes. It fails if replay data is
missing — so it fails loudly if someone edits `guidelines.yml` without regenerating the lock.
