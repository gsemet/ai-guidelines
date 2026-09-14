# Lockfile format (`guidelines.lock.json`)

`guidelines.lock.json` is **generated**.
Never hand-edit it.
Commit it.

It answers three questions: exactly which revision was installed, exactly which files it produced,
and exactly what those files contained when the tool last managed them.

## Document

```json
{
  "version": 1,
  "manager": "ai-guidelines",
  "lock_format": "ai-guidelines",
  "lock_format_version": 1,
  "manager_version": "0.1.0",
  "generated_at": "2026-08-26T12:00:00Z",
  "guidelines": []
}
```

`version`, `lock_format_version`
: Only `1` is supported.
  A higher major fails clearly rather than being misread.

`lock_format`, `manager`
: Must be `ai-guidelines`.
  This is the self-identification that distinguishes the document from a foreign lockfile.

`generated_at`
: Normalized to UTC with second precision, so round trips are byte-identical.

## Entry

```json
{
  "expression": "gsemet/ai-guidelines-registry/SWE/Python",
  "name": "Python_Unit_Test",
  "canonical_source": "https://github.com/gsemet/ai-guidelines-registry",
  "source_type": "github",
  "requested_ref": "v1.2.0",
  "path": "SWE/Python",
  "resolved_ref": "v1.2.0",
  "commit": "3da7cf8a1b2c3d4e5f60718293a4b5c6d7e8f901",
  "reference_kind": "tag",
  "captured_at": "2026-08-26T12:00:00Z",
  "target_path": ".github/guidelines",
  "files": []
}
```

`name`
: The stable identity used for reconciliation.
  For a registry leaf, this is the **leaf folder name**.

`expression`
: The original source expression retained for diagnostics and provenance.

`canonical_source`
: The normalized source identity.
  It never contains credentials or query data.

`requested_ref` vs `resolved_ref`
: What you asked for, and what it resolved to.
  For a range like `^1.0.0`, these differ.

`path`, `paths`, `pattern`, `target_path`, `alias`
: The selector, destination, and display identity copied from the declaration.
  `paths` is mutually exclusive with `path` and `pattern`.

`semver_constraint`, `resolved_tag`, `resolution_timestamp`
: Range-resolution provenance.
  These fields are either all present or all absent.

`reference_kind`
: One of `branch`, `tag`, `ambiguous`, `commit`, `unknown`, `local`.
  This is the field that tells you whether the entry can move.
  Provider reference metadata takes precedence over name-based guessing.

## File record

```json
{
  "target_path": ".github/guidelines/python-unittest.guidelines.md",
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

`sha256` is the hash of the content **as last managed by the tool**.
It is how ownership is established: if the file on disk hashes differently, it was edited
locally, and the tool reports that rather than silently overwriting without notice.

## Completeness and frozen replay

An entry is complete only when it has its identity, target, managed-file hashes, and replayable
revision.
Local entries use `resolved_ref`.
Remote entries require a full 40-character hexadecimal `commit`; a seven-character Git
abbreviation may be accepted as input metadata but does not make the entry complete.
Frozen replay also requires the matching commit-addressed materialized snapshot to be present
and to contain the locked source paths and hashes.

## Atomicity and recovery

Each file replacement is atomic, and normal operations take an advisory lock.
Synchronization, update, `add`, and `remove` capture the affected project state in a rollback
journal before publishing files and lock metadata.

If lock publication or a later coordinated step fails, the journal restores the prior files,
manifest, and lockfile state.
The previous lockfile is never left partially written.
A failed operation can still report the original error; rerun the command after resolving any
external filesystem problem.

## Foreign lockfiles

A document carrying unrecognized top-level keys without identifying itself as an
`ai-guidelines` lockfile is rejected with `unrecognized lockfile format; regenerate it`.

There is no importer and no migration command for any other tool's lockfile.
Unknown keys in a document that *does* self-identify are ignored, which is what allows forward
compatibility within major version 1.
