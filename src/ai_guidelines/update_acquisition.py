"""Acquisition helpers used by the read-only update planner."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack
from typing import Any, cast

from ai_guidelines.cache import MaterializedGuidelineCache
from ai_guidelines.fetch import AcquiredSource, SourceFetcher
from ai_guidelines.locations import SourceLocation
from ai_guidelines.models import GuidelineDeclaration
from ai_guidelines.sparse import selector_sparse_patterns

UpdateRequest = tuple[int, GuidelineDeclaration, SourceLocation]


def acquire_update_sources(
    requests: Sequence[UpdateRequest], *, fetcher: Any, source_stack: ExitStack
) -> dict[int, AcquiredSource]:
    """Acquire update requests using sparse, content-addressed caching."""
    result: dict[int, AcquiredSource] = {}
    groups: dict[tuple[str | None, str | None], list[UpdateRequest]] = {}
    for request in requests:
        index, _declaration, location = request
        if location.source_type == "local":
            result[index] = source_stack.enter_context(fetcher.acquire(location))
        else:
            groups.setdefault((location.repository, location.requested_ref), []).append(request)
    cache = MaterializedGuidelineCache() if isinstance(fetcher, SourceFetcher) else None
    for group in groups.values():
        locations = [item[2] for item in group]
        cached: dict[int, AcquiredSource] = {}
        for index, declaration, location in group:
            snapshot = (
                cache.lookup(
                    location,
                    required_paths=declaration.paths
                    or ([declaration.path] if declaration.path else None),
                    required_pattern=declaration.pattern,
                )
                if cache is not None
                else None
            )
            if snapshot is not None:
                source_path = (
                    snapshot.root / location.relative_path
                    if location.relative_path != "."
                    else snapshot.root
                )
                resolved_location = (
                    location.model_copy(update={"kind": "folder"})
                    if source_path.is_dir() and location.kind != "folder"
                    else location
                )
                cached[index] = AcquiredSource(
                    location=resolved_location,
                    root=snapshot.root,
                    path=source_path,
                    resolved_ref=snapshot.resolved_ref,
                    commit=snapshot.commit,
                    reference_kind=cast(Any, snapshot.reference_kind),
                    temporary=False,
                )
        if len(cached) == len(group):
            result.update(cached)
            continue
        acquire_many = getattr(fetcher, "acquire_many", None)
        if callable(acquire_many):
            patterns = list(
                dict.fromkeys(
                    pattern
                    for _, declaration, _ in group
                    for pattern in selector_sparse_patterns(declaration)
                )
            )
            try:
                acquired = source_stack.enter_context(
                    acquire_many(
                        locations,
                        sparse_patterns=patterns,
                        checkout_root=cache.checkout_path(locations[0]) if cache else None,
                    )
                )
            except TypeError as error:
                if "checkout_root" not in str(error):
                    raise
                acquired = source_stack.enter_context(
                    acquire_many(locations, sparse_patterns=patterns)
                )
        else:
            acquired = [
                source_stack.enter_context(fetcher.acquire(location)) for location in locations
            ]
        acquired_by_index = {item[0]: source for item, source in zip(group, acquired, strict=True)}
        first = next(iter(acquired_by_index.values()))
        if cache is not None and first.commit is not None:
            snapshot = cache.write_snapshot(
                locations[0],
                first.root,
                locations,
                resolved_ref=first.resolved_ref,
                commit=first.commit,
                reference_kind=first.reference_kind,
            )
            # ``write_snapshot`` atomically moves cache-owned checkouts into
            # their final path. Rebuild returned sources from that path;
            # retaining the pre-move checkout would intermittently leave
            # update discovery with a non-existent source directory.
            acquired_by_index = {
                index: AcquiredSource(
                    location=(
                        source.location.model_copy(update={"kind": "folder"})
                        if (snapshot.root / source.location.relative_path).is_dir()
                        and source.location.kind != "folder"
                        else source.location
                    ),
                    root=snapshot.root,
                    path=(
                        snapshot.root
                        if source.location.relative_path == "."
                        else snapshot.root.joinpath(*source.location.relative_path.split("/"))
                    ),
                    resolved_ref=snapshot.resolved_ref,
                    commit=snapshot.commit,
                    reference_kind=cast(Any, snapshot.reference_kind),
                    temporary=False,
                )
                for index, source in acquired_by_index.items()
            }
        result.update(acquired_by_index)
    return result


_acquire_update_sources = acquire_update_sources
