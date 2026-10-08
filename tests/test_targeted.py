"""Exact acquisition, scope enforcement, provenance and explicit fallback contracts."""

from dataclasses import replace
import subprocess

import pytest

from fetchcore.adapters.retrieval.local.backend import LocalSemanticBackend
from fetchcore.adapters.retrieval.solr.backend import SolrBackend
from fetchcore.adapters.retrieval.opensearch.backend import OpenSearchBackend
from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorBackend
from fetchcore.application import (
    BackendRegistry,
    RepositoryBinding,
    RetrievalApplicationService,
    SearchRequest,
)
from fetchcore.retrieval import RetrievalConfig, DenseRetrievalConfig, RetrievalBackendCapabilities
from fetchcore.retrieval.contracts import (
    Anchor,
    KnowledgeSearchError,
    KnowledgeSearchResponse,
    SearchIntent,
    SearchResult,
    SearchScope,
    Term,
)
from fetchcore.retrieval.evidence import QueryMatchEvidence, EvidenceObservation
from fetchcore.retrieval.targeted import targetable_anchors


class RecordingLocal(LocalSemanticBackend):
    def __init__(self):
        super().__init__()
        self.calls = []

    def search(self, intent, scope, requirements):
        self.calls.append((intent, scope))
        return super().search(intent, scope, requirements)


def service(tmp_path, backend=None, **kwargs):
    backend = backend or RecordingLocal()
    binding = RepositoryBinding("owner/repo", tmp_path, backend.backend_id, **kwargs)
    return RetrievalApplicationService(
        {binding.repository: binding},
        backends=BackendRegistry({backend.backend_id: lambda: backend}),
    ), backend


def request(kind="path", value="src/service.py", **kwargs):
    if kind == "source_ref" and not value.startswith("repository://"):
        value = "repository://owner/repo/" + value
    return SearchRequest(
        intent=SearchIntent(canonical_query="product", anchors=(Anchor(kind, value),), **kwargs)
    )


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "src/service.py"
    path.parent.mkdir()
    path.write_text("def product():\n    return 42\n")
    return path


@pytest.mark.parametrize("kind", ["path", "source_ref"])
def test_exact_hit_preserves_provenance_and_never_opens_dense(tmp_path, source, kind):
    app, backend = service(
        tmp_path, retrieval=RetrievalConfig(dense=DenseRetrievalConfig(enabled=True))
    )
    response = app.search(request(kind))
    assert not backend.calls
    (hit,) = response.results
    assert hit.source_ref == "repository://owner/repo/src/service.py"
    assert hit.source == "src/service.py"
    assert hit.checksum.startswith("sha256:")
    assert hit.revision is None  # No invented revision.
    assert hit.line_start == 1 and hit.line_end == 2
    match = hit.query_matches[0]
    assert match.strength is None
    assert QueryMatchEvidence.from_mapping(match.to_mapping()) == match
    observation = hit.observations[0]
    assert EvidenceObservation.from_mapping(observation.to_mapping()) == observation
    assert response.metadata["targeted"]["outcome"] == "exact_unique"
    assert response.telemetry.candidates_examined == 1
    assert response.telemetry.metadata["targeted"]["fallback_used"] is False
    assert response.to_mapping()["results"][0]["source_ref"] == hit.source_ref


@pytest.mark.parametrize("kind", ["path", "source_ref"])
def test_missing_runs_normal_retrieval_and_preserves_required_key(tmp_path, source, kind):
    app, backend = service(tmp_path)
    with pytest.raises(KnowledgeSearchError) as error:
        app.search(request(kind, "src/missing.py"))
    assert error.value.category == "unsupported_semantics"
    assert error.value.metadata["targeted"]["fallback_reason"] == "not_found"
    assert not backend.calls


@pytest.mark.parametrize(
    "value",
    [
        "../secret.py",
        "/tmp/secret.py",
        "src/../secret.py",
        "src//service.py",
        "./src/service.py",
        "src\\service.py",
    ],
)
def test_invalid_paths_fail_before_lookup_or_fallback(tmp_path, value):
    app, backend = service(tmp_path)
    with pytest.raises(KnowledgeSearchError, match="invalid repository path"):
        app.search(request(value=value))
    assert not backend.calls


@pytest.mark.parametrize(
    "value",
    [
        "repository://other/repo/src/service.py",
        "repository://owner/repo/%2e%2e/secret.py",
        "repository://owner/repo/src/service.py#L1",
    ],
)
def test_invalid_source_refs_fail_closed(tmp_path, source, value):
    app, backend = service(tmp_path)
    with pytest.raises(KnowledgeSearchError):
        app.search(request("source_ref", value))
    assert not backend.calls


def test_symlink_and_path_scope_cannot_escape(tmp_path, source):
    (tmp_path / "link.py").symlink_to(source)
    app, backend = service(tmp_path)
    with pytest.raises(KnowledgeSearchError):
        app.search(request(value="link.py"))
    app, backend = service(tmp_path, repository_paths=("other.py",))
    with pytest.raises(KnowledgeSearchError):
        app.search(request())
    assert not backend.calls


def test_knowledge_type_scope_cannot_be_widened(tmp_path, source):
    app, _ = service(tmp_path, knowledge_types=("documentation",))
    with pytest.raises(KnowledgeSearchError) as error:
        app.search(request())
    assert error.value.category == "unsupported_semantics"
    with pytest.raises(KnowledgeSearchError):
        app.search(replace(request(), knowledge_types=("code",)))


def test_revision_checked_against_head_and_source_bytes(tmp_path, source):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-qm", "fixture")
    revision = git("rev-parse", "HEAD")
    app, _ = service(tmp_path, revision=revision)
    assert app.search(request()).results[0].revision == revision
    source.write_text("changed product")
    with pytest.raises(KnowledgeSearchError) as error:
        app.search(request())
    assert error.value.category == "snapshot_revision_mismatch"
    app, _ = service(tmp_path, revision="different")
    with pytest.raises(KnowledgeSearchError):
        app.search(request())


def test_revision_verification_uses_original_non_utf8_bytes(tmp_path, source):
    source.write_bytes(b"def product():\n    return '\xff'\n")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-qm", "fixture")
    revision = git("rev-parse", "HEAD")
    app, _ = service(tmp_path, revision=revision)
    assert app.search(request()).results[0].revision == revision
    source.write_bytes(b"def product():\n    return '\xfe'\n")
    with pytest.raises(KnowledgeSearchError) as error:
        app.search(request())
    assert error.value.category == "snapshot_revision_mismatch"


def test_classification_does_not_infer_from_query_signals_or_nested_expression():
    assert targetable_anchors(SearchIntent(canonical_query="src/service.py")) == ()
    assert targetable_anchors(SearchIntent(expression=Anchor("path", "src/service.py"))) == ()
    assert targetable_anchors(SearchIntent(anchors=(Anchor("identifier", "Product"),))) == ()


def test_disabled_policy_legacy_and_ambiguous_lookup_keep_broad_path(tmp_path, source):
    app, backend = service(tmp_path, retrieval=RetrievalConfig(targeted="disabled"))
    with pytest.raises(KnowledgeSearchError) as error:
        app.search(request())
    assert error.value.category == "unsupported_semantics"
    assert not backend.calls
    assert app.search(SearchRequest(intent=SearchIntent(semantic_terms=("product",)))).results
    assert len(backend.calls) == 1
    original = backend.lookup_exact

    def ambiguous(anchor, scope):
        found = original(anchor, scope)
        return replace(found, results=found.results * 2)

    backend.lookup_exact = ambiguous
    app, _ = service(tmp_path, backend)
    with pytest.raises(KnowledgeSearchError) as error:
        app.search(request())
    assert error.value.metadata["targeted"]["fallback_reason"] == "exact_ambiguous"
    assert len(backend.calls) == 1


def test_other_required_semantics_not_silently_removed(tmp_path, source):
    app, backend = service(tmp_path)
    with pytest.raises(KnowledgeSearchError) as error:
        app.search(request(expression=Term("missing")))
    assert error.value.category == "unsupported_semantics"
    assert error.value.metadata["targeted"]["fallback_reason"] == "additional_required_semantics"
    assert not backend.calls


def test_optional_lookup_capability_falls_back(tmp_path, source):
    backend = RecordingLocal()
    backend.retrieval_capabilities = RetrievalBackendCapabilities(
        lexical=True, canonical_query=True
    )
    app, _ = service(tmp_path, backend)
    with pytest.raises(KnowledgeSearchError) as error:
        app.search(request())
    assert not backend.calls
    assert error.value.metadata["targeted"]["fallback_reason"] == "unsupported"
    assert not error.value.metadata["targeted"]["targeted_attempted"]
    assert not QdrantVectorBackend.retrieval_capabilities.targeted_lookup


@pytest.mark.parametrize("backend_class", [SolrBackend, OpenSearchBackend])
def test_remote_exact_uses_bounded_structured_query_without_dense(backend_class, tmp_path):
    backend = backend_class()
    calls = []

    def search(intent, scope, requirements):
        calls.append((intent, scope))
        return KnowledgeSearchResponse(
            backend.backend_id, "success", metadata={"output_truncated": False}
        )

    backend.search = search
    scope = SearchScope(
        tmp_path,
        "rev",
        "owner/repo",
        ("code",),
        1,
        settings={"diagnostic_candidate_window": 1000},
        retrieval=RetrievalConfig(dense=DenseRetrievalConfig(enabled=True)),
    )
    backend.lookup_exact(Anchor("path", "src/service.py"), scope)
    intent, exact_scope = calls[0]
    assert exact_scope.retrieval is None
    assert exact_scope.max_results == 2
    assert exact_scope.settings["diagnostic_candidate_window"] == 2
    assert exact_scope.repository == scope.repository and exact_scope.revision == scope.revision
    assert intent.anchors == (Anchor("path", "src/service.py"),)
    assert intent.canonical_query == ""


def test_backend_failures_never_become_fallback(tmp_path, source):
    app, backend = service(tmp_path)

    def broken(anchor, scope):
        raise KnowledgeSearchError("broken protocol", category="response_parsing_failure")

    backend.lookup_exact = broken
    with pytest.raises(KnowledgeSearchError):
        app.search(request())
    assert not backend.calls


def test_contract_serialization_compatibility():
    old = RetrievalConfig().to_mapping()
    assert "targeted" not in old
    assert RetrievalConfig.from_mapping(old) == RetrievalConfig()
    disabled = RetrievalConfig(targeted="disabled")
    assert RetrievalConfig.from_mapping(disabled.to_mapping()) == disabled
    with pytest.raises(ValueError):
        RetrievalConfig.from_mapping({"targeted": "confidence"})
    assert SearchIntent.from_mapping(SearchIntent(("product",)).to_mapping()) == SearchIntent(
        ("product",)
    )


def test_admission_and_content_bounds_preserved(tmp_path, source):
    source.write_text("product\n" * 500)
    app, _ = service(tmp_path, settings={"targeted_max_lines": 3})
    response = app.search(request())
    assert response.results[0].content.count("\n") == 2
    assert response.results[0].metadata["content_truncated"]
    app, _ = service(tmp_path, settings={"exclude_dirs": ["src"]})
    with pytest.raises(KnowledgeSearchError) as error:
        app.search(request())
    assert error.value.category == "unsupported_semantics"


def test_mismatched_backend_identity_or_revision_rejected(tmp_path, source):
    app, backend = service(tmp_path)
    for changes in (
        {"source_ref": "repository://other/repo/src/service.py"},
        {"source_ref": "repository://owner/repo/other.py"},
    ):
        result = SearchResult(
            "id", "src/service.py", "repository://owner/repo/src/service.py", "code", "product", 0.0
        )
        backend.lookup_exact = lambda *args, result=result, changes=changes: (
            KnowledgeSearchResponse(
                backend.backend_id,
                "success",
                (replace(result, **changes),),
                metadata={"output_truncated": False},
            )
        )
        with pytest.raises(KnowledgeSearchError):
            app.search(request())


def test_mcp_transports_v2_intent_through_application(tmp_path, source):
    import asyncio
    from fetchcore.mcp_server import create_mcp_server

    app, backend = service(tmp_path)
    server = create_mcp_server(app)
    result = asyncio.run(server.call_tool("search", {"intent": request().intent.to_mapping()}))
    assert not result.is_error
    assert (
        result.structured_content["results"][0]["source_ref"]
        == "repository://owner/repo/src/service.py"
    )
    assert result.structured_content["metadata"]["targeted"]["outcome"] == "exact_unique"
    assert not backend.calls


def test_multiple_exact_anchors_are_all_enforced_on_fallback(tmp_path, source):
    app, backend = service(tmp_path)
    intent = SearchIntent(
        canonical_query="product",
        anchors=(
            Anchor("path", "src/service.py"),
            Anchor("source_ref", "repository://owner/repo/other.py"),
        ),
    )
    with pytest.raises(KnowledgeSearchError) as error:
        app.search(SearchRequest(intent=intent))
    assert error.value.category == "unsupported_semantics"
    assert error.value.metadata["targeted"]["fallback_reason"] == "additional_required_semantics"
    assert not backend.calls


def test_fallback_passes_original_intent_to_capable_lexical_backend(tmp_path, source):
    backend = RecordingLocal()
    backend.retrieval_capabilities = RetrievalBackendCapabilities(
        lexical=True, canonical_query=True, structured_anchors=True
    )
    received = []

    def search(intent, scope, requirements):
        received.append(intent)
        return KnowledgeSearchResponse(backend.backend_id, "success")

    backend.search = search
    intent = request().intent
    app, _ = service(tmp_path, backend, retrieval=RetrievalConfig(targeted="disabled"))
    app.search(SearchRequest(intent=intent))
    assert received == [intent]
    app, _ = service(tmp_path, backend)
    app.search(SearchRequest(intent=intent))
    assert received[-1] is intent
    backend.retrieval_capabilities = RetrievalBackendCapabilities(
        lexical=True, canonical_query=True, structured_anchors=True, targeted_lookup=True
    )
    original_lookup = backend.lookup_exact

    def ambiguous(anchor, scope):
        found = original_lookup(anchor, scope)
        return replace(found, results=found.results * 2)

    backend.lookup_exact = ambiguous
    app.search(SearchRequest(intent=intent))
    assert received[-1] is intent
    assert len(received) == 3


@pytest.mark.parametrize("kind", ["path", "source_ref"])
def test_dense_without_exact_capability_rejects_required_anchor(tmp_path, source, kind):
    backend = RecordingLocal()
    backend.retrieval_capabilities = RetrievalBackendCapabilities(
        dense=True, canonical_query=True
    )
    app, _ = service(tmp_path, backend, retrieval=RetrievalConfig(
        dense=DenseRetrievalConfig(enabled=True)
    ))
    with pytest.raises(KnowledgeSearchError) as error:
        app.search(request(kind))
    assert error.value.category == "unsupported_semantics"
    assert error.value.metadata["targeted"]["fallback_reason"] == "unsupported"
    assert not backend.calls


def test_qdrant_required_anchor_rejects_before_backend_search(tmp_path, source):
    app, _ = service(tmp_path, QdrantVectorBackend(), retrieval=RetrievalConfig(
        dense=DenseRetrievalConfig(enabled=True)
    ))
    with pytest.raises(KnowledgeSearchError) as error:
        app.search(request())
    assert error.value.category == "unsupported_semantics"
    assert error.value.metadata["targeted"]["fallback_reason"] == "unsupported"


def test_empty_intent_and_mixed_convenience_fields_rejected(tmp_path):
    app, backend = service(tmp_path)
    for req in (SearchRequest(intent=SearchIntent()), replace(request(), query="product")):
        with pytest.raises(KnowledgeSearchError):
            app.search(req)
    assert not backend.calls
