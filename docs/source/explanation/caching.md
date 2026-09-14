# How caching works

The cache exists so that repeated `sync`, `list`, `search`, `outdated`, and `update` runs do
not re-clone a repository for every declaration.
It is **disposable derived state**: deleting it can never lose information, only time.

:::{important}
The cache is never a source of truth. `guidelines.lock.json` records project intent and
provenance; the cache is disposable derived state. Normal operations can reacquire or replace
cache entries, while frozen replay fails when the exact required cache state is unavailable.
:::

## Two caches, two jobs

There are two independent caches, both outside your project, under the platform user cache
directory reported by `guidelines cache dir`:

```text
<user cache>/ai-guidelines/
├── guidelines/          ← discovery cache (metadata, JSON)
└── guideline-sources/   ← materialized cache (sparse checkouts)
```

### The discovery cache

Stores the *result of looking*: which guideline files a source contains, plus their name,
description, and frontmatter.
One JSON file per identity.
It never stores checkout paths, only source-relative ones, so an entry cannot be used to reach
outside its own source.

This is what makes `guidelines search` and `guidelines list` feel instant on a source you just
used.

### The materialized cache

Stores the *content itself*: sparse repository snapshots, keyed by repository **and commit**.
Only the declared subpaths are fetched, so consuming one leaf of a large registry stays cheap.

Because the key includes the full 40-character commit, two declarations resolving to the same
commit share one snapshot, and a snapshot can never silently represent different content than
its key claims.

## Cache identity excludes credentials

This is the part worth understanding, because it is a security property rather than an
optimization.

Discovery-cache identity is built from the **canonical source**, requested revision, and source
relative path, serialized deterministically and hashed with SHA-256:

```text
sha256({"relative_path": "...", "revision": "...", "source": "..."})
```

Materialized-cache identity is different: it uses the credential-free repository identity and
the full 40-character commit.
The resulting directory includes a readable repository label, an identity digest, and the
normalized commit:

```text
<repository>__<identity-sha256>__<full-commit>
```

The canonical source is credential-free by construction: userinfo, query strings, and
fragments never survive into it.
Consequences:

- No credential is ever written into a cache key, a directory name, or a file path.
- Two declarations differing *only* in credentials resolve to the same entry, because they
  genuinely refer to the same content.
- A local source can never be stored in the remote materialized cache at all.

The materialized cache additionally derives a readable directory name from the host and
repository path, with every unsafe character replaced, alongside the digest,
so the directory is browsable without becoming spoofable.

## Freshness: two clocks

| Clock | Default | Meaning |
|---|---|---|
| Refresh TTL | 10 minutes | After this, a moving reference is re-resolved against the remote |
| Eviction TTL | 10 days | After this, an unused snapshot becomes eligible for removal |

The split matters.
A short refresh window keeps `main` honest without re-resolving on every working-session command.
A long eviction window keeps the disk cost of *pinned* entries low without re-cloning content that
cannot have changed.

An exact tag or commit does not need re-resolution at all: the commit is the identity.

## How the flags interact with the cache

`--refresh`
: Ignore the discovery refresh TTL and revalidate against the remote now.
  This option belongs to `guidelines search`; `guidelines update` does not accept it.

`--no-cache`
: Bypass discovery-cache reads and writes for `guidelines search`.
  It is the slowest option and the correct choice when you suspect discovery metadata itself.

`--frozen`
: Perform no resolution, no acquisition, cache publication, metadata touch, or project writes.
  It replays exact lockfile and materialized-cache state.

  This is the flag whose cache interaction surprises people:
  `--frozen` succeeds only if it needs nothing that is missing.
  After `guidelines cache clean`, a frozen replay that requires an exact cached revision **fails**
  rather than quietly reaching for the network, which is precisely what you want from a
  reproducibility check.

## Concurrency

The materialized cache keeps its metadata in a single YAML file that is replaced atomically,
guarded by an advisory sidecar lock.
Snapshots are built in a uniquely named pending directory and only then moved into place,
so a concurrent reader never observes a half-materialized checkout,
and an interrupted run leaves a discardable pending directory rather than a corrupt entry.

## What the cache never does

- It never lives inside your project, so it cannot pollute a diff or ship in a build.
- It never holds project state.
  Removing a declaration or deleting the cache does not change what is installed.
- It never decides what is current.
  Only resolution and the lockfile do.

## When to reach for the cache commands

- **Routine:** `guidelines cache prune` removes entries past their eviction TTL.
- **Reclaiming disk:** `guidelines cache size`, then `guidelines cache clean`.
- **Debugging:** if a result looks stale, `--refresh` first, `--no-cache` second, and
  `cache clean` only if both point at the cache.

The practical commands are in {doc}`../how-to/manage-the-cache`.
