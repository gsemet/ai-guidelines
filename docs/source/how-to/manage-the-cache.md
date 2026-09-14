# Manage the cache

**Goal:** inspect, prune, or clear the cache of acquired source material.

:::{seealso}
For how the cache is structured, why its identity excludes credentials, and how its two TTLs
behave, see {doc}`../explanation/caching`.
:::

## Where it lives

Outside your project, in a platform-appropriate user cache directory.
It is never written inside the repository, so it never pollutes a diff and never ships in a build.

```console
$ guidelines cache dir
```

## How big is it

```console
$ guidelines cache size
```

## Remove entries no longer referenced

```console
$ guidelines cache prune
```

`prune` is the safe routine operation: it removes materialized snapshots whose last access is
older than the eviction TTL. It does not inspect the current manifest to decide whether an entry
is referenced.

## Remove everything

```console
$ guidelines cache clean
```

Safe at any time.
The cache is derived state: the next `sync` re-acquires whatever it needs.

The one thing to know: after `cache clean`, `sync --frozen` still succeeds only if it needs no
acquisition.
If a replay requires material that is no longer cached, it fails rather than silently reaching
for the network.

## Bypass the cache for one command

```console
$ guidelines search LOCATION QUERY --refresh
$ guidelines search LOCATION QUERY --no-cache
```

`--refresh` revalidates against the remote.
`--no-cache` bypasses the cache entirely.

## Credentials and cache identity

Cache identity is derived from the *canonical* source, which excludes credentials and query data.
Two declarations that differ only in credentials therefore share a cache entry,
and no credential is ever written into a cache key or path.

## See also

- {doc}`../explanation/caching` — how the cache works and why it is designed this way.
- {doc}`../reference/cli` — full option lists for every subcommand.
