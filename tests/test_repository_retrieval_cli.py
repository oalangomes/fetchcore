from __future__ import annotations

import ast
import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from fetchcore import cli
from fetchcore.application import (
    BackendRegistry,
    FetchCoreRuntimeConfig,
    RepositoryRetrievalApplicationService,
    SearchRequest,
    SourceReadRequest,
    service_from_runtime_config,
)
from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.retrieval import RetrievalBackendCapabilities
from fetchcore.retrieval.contracts import (
    KnowledgeSearchError,
    KnowledgeSearchResponse,
    SearchIntent,
    SearchRequirement,
    SearchResult,
    SearchScope,
)
from fetchcore.repository.registry import RepositoryRegistryError
from fetchcore.mcp_server import create_mcp_server


class FixtureBackend:
    backend_id = "solr"
    retrieval_capabilities = RetrievalBackendCapabilities(
        lexical=True,
        canonical_query=True,
    )

    def __init__(self) -> None:
        self.calls: list[tuple[SearchIntent, SearchScope, tuple[SearchRequirement, ...]]] = []
        self.failure: KnowledgeSearchError | None = None
        self.empty = False

    def search(
        self,
        intent: SearchIntent,
        scope: SearchScope,
        requirements: tuple[SearchRequirement, ...],
    ) -> KnowledgeSearchResponse:
        if self.failure is not None:
            raise self.failure
        self.calls.append((intent, scope, requirements))
        return KnowledgeSearchResponse(
            backend=self.backend_id,
            status="success",
            duration_ms=7,
            results=() if self.empty else (
                SearchResult(
                    id="runtime-config",
                    source="src/runtime_config.py",
                    source_ref=f"repository://{scope.repository}/src/runtime_config.py#L2-L4",
                    knowledge_type="code",
                    content="runtime configuration evidence",
                    score=0.93,
                    line_start=2,
                    line_end=4,
                    revision=scope.revision,
                    checksum="sha256:fixture",
                ),
            ),
        )


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _workspace(path: Path, identity: str = "acme/fetchcore") -> Path:
    path.mkdir(parents=True)
    source = path / "src" / "runtime_config.py"
    source.parent.mkdir()
    source.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
    _git("init", cwd=path)
    _git("config", "user.email", "fetchcore@example.test", cwd=path)
    _git("config", "user.name", "FetchCore Tests", cwd=path)
    _git("add", ".", cwd=path)
    _git("commit", "-m", "fixture", cwd=path)
    _git("remote", "add", "origin", f"https://github.com/{identity}.git", cwd=path)
    return path


def _application(
    tmp_path: Path,
    *,
    identities: tuple[tuple[str, str], ...] = (("fetchcore", "acme/fetchcore"),),
    runtime_repository: str | None = None,
    revision: str | None = None,
) -> tuple[RepositoryRetrievalApplicationService, FixtureBackend, Path]:
    repositories = RepositoryApplicationService(
        cache_root=tmp_path / "cache",
        allowed_roots=(tmp_path,),
    )
    workspace = tmp_path / identities[0][0]
    for name, identity in identities:
        repositories.add(str(_workspace(tmp_path / name, identity)))

    backend = FixtureBackend()
    runtime = FetchCoreRuntimeConfig(
        backend="solr",
        backend_settings={
            "url": "http://127.0.0.1:8983/solr",
            "collection": "fetchcore-test",
        },
        repository=runtime_repository,
        revision=revision,
    )
    application = RepositoryRetrievalApplicationService(
        repositories,
        runtime,
        backends=BackendRegistry({"solr": lambda: backend}),
    )
    return application, backend, workspace


def _use_application(monkeypatch: pytest.MonkeyPatch, application) -> None:
    monkeypatch.setattr(
        RepositoryRetrievalApplicationService,
        "from_sources",
        classmethod(lambda cls, **kwargs: application),
    )


def test_search_and_source_round_trip_use_canonical_application_contracts(tmp_path: Path) -> None:
    application, backend, workspace = _application(tmp_path)

    response = application.search(
        SearchRequest(
            query="where is runtime configuration resolved?",
            repository="fetchcore",
            limit=5,
            knowledge_types=("code",),
        )
    )

    assert response.backend == "solr"
    assert response.results[0].source_ref == (
        "repository://acme/fetchcore/src/runtime_config.py#L2-L4"
    )
    assert response.results[0].revision == _git("rev-parse", "HEAD", cwd=workspace)
    intent, scope, requirements = backend.calls[0]
    assert intent.canonical_query == "where is runtime configuration resolved?"
    assert scope.repository == "acme/fetchcore"
    assert scope.max_results == 5
    assert scope.knowledge_types == ("code",)
    assert requirements == ()

    source = application.get_source(
        SourceReadRequest(
            source_ref=response.results[0].source_ref,
            line_start=3,
            line_end=3,
            context_lines=1,
        )
    )
    assert source.repository == "acme/fetchcore"
    assert source.revision == response.results[0].revision
    assert source.content == "two\nthree\nfour"
    assert (source.line_start, source.line_end, source.total_lines) == (2, 4, 5)


def test_cli_composition_and_mcp_share_search_and_source_application_semantics(
    tmp_path: Path,
) -> None:
    application, backend, workspace = _application(tmp_path)
    registered = application.resolve_repository("fetchcore")
    revision = _git("rev-parse", "HEAD", cwd=workspace)
    runtime = FetchCoreRuntimeConfig(
        backend="solr",
        backend_settings={
            "url": "http://127.0.0.1:8983/solr",
            "collection": "fetchcore-test",
        },
        workspace=workspace,
        repository=registered.repository,
        revision=revision,
    )
    backends = BackendRegistry({"solr": lambda: backend})
    mcp_service = service_from_runtime_config(runtime, backends=backends)
    server = create_mcp_server(mcp_service)
    request = SearchRequest(
        query="where is runtime configuration resolved?",
        repository=registered.repository,
        limit=5,
        knowledge_types=("code",),
    )

    cli_response = application.search(request)
    mcp_response = asyncio.run(
        server.call_tool(
            "search",
            {
                "query": request.query,
                "repository": request.repository,
                "limit": request.limit,
                "knowledge_types": list(request.knowledge_types),
            },
        )
    )
    assert mcp_response.is_error is False
    assert mcp_response.structured_content == cli_response.to_mapping()
    assert backend.calls[0] == backend.calls[1]

    source_ref = cli_response.results[0].source_ref
    cli_source = application.get_source(SourceReadRequest(source_ref))
    mcp_source = asyncio.run(
        server.call_tool("get_source", {"source_ref": source_ref})
    )
    assert mcp_source.is_error is False
    assert mcp_source.structured_content == cli_source.to_mapping()


def test_cli_search_source_json_and_human_outputs_share_application_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _, _ = _application(tmp_path)
    _use_application(monkeypatch, application)
    runner = CliRunner()

    search_args = [
        "search",
        "where is runtime configuration resolved?",
        "--repo",
        "fetchcore",
        "--limit",
        "5",
        "--knowledge-type",
        "code",
    ]
    first = runner.invoke(cli.main, [*search_args, "--json"])
    repeat = runner.invoke(cli.main, [*search_args, "--json"])
    assert first.exit_code == repeat.exit_code == 0, first.output
    assert first.output == repeat.output
    payload = json.loads(first.output)
    hit = payload["result"]["results"][0]
    assert payload["result"]["repository"] == "acme/fetchcore"
    assert hit["source_ref"] == (
        "repository://acme/fetchcore/src/runtime_config.py#L2-L4"
    )
    assert hit["revision"]

    human = runner.invoke(cli.main, search_args)
    assert human.exit_code == 0, human.output
    for value in (
        "repository: acme/fetchcore",
        "src/runtime_config.py:2-4",
        hit["source_ref"],
        hit["revision"],
        "knowledge_type: code",
        "score: 0.93",
    ):
        assert value in human.output

    source_args = [
        "source",
        hit["source_ref"],
        "--line-start",
        "3",
        "--line-end",
        "3",
        "--context-lines",
        "1",
    ]
    source_json = runner.invoke(cli.main, [*source_args, "--json"])
    source_repeat = runner.invoke(cli.main, [*source_args, "--json"])
    assert source_json.exit_code == source_repeat.exit_code == 0, source_json.output
    assert source_json.output == source_repeat.output
    source_payload = json.loads(source_json.output)
    assert source_payload["result"]["content"] == "two\nthree\nfour"
    assert source_payload["result"]["repository"] == payload["result"]["repository"]
    source_human = runner.invoke(cli.main, source_args)
    assert source_human.exit_code == 0, source_human.output
    for value in (
        "repository=acme/fetchcore",
        f"revision={hit['revision']}",
        "lines=2-4/5",
        "truncated=true",
        "two\nthree\nfour",
    ):
        assert value in source_human.output


def test_repository_resolution_supports_canonical_unique_and_rejects_ambiguous_or_missing(
    tmp_path: Path,
) -> None:
    application, _, _ = _application(
        tmp_path,
        identities=(
            ("first", "acme/widgets"),
            ("second", "other/widgets"),
        ),
    )

    assert application.resolve_repository("acme/widgets").repository == "acme/widgets"
    with pytest.raises(RepositoryRegistryError) as ambiguous:
        application.resolve_repository("widgets")
    assert ambiguous.value.category == "ambiguous_repository"
    with pytest.raises(RepositoryRegistryError) as missing:
        application.resolve_repository("missing")
    assert missing.value.category == "repository_not_found"


def test_runtime_repository_conflict_fails_closed(tmp_path: Path) -> None:
    application, _, _ = _application(
        tmp_path,
        identities=(("first", "acme/widgets"), ("second", "other/widgets")),
        runtime_repository="other/widgets",
    )

    with pytest.raises(KnowledgeSearchError) as caught:
        application.search(SearchRequest(query="needle", repository="acme/widgets"))
    assert caught.value.category == "unauthorized_repository"


def test_runtime_revision_conflict_fails_closed(tmp_path: Path) -> None:
    application, _, _ = _application(tmp_path, revision="stale-revision")

    with pytest.raises(KnowledgeSearchError) as caught:
        application.search(SearchRequest(query="needle", repository="fetchcore"))
    assert caught.value.category == "schema_mismatch"


def test_relative_source_requires_explicit_repository(tmp_path: Path) -> None:
    application, _, _ = _application(tmp_path)

    with pytest.raises(KnowledgeSearchError) as caught:
        application.get_source(SourceReadRequest("src/runtime_config.py"))
    assert caught.value.category == "repository_scope_required"


def test_local_source_reobserves_current_revision_instead_of_reusing_stale_registration(
    tmp_path: Path,
) -> None:
    application, _, workspace = _application(tmp_path)
    registered = application.resolve_repository("fetchcore")
    path = workspace / "src" / "runtime_config.py"
    path.write_text("current workspace content\n", encoding="utf-8")
    _git("add", ".", cwd=workspace)
    _git("commit", "-m", "advance workspace", cwd=workspace)
    current_revision = _git("rev-parse", "HEAD", cwd=workspace)

    source = application.get_source(
        SourceReadRequest("repository://acme/fetchcore/src/runtime_config.py")
    )

    assert current_revision != registered.revision
    assert source.revision == current_revision
    assert source.content == "current workspace content"


def test_cached_git_search_does_not_materialize_and_source_uses_pinned_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git_source = _workspace(tmp_path / "git-source")
    repositories = RepositoryApplicationService(
        cache_root=tmp_path / "cache",
        allowed_roots=(tmp_path,),
    )
    registered = repositories.add(git_source.as_uri(), provider="git")
    backend = FixtureBackend()
    runtime = FetchCoreRuntimeConfig(
        backend="solr",
        backend_settings={
            "url": "http://127.0.0.1:8983/solr",
            "collection": "fetchcore-test",
        },
    )
    application = RepositoryRetrievalApplicationService(
        repositories,
        runtime,
        backends=BackendRegistry({"solr": lambda: backend}),
    )
    calls: list[bool] = []
    provider = repositories.git
    original_materialize = provider.materialize

    def track_materialization(request, *, refresh=True):
        calls.append(refresh)
        return original_materialize(request, refresh=refresh)

    monkeypatch.setattr(provider, "materialize", track_materialization)
    response = application.search(
        SearchRequest(query="runtime configuration", repository=registered.repository)
    )

    assert calls == []
    assert response.results[0].revision == registered.revision
    source = application.get_source(
        SourceReadRequest(response.results[0].source_ref)
    )
    assert calls == [False]
    assert source.revision == registered.revision
    assert source.content == "one\ntwo\nthree\nfour\nfive"
    assert list((provider.cache_root / "worktrees").iterdir()) == []


def test_invalid_search_limit_fails_before_backend_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, backend, _ = _application(tmp_path)
    _use_application(monkeypatch, application)

    result = CliRunner().invoke(
        cli.main,
        ["search", "needle", "--repo", "fetchcore", "--limit", "101", "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["category"] == "schema_mismatch"
    assert backend.calls == []


def test_empty_search_reports_same_repository_and_status_in_human_and_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, backend, _ = _application(tmp_path)
    backend.empty = True
    _use_application(monkeypatch, application)

    json_result = CliRunner().invoke(
        cli.main,
        ["search", "needle", "--repo", "fetchcore", "--json"],
    )
    human_result = CliRunner().invoke(
        cli.main,
        ["search", "needle", "--repo", "fetchcore"],
    )

    assert json_result.exit_code == human_result.exit_code == 0
    payload = json.loads(json_result.output)["result"]
    assert payload["repository"] == "acme/fetchcore"
    assert human_result.output.startswith(
        f"search: backend={payload['backend']} status={payload['status']} "
        f"repository={payload['repository']}"
    )
    assert "No evidence returned." in human_result.output


@pytest.mark.parametrize(
    ("source_ref", "category"),
    [
        ("repository://acme/fetchcore/%2e%2e/outside", "unauthorized_source"),
        ("/etc/passwd", "unauthorized_source"),
        ("repository://acme/fetchcore/src/missing.py", "source_missing"),
    ],
)
def test_source_failures_are_typed_and_do_not_expose_raw_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_ref: str,
    category: str,
) -> None:
    application, _, _ = _application(tmp_path)
    _use_application(monkeypatch, application)
    args = ["source", source_ref, "--json"]
    if source_ref.startswith("/"):
        args.extend(["--repo", "fetchcore"])

    result = CliRunner().invoke(cli.main, args)

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["category"] == category
    assert "passwd" not in result.output


def test_source_rejects_symlink_escape_and_oversized_file(tmp_path: Path) -> None:
    application, _, workspace = _application(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("secret\n", encoding="utf-8")
    (workspace / "src" / "escape.py").symlink_to(outside)
    oversized = workspace / "src" / "large.py"
    oversized.write_bytes(b"x" * 1_000_001)

    with pytest.raises(KnowledgeSearchError) as symlink:
        application.get_source(
            SourceReadRequest("repository://acme/fetchcore/src/escape.py")
        )
    assert symlink.value.category == "unauthorized_source"

    with pytest.raises(KnowledgeSearchError) as too_large:
        application.get_source(
            SourceReadRequest("repository://acme/fetchcore/src/large.py")
        )
    assert too_large.value.category == "source_too_large"


def test_backend_failure_output_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, backend, _ = _application(tmp_path)
    backend.failure = KnowledgeSearchError(
        "transport failed for https://user:secret@host/private",
        category="connection_failure",
    )
    _use_application(monkeypatch, application)

    result = CliRunner().invoke(
        cli.main,
        ["search", "needle", "--repo", "fetchcore", "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["category"] == "connection_failure"
    assert "secret" not in result.output
    assert "private" not in result.output


def test_invalid_runtime_config_is_reported_without_exception_details(
    tmp_path: Path,
) -> None:
    config = tmp_path / "runtime.yaml"
    config.write_text("version: 99\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli.main,
        [
            "search",
            "needle",
            "--repo",
            "fetchcore",
            "--config",
            str(config),
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["category"] == "invalid_runtime_configuration"
    assert str(config) not in result.output


def test_cli_does_not_import_retrieval_adapters() -> None:
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not any(name.startswith("fetchcore.adapters") for name in imported_modules)
