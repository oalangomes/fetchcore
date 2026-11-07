from __future__ import annotations

import asyncio
import json
import subprocess
from types import SimpleNamespace
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
from fetchcore.application.index_operations import prepare_repository_index_runtime
from fetchcore.repository.semantic_indexing import SemanticDocumentRepositoryIndexer
from fetchcore.adapters.retrieval.solr.backend import SolrDocumentMapper
from fetchcore.projection.core import corpus_checksum
from fetchcore.repository.local_references import LocalReferenceResolver
from fetchcore.repository.structure_lookup import (
    structural_coverage_summary, structural_lookup_keys,
)
from fetchcore.application.index_store import (
    STRUCTURAL_SELECTOR_FIELDS, StructuralDocumentPage,
)


def _lookup_index(documents):
    """Build a fixture key map once, before any structural query."""

    indexed = {}
    for document in documents:
        keys = structural_lookup_keys(document).to_mapping()
        keys["source_ref"] = [document.source_ref]
        for field, values in keys.items():
            for value in values:
                indexed.setdefault((field, value), []).append(document)
    return indexed


def _lookup_page(indexed, repository, revision, selector, value, limit):
    field = STRUCTURAL_SELECTOR_FIELDS[selector]
    candidates = tuple(doc for doc in indexed.get((field, value), ())
                       if doc.repository == repository and doc.revision == revision)
    return StructuralDocumentPage(candidates[:limit], len(candidates) > limit)


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
    (tmp_path / "unknown.json").write_text('{"fetch": true}\n', encoding="utf-8")
    (tmp_path / "source.md").write_text(
        "# Source\n\n[read target](target.md)\n", encoding="utf-8",
    )
    (tmp_path / "target.md").write_text("# Hidden target\n", encoding="utf-8")
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
    runtime = prepare_repository_index_runtime(
        tmp_path, repository=binding.repository, branch=None, revision=None,
    )
    indexed = runtime.indexer.index(tmp_path)
    lookup_index = _lookup_index(indexed.documents)

    class MaterializedStore:
        def load_snapshot(self, repository: str):
            assert repository == binding.repository
            return SimpleNamespace(
                revision=indexed.revision, indexing_fingerprint=runtime.fingerprint,
                document_count=len(indexed.documents),
                corpus_checksum=corpus_checksum(indexed.documents),
                structural_coverage=structural_coverage_summary(indexed.documents),
            )

        def load_documents(self, repository: str):
            assert repository == binding.repository
            return indexed.documents

        def lookup_structural_documents(self, repository, revision, *, selector, value, limit):
            return _lookup_page(lookup_index, repository, revision, selector, value, limit)

        def load_structural_targets(self, repository, revision, identifiers):
            return tuple(doc for doc in indexed.documents if doc.id in identifiers)

    return RetrievalApplicationService(
        {binding.repository: binding}, backends=BackendRegistry({"solr": forbidden_backend}),
        navigation_store=lambda _binding: MaterializedStore(),  # type: ignore[arg-type]
    )


def _payload(server, name: str, arguments: dict[str, object]) -> dict[str, object]:
    outcome = asyncio.run(server.call_tool(name, arguments))
    assert not outcome.is_error
    return outcome.structured_content


def _publish(application: RetrievalApplicationService, workspace: Path) -> None:
    """Explicitly replace the test's materialized canonical snapshot."""
    runtime = prepare_repository_index_runtime(
        workspace, repository="owner/repo", branch=None, revision=None,
    )
    indexed = runtime.indexer.index(workspace)
    lookup_index = _lookup_index(indexed.documents)

    class MaterializedStore:
        def load_snapshot(self, repository: str):
            assert repository == "owner/repo"
            return SimpleNamespace(
                revision=indexed.revision, indexing_fingerprint=runtime.fingerprint,
                document_count=len(indexed.documents),
                corpus_checksum=corpus_checksum(indexed.documents),
                structural_coverage=structural_coverage_summary(indexed.documents),
            )

        def load_documents(self, repository: str):
            assert repository == "owner/repo"
            return indexed.documents

        def lookup_structural_documents(self, repository, revision, *, selector, value, limit):
            return _lookup_page(lookup_index, repository, revision, selector, value, limit)

        def load_structural_targets(self, repository, revision, identifiers):
            return tuple(doc for doc in indexed.documents if doc.id in identifiers)

    application._navigation_store = lambda _binding: MaterializedStore()  # type: ignore[assignment]


def test_repeated_navigation_uses_materialized_documents(
    application: RetrievalApplicationService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_index(*_args: object, **_kwargs: object) -> None:
        pytest.fail("navigation must not index a repository")

    monkeypatch.setattr(SemanticDocumentRepositoryIndexer, "index", forbidden_index)
    for _ in range(2):
        found = application.find_symbol(FindSymbolRequest("catalog.Client.Fetch"))
        assert found.results
        related = application.get_related(GetRelatedRequest(
            found.results[0].source_ref, relation_types=("imports",),
        ))
        assert related.results[0].target == "net/http"


def test_missing_snapshot_is_reported_without_indexing(
    application: RetrievalApplicationService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingStore:
        def load_snapshot(self, repository: str) -> None:
            raise KnowledgeSearchError("snapshot absent", category="snapshot_missing")

    monkeypatch.setattr(application, "_navigation_store", lambda _binding: MissingStore())
    monkeypatch.setattr(
        SemanticDocumentRepositoryIndexer, "index",
        lambda *_args, **_kwargs: pytest.fail("navigation must not index"),
    )
    with pytest.raises(KnowledgeSearchError) as caught:
        application.find_symbol(FindSymbolRequest("Fetch"))
    assert caught.value.category == "snapshot_missing"


def test_missing_structural_projection_is_rejected_without_full_scan(
    application: RetrievalApplicationService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_store = application._navigation_store(application._bindings["owner/repo"])
    original_snapshot = original_store.load_snapshot("owner/repo")

    class IncompleteStore:
        def load_snapshot(self, repository: str):
            return original_snapshot

        def load_documents(self, repository: str):
            pytest.fail("navigation must not load the full corpus")

        def lookup_structural_documents(self, repository, revision, *, selector, value, limit):
            raise KnowledgeSearchError("structural projection absent", category="schema_mismatch")

    monkeypatch.setattr(application, "_navigation_store", lambda _binding: IncompleteStore())
    with pytest.raises(KnowledgeSearchError) as caught:
        application.find_symbol(FindSymbolRequest("Fetch"))
    assert caught.value.category == "schema_mismatch"


def test_explicit_index_persisted_projection_navigation_and_source(
    application: RetrievalApplicationService, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist canonical documents through Projection v3 before repeated reads."""
    original_store = application._navigation_store(application._bindings["owner/repo"])
    snapshot = original_store.load_snapshot("owner/repo")
    mapper = SolrDocumentMapper()
    artifact = tmp_path.parent / "canonical-snapshot.json"
    artifact.write_text(json.dumps({
        "revision": snapshot.revision,
        "fingerprint": snapshot.indexing_fingerprint,
        "document_count": snapshot.document_count,
        "corpus_checksum": snapshot.corpus_checksum,
        "structural_coverage": snapshot.structural_coverage,
        "documents": [mapper.to_solr(doc) for doc in original_store.load_documents("owner/repo")],
    }), encoding="utf-8")
    persisted_documents = tuple(
        mapper.from_solr(item) for item in json.loads(artifact.read_text())["documents"]
    )
    lookup_index = _lookup_index(persisted_documents)

    class PersistedStore:
        def load_snapshot(self, repository: str):
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            return SimpleNamespace(
                revision=payload["revision"], indexing_fingerprint=payload["fingerprint"],
                document_count=payload["document_count"],
                corpus_checksum=payload["corpus_checksum"],
                structural_coverage=payload["structural_coverage"],
            )

        def load_documents(self, repository: str):
            pytest.fail("navigation must not load the full persisted corpus")

        def lookup_structural_documents(self, repository, revision, *, selector, value, limit):
            return _lookup_page(lookup_index, repository, revision, selector, value, limit)

        def load_structural_targets(self, repository, revision, identifiers):
            return tuple(doc for doc in persisted_documents if doc.id in identifiers)

    monkeypatch.setattr(application, "_navigation_store", lambda _binding: PersistedStore())
    monkeypatch.setattr(
        SemanticDocumentRepositoryIndexer, "index",
        lambda *_args, **_kwargs: pytest.fail("query attempted reindexing"),
    )
    for _ in range(2):
        found = application.find_symbol(FindSymbolRequest("catalog.Client.Fetch"))
        definition = found.results[0]
        related = application.get_related(GetRelatedRequest(
            definition.source_ref, relation_types=("imports",),
        ))
        assert related.results[0].target == "net/http"
        source = application.get_source(SourceReadRequest(
            definition.source_ref, revision=definition.revision,
            line_start=definition.line_start, line_end=definition.line_end,
            checksum=definition.checksum,
        ))
        assert "Fetch" in source.content


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
        checksum=definition.checksum,
    ))
    assert text.path == path
    assert text.revision == definition.revision
    assert "Fetch" in text.content if path.endswith(".go") else "fetch" in text.content
    assert _payload(server, "get_source", {
        "source_ref": definition.source_ref, "revision": definition.revision,
        "line_start": definition.line_start, "line_end": definition.line_end,
        "checksum": definition.checksum,
    }) == text.to_mapping()
    assert _cli(monkeypatch, application, [
        "source", definition.source_ref, "--revision", definition.revision,
        "--checksum", definition.checksum,
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
    unsupported = application.find_symbol(FindSymbolRequest("fetch", language="json"))
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
    expected = application.find_symbol(FindSymbolRequest("fetch", language="json")).to_mapping()
    assert _payload(create_mcp_server(application), "find_symbol", {
        "name": "fetch", "language": "json",
    }) == expected
    assert _cli(monkeypatch, application, [
        "find-symbol", "fetch", "--language", "json",
    ]) == expected
    human = CliRunner().invoke(cli.main, [
        "find-symbol", "fetch", "--language", "json", "--repo", "owner/repo",
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
    _publish(application, tmp_path)
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


def test_changed_index_policy_requires_explicit_snapshot_rebuild(
    application: RetrievalApplicationService, tmp_path: Path,
) -> None:
    policy = tmp_path / ".fetchcore"
    policy.mkdir()
    (policy / "indexing.yaml").write_text(
        "version: 1\nlimits:\n  max_files: 1\n", encoding="utf-8",
    )
    with pytest.raises(KnowledgeSearchError) as caught:
        application.find_symbol(FindSymbolRequest("missing"))
    assert caught.value.category == "schema_mismatch"


def test_rename_removal_and_revision_preconditions(
    application: RetrievalApplicationService, tmp_path: Path,
) -> None:
    original = application.find_symbol(FindSymbolRequest("fetch", path="other.py")).results[0]
    (tmp_path / "other.py").write_text("def renamed():\n    pass\n", encoding="utf-8")
    with pytest.raises(KnowledgeSearchError) as stale_source:
        application.get_source(SourceReadRequest(
            original.source_ref, revision=original.revision, checksum=original.checksum,
        ))
    assert stale_source.value.category == "source_revision_mismatch"
    assert application.find_symbol(FindSymbolRequest("fetch", path="other.py")).results[0] == original
    assert application.get_related(GetRelatedRequest(original.identity)).status == "not_found"
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "rename")
    _publish(application, tmp_path)
    with pytest.raises(KnowledgeSearchError) as caught:
        application.find_symbol(FindSymbolRequest("renamed"))
    assert caught.value.category == "source_revision_mismatch"


@pytest.mark.parametrize("versioned", [True, False])
def test_unpinned_binding_without_operational_snapshot_is_explicit(tmp_path: Path, versioned: bool) -> None:
    (tmp_path / "simple.py").write_text("def example():\n    pass\n", encoding="utf-8")
    if versioned:
        _git(tmp_path, "init")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "-c", "user.name=Tests", "-c", "user.email=tests@example.test",
             "commit", "-m", "fixture")
    application = RetrievalApplicationService({
        "owner/repo": RepositoryBinding("owner/repo", tmp_path, "local-semantic"),
    })
    with pytest.raises(KnowledgeSearchError) as caught:
        application.find_symbol(FindSymbolRequest("example"))
    assert caught.value.category == "unsupported_backend"


def test_binding_scope_applies_to_symbols_and_relations(
    application: RetrievalApplicationService, tmp_path: Path,
) -> None:
    hidden = application.find_symbol(FindSymbolRequest("fetch", path="other.py")).results[0]
    restricted = RetrievalApplicationService({
        "owner/repo": RepositoryBinding(
            "owner/repo", tmp_path, "solr", repository_paths=("consumer.py",),
        ),
    }, navigation_store=application._navigation_store)
    found = restricted.find_symbol(FindSymbolRequest("fetch"))
    assert len(found.results) == 1
    assert found.results[0].path == "consumer.py"
    with pytest.raises(KnowledgeSearchError) as caught:
        restricted.get_related(GetRelatedRequest(hidden.identity))
    assert caught.value.category == "source_missing"
    with pytest.raises(KnowledgeSearchError):
        restricted.get_related(GetRelatedRequest(hidden.source_ref))
    with pytest.raises(KnowledgeSearchError) as source_error:
        restricted.get_source(SourceReadRequest(hidden.source_ref))
    assert source_error.value.category == "unauthorized_source"
    (tmp_path / "consumer_alias.py").symlink_to(tmp_path / "other.py")
    alias_restricted = RetrievalApplicationService({
        "owner/repo": RepositoryBinding(
            "owner/repo", tmp_path, "solr", repository_paths=("consumer_alias.py",),
        ),
    }, navigation_store=application._navigation_store)
    with pytest.raises(KnowledgeSearchError) as alias_error:
        alias_restricted.get_source(SourceReadRequest("consumer_alias.py"))
    assert alias_error.value.category == "unauthorized_source"


def test_related_target_location_respects_binding_scope(
    application: RetrievalApplicationService, tmp_path: Path,
) -> None:
    original_store = application._navigation_store(application._bindings["owner/repo"])
    original_snapshot = original_store.load_snapshot("owner/repo")
    documents = LocalReferenceResolver().resolve(original_store.load_documents("owner/repo"))
    lookup_index = _lookup_index(documents)

    class ResolvedStore:
        def load_snapshot(self, repository: str):
            return SimpleNamespace(
                revision=original_snapshot.revision,
                indexing_fingerprint=original_snapshot.indexing_fingerprint,
                document_count=len(documents), corpus_checksum=corpus_checksum(documents),
                structural_coverage=structural_coverage_summary(documents),
            )

        def load_documents(self, repository: str):
            return documents

        def lookup_structural_documents(self, repository, revision, *, selector, value, limit):
            return _lookup_page(lookup_index, repository, revision, selector, value, limit)

        def load_structural_targets(self, repository, revision, identifiers):
            return tuple(doc for doc in documents if doc.id in identifiers)

    application._navigation_store = lambda _binding: ResolvedStore()  # type: ignore[assignment]
    unrestricted = application.get_related(GetRelatedRequest("source.md"))
    relation = next(item for item in unrestricted.results if item.kind == "markdown_reference")
    assert relation.target_path == "target.md"
    assert relation.target_source_ref == "repository://owner/repo/target.md"

    restricted = RetrievalApplicationService({
        "owner/repo": RepositoryBinding(
            "owner/repo", tmp_path, "solr", repository_paths=("source.md",),
        ),
    }, navigation_store=application._navigation_store)
    observed = restricted.get_related(GetRelatedRequest("source.md"))
    hidden = next(item for item in observed.results if item.kind == "markdown_reference")
    assert hidden.target == relation.target
    assert hidden.target_path is None
    assert hidden.target_source_ref is None
    assert hidden.target_document_id is None
    assert hidden.target_revision is None
    assert hidden.target_checksum is None


def test_source_read_enforces_knowledge_type_scope(
    application: RetrievalApplicationService, tmp_path: Path,
) -> None:
    restricted = RetrievalApplicationService({
        "owner/repo": RepositoryBinding(
            "owner/repo", tmp_path, "solr", knowledge_types=("test",),
        ),
    }, navigation_store=application._navigation_store)
    with pytest.raises(KnowledgeSearchError) as caught:
        restricted.get_source(SourceReadRequest("client.go"))
    assert caught.value.category == "unauthorized_source"

    allowed = RetrievalApplicationService({
        "owner/repo": RepositoryBinding(
            "owner/repo", tmp_path, "solr", knowledge_types=("code",),
        ),
    }, navigation_store=application._navigation_store)
    assert "Fetch" in allowed.get_source(SourceReadRequest("client.go")).content
    (tmp_path / "client.go").write_text("package catalog\n", encoding="utf-8")
    with pytest.raises(KnowledgeSearchError) as changed:
        allowed.get_source(SourceReadRequest("client.go"))
    assert changed.value.category == "source_revision_mismatch"


def test_registry_without_materialized_snapshot_fails_explicitly(
    application: RetrievalApplicationService, tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    repositories = RepositoryApplicationService(cache_root=cache, allowed_roots=(tmp_path,))
    repositories.add(str(tmp_path))
    registered = RepositoryRetrievalApplicationService(
        repositories, FetchCoreRuntimeConfig(backend="solr"),
    )
    with pytest.raises(KnowledgeSearchError) as caught:
        registered.find_symbol(FindSymbolRequest(
            "catalog.Client.Fetch", repository="repo", path="client.go",
        ))
    assert caught.value.category == "unavailable"
    config = tmp_path / "operator.yaml"
    config.write_text("version: 1\nruntime:\n  backend: solr\n", encoding="utf-8")
    outcome = CliRunner().invoke(cli.main, [
        "find-symbol", "catalog.Client.Fetch", "--repo", "repo", "--path", "client.go",
        "--cache-root", str(cache), "--allowed-root", str(tmp_path),
        "--config", str(config), "--json",
    ])
    assert outcome.exit_code == 1
    assert json.loads(outcome.output)["error"]["category"] == "unavailable"
