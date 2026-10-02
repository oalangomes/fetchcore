"""FetchCore command-line composition for operational repository workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

import click

from fetchcore.application.index_operations import (
    RepositoryIndexRuntime,
    dry_run_repository_index,
    prepare_repository_index_runtime,
    repository_index_status,
)
from fetchcore.backends.solr import SolrIndexStore, SolrSettings, UrllibSolrTransport
from fetchcore.adapters.retrieval.solr.incremental_persistence import (
    TargetedGitAwareSolrSynchronizer,
)
from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.repository.index_policy import RepositoryIndexPolicyError


@dataclass(frozen=True)
class _IndexCommandContext:
    workspace: Path
    backend: str
    runtime: RepositoryIndexRuntime
    store: SolrIndexStore


@click.group()
def main() -> None:
    """FetchCore repository knowledge and retrieval utilities."""


@main.group("index")
@click.option(
    "--workspace",
    type=click.Path(path_type=Path, file_okay=False),
    default=".",
    show_default=True,
    envvar="FETCHCORE_INDEX_WORKSPACE",
)
@click.option(
    "--repository",
    required=True,
    envvar="FETCHCORE_INDEX_REPOSITORY",
    help="Stable repository identity, for example owner/name.",
)
@click.option("--branch", envvar="FETCHCORE_INDEX_BRANCH")
@click.option("--revision", envvar="FETCHCORE_INDEX_REVISION")
@click.option(
    "--backend",
    type=click.Choice(("solr",), case_sensitive=True),
    default="solr",
    show_default=True,
    envvar="FETCHCORE_INDEX_BACKEND",
)
@click.option(
    "--backend-settings",
    default="{}",
    show_default=True,
    envvar="FETCHCORE_INDEX_BACKEND_SETTINGS",
    help="Backend settings as a JSON object.",
)
@click.pass_context
def index_group(
    ctx: click.Context,
    workspace: Path,
    repository: str,
    branch: str | None,
    revision: str | None,
    backend: str,
    backend_settings: str,
) -> None:
    """Inspect, preview, or synchronize a repository index."""
    try:
        ctx.obj = _build_index_context(
            workspace=workspace,
            repository=repository,
            branch=branch,
            revision=revision,
            backend=backend,
            backend_settings=_json_object(backend_settings),
        )
    except (KnowledgeSearchError, RepositoryIndexPolicyError, OSError, ValueError) as exc:
        raise click.ClickException(_error_message(exc)) from exc


@index_group.command("bootstrap")
@click.pass_obj
def index_bootstrap(context: _IndexCommandContext) -> None:
    """Provision and validate the explicit backend schema contract."""
    def validate_schema() -> dict[str, object]:
        context.store.validate_schema(require_vectors=False)
        return {
            "schema": "fetchcore.solr.projection-v3",
            "projection_version": context.store.settings.projection_version,
            "validated": True,
        }

    _run_operation("bootstrap", context, validate_schema)


@index_group.command("status")
@click.pass_obj
def index_status(context: _IndexCommandContext) -> None:
    """Report persisted snapshot compatibility and repository freshness."""
    _run_operation(
        "status",
        context,
        lambda: repository_index_status(context.store, context.runtime),
    )


@index_group.command("dry-run")
@click.pass_obj
def index_dry_run(context: _IndexCommandContext) -> None:
    """Preview the next repository indexing mutation without persistent writes."""
    _run_operation(
        "dry-run",
        context,
        lambda: dry_run_repository_index(context.store, context.runtime),
    )


@index_group.command("sync")
@click.pass_obj
def index_sync(context: _IndexCommandContext) -> None:
    """Synchronize canonical repository knowledge into the configured index."""
    def execute() -> dict[str, object]:
        synchronizer = TargetedGitAwareSolrSynchronizer(
            context.store,
            context.runtime.indexer,
        )
        return synchronizer.sync(context.workspace).to_mapping()

    _run_operation("sync", context, execute)


def _build_index_context(
    *,
    workspace: Path,
    repository: str,
    branch: str | None,
    revision: str | None,
    backend: str,
    backend_settings: dict[str, object],
) -> _IndexCommandContext:
    root = workspace.expanduser().resolve(strict=True)
    if backend != "solr":
        raise KnowledgeSearchError(
            f"unsupported operational indexing backend {backend!r}",
            category="unsupported_backend",
        )
    runtime = prepare_repository_index_runtime(
        root,
        repository=repository.strip(),
        branch=_optional_text(branch),
        revision=_optional_text(revision),
    )
    settings = SolrSettings.from_mapping(backend_settings)
    store = SolrIndexStore(UrllibSolrTransport(settings), settings)
    return _IndexCommandContext(
        workspace=root,
        backend=backend,
        runtime=runtime,
        store=store,
    )


def _run_operation(
    operation: str,
    context: _IndexCommandContext,
    execute: Callable[[], dict[str, object]],
) -> None:
    try:
        result = execute()
    except (KnowledgeSearchError, RepositoryIndexPolicyError, OSError, ValueError) as exc:
        payload = {
            "operation": operation,
            "status": "failed",
            "backend": context.backend,
            "repository": context.runtime.repository,
            "revision": context.runtime.revision,
            "error": {
                "category": getattr(exc, "category", type(exc).__name__),
                "message": str(exc),
            },
        }
        click.echo(json.dumps(payload, sort_keys=True, indent=2))
        raise click.exceptions.Exit(1) from exc

    payload = {
        "operation": operation,
        "status": "succeeded",
        "backend": context.backend,
        "repository": context.runtime.repository,
        "revision": context.runtime.revision,
        "indexing_fingerprint": context.runtime.fingerprint,
        "result": result,
    }
    click.echo(json.dumps(payload, sort_keys=True, indent=2))


def _json_object(raw: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("backend settings must contain valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("backend settings must contain a JSON object")
    return {str(key): item for key, item in value.items()}


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    rendered = value.strip()
    return rendered or None


def _error_message(error: Exception) -> str:
    category = getattr(error, "category", type(error).__name__)
    return f"{category}: {error}"


if __name__ == "__main__":
    main()
