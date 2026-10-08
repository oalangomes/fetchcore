"""FetchCore command-line composition for operational repository workflows."""

from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Callable

import click
import yaml

from fetchcore.application.indexing import (
    IndexContext as _IndexCommandContext,
    RepositoryIndexApplicationService,
    build_index_context as _build_index_context,
)
from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.repository.index_policy import RepositoryIndexPolicyError
from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.application.runtime_inspection import (
    RuntimeInspectionService,
)
from fetchcore.repository.registry import RepositoryRegistryError


@click.group()
def main() -> None:
    """FetchCore repository knowledge and retrieval utilities."""


@main.command("doctor")
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON.")
def doctor(config_path: Path | None, as_json: bool) -> None:
    """Run read-only checks against the resolved runtime composition."""
    report = RuntimeInspectionService.doctor_from_sources(path=config_path)
    payload = report.to_mapping()
    _emit_runtime_result("doctor", payload, as_json)
    if not report.healthy:
        raise click.exceptions.Exit(1)


@main.command("capabilities")
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON.")
def capabilities(config_path: Path | None, as_json: bool) -> None:
    """Show capabilities declared by the selected runtime composition."""
    _run_runtime_operation(
        "capabilities",
        as_json,
        lambda: RuntimeInspectionService.from_sources(path=config_path).capabilities(),
    )


@main.group("config")
def config_group() -> None:
    """Inspect operator-owned runtime configuration."""


@config_group.command("show")
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON.")
def config_show(config_path: Path | None, as_json: bool) -> None:
    """Show resolved runtime values and their known source layers."""
    _run_runtime_operation(
        "config show",
        as_json,
        lambda: RuntimeInspectionService.from_sources(path=config_path).resolved_config(),
    )


@main.group("repo")
@click.option("--cache-root", type=click.Path(path_type=Path, file_okay=False),
              help="Operator repository cache; defaults to FETCHCORE_REPOSITORY_CACHE.")
@click.option("--allowed-root", multiple=True, type=click.Path(path_type=Path, file_okay=False),
              help="Authorized local source root (repeatable; defaults to current directory).")
@click.pass_context
def repo_group(ctx: click.Context, cache_root: Path | None, allowed_root: tuple[Path, ...]) -> None:
    """Register and inspect repository sources without indexing them."""
    ctx.obj = RepositoryApplicationService(cache_root=cache_root, allowed_roots=allowed_root or None)


@repo_group.command("add")
@click.argument("source")
@click.option("--provider", type=click.Choice(("local-workspace", "git")),
              help="Defaults to local-workspace for paths, git for URLs/SSH remotes.")
@click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON.")
@click.pass_obj
def repo_add(service: RepositoryApplicationService, source: str,
             provider: str | None, as_json: bool) -> None:
    """Materialize and register SOURCE; an existing source is never refreshed."""
    _run_repo_operation("add", as_json, lambda: service.present(service.add(source, provider=provider)))


@repo_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON.")
@click.pass_obj
def repo_list(service: RepositoryApplicationService, as_json: bool) -> None:
    """List only known registrations, ordered by canonical identity."""
    _run_repo_operation("list", as_json, lambda: [service.present(r) for r in service.list()])


@repo_group.command("inspect")
@click.argument("repository")
@click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON.")
@click.pass_obj
def repo_inspect(service: RepositoryApplicationService, repository: str, as_json: bool) -> None:
    """Resolve a canonical identity or an unambiguous repository leaf name."""
    _run_repo_operation("inspect", as_json, lambda: service.present(service.inspect(repository)))


def _run_repo_operation(
    operation: str, as_json: bool,
    execute: Callable[[], dict[str, object] | list[dict[str, object]]],
) -> None:
    try:
        result = execute()
    except RepositoryRegistryError as exc:
        if as_json:
            click.echo(json.dumps({
                "operation": operation, "status": "failed",
                "error": {"category": exc.category, "message": str(exc)},
            }, sort_keys=True, indent=2))
            raise click.exceptions.Exit(1) from exc
        raise click.ClickException(_error_message(exc)) from exc
    if as_json:
        click.echo(json.dumps({"operation": operation, "status": "succeeded", "result": result},
                              sort_keys=True, indent=2))
        return
    records = result if isinstance(result, list) else [result]
    if not records:
        click.echo("No registered repositories.")
    for record in records:
        click.echo(" | ".join(f"{key}={value if value is not None else 'unavailable'}"
                              for key, value in record.items()))


def _run_runtime_operation(
    operation: str,
    as_json: bool,
    execute: Callable[[], dict[str, object]],
) -> None:
    try:
        result = execute()
    except (KnowledgeSearchError, OSError, ValueError, yaml.YAMLError) as exc:
        category = getattr(exc, "category", "invalid_runtime_configuration")
        payload = {
            "operation": operation,
            "status": "failed",
            "error": {
                "category": category,
            },
        }
        if as_json:
            click.echo(json.dumps(payload, sort_keys=True, indent=2))
        else:
            click.echo(f"{operation}: failed")
            click.echo(f"category={category}")
        raise click.exceptions.Exit(1) from exc
    _emit_runtime_result(operation, result, as_json)


def _emit_runtime_result(
    operation: str,
    result: dict[str, object],
    as_json: bool,
) -> None:
    payload = {"operation": operation, "status": "succeeded", "result": result}
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, indent=2))
        return
    if operation == "doctor":
        status = result.get("status", "unknown")
        click.echo(f"doctor: {status}")
        checks = result.get("checks", [])
        if isinstance(checks, list):
            for check in checks:
                if isinstance(check, dict):
                    category = check.get("category")
                    suffix = f" category={category}" if category else ""
                    click.echo(f"{check.get('name')}={check.get('status')}{suffix}")
        return
    if operation == "capabilities":
        for group, values in sorted(result.items()):
            if isinstance(values, dict):
                for name, value in sorted(values.items()):
                    click.echo(f"{group}.{name}={str(value).lower()}")
            else:
                click.echo(f"{group}={values}")
        return
    config = result.get("config")
    sources = result.get("sources")
    if isinstance(config, dict):
        source_values = sources if isinstance(sources, dict) else {}
        for key, value in sorted(_flatten_values(config).items()):
            source = source_values.get(key)
            suffix = f" source={source}" if source else ""
            click.echo(f"{key}={value}{suffix}")


def _flatten_values(value: dict[str, object], prefix: str = "") -> dict[str, str]:
    flattened: dict[str, str] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            flattened.update(_flatten_values(item, path))
        else:
            flattened[path] = json.dumps(item, sort_keys=True, separators=(",", ":"))
    return flattened


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
    envvar="FETCHCORE_INDEX_REPOSITORY",
    help="Stable repository identity, for example owner/name.",
)
@click.option("--branch", envvar="FETCHCORE_INDEX_BRANCH")
@click.option("--revision", envvar="FETCHCORE_INDEX_REVISION")
@click.option(
    "--backend",
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
@click.option("--cache-root", type=click.Path(path_type=Path, file_okay=False))
@click.option("--allowed-root", multiple=True, type=click.Path(path_type=Path, file_okay=False))
@click.pass_context
def index_group(
    ctx: click.Context, workspace: Path, repository: str | None,
    branch: str | None, revision: str | None, backend: str, backend_settings: str,
    cache_root: Path | None, allowed_root: tuple[Path, ...],
) -> None:
    """Index registered repositories, or use the explicit legacy CI options."""
    ctx.obj = {
        "workspace": workspace, "repository": repository, "branch": branch,
        "revision": revision, "backend": backend, "backend_settings": backend_settings,
        "cache_root": cache_root, "allowed_root": allowed_root,
    }


def _dispatch_index(ctx: click.Context, operation: str, reference: str | None,
                    as_json: bool) -> None:
    assert ctx.parent is not None
    parent = ctx.parent
    options = parent.obj
    if reference is not None:
        def execute() -> dict[str, object]:
            for name in ("workspace", "repository", "branch", "revision"):
                if parent.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE:
                    raise RepositoryRegistryError(
                        "invalid_repository_reference",
                        "Registered targets cannot be combined with explicit workspace/identity options",
                    )
            try:
                settings = _json_object(options["backend_settings"])
                service = RepositoryIndexApplicationService(
                    RepositoryApplicationService(cache_root=options["cache_root"],
                                                  allowed_roots=options["allowed_root"] or None),
                    backend=options["backend"], backend_settings=settings,
                )
                return service.run(operation, reference)
            except RepositoryRegistryError:
                raise
            except (KnowledgeSearchError, RepositoryIndexPolicyError, OSError, ValueError) as exc:
                category = getattr(exc, "category", "indexing_failure")
                # Transport errors can contain endpoints, credentials, or raw backend responses.
                message = str(exc) if category in {"snapshot_missing", "rebuild_required"} else (
                    "Indexing operation failed; check source, configuration and backend compatibility"
                )
                raise RepositoryRegistryError(category, message) from exc
        _run_repo_operation(operation, as_json, execute)
        return
    if operation in {"plan", "rebuild"}:
        raise click.UsageError("A registered repository reference is required")
    if not options["repository"]:
        raise click.UsageError("Provide a registered repository or legacy --repository")
    try:
        context = _build_index_context(
            workspace=options["workspace"], repository=options["repository"],
            branch=_optional_text(options["branch"]), revision=_optional_text(options["revision"]),
            backend=options["backend"], backend_settings=_json_object(options["backend_settings"]),
        )
    except (KnowledgeSearchError, RepositoryIndexPolicyError, OSError, ValueError) as exc:
        raise click.ClickException(_error_message(exc)) from exc
    _run_operation(operation, context, lambda: context.legacy(operation))


@index_group.command("bootstrap")
@click.pass_context
def index_bootstrap(ctx: click.Context) -> None:
    """Validate the explicit backend schema contract (legacy CI surface)."""
    _dispatch_index(ctx, "bootstrap", None, True)


@index_group.command("status")
@click.argument("repository", required=False)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def index_status(ctx: click.Context, repository: str | None, as_json: bool) -> None:
    """Report persisted compatibility and observed repository currentness."""
    _dispatch_index(ctx, "status", repository, as_json)


@index_group.command("dry-run")
@click.pass_context
def index_dry_run(ctx: click.Context) -> None:
    """Preview indexing without writes (legacy CI surface)."""
    _dispatch_index(ctx, "dry-run", None, True)


@index_group.command("sync")
@click.argument("repository", required=False)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def index_sync(ctx: click.Context, repository: str | None, as_json: bool) -> None:
    """Synchronize a registered index without implicit rebuild."""
    _dispatch_index(ctx, "sync", repository, as_json)


@index_group.command("plan")
@click.argument("repository")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def index_plan(ctx: click.Context, repository: str, as_json: bool) -> None:
    """Plan a registered repository index without persistent mutation."""
    _dispatch_index(ctx, "plan", repository, as_json)


@index_group.command("rebuild")
@click.argument("repository")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def index_rebuild(ctx: click.Context, repository: str, as_json: bool) -> None:
    """Explicitly rebuild a registered repository through canonical indexing."""
    _dispatch_index(ctx, "rebuild", repository, as_json)


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
