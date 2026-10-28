from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from mcp.server.mcpserver.exceptions import ToolError

from fetchcore import cli
from fetchcore.application import (
    BackendRegistry, FindSymbolRequest, GetRelatedRequest, RepositoryBinding,
    RetrievalApplicationService, SourceReadRequest, FetchCoreRuntimeConfig,
    RepositoryRetrievalApplicationService,
)
from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.mcp_server import create_mcp_server
from fetchcore.retrieval.contracts import KnowledgeSearchError


def _git(workspace: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=workspace, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def application(tmp_path: Path) -> RetrievalApplicationService:
    (tmp_path / "client.go").write_text(
        'package catalog\nimport "net/http"\ntype Client struct {}\n'
        'func (c *Client) Fetch() {}\n', encoding="utf-8",
    )
    (tmp_path / "consumer.py").write_text(
        'class Client:\n    def fetch(self, service):\n        service.refresh()\n',
        encoding="utf-8",
    )
    (tmp_path / "other.py").write_text("def fetch():\n    pass\n", encoding="utf-8")
    (tmp_path / "unknown.rs").write_text("fn fetch() {}\n", encoding="utf-8")
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "FetchCore Tests")
    _git(tmp_path, "config", "user.email", "fetchcore@example.test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "fixture")
    _git(tmp_path, "remote", "add", "origin", "https://github.com/owner/repo.git")

    def forbidden_backend() -> None:
        pytest.fail("structural navigation must not construct a search backend")

    binding = RepositoryBinding(
        "owner/repo", tmp_path, "solr", revision=_git(tmp_path, "rev-parse", "HEAD"),
    )
    return RetrievalApplicationService(
        {binding.repository: binding}, backends=BackendRegistry({"solr": forbidden_backend}),
    )


def _payload(server, name: str, arguments: dict[str, object]) -> dict[str, object]:
    outcome = asyncio.run(server.call_tool(name, arguments))
    assert not outcome.is_error
    return outcome.structured_content


def _cli(monkeypatch: pytest.MonkeyPatch, application: RetrievalApplicationService,
         args: list[str]) -> dict[str, object]:
    monkeypatch.setattr(
        cli.RepositoryRetrievalApplicationService, "from_sources",
        classmethod(lambda cls, **kwargs: application),
    )
    outcome = CliRunner().invoke(cli.main, [*args, "--repo", "owner/repo", "--json"])
    assert outcome.exit_code == 0, outcome.output
    return json.loads(outcome.output)["result"]


@pytest.mark.parametrize(
    ("name", "path", "types"),
    [("catalog.Client.Fetch", "client.go", ("imports",)),
     ("consumer.Client.fetch", "consumer.py", ("calls",))],
)
def test_end_to_end_and_adapter_parity(
    application: RetrievalApplicationService, monkeypatch: pytest.MonkeyPatch,
    name: str, path: str, types: tuple[str, ...],
) -> None:
    server = create_mcp_server(application)
    found = application.find_symbol(FindSymbolRequest(name))
    assert found.status == "partial"
    definition = found.results[0]
    assert definition.path == path
    assert definition.checksum.startswith("sha256:")
    assert _payload(server, "find_symbol", {"name": name}) == found.to_mapping()
    assert _cli(monkeypatch, application, ["find-symbol", name]) == found.to_mapping()
    source = definition.source_ref if path.endswith(".go") else definition.identity
    related = application.get_related(GetRelatedRequest(
        source, revision=definition.revision, relation_types=types,
    ))
    assert related.status == "success"
    assert related.results
    assert related.results[0].revision == definition.revision
    assert related.results[0].checksum == definition.checksum
    assert related.results[0].source_ref == definition.source_ref
    assert _payload(server, "get_related", {
        "source": source, "revision": definition.revision, "relation_types": list(types),
    }) == related.to_mapping()
    assert _cli(monkeypatch, application, [
        "get-related", source, "--revision", definition.revision, "--relation-type", types[0],
    ]) == related.to_mapping()
    text = application.get_source(SourceReadRequest(
        definition.source_ref, revision=definition.revision,
        line_start=definition.line_start, line_end=definition.line_end,
    ))
    assert text.path == path
    assert text.revision == definition.revision
    assert "Fetch" in text.content if path.endswith(".go") else "fetch" in text.content
    assert _payload(server, "get_source", {
        "source_ref": definition.source_ref, "revision": definition.revision,
        "line_start": definition.line_start, "line_end": definition.line_end,
    }) == text.to_mapping()
    assert _cli(monkeypatch, application, [
        "source", definition.source_ref, "--revision", definition.revision,
        "--line-start", str(definition.line_start), "--line-end", str(definition.line_end),
    ]) == text.to_mapping()


def test_homonyms_scopes_and_bounds(application: RetrievalApplicationService) -> None:
    found = application.find_symbol(FindSymbolRequest("fetch", language="python"))
    assert len(found.results) == 2
    assert len({item.identity for item in found.results}) == 2
    assert [item.path for item in found.results] == ["consumer.py", "other.py"]
    bounded = application.find_symbol(FindSymbolRequest("fetch", language="python", limit=1))
    assert bounded.results == found.results[:1]
    assert bounded.limit_reached
    scoped = application.find_symbol(FindSymbolRequest("fetch", path="other.py", kind="function"))
    assert len(scoped.results) == 1
    assert application.find_symbol(FindSymbolRequest("absent", language="python")).status == "not_found"
    assert application.find_symbol(FindSymbolRequest("absent", path="missing.py")).status == "unknown"
    with pytest.raises(KnowledgeSearchError, match="authorized"):
        application.find_symbol(FindSymbolRequest("fetch", repository="outside/repo"))


def test_partial_and_unsupported_are_not_false_misses(
    application: RetrievalApplicationService,
) -> None:
    unsupported = application.find_symbol(FindSymbolRequest("fetch", language="rust"))
    assert unsupported.status == "unsupported"
    assert unsupported.results == ()
    partial = application.find_symbol(FindSymbolRequest("absent"))
    assert partial.status == "partial"
    go = application.find_symbol(FindSymbolRequest("catalog.Client.Fetch")).results[0]
    no_calls = application.get_related(GetRelatedRequest(go.identity, relation_types=("calls",)))
    assert no_calls.status == "unsupported"
    assert no_calls.results == ()
    unresolved = application.get_related(GetRelatedRequest(
        go.source_ref, relation_types=("imports",), require_resolution=True,
    ))
    assert unresolved.status == "partial"
    assert unresolved.results == ()
    assert unresolved.coverage[-1].status == "unsupported"


def test_unsupported_adapter_parity_and_human_output(
    application: RetrievalApplicationService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = application.find_symbol(FindSymbolRequest("fetch", language="rust")).to_mapping()
    assert _payload(create_mcp_server(application), "find_symbol", {
        "name": "fetch", "language": "rust",
    }) == expected
    assert _cli(monkeypatch, application, [
        "find-symbol", "fetch", "--language", "rust",
    ]) == expected
    human = CliRunner().invoke(cli.main, [
        "find-symbol", "fetch", "--language", "rust", "--repo", "owner/repo",
    ])
    assert human.exit_code == 0
    assert "status=unsupported" in human.output
    assert "capability=declarations coverage=unsupported" in human.output


def test_related_output_bound_and_adapter_parity(
    application: RetrievalApplicationService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "consumer.py").write_text(
        "def many(service):\n    service.first()\n    service.second()\n    service.third()\n",
        encoding="utf-8",
    )
    definition = application.find_symbol(FindSymbolRequest("many")).results[0]
    request = GetRelatedRequest(definition.identity, relation_types=("calls",), limit=1)
    related = application.get_related(request)
    assert len(related.results) == 1
    assert related.results[0].target == "service.first"
    assert related.limit_reached
    assert related.to_mapping() == _payload(create_mcp_server(application), "get_related", {
        "source": definition.identity, "relation_types": ["calls"], "limit": 1,
    })
    assert related.to_mapping() == _cli(monkeypatch, application, [
        "get-related", definition.identity, "--relation-type", "calls", "--limit", "1",
    ])


@pytest.mark.parametrize("navigation_request", [
    FindSymbolRequest("", limit=1), FindSymbolRequest("fetch", limit=0),
    FindSymbolRequest("fetch", limit=101), FindSymbolRequest("fetch", path="../outside.py"),
    GetRelatedRequest("client.go", limit=0),
    GetRelatedRequest("client.go", depth=2),
    GetRelatedRequest("client.go", direction="incoming"),
    GetRelatedRequest("client.go", relation_types=("invented",)),
    GetRelatedRequest("repository://other/repo/client.go"),
    GetRelatedRequest("repository://owner/repo/../outside.py"),
    GetRelatedRequest("symbol:v1:sha256:missing"),
    GetRelatedRequest("missing.py"),
    FindSymbolRequest("fetch", revision="old"),
])
def test_invalid_requests_have_categorized_errors(
    application: RetrievalApplicationService,
    navigation_request: FindSymbolRequest | GetRelatedRequest,
) -> None:
    with pytest.raises(KnowledgeSearchError) as caught:
        if isinstance(navigation_request, FindSymbolRequest):
            application.find_symbol(navigation_request)
        else:
            application.get_related(navigation_request)
    assert caught.value.category in {
        "schema_mismatch", "unsupported_capability", "unauthorized_source",
        "unauthorized_repository", "source_missing", "source_revision_mismatch",
    }


def test_mcp_and_cli_errors_preserve_category(
    application: RetrievalApplicationService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = create_mcp_server(application)
    with pytest.raises(ToolError, match="unsupported_capability"):
        asyncio.run(server.call_tool("get_related", {"source": "client.go", "depth": 2}))
    monkeypatch.setattr(
        cli.RepositoryRetrievalApplicationService, "from_sources",
        classmethod(lambda cls, **kwargs: application),
    )
    outcome = CliRunner().invoke(cli.main, [
        "get-related", "client.go", "--depth", "2", "--repo", "owner/repo", "--json",
    ])
    assert outcome.exit_code == 1
    assert json.loads(outcome.output)["error"]["category"] == "unsupported_capability"


def test_scan_truncation_is_explicit(
    application: RetrievalApplicationService, tmp_path: Path,
) -> None:
    policy = tmp_path / ".fetchcore"
    policy.mkdir()
    (policy / "indexing.yaml").write_text(
        "version: 1\nlimits:\n  max_files: 1\n", encoding="utf-8",
    )
    found = application.find_symbol(FindSymbolRequest("missing"))
    assert found.status == "partial"
    assert not found.scan_complete
    assert found.scan_diagnostics


def test_rename_removal_and_revision_preconditions(
    application: RetrievalApplicationService, tmp_path: Path,
) -> None:
    original = application.find_symbol(FindSymbolRequest("fetch", path="other.py")).results[0]
    (tmp_path / "other.py").write_text("def renamed():\n    pass\n", encoding="utf-8")
    assert application.find_symbol(FindSymbolRequest("fetch", path="other.py")).status == "not_found"
    with pytest.raises(KnowledgeSearchError) as caught:
        application.get_related(GetRelatedRequest(original.identity))
    assert caught.value.category == "source_missing"
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "rename")
    with pytest.raises(KnowledgeSearchError) as caught:
        application.find_symbol(FindSymbolRequest("renamed"))
    assert caught.value.category == "source_revision_mismatch"


@pytest.mark.parametrize("versioned", [True, False])
def test_unpinned_binding_can_hydrate_navigation_revision(tmp_path: Path, versioned: bool) -> None:
    (tmp_path / "simple.py").write_text("def example():\n    pass\n", encoding="utf-8")
    if versioned:
        _git(tmp_path, "init")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "-c", "user.name=Tests", "-c", "user.email=tests@example.test",
             "commit", "-m", "fixture")
    application = RetrievalApplicationService({
        "owner/repo": RepositoryBinding("owner/repo", tmp_path, "local-semantic"),
    })
    definition = application.find_symbol(FindSymbolRequest("example")).results[0]
    source = application.get_source(SourceReadRequest(
        definition.source_ref, revision=definition.revision,
        line_start=definition.line_start, line_end=definition.line_end,
    ))
    assert source.revision == definition.revision
    assert "def example" in source.content


def test_binding_scope_applies_to_symbols_and_relations(
    application: RetrievalApplicationService, tmp_path: Path,
) -> None:
    hidden = application.find_symbol(FindSymbolRequest("fetch", path="other.py")).results[0]
    restricted = RetrievalApplicationService({
        "owner/repo": RepositoryBinding(
            "owner/repo", tmp_path, "solr", repository_paths=("consumer.py",),
        ),
    })
    found = restricted.find_symbol(FindSymbolRequest("fetch"))
    assert len(found.results) == 1
    assert found.results[0].path == "consumer.py"
    with pytest.raises(KnowledgeSearchError) as caught:
        restricted.get_related(GetRelatedRequest(hidden.identity))
    assert caught.value.category == "source_missing"
    with pytest.raises(KnowledgeSearchError):
        restricted.get_related(GetRelatedRequest(hidden.source_ref))


def test_real_registry_and_cli_without_backend(
    application: RetrievalApplicationService, tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    repositories = RepositoryApplicationService(cache_root=cache, allowed_roots=(tmp_path,))
    repositories.add(str(tmp_path))
    registered = RepositoryRetrievalApplicationService(
        repositories, FetchCoreRuntimeConfig(backend="solr"),
    )
    expected = registered.find_symbol(FindSymbolRequest(
        "catalog.Client.Fetch", repository="repo", path="client.go",
    ))
    assert expected.repository == "owner/repo"
    assert expected.status == "success"
    config = tmp_path / "operator.yaml"
    config.write_text("version: 1\nruntime:\n  backend: solr\n", encoding="utf-8")
    outcome = CliRunner().invoke(cli.main, [
        "find-symbol", "catalog.Client.Fetch", "--repo", "repo", "--path", "client.go",
        "--cache-root", str(cache), "--allowed-root", str(tmp_path),
        "--config", str(config), "--json",
    ])
    assert outcome.exit_code == 0, outcome.output
    assert json.loads(outcome.output)["result"] == expected.to_mapping()
    definition = expected.results[0]
    related = registered.get_related(GetRelatedRequest(
        definition.source_ref, relation_types=("imports",),
    ))
    assert related.results[0].target == "net/http"
    source = registered.get_source(SourceReadRequest(
        definition.source_ref, revision=definition.revision,
        line_start=definition.line_start, line_end=definition.line_end,
    ))
    assert "Fetch" in source.content
