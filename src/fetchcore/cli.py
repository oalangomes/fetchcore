"""FetchCore command-line composition for operational repository workflows."""

from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Callable
from typing import TypeVar

import click
import yaml

from fetchcore.application import (
    EmbeddingProviderRegistry,
    RepositoryRetrievalApplicationService,
    SearchRequest,
    SourceReadRequest,
    SourceReadResult,
    load_runtime_config,
)
from fetchcore.application.indexing import (
    IndexContext as _IndexCommandContext,
    RepositoryIndexApplicationService,
    build_index_context as _build_index_context,
)
from fetchcore.application.index_operations import inspect_local_corpus
from fetchcore.application.index_artifacts import IndexFailureDiagnostics
from fetchcore.retrieval.contracts import KnowledgeSearchError, KnowledgeSearchResponse
from fetchcore.repository.index_policy import RepositoryIndexPolicyError
from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.application.runtime_inspection import (
    RuntimeInspectionService,
)
from fetchcore.repository.registry import RepositoryRegistryError
from fetchcore.application.navigation import FindSymbolRequest, GetRelatedRequest, NavigationResult

_RetrievalResult = TypeVar("_RetrievalResult")


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


@main.command("readiness")
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON.")
def readiness(config_path: Path | None, as_json: bool) -> None:
    """Check if the configured repository, snapshot, and retrieval mode are usable."""
    from fetchcore.application.readiness import inspect_readiness

    try:
        report = inspect_readiness(RuntimeInspectionService.from_sources(path=config_path))
    except (KnowledgeSearchError, OSError, ValueError, yaml.YAMLError) as exc:
        category = getattr(exc, "category", "invalid_runtime_configuration")
        if as_json:
            click.echo(json.dumps({
                "operation": "readiness", "status": "failed", "error": {"category": category},
            }, sort_keys=True, indent=2))
        else:
            click.echo(f"readiness: failed category={category}")
        raise click.exceptions.Exit(1) from exc
    payload = report.to_mapping()
    if as_json:
        click.echo(json.dumps({
            "operation": "readiness", "status": "succeeded", "result": payload,
        }, sort_keys=True, indent=2))
    else:
        click.echo(f"readiness: {payload['status']} backend={report.backend} mode={report.mode}")
        click.echo(f"repository={report.repository or 'unbound'} requested_revision={report.requested_revision or 'unknown'}")
        for name, fact in sorted(report.checks.items()):
            click.echo(f"{name}={fact.state}" + (f" category={fact.category}" if fact.category else ""))
    if not report.ready:
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


@main.command("search")
@click.argument("query")
@click.option("--repo", "repository", required=True, help="Canonical identity or unique repository name.")
@click.option("--limit", type=int, default=10, show_default=True)
@click.option("--knowledge-type", "knowledge_types", multiple=True)
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--cache-root", type=click.Path(path_type=Path, file_okay=False))
@click.option("--allowed-root", multiple=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON.")
def search(
    query: str,
    repository: str,
    limit: int,
    knowledge_types: tuple[str, ...],
    config_path: Path | None,
    cache_root: Path | None,
    allowed_root: tuple[Path, ...],
    as_json: bool,
) -> None:
    """Search bounded repository evidence through the configured runtime."""
    _run_repository_retrieval(
        "search",
        as_json,
        lambda: _execute_search(
            RepositoryRetrievalApplicationService.from_sources(
                config_path=config_path,
                cache_root=cache_root,
                allowed_roots=allowed_root or None,
            ),
            query,
            repository,
            limit,
            knowledge_types,
        ),
        lambda outcome: {
            "repository": outcome[0],
            **outcome[1].to_mapping(),
        },
        lambda outcome: _render_search(outcome[1], outcome[0]),
    )


@main.command("source")
@click.argument("source_ref")
@click.option("--repo", "repository", help="Canonical identity or unique repository name.")
@click.option("--revision", help="Require the materialized source to match this revision.")
@click.option("--line-start", type=int)
@click.option("--line-end", type=int)
@click.option("--context-lines", type=int, default=0, show_default=True)
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--cache-root", type=click.Path(path_type=Path, file_okay=False))
@click.option("--allowed-root", multiple=True, type=click.Path(path_type=Path, file_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON.")
def source(
    source_ref: str,
    repository: str | None,
    revision: str | None,
    line_start: int | None,
    line_end: int | None,
    context_lines: int,
    config_path: Path | None,
    cache_root: Path | None,
    allowed_root: tuple[Path, ...],
    as_json: bool,
) -> None:
    """Read bounded source identified by a FetchCore source_ref."""
    _run_repository_retrieval(
        "source",
        as_json,
        lambda: RepositoryRetrievalApplicationService.from_sources(
            config_path=config_path,
            cache_root=cache_root,
            allowed_roots=allowed_root or None,
        ).get_source(
            SourceReadRequest(
                source_ref=source_ref,
                repository=repository,
                revision=revision,
                line_start=line_start,
                line_end=line_end,
                context_lines=context_lines,
            )
        ),
        lambda result: result.to_mapping(),
        _render_source,
    )


def _navigation_options(function: Callable[..., None]) -> Callable[..., None]:
    for option in (
        click.option("--repo", "repository", required=True),
        click.option("--revision"),
        click.option("--limit", type=int, default=20, show_default=True),
        click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False)),
        click.option("--cache-root", type=click.Path(path_type=Path, file_okay=False)),
        click.option("--allowed-root", multiple=True, type=click.Path(path_type=Path, file_okay=False)),
        click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON."),
    ):
        function = option(function)
    return function


@main.command("find-symbol")
@click.argument("name")
@click.option("--language")
@click.option("--kind")
@click.option("--path")
@_navigation_options
def find_symbol(
    name: str, language: str | None, kind: str | None, path: str | None,
    repository: str, revision: str | None, limit: int, config_path: Path | None,
    cache_root: Path | None, allowed_root: tuple[Path, ...], as_json: bool,
) -> None:
    """Find exact declarations, preserving homonyms and extraction coverage."""
    _run_repository_retrieval(
        "find_symbol", as_json,
        lambda: RepositoryRetrievalApplicationService.from_sources(
            config_path=config_path, cache_root=cache_root, allowed_roots=allowed_root or None,
        ).find_symbol(FindSymbolRequest(
            name, repository, revision, language, kind, path, limit,
        )),
        lambda result: result.to_mapping(), _render_navigation,
    )


@main.command("get-related")
@click.argument("source")
@click.option("--relation-type", "relation_types", multiple=True)
@click.option("--direction", default="outgoing", show_default=True)
@click.option("--depth", type=int, default=1, show_default=True)
@click.option("--require-resolution", is_flag=True)
@_navigation_options
def get_related(
    source: str, relation_types: tuple[str, ...], direction: str, depth: int,
    require_resolution: bool, repository: str, revision: str | None, limit: int,
    config_path: Path | None, cache_root: Path | None,
    allowed_root: tuple[Path, ...], as_json: bool,
) -> None:
    """Read directed observations by canonical symbol identity or source reference."""
    _run_repository_retrieval(
        "get_related", as_json,
        lambda: RepositoryRetrievalApplicationService.from_sources(
            config_path=config_path, cache_root=cache_root, allowed_roots=allowed_root or None,
        ).get_related(GetRelatedRequest(
            source, repository, revision, relation_types, direction, limit, depth, require_resolution,
        )),
        lambda result: result.to_mapping(), _render_navigation,
    )


def _render_navigation(result: NavigationResult) -> None:
    click.echo(f"navigation: status={result.status} repository={result.repository}")
    click.echo(f"revision={result.revision} limit_reached={str(result.limit_reached).lower()}")
    click.echo(f"scan_complete={str(result.scan_complete).lower()}")
    for coverage in result.coverage:
        click.echo(f"capability={coverage.capability} coverage={coverage.status}")
    for diagnostic in result.scan_diagnostics:
        click.echo(f"scan_diagnostic={diagnostic}")
    for item in result.results:
        click.echo(json.dumps(item.to_mapping(), sort_keys=True))


def _run_repo_operation(
    operation: str, as_json: bool,
    execute: Callable[[], dict[str, object] | list[dict[str, object]]],
) -> None:
    try:
        result = execute()
    except RepositoryRegistryError as exc:
        diagnostics = (
            IndexFailureDiagnostics.from_metadata(exc.__cause__.metadata).to_mapping()
            if operation in {"status", "plan", "sync", "rebuild"}
            and isinstance(exc.__cause__, KnowledgeSearchError) else {}
        )
        if as_json:
            error: dict[str, object] = {"category": exc.category, "message": str(exc)}
            if diagnostics:
                error["diagnostics"] = diagnostics
            click.echo(json.dumps({
                "operation": operation, "status": "failed",
                "error": error,
            }, sort_keys=True, indent=2))
            raise click.exceptions.Exit(1) from exc
        message = _error_message(exc)
        if diagnostics:
            message += "; diagnostics=" + json.dumps(diagnostics, sort_keys=True)
        raise click.ClickException(message) from exc
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


def _run_repository_retrieval(
    operation: str,
    as_json: bool,
    execute: Callable[[], _RetrievalResult],
    to_mapping: Callable[[_RetrievalResult], dict[str, object]],
    render: Callable[[_RetrievalResult], None],
) -> None:
    try:
        result = execute()
    except (KnowledgeSearchError, RepositoryRegistryError, OSError, ValueError, yaml.YAMLError) as exc:
        category = getattr(exc, "category", "invalid_runtime_configuration")
        payload = {
            "operation": operation,
            "status": "failed",
            "error": {"category": category},
        }
        if as_json:
            click.echo(json.dumps(payload, sort_keys=True, indent=2))
        else:
            click.echo(f"{operation}: failed")
            click.echo(f"category={category}")
        raise click.exceptions.Exit(1) from exc
    if as_json:
        click.echo(json.dumps({
            "operation": operation,
            "status": "succeeded",
            "result": to_mapping(result),
        }, sort_keys=True, indent=2))
        return
    render(result)


def _execute_search(
    service: RepositoryRetrievalApplicationService,
    query: str,
    repository: str,
    limit: int,
    knowledge_types: tuple[str, ...],
) -> tuple[str, KnowledgeSearchResponse]:
    canonical_repository = service.resolve_repository(repository).repository
    response = service.search(
        SearchRequest(
            query=query,
            repository=canonical_repository,
            limit=limit,
            knowledge_types=knowledge_types,
        )
    )
    return canonical_repository, response


def _render_search(response: KnowledgeSearchResponse, repository: str) -> None:
    click.echo(
        f"search: backend={response.backend} status={response.status} "
        f"repository={repository}"
    )
    if not response.results:
        click.echo("No evidence returned.")
        return
    for rank, result in enumerate(response.results, start=1):
        location = result.source or result.source_ref
        if result.line_start is not None:
            location += f":{result.line_start}"
            if result.line_end is not None:
                location += f"-{result.line_end}"
        click.echo(f"{rank}. {location}")
        click.echo(f"   repository: {repository}")
        if result.revision is not None:
            click.echo(f"   revision: {result.revision}")
        click.echo(f"   knowledge_type: {result.knowledge_type}")
        click.echo(f"   score: {result.score:g}")
        click.echo(f"   source_ref: {result.source_ref}")
        preview = result.content[:1000]
        if len(result.content) > len(preview):
            preview += "..."
        if preview:
            click.echo(f"   evidence:\n{preview}")


def _render_source(result: SourceReadResult) -> None:
    click.echo(f"source: repository={result.repository}")
    click.echo(f"revision={result.revision or 'unavailable'}")
    click.echo(f"path={result.path}")
    click.echo(
        f"lines={result.line_start}-{result.line_end}/{result.total_lines}"
    )
    click.echo(f"source_ref={result.source_ref}")
    click.echo(f"truncated={str(result.truncated).lower()}")
    click.echo(result.content)


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
@click.option("--config", "config_path", type=click.Path(path_type=Path, dir_okay=False))
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
    ctx: click.Context, config_path: Path | None, workspace: Path, repository: str | None,
    branch: str | None, revision: str | None, backend: str, backend_settings: str,
    cache_root: Path | None, allowed_root: tuple[Path, ...],
) -> None:
    """Index registered repositories, or use the explicit legacy CI options."""
    ctx.obj = {
        "workspace": workspace, "repository": repository, "branch": branch,
        "revision": revision, "backend": backend, "backend_settings": backend_settings,
        "cache_root": cache_root, "allowed_root": allowed_root, "config_path": config_path,
    }


@index_group.command("artifact")
@click.option("--input", "input_path", required=True, type=click.Path(path_type=Path, exists=True))
@click.option("--configuration", required=True, type=click.Path(path_type=Path, exists=True),
              help="JSON of the effective indexing/backend/embedding configuration (hashed only).")
@click.option("--run-id", required=True)
@click.option("--observed-at", required=True, help="Recorded observation time, ISO 8601 with timezone.")
@click.option("--repository", required=True)
@click.option("--backend", required=True)
@click.option("--target", required=True, help="Stable operator label for backend configuration/destination.")
def index_artifact(input_path: Path, configuration: Path, run_id: str, observed_at: str, repository: str,
                   backend: str, target: str) -> None:
    """Wrap repository-targeted --json output offline; never run indexing."""
    from fetchcore.application.index_artifacts import IndexRunArtifact, parse_index_json
    try:
        import hashlib
        config = parse_index_json(configuration.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or not config:
            raise ValueError("Effective configuration must be a nonempty JSON object")
        fingerprint = hashlib.sha256(json.dumps(
            config, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        command = parse_index_json(input_path.read_text(encoding="utf-8"))
        artifact = IndexRunArtifact.from_mapping({
            "schema_version": 1, "kind": "fetchcore.index-run", "run_id": run_id,
            "observed_at": observed_at, "repository": repository, "backend": backend,
            "target": target, "configuration_fingerprint": fingerprint,
            "command": command,
        })
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(artifact.to_mapping(), sort_keys=True, indent=2))


@index_group.command("corpus")
@click.option("--path", help="Inspect one repository-relative path with canonical admission rules.")
@click.option("--deep", is_flag=True, help="Run bounded canonical extraction in memory.")
@click.option("--proposal", type=click.Path(path_type=Path, exists=True, dir_okay=False),
              help="Validate an external policy and compare observed source admission.")
@click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON.")
@click.pass_context
def index_corpus(ctx: click.Context, path: str | None, deep: bool,
                 proposal: Path | None, as_json: bool) -> None:
    """Inspect a local corpus without registration, backend, or index writes."""
    assert ctx.parent is not None
    options = ctx.parent.obj
    try:
        result = inspect_local_corpus(
            options["workspace"], path=path, deep=deep, proposal=proposal,
        )
    except (OSError, ValueError, RepositoryIndexPolicyError, yaml.YAMLError) as exc:
        category = getattr(exc, "category", "corpus_inspection_failure")
        if as_json:
            click.echo(json.dumps({"operation": "corpus", "status": "failed",
                                   "error": {"category": category}}, sort_keys=True, indent=2))
            raise click.exceptions.Exit(1) from exc
        raise click.ClickException(f"corpus inspection failed: {category}") from exc
    payload = {"operation": "corpus", "status": "succeeded", "result": result}
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True, indent=2))
    else:
        scan = result.get("scan")
        repository_info = result["repository"]
        assert isinstance(repository_info, dict)
        click.echo(f"corpus: {result['mode']} workspace={repository_info['workspace']}")
        if isinstance(scan, dict):
            click.echo(f"discovered={scan['discovered_files']} eligible={scan['eligible_files']} "
                       f"read={scan['files_read']} truncated={scan['truncated']}")
        if path is not None:
            click.echo(f"{path}: {result['path_status']}")


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
                runtime = load_runtime_config(path=options["config_path"])
                overrides: dict[str, object] = {}
                backend = runtime.backend
                if (parent.get_parameter_source("backend") != click.core.ParameterSource.DEFAULT
                        or "runtime.backend" not in runtime.sources):
                    backend = options["backend"]
                    overrides["runtime"] = {"backend": backend}
                if (parent.get_parameter_source("backend_settings")
                        != click.core.ParameterSource.DEFAULT):
                    overrides["backends"] = {
                        backend: _json_object(options["backend_settings"]),
                    }
                if overrides:
                    runtime = load_runtime_config(
                        path=options["config_path"], overrides=overrides,
                    )
                providers = EmbeddingProviderRegistry(
                    settings={"ollama": runtime.ollama.to_mapping()}
                )
                service = RepositoryIndexApplicationService(
                    RepositoryApplicationService(cache_root=options["cache_root"],
                                                  allowed_roots=options["allowed_root"] or None),
                    backend=runtime.backend,
                    backend_settings=runtime.backend_settings,
                    retrieval=runtime.retrieval,
                    providers=providers,
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
        settings = _json_object(options["backend_settings"])
        runtime = load_runtime_config(
            path=options["config_path"],
            overrides={
                "runtime": {"backend": options["backend"]},
                "backends": {options["backend"]: settings},
            },
        )
        context = _build_index_context(
            workspace=options["workspace"], repository=options["repository"],
            branch=_optional_text(options["branch"]), revision=_optional_text(options["revision"]),
            backend=runtime.backend,
            backend_settings=dict(runtime.backend_settings),
            retrieval=runtime.retrieval,
            providers=EmbeddingProviderRegistry(
                settings={"ollama": runtime.ollama.to_mapping()}
            ),
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
