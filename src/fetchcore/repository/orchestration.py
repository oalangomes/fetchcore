"""Failure-isolated runtime for declarative multi-repository indexing.

The scheduler composes the source -> repository policy -> canonical indexer ->
projection pipeline established by Parts 1-7. Manifest parsing and validation
live separately in ``repository_orchestration_manifest``.
"""

from __future__ import annotations

from _thread import LockType, allocate_lock
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from dataclasses import dataclass

from fetchcore.search import KnowledgeSearchError
from fetchcore.repository.git_source import GitRepositorySourceProvider
from fetchcore.repository.index_operations import (
    RepositoryIndexRuntime,
    prepare_repository_index_runtime,
)
from fetchcore.repository.index_policy import RepositoryIndexPolicyError
from fetchcore.repository.orchestration_manifest import (
    MAX_CONCURRENCY,
    RepositoryOrchestrationError,
    RepositoryOrchestrationManifest,
    RepositoryPurpose,
    RepositoryTarget,
    load_repository_orchestration_manifest,
)
from fetchcore.repository.sources import (
    LocalWorkspaceSourceProvider,
    MaterializedRepository,
    RepositorySourceProvider,
    RepositorySourceRequest,
)


__all__ = (
    "MAX_CONCURRENCY",
    "PreparedRepositoryTarget",
    "RepositoryOrchestrationError",
    "RepositoryOrchestrationManifest",
    "RepositoryPurpose",
    "RepositoryRunner",
    "RepositoryTarget",
    "load_repository_orchestration_manifest",
    "orchestrate_repository_indexes",
)


@dataclass(frozen=True)
class PreparedRepositoryTarget:
    order: int
    target: RepositoryTarget
    purpose: RepositoryPurpose
    materialized: MaterializedRepository
    runtime: RepositoryIndexRuntime

    @property
    def projection_key(self) -> tuple[str, str]:
        return self.purpose.collection, self.runtime.repository

    def to_mapping(self) -> dict[str, object]:
        return {
            "name": self.target.name,
            "purpose": self.purpose.name,
            "projection": self.purpose.to_mapping(),
            "source": {
                "provider": self.materialized.provider,
                "source_ref": self.materialized.source_ref,
                "workspace": str(self.materialized.workspace),
            },
            "repository": {
                "id": self.runtime.repository,
                "branch": self.runtime.branch,
                "revision": self.runtime.revision,
            },
            "indexing_fingerprint": self.runtime.fingerprint,
        }


RepositoryRunner = Callable[[PreparedRepositoryTarget], object]


def orchestrate_repository_indexes(
    manifest: RepositoryOrchestrationManifest,
    *,
    runner: RepositoryRunner,
    providers: Mapping[str, RepositorySourceProvider] | None = None,
    max_concurrency: int | None = None,
) -> dict[str, object]:
    """Prepare and run independent repository projections with bounded execution.

    Preparation resolves canonical repository identity and repository-local policy
    before any runner is invoked. Duplicate writers for the same repository in
    the same collection are therefore rejected before projection mutation.
    """

    concurrency = manifest.max_concurrency if max_concurrency is None else max_concurrency
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise RepositoryOrchestrationError(
            f"max_concurrency must be between 1 and {MAX_CONCURRENCY}"
        )

    provider_registry: dict[str, RepositorySourceProvider] = {
        "local-workspace": LocalWorkspaceSourceProvider(),
        "git": GitRepositorySourceProvider(),
        **dict(providers or {}),
    }
    results: dict[int, dict[str, object]] = {}
    prepared: list[PreparedRepositoryTarget] = []

    with ExitStack() as stack:
        for order, target in enumerate(manifest.repositories):
            if not target.enabled:
                results[order] = _skipped_result(target, manifest.purposes[target.purpose])
                continue
            try:
                item = _prepare_target(
                    order,
                    target,
                    manifest.purposes[target.purpose],
                    providers=provider_registry,
                    stack=stack,
                )
            except Exception as exc:  # noqa: BLE001 - one source must not cancel the manifest
                results[order] = _failed_result(
                    target,
                    manifest.purposes[target.purpose],
                    phase="prepare",
                    error=exc,
                )
                continue
            prepared.append(item)

        duplicate_keys = {
            key
            for key, count in Counter(item.projection_key for item in prepared).items()
            if count > 1
        }
        executable: list[PreparedRepositoryTarget] = []
        for item in prepared:
            if item.projection_key in duplicate_keys:
                results[item.order] = _failed_prepared_result(
                    item,
                    phase="prepare",
                    category="duplicate_projection_target",
                    message=(
                        "multiple manifest entries resolve to repository "
                        f"{item.runtime.repository!r} in collection "
                        f"{item.purpose.collection!r}"
                    ),
                )
            else:
                executable.append(item)

        collection_locks: dict[str, LockType] = {
            collection: allocate_lock()
            for collection in {item.purpose.collection for item in executable}
        }
        with ThreadPoolExecutor(
            max_workers=concurrency,
            thread_name_prefix="fetchcore-index",
        ) as executor:
            futures: dict[Future[object], PreparedRepositoryTarget] = {
                executor.submit(
                    _run_serialized,
                    item,
                    runner,
                    collection_locks[item.purpose.collection],
                ): item
                for item in executable
            }
            for future in as_completed(futures):
                item = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 - per-repository failure isolation
                    results[item.order] = _failed_prepared_result(
                        item,
                        phase="execute",
                        category=_error_category(exc),
                        message=str(exc),
                        metadata=_error_metadata(exc),
                    )
                else:
                    results[item.order] = _succeeded_result(item, result)

    ordered = [results[index] for index in range(len(manifest.repositories))]
    return _orchestration_mapping(manifest, concurrency, ordered)


def _prepare_target(
    order: int,
    target: RepositoryTarget,
    purpose: RepositoryPurpose,
    *,
    providers: Mapping[str, RepositorySourceProvider],
    stack: ExitStack,
) -> PreparedRepositoryTarget:
    provider = providers.get(target.provider)
    if provider is None:
        raise RepositoryOrchestrationError(
            f"unsupported repository source provider {target.provider!r}"
        )
    materialized = stack.enter_context(
        provider.materialize(
            RepositorySourceRequest(
                source=target.source,
                repository=target.repository,
                branch=target.branch,
                revision=target.revision,
            )
        )
    )
    try:
        runtime = prepare_repository_index_runtime(
            materialized.workspace,
            repository=materialized.repository,
            branch=materialized.branch,
            revision=materialized.revision,
        )
    except RepositoryIndexPolicyError as exc:
        raise KnowledgeSearchError(
            str(exc),
            category="index_policy_invalid",
            metadata={"workspace": str(materialized.workspace)},
        ) from exc
    return PreparedRepositoryTarget(
        order=order,
        target=target,
        purpose=purpose,
        materialized=materialized,
        runtime=runtime,
    )


def _run_serialized(
    item: PreparedRepositoryTarget,
    runner: RepositoryRunner,
    collection_lock: LockType,
) -> object:
    with collection_lock:
        return runner(item)


def _succeeded_result(
    item: PreparedRepositoryTarget,
    result: object,
) -> dict[str, object]:
    payload = item.to_mapping()
    payload["status"] = "succeeded"
    payload["result"] = result
    return payload


def _skipped_result(
    target: RepositoryTarget,
    purpose: RepositoryPurpose,
) -> dict[str, object]:
    return {
        "name": target.name,
        "purpose": purpose.name,
        "projection": purpose.to_mapping(),
        "source": _target_source_mapping(target),
        "status": "skipped",
        "reason": "disabled",
    }


def _failed_result(
    target: RepositoryTarget,
    purpose: RepositoryPurpose,
    *,
    phase: str,
    error: Exception,
) -> dict[str, object]:
    return {
        "name": target.name,
        "purpose": purpose.name,
        "projection": purpose.to_mapping(),
        "source": _target_source_mapping(target),
        "status": "failed",
        "phase": phase,
        "error": {
            "category": _error_category(error),
            "message": str(error),
            "metadata": _error_metadata(error),
        },
    }


def _target_source_mapping(target: RepositoryTarget) -> dict[str, object]:
    key = "url" if target.provider == "git" else "path"
    return {
        "provider": target.provider,
        key: str(target.source),
    }


def _failed_prepared_result(
    item: PreparedRepositoryTarget,
    *,
    phase: str,
    category: str,
    message: str,
    metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    payload = item.to_mapping()
    payload["status"] = "failed"
    payload["phase"] = phase
    payload["error"] = {
        "category": category,
        "message": message,
        "metadata": dict(metadata or {}),
    }
    return payload


def _orchestration_mapping(
    manifest: RepositoryOrchestrationManifest,
    concurrency: int,
    repositories: list[dict[str, object]],
) -> dict[str, object]:
    statuses = Counter(str(item["status"]) for item in repositories)
    failed = statuses.get("failed", 0)
    succeeded = statuses.get("succeeded", 0)
    if failed == 0:
        status = "succeeded"
    elif succeeded:
        status = "partial_failure"
    else:
        status = "failed"
    return {
        "status": status,
        "manifest": str(manifest.path),
        "version": manifest.version,
        "max_concurrency": concurrency,
        "scheduling": {
            "global_concurrency": concurrency,
            "per_collection_concurrency": 1,
        },
        "purposes": {
            name: purpose.to_mapping()
            for name, purpose in sorted(manifest.purposes.items())
        },
        "summary": {
            "total": len(repositories),
            "succeeded": succeeded,
            "failed": failed,
            "skipped": statuses.get("skipped", 0),
        },
        "repositories": repositories,
    }


def _error_category(error: Exception) -> str:
    if isinstance(error, KnowledgeSearchError):
        return error.category
    if isinstance(error, RepositoryIndexPolicyError):
        return "index_policy_invalid"
    if isinstance(error, RepositoryOrchestrationError):
        return "orchestration_invalid"
    if isinstance(error, (OSError, ValueError)):
        return "source_invalid"
    return type(error).__name__


def _error_metadata(error: Exception) -> dict[str, object]:
    if isinstance(error, KnowledgeSearchError):
        return dict(error.metadata)
    return {}
