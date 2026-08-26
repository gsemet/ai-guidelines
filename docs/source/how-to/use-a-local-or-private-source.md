# Use a local or private source

**Goal:** consume guidelines that are not in a public GitHub repository.

## A local folder

No Git required.

```console
$ guidelines add ../shared-guidelines "*.guideline.md"
$ guidelines sync
```

In `guidelines.yml`:

```yaml
guidelines:
  - source: ../shared-guidelines
    pattern: "*.guideline.md"
```

Local sources must stay inside the resolved base directory. Traversal outside it, and escape
through a symlink, are both rejected.

This is the fastest way to develop a guideline: point at a working copy, iterate, then publish
it to a registry when it stabilizes.

## A single local file

```yaml
guidelines:
  - source: ./team/quality.guideline.md
```

## A self-hosted GitLab repository

```yaml
guidelines:
  - source: https://gitlab.example.com/platform/guidelines.git
    ref: v3.0.0
    paths:
      - SWE/Python
```

GitLab web URLs are also accepted directly, including the `/-/blob/` and `/-/tree/` forms.

## An SSH remote

For private repositories, SSH is usually the least friction: the tool shells out to Git, so
your existing SSH agent, `~/.ssh/config`, and deploy keys all apply unchanged.

```yaml
guidelines:
  - source: git@github.com:your-org/private-guidelines.git
    ref: main
```

The `ssh://` form works too:

```yaml
guidelines:
  - source: ssh://git@gitlab.example.com/platform/guidelines.git
```

`ssh://git@host` is the one accepted userinfo value, because `git` is a transport user rather
than a secret.

## Credentials are rejected, by design

The tool refuses any source expression that embeds a credential:

```yaml
# All of these are rejected
- source: https://user:token@github.com/org/repo.git
- source: https://github.com/org/repo.git?private_token=abc123
- source: https://storage.example.com/repo.git?X-Amz-Signature=abc
```

Rejected values include URL userinfo, and query or fragment keys that look like credentials
(`token`, `secret`, `password`, `credential`, `apikey`, `auth`, `signature`, `key`,
`accesskey`, `sig`, `expires`).

This is not merely hygiene. `guidelines.yml` and `guidelines.lock.json` are committed files —
a credential accepted here becomes a credential leaked into version control, and into every
clone of every consumer.

Authenticate through Git instead: SSH keys, a credential helper, or `~/.netrc`.

## Verify what resolved

```console
$ guidelines list
```

Then check `canonical_source` in `guidelines.lock.json`. Credentials never appear there,
because they never enter the canonical identity.
