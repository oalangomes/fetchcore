"""Declared semantics, compatibility and adapter translation boundaries for #19."""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from fetchcore.adapters.retrieval.local.backend import LocalSemanticBackend
from fetchcore.adapters.retrieval.opensearch.backend import OpenSearchBackend, OpenSearchSettings
from fetchcore.adapters.retrieval.opensearch.query import _search_body
from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorBackend
from fetchcore.adapters.retrieval.qdrant.sparse import intent_sparse_terms
from fetchcore.adapters.retrieval.solr.backend import SolrBackend, SolrSettings
from fetchcore.adapters.retrieval.solr.query import _search_params
from fetchcore.retrieval.config import RetrievalConfig
from fetchcore.retrieval.contracts import (
    AllOf,
    Anchor,
    AnyOf,
    KnowledgeSearchError,
    QueryExpansion,
    SearchIntent,
    SearchScope,
    Term,
    expression_from_mapping,
)
from fetchcore.retrieval.embedding import ProjectionV3EmbeddingSerializer
from fetchcore.retrieval.signals import QuerySignal
from fetchcore.retrieval.translation import IntentTranslation, translation_diagnostics

QUERY = "Where is the repository-local .fetchcore/indexing.yaml policy loaded and validated?"
BRANCHES = AnyOf(
    (
        AllOf((Anchor("identifier", "ProductService"), Term("checkout"))),
        AllOf((Anchor("identifier", "ProductFacade"), Term("cart"))),
    )
)


def scope(tmp_path, *, mode="lexical", diagnostics=False):
    retrieval = RetrievalConfig.from_mapping(
        {
            "dense": {"enabled": mode != "lexical"},
            "hybrid": {"enabled": mode == "hybrid"},
        }
    )
    return SearchScope(
        tmp_path,
        None,
        "owner/repo",
        (),
        10,
        retrieval=retrieval,
        settings={"diagnostic_candidate_window": 10} if diagnostics else {},
    )


def signal():
    return QuerySignal(
        "ProductService",
        "exact",
        "exact_identifier",
        10,
        1,
        "caller",
        "query",
        "subject",
        required=True,
    )


@pytest.mark.parametrize("kind", ["identifier", "qualified_symbol", "path", "route", "source_ref"])
def test_v2_round_trip_all_fields_and_expression_nodes(kind):
    intent = SearchIntent(
        canonical_query="  original query\n",
        anchors=(Anchor(kind, "value", "explicit-reference"),),
        expression=BRANCHES,
        expansions=(QueryExpansion("auth", "caller:dictionary-v1"),),
        hints=("probably Java",),
        signals=(signal(),),
        exact_terms=("legacy",),
        semantic_terms=("legacy phrase",),
    )
    wire = json.loads(json.dumps(intent.to_mapping()))
    assert wire["schema_version"] == 2
    assert SearchIntent.from_mapping(wire) == intent
    assert expression_from_mapping(BRANCHES.to_mapping()) == BRANCHES


def test_v1_mapping_preserves_serialized_identity_and_does_not_invent_requirements():
    legacy = {
        "exact_terms": ["ProductService", "ProductService"],
        "semantic_terms": ["ProductService or ProductFacade"],
        "hints": ["service"],
        "signals": [signal().to_mapping()],
    }
    intent = SearchIntent.from_mapping(legacy)
    assert intent.to_mapping() == legacy
    assert intent.canonical_query == ""
    assert intent.anchors == () and intent.expression is None and intent.expansions == ()
    assert intent.terms == ("ProductService", "ProductService or ProductFacade")
    assert (
        SearchIntent(
            tuple(legacy["exact_terms"]), tuple(legacy["semantic_terms"]), ("service",), (signal(),)
        )
        == intent
    )
    assert SearchIntent.from_mapping({**legacy, "schema_version": 1}) == intent


def test_expansions_and_hints_do_not_mutate_canonical_or_required_identity():
    original = SearchIntent(
        canonical_query="authentication",
        anchors=(Anchor("path", "src/auth.py"),),
        expression=BRANCHES,
    )
    expanded = replace(
        original, expansions=(QueryExpansion("auth", "caller"),), hints=("probably Java",)
    )
    assert expanded.canonical_query == original.canonical_query
    assert expanded.anchors == original.anchors
    assert expanded.expression == original.expression
    assert expanded.terms == original.terms == ()


@pytest.mark.parametrize(
    "mapping",
    [
        {"schema_version": 3},
        {"schema_version": True},
        {"canonical_query": "query"},
        {"schema_version": 2, "canonical_query": None},
        {"exact_terms": "text"},
        {"exact_terms": [2]},
        {"unknown": True},
        {"schema_version": 2, "anchors": [{"type": "term", "value": "not an anchor"}]},
        {"schema_version": 2, "expression": {"type": "all_of", "children": []}},
        {"schema_version": 2, "expression": {"type": "not", "value": "A"}},
        {"schema_version": 2, "expansions": [{"value": "auth"}]},
    ],
)
def test_invalid_or_future_wire_semantics_fail_closed(mapping):
    with pytest.raises(ValueError):
        SearchIntent.from_mapping(mapping)


def test_expression_depth_and_mutability_are_bounded():
    with pytest.raises(ValueError):
        AllOf([Term("mutable")])
    nested = Term("leaf").to_mapping()
    for _ in range(18):
        nested = {"type": "all_of", "children": [nested]}
    with pytest.raises(ValueError, match="depth"):
        expression_from_mapping(nested)


@pytest.mark.parametrize(
    "backend,mode",
    [
        (LocalSemanticBackend, "lexical"),
        (SolrBackend, "dense"),
        (SolrBackend, "hybrid"),
        (OpenSearchBackend, "dense"),
        (OpenSearchBackend, "hybrid"),
        (QdrantVectorBackend, "dense"),
        (QdrantVectorBackend, "hybrid"),
    ],
)
@pytest.mark.parametrize(
    "fields,unsupported",
    [
        ({"anchors": (Anchor("path", "src/auth.py"),)}, "anchors"),
        ({"expression": BRANCHES}, "expression"),
    ],
)
def test_unsupported_structure_rejected_before_scanning_or_transport(
    tmp_path, backend, mode, fields, unsupported
):
    intent = SearchIntent(canonical_query=QUERY, **fields)
    with pytest.raises(KnowledgeSearchError) as caught:
        backend().search(intent, scope(tmp_path / "missing", mode=mode), ())
    assert caught.value.category == "unsupported_semantics"
    observation = caught.value.metadata["intent_translation"]
    assert observation["status"] == "rejected"
    assert unsupported in observation["unsupported"]
    assert observation["reasons"]
    assert caught.value.metadata["translation_policy"] == "reject"
    assert intent == SearchIntent.from_mapping(intent.to_mapping())


def test_native_lexical_query_and_required_branches_survive_both_translators(tmp_path):
    intent = SearchIntent(
        canonical_query=QUERY,
        anchors=(Anchor("path", "src/policy.py"),),
        expression=BRANCHES,
        hints=("not required",),
    )
    snapshot = SimpleNamespace(repository="owner/repo")
    solr = _search_params(intent, scope(tmp_path), snapshot, SolrSettings())
    assert solr["canonical_query"] == QUERY
    assert QUERY not in solr["q"]  # separate parameter, never manufactured into a phrase
    assert solr["uf"] == "* _query_"
    assert '(content:"checkout")' not in solr["q"]
    assert solr["fq"][-1].count(" AND ") == 2
    assert 'content:"checkout"' in solr["fq"][-1]
    assert r'(path:"src\/policy.py")' in solr["fq"]
    assert "not required" not in solr["q"]
    assert solr["bq"]

    body = _search_body(intent, scope(tmp_path), snapshot, OpenSearchSettings())
    main_query = body["query"]["bool"]["must"][0]["bool"]
    assert main_query["should"][0]["multi_match"]["query"] == QUERY
    groups = main_query["filter"][-1]["bool"]
    assert groups["minimum_should_match"] == 1
    assert len(groups["should"]) == 2
    assert all(len(branch["bool"]["must"]) == 2 for branch in groups["should"])
    assert body["query"]["bool"]["minimum_should_match"] == 0  # hints stay optional


def test_scope_paths_do_not_discard_v2_query_or_declared_requirements(tmp_path):
    scoped = replace(scope(tmp_path), repository_paths=("src/policy.py",))
    intent = SearchIntent(canonical_query=QUERY, expression=BRANCHES)
    snapshot = SimpleNamespace(repository="owner/repo")
    solr = _search_params(intent, scoped, snapshot, SolrSettings())
    opensearch = _search_body(intent, scoped, snapshot, OpenSearchSettings())
    assert solr["q"] != "*:*" and len(solr["fq"]) == 4
    assert opensearch["query"]["bool"]["should"][0]["multi_match"]["query"] == QUERY
    assert len(opensearch["query"]["bool"]["filter"]) == 4


def test_adding_expansion_to_anchor_only_intent_does_not_narrow_it(tmp_path):
    intent = SearchIntent(
        anchors=(Anchor("path", "src/auth.py"),), expansions=(QueryExpansion("auth", "caller"),)
    )
    snapshot = SimpleNamespace(repository="owner/repo")
    solr = _search_params(intent, scope(tmp_path), snapshot, SolrSettings())
    opensearch = _search_body(intent, scope(tmp_path), snapshot, OpenSearchSettings())
    assert solr["q"].startswith("*:* OR ")
    assert opensearch["query"]["bool"]["should"][0] == {"match_all": {}}


def test_translation_observations_are_optional_and_native_analysis_is_preserved(tmp_path):
    intent = SearchIntent(canonical_query=QUERY)
    assert translation_diagnostics(intent, scope(tmp_path), lexical={"query": QUERY}) == {}
    diagnostics = translation_diagnostics(
        intent, scope(tmp_path, diagnostics=True), lexical={"query": QUERY}
    )
    item = diagnostics["intent_translation"][0]
    assert item["canonical_query"] == QUERY
    assert item["status"] == "preserved" and item["reasons"] == []
    expanded = replace(intent, expansions=(QueryExpansion("auth", "caller"),))
    item = translation_diagnostics(expanded, scope(tmp_path, diagnostics=True), lexical={})[
        "intent_translation"
    ][0]
    assert item["status"] == "broadened"
    assert item["reasons"] == ["caller_supplied_expansions"]
    assert item["unsupported"] == []


@pytest.mark.parametrize("status", ["preserved", "broadened", "partially_unsupported", "rejected"])
def test_translation_observation_round_trip(status):
    observation = IntentTranslation("dense", status, ("reason",), ("expression",))
    assert (
        IntentTranslation.from_mapping(json.loads(json.dumps(observation.to_mapping())))
        == observation
    )


def test_embedding_uses_canonical_query_directly_and_preserves_v1_bytes():
    serializer = ProjectionV3EmbeddingSerializer()
    assert (
        serializer.serialize_query(SearchIntent(canonical_query="  " + QUERY + "\n"))
        == "  " + QUERY + "\n"
    )
    assert (
        serializer.serialize_query(SearchIntent(exact_terms=("A",), semantic_terms=("B C",)))
        == "exact:\nA\nsemantic:\nB C"
    )
    assert (
        serializer.serialize_query(SearchIntent(canonical_query=QUERY, exact_terms=("A",)))
        == QUERY + "\nexact:\nA"
    )
    with pytest.raises(KnowledgeSearchError):
        serializer.serialize_query(SearchIntent(canonical_query=QUERY, expression=BRANCHES))
    with pytest.raises(KnowledgeSearchError):
        serializer.serialize_query(
            SearchIntent(canonical_query=QUERY, expansions=(QueryExpansion("auth", "caller"),))
        )


def test_qdrant_uses_existing_sparse_analyzer_for_canonical_query():
    assert intent_sparse_terms(SearchIntent(canonical_query=QUERY)) == intent_sparse_terms(
        SearchIntent(semantic_terms=(QUERY,))
    )
    assert intent_sparse_terms(SearchIntent(canonical_query=QUERY))


def test_local_uses_existing_phrase_matching_and_exposes_translation(tmp_path):
    (tmp_path / "demo.py").write_text('"authentication works"\n')
    response = LocalSemanticBackend().search(
        SearchIntent(canonical_query="authentication works"), scope(tmp_path, diagnostics=True), ()
    )
    assert len(response.results) == 1
    assert response.metadata["intent_translation"][0]["status"] == "preserved"
    assert response.results[0].source_ref == "repository://owner/repo/demo.py"
    assert response.results[0].checksum


def test_core_contract_has_no_vendor_imports_or_scope_policy_fields():
    from fetchcore.retrieval import contracts

    source = Path(contracts.__file__).read_text()
    tree = ast.parse(source)
    imported = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(
        "adapter" in (module or "") or "backends" in (module or "") for module in imported
    )
    assert (
        not {"repository", "revision", "backend", "budget", "mode", "authority", "satisfaction"}
        & SearchIntent.__dataclass_fields__.keys()
    )
    assert (
        not {"qf", "fq", "bool", "must", "minimum_should_match"}
        & SearchIntent(canonical_query=QUERY, expression=BRANCHES).to_mapping().keys()
    )


def test_local_canonical_query_survives_legacy_budget_and_scope_cannot_replace_it(tmp_path):
    from fetchcore.adapters.retrieval.local.backend import _local_query

    intent = SearchIntent(canonical_query=QUERY, exact_terms=("legacy",))
    query = _local_query(intent, {"max_expanded_query_terms": 1})
    assert QUERY in query.semantic_terms
    (tmp_path / "demo.py").write_text("unrelated\n")
    response = LocalSemanticBackend().search(
        intent,
        replace(scope(tmp_path), repository_paths=("demo.py",)),
        (),
    )
    assert response.results == ()


def test_all_twenty_frozen_v1_translations_match_pre_change_digest():
    """Digest captured from 32fb976, not regenerated from the implementation under test."""
    import hashlib
    from dataclasses import asdict
    from fetchcore.adapters.retrieval.local.backend import _local_query

    root = Path(__file__).resolve().parents[1]
    cases = json.loads((root / "benchmarks/baseline-v1/ground-truth.json").read_text())["queries"]
    scoped = SearchScope(Path("."), None, "owner/repo", (), 20)
    snapshot = SimpleNamespace(repository="owner/repo")
    translations = []
    for case in cases:
        intent = SearchIntent.from_mapping(case["intent"])
        translations.append(
            {
                "intent": intent.to_mapping(),
                "solr": _search_params(intent, scoped, snapshot, SolrSettings()),
                "opensearch": _search_body(intent, scoped, snapshot, OpenSearchSettings()),
                "local": asdict(_local_query(intent, {})),
                "sparse": intent_sparse_terms(intent),
                "embedding": ProjectionV3EmbeddingSerializer().serialize_query(intent),
            }
        )
    assert len(translations) == 20
    digest = hashlib.sha256(
        json.dumps(translations, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert digest == "4a3b18e742c447e878b1bf35ef4b78370c98e1f1db5b110269ae04c4882ccc12"


def test_baseline_reader_preserves_v2_instead_of_dropping_new_fields():
    from fetchcore.baseline import BaselineConfigurationError, BaselineSuite

    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "benchmarks/baseline-v1/ground-truth.json").read_text())
    intent = SearchIntent(canonical_query=QUERY, expression=BRANCHES)
    payload["queries"][0]["intent"] = intent.to_mapping()
    assert BaselineSuite.from_mapping(payload).cases[0].intent == intent
    payload["queries"][0]["intent"]["schema_version"] = 999
    with pytest.raises(BaselineConfigurationError, match="schema_version"):
        BaselineSuite.from_mapping(payload)


def test_sparse_channel_cannot_silently_discard_declared_boolean_groups():
    with pytest.raises(KnowledgeSearchError) as caught:
        intent_sparse_terms(SearchIntent(canonical_query=QUERY, expression=BRANCHES))
    assert caught.value.metadata["intent_translation"]["channel"] == "sparse"
    assert caught.value.metadata["intent_translation"]["status"] == "rejected"


def test_intent_rejects_excessive_combined_structure():
    with pytest.raises(ValueError, match="limit"):
        SearchIntent(anchors=tuple(Anchor("identifier", str(i)) for i in range(257)))
    with pytest.raises(ValueError, match="limit"):
        SearchIntent(expression=AllOf(tuple(Term(str(i)) for i in range(257))))
