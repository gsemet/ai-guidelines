# Security and trust

Guideline text is copied as data and becomes agent context; it is never executed by
`ai-guidelines`. Review every source before trusting its rules, especially remote repositories and
local files supplied by another person.

The manager validates source URLs, revisions, selectors, credentials, control characters,
traversal, and symlink boundaries before writing. Credentials are rejected from source identities
and cache keys. SHA-256 hashes record the last content managed by this tool; they are ownership
metadata, not proof that a source is safe.

Normal synchronization warns about a local edit to a managed file and may overwrite it when the
user requests ordinary synchronization. Stale source files are removed only when their current
content still matches the recorded managed hash. Locally modified stale files are never deleted
automatically, and removing a declaration leaves installed files not deleted.

Operations validate a complete destination plan before mutation and replace individual files
atomically where possible. This is best-effort per-file atomicity, not a transaction: if files are
reconciled but lock publication fails, the previous lock remains while files may have changed.
Use dry-run and frozen mode for additional review and reproducibility.

Report suspected vulnerabilities privately to the repository maintainers before public disclosure.
Do not include credentials or other sensitive data in reports. For ordinary bugs, open a focused
public issue with reproduction steps.
