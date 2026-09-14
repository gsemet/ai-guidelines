# ai-guidelines Refactor Specification

## Status

Draft for implementation planning.

## Purpose

Improve the safety, reproducibility, consistency, and maintainability of
`ai-guidelines` without removing or weakening any existing supported feature.
The implementation must preserve the public package name, import package,
`guidelines` command, manifest format, lockfile format version, supported source
locations, selector behavior, cache commands, and documented CLI workflows unless
a compatibility correction is explicitly described below.

## Governing Rules

1. Read and follow `CONSTITUTION.md` before implementation.
2. Preserve all existing features and public contracts. Prefer compatibility
   aliases and migration-safe normalization over breaking renames.
3. Validate all inputs and complete all destination planning before invoking Git,
   writing project files, or publishing lock state.
4. Keep source content inert. Markdown and acquired source files must never be
   executed as code.
5. Run `just preflight` before completion. Run `just build` as part of the
   implementation validation until package building is included in `preflight`.
6. Do not modify repositories outside `ai-guidelines`.

### Mandatory Documentation Rewrite Rule

Before the refactor is considered complete, audit and rewrite **all docstrings
in maintained Python code**, not only docstrings in files changed by the refactor,
to follow the repository guideline exactly:

[Python Module Documentation Guideline](.github/guidelines/python-module-documentation.guidelines.md)

This is a mandatory repository-wide requirement. No existing maintained docstring
may be left unchanged merely because its surrounding implementation was not
refactored. The audit covers package modules, CLI modules, scripts, and tests
according to the guideline's policy for maintained code. Generated, vendored, and
third-party code remain governed by their own ownership rules.

The rewrite must specifically verify:

- Every maintained public module has a useful module docstring describing purpose,
  constraints, and important public APIs.
- Every maintained public class and function has an accurate Google-style
  docstring with only applicable `Args`, `Returns`, `Raises`, and `Examples`
  sections.
- Every `Args` item has its own line and an indented description.
- Functions with three or more parameters use one parameter per signature line.
- Functions with more than two parameters or non-obvious behavior include a
  runnable `Examples` section using the actual API.
- Added or changed public APIs, models, exceptions, and CLI options include the
  appropriate `.. versionadded::` or `.. versionchanged::` directive immediately
  after the summary line.
- Type hints, terminology, clause-aware wrapping, Sphinx/Napoleon parsing, and
  MyST configuration remain valid.

The documentation rewrite must be validated independently through targeted
inspection and `just docs-check`; docstring changes must not be hidden inside an
unrelated formatting-only change.

## Scope

### In scope

- Lock-entry identity and provenance preservation.
- Reviewed update-plan integrity and stale-plan rejection.
- Local source containment, including symlink containment.
- Cached acquisition and alternate guideline suffix handling.
- Temporary checkout and materialized-cache lifecycle.
- Shared selector and target-resolution behavior across API and CLI commands.
- Manifest/lock/project-state consistency around `add` and `remove`.
- Semantic-version range parsing and documentation alignment.
- Lock completeness semantics and frozen replay validation.
- Repository-wide Python docstring rewrite required above.
- Regression tests, documentation corrections, and quality-gate improvements.

### Out of scope

- Removing supported source syntaxes or CLI commands.
- Changing the lockfile major version.
- Introducing an agent-host-specific integration.
- Replacing Pydantic, Click, Git, or the existing cache approach without a
  compatibility-preserving reason.
- Broad stylistic rewrites unrelated to behavior, architecture, or the mandatory
  docstring audit.

## Current Problems to Correct

### 1. Lock identity is not preserved by update application

`apply_update_plan` reconstructs lock entries without all declaration selectors,
alias data, and semantic-version provenance. The resulting entry can no longer
match the declaration that produced it, causing later syncs to append entries or
frozen replay to fail.

### 2. Reviewed plans are not bound to the reviewed manifest

An update plan is applied positionally against the manifest currently on disk.
There is no manifest fingerprint, declaration identity, or plan immutability
boundary. A plan must be rejected before acquisition or mutation when the
manifest, declaration order, selectors, aliases, targets, or relevant lock state
has changed.

### 3. Local source selectors can cross symlink boundaries

Appending a validated relative selector to a local source must retain the
original source-root boundary. A selector that resolves through an existing
symlink outside that root must be rejected before acquisition.

### 4. Cache publication loses resolved source paths

Acquisition may resolve `.guideline.md` to `.guidelines.md` or vice versa, but
cache-backed synchronization rebuilds paths from the original request. The
resolved location and path must survive cache publication and reconstruction.

### 5. Project state changes are not coordinated

`add` and `remove` publish related manifest, lockfile, and managed-file state in
separate operations. Failures can leave a valid-looking manifest paired with
stale lock state. The workflow needs a recoverable transaction or journal, or a
preflight and rollback design with a clearly tested recovery path.

### 6. Repeated acquisition can leave orphaned checkouts

Successful standalone remote acquisitions need an explicit lifecycle. Temporary
checkouts must be removed on context exit; persistent materialized snapshots must
be represented in cache metadata and cleaned according to the cache policy.

### 7. Selection and target policy is duplicated

Sync, update, search, list, cache coverage, discovery, and sparse checkout each
partly implement selector or target semantics. They must share canonical services
so a declaration means the same thing in every workflow.

### 8. Contracts and documentation have drifted

Semantic-version grammar, lockfile field documentation, update options, lock
completeness, explicit file targets, and the preflight build claim must be made
consistent with the actual supported behavior.

## Target Architecture

### Declaration identity

Introduce one internal, immutable declaration-identity value containing the
normalized source identity, source type, requested revision, literal path or
plural selectors, filename pattern, alias, and effective target. Use it for:

- lock-entry matching;
- update-plan fingerprints;
- stale-plan detection;
- cache grouping;
- diagnostic context.

The public manifest model remains compatible. The identity value is an internal
coordination boundary, not a new required user-facing format.

### Lock-entry factory

Create one canonical builder for resolved lock entries. It must preserve every
identity field and all applicable provenance fields, including:

- requested and resolved revisions;
- exact commit;
- reference kind;
- semantic-version constraint, resolved tag, and resolution timestamp;
- path, paths, pattern, alias, target, and managed file hashes.

Normal sync and update-plan application must use the same builder. Exact replay
must retain the locked entry where appropriate; moving references must refresh
resolved provenance deliberately rather than copying stale metadata.

### Validated source-path operations

Create one safe source-root join operation that:

- validates the relative selector;
- resolves existing symlink components;
- rejects any resolved path outside the acquired or declared source root;
- preserves whether the result is a file or folder;
- returns a freshly validated `SourceLocation`.

Do not use unvalidated `model_copy(update=...)` for security-sensitive source
identity changes.

### Acquisition lifecycle

Represent acquisition ownership explicitly:

- temporary checkout: removed when its context exits;
- cache-owned pending checkout: atomically promoted to a materialized snapshot;
- materialized snapshot: indexed by credential-free repository and exact commit;
- frozen lookup: no metadata touch, cleanup, checkout, or publication.

All reconstructed cached sources must use the resolved location returned by
acquisition, not the original unresolved request.

### Shared selection and target service

Centralize:

- declaration selector expansion;
- discovery filtering;
- sparse-checkout pattern generation;
- effective target precedence;
- folder versus explicit-file target classification;
- installed-file status resolution.

The CLI should call these services instead of reproducing partial policies.

### Reviewed-plan boundary

Make update plans immutable at the API boundary and include a deterministic
manifest/declaration fingerprint. Applying a plan must:

1. Load the current manifest and lockfile.
2. Recompute the fingerprint.
3. Reject stale, reordered, missing, or extra declarations.
4. Verify each planned source identity and target.
5. Acquire only the exact reviewed revision.
6. Revalidate the result before any project mutation.

## Functional Requirements

### FR-1: Lock identity preservation

Applying a valid update plan must produce a lock entry that matches the current
declaration immediately. A subsequent normal sync must reuse that entry, and a
frozen sync must accept it when the source/cache state is available.

### FR-2: Stale-plan rejection

A plan created from one manifest must not apply after any identity-affecting
manifest change. The failure must be explicit, sanitized, and write-free.

### FR-3: Source containment

Local selectors that traverse, use unsafe path syntax, or resolve through a
symlink outside the declared source root must fail before reading source content.

### FR-4: Suffix compatibility

Both `.guideline.md` and `.guidelines.md` remain accepted source suffixes. Cache
hits and misses must produce identical discovery and reconciliation behavior.
Installed targets continue to use the canonical `.guidelines.md` suffix.

### FR-5: Cache cleanup and read-only semantics

Cache operations must document and enforce whether they are:

- project-read-only but cache-mutating;
- fully filesystem-read-only;
- allowed to acquire remote content;
- allowed to publish snapshots.

Frozen mode must remain fully resolution-free and write-free.

### FR-6: Consistent CLI behavior

`search` must apply the same declaration selectors and effective target rules as
sync. `list` must support both directory targets and explicit file targets.

### FR-7: State consistency

Failed `add`, `remove`, sync, or update operations must either leave the prior
complete state intact or leave a documented recovery record that the next command
can safely complete. No silent partial state is acceptable.

### FR-8: Semantic-version compatibility

Choose and document one grammar for ranges. The implementation must support the
examples in the documentation, including whitespace-separated bounds, or the
documentation must be corrected before release. Tests must cover caret, tilde,
comparison, wildcard, exact, and invalid ranges.

### FR-9: Completeness contract

`is_complete()` and frozen validation must use the same definition of replayable
state. A remote entry without an exact commit must not be reported as complete if
frozen mode will reject it.

### FR-10: Documentation quality

All maintained Python docstrings must satisfy the mandatory documentation rewrite
rule in this specification and the linked repository guideline. This requirement
is a release blocker, not an optional cleanup task.

## Test Plan

Add focused function-based tests before or alongside implementation for:

1. Update-plan application with `path`, `paths`, `pattern`, `alias`, explicit
   targets, and semantic-version ranges.
2. Immediate lock matching and frozen replay after update application.
3. Reordered, removed, added, and edited declarations after plan creation.
4. Local source selectors through internal and escaping symlinks.
5. Singular-to-plural and plural-to-singular suffix fallback with and without
   materialized cache hits.
6. Temporary checkout cleanup on successful and failed acquisition.
7. Fully read-only frozen cache lookup and cache-mutating normal lookup.
8. Search filtering with declaration selectors and manifest default targets.
9. Listing directory targets and explicit file targets.
10. Manifest/lock publication failures during add and remove.
11. Every documented semantic-version range form.
12. `is_complete()` behavior matching frozen validation.
13. Module-level collection of every regression test, including the current
    nested cache-publication test.
14. Docstring examples through doctest or an equivalent executable validation
    where practical.

Keep tests offline. Use temporary repositories, temporary project roots, and
injected Git runners/fetchers.

## Documentation and Tooling Updates

- Rewrite all maintained Python docstrings before implementation completion.
- Correct the lockfile reference to describe the actual serialized model.
- Align semantic-version examples with the parser and tests.
- Either implement documented update cache options or remove the unsupported
  options from documentation.
- Make `just preflight` invoke package building, or change the Constitution and
  repository documentation so the quality-gate claim is accurate.
- Document cache lifecycle and read-only meanings precisely.
- Add a short migration/recovery note if a journal or plan fingerprint is added.

## Acceptance Criteria

The work is complete only when all of the following are true:

- Existing tests and supported CLI workflows continue to pass.
- New regression tests cover every functional requirement in this specification.
- `just preflight` passes, including package build validation.
- `uv build` passes independently.
- Frozen sync remains strict, resolution-free, and write-free.
- No source or target path can escape its declared boundary through traversal or
  symlink resolution.
- Applying a reviewed update plan cannot silently target a different manifest.
- Sync and update produce equivalent lock-entry identity and provenance.
- The full maintained Python docstring audit is complete against the linked
  documentation guideline.
- Documentation describes the behavior actually implemented.
- No unrelated repositories or user changes are modified.

## Implementation Sequence

1. Add characterization tests and move the nested regression test to module
   scope.
2. Implement declaration identity, lock-entry construction, and stale-plan
   validation.
3. Fix source-root containment and cached resolved-path propagation.
4. Define acquisition ownership and cleanup behavior.
5. Consolidate selector and target services, then align search and list.
6. Resolve manifest/lock transaction behavior.
7. Align semantic-version parsing, completeness validation, and documentation.
8. Perform the repository-wide docstring rewrite using the linked guideline.
9. Run targeted tests, `just preflight`, `uv build`, and the final documentation
   audit.
