# Lockfile format (`guidelines.lock.json`)

`guidelines.lock.json` is **generated**. Never hand-edit it. Commit it.

It answers three questions: exactly which revision was installed, exactly which files it
produced, and exactly what those files contained when the tool last managed them.

## Document

```json
{
  "version": 1,
  "manager": "ai-guidelines",
  "lock_format": "ai-guidelines",
  "lock_format_version": 1,
  "manager_version": "1.0.0",
  "generated_at": "2026-08-26T12:00:00Z",
  "guidelines": []
}
```

`version`, `lock_format_version`
: Only `1` is supported. A higher major fails clearly rather than being misread.

`lock_format`, `manager`
: Must be `ai-guidelines`. This is the self-identification that distinguishes the document
  from a foreign lockfile.

`generated_at`
: Normalized to UTC with second precision, so round trips are byte-identical.

## Entry

```json
{
  "name": "Python_Unit_Test",
  "declaration": { "source": "gsemet/ai-guidelines-registry", "ref": "v1.2.0" },
  "canonical_source": "https://github.com/gsemet/ai-guidelines-registry",
  "source_type": "github",
  "requested_ref": "v1.2.0",
  "resolved_ref": "v1.2.0",
  "commit": "3da7cf8a1b2c3d4e5f60718293a4b5c6d7e8f901",
  "reference_kind": "tag",
  "target_path": ".github/guidelines",
  "files": []
}
```

`name`
: The stable identity used for reconciliation. For a registry leaf, this is the **leaf folder
  name**.

`declaration`
: The original manifest declaration, retained verbatim so the lock is self-describing.

`canonical_source`
: The normalized source identity. Never contains credentials or query data.

`requested_ref` vs `resolved_ref`
: What you asked for, and what it resolved to. For a range like `^1.0.0`, these differ.

`reference_kind`
: One of `branch`, `tag`, `ambiguous`, `commit`, `unknown`, `local`. This is the field that
  tells you whether the entry can move. Provider reference metadata takes precedence over
  name-based guessing.

## File record

```json
{
  "target_path": ".github/guidelines/python-unittest.guidelines.md",
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

`sha256` is the hash of the content **as last managed by the tool**. It is how ownership is
established: if the file on disk hashes differently, it was edited locally, and the tool
reports that rather than silently overwriting without notice.

## Atomicity guarantee, stated honestly

Replacement is atomic **per file**, and operations take an advisory lock. This is *not* a
multi-file transaction.

The failure mode worth knowing: if reconciliation succeeds but lock publication then fails,
files may already have changed while the previous lockfile remains intact. Re-running `sync`
resolves it. The previous lockfile is never left corrupted.

## Foreign lockfiles

A document carrying unrecognized top-level keys without identifying itself as an
`ai-guidelines` lockfile is rejected with `unrecognized lockfile format; regenerate it`.

There is no importer and no migration command for any other tool's lockfile. Unknown keys in
a document that *does* self-identify are ignored, which is what allows forward compatibility
within major version 1.
