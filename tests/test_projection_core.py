from __future__ import annotations

import ast
from pathlib import Path

from fetchcore.projection import (
    PROJECTION_VERSION,
    ProjectionV3Compiler,
    projection_contract_fingerprint,
)
from fetchcore.projection_semantics import (
    match_projection_record,
    query_match_evidence,
    validate_projection_v3_controls,
)
from fetchcore.repository.indexing import KnowledgeDocument, KnowledgeProvenance
from fetchcore.search import SearchIntent
from fetchcore.signals import QuerySignal


FROZEN_PROJECTION_FINGERPRINT = (
    "sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039"
)


def _document() -> KnowledgeDocument:
    source_ref = "repo://example/repo/src/example.py"
    chunk_ref = f"{source_ref}#symbol:example:0:segment:0"
    return KnowledgeDocument(
        id="kdoc-projection-core",
        source_type="repository",
        source_ref=source_ref,
        chunk_ref=chunk_ref,
        repository="example/repo",
        branch="main",
        revision="rev-a",
        path="src/example.py",
        filename="example.py",
        language="python",
        knowledge_type="code",
        technical_role="service",
        symbols=("example",),
        symbol_types=("function",),
        content="def example():\n    return True\n",
        line_start=1,
        line_end=2,
        checksum="chunk-checksum",
        provenance=KnowledgeProvenance(
            source_ref=source_ref,
            chunk_ref=chunk_ref,
            repository="example/repo",
            revision="rev-a",
            path="src/example.py",
            line_start=1,
            line_end=2,
            source_checksum="source-checksum",
        ),
        metadata={},
        indexed_at="2026-09-17T00:00:00+00:00",
    )


def _signal() -> QuerySignal:
    return QuerySignal(
        value="example",
        type="exact",
        classification="exact_identifier",
        priority=100,
        weight=1.0,
        origin="caller",
        source="query",
        section="subject",
        protected=True,
        required=True,
    )


def test_projection_v3_fingerprint_is_preserved_during_extraction() -> None:
    assert PROJECTION_VERSION == "3"
    assert projection_contract_fingerprint() == FROZEN_PROJECTION_FINGERPRINT


def test_projection_semantics_use_fetchcore_query_and_evidence_contracts() -> None:
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    controls = validate_projection_v3_controls({}, backend_name="fetchcore-test")
    match = match_projection_record(
        SearchIntent(signals=(_signal(),)),
        mapping,
        controls,
    )

    assert "example" in match.matched_terms
    assert match.matched_signals == (_signal(),)
    assert match.channels
    evidence = query_match_evidence(
        match,
        source_ref=_document().source_ref,
        provenance="fetchcore_projection_v3_match",
    )
    assert evidence
    assert {item.provenance for item in evidence} == {
        "fetchcore_projection_v3_match"
    }


def test_projection_ranking_core_has_no_agentsorch_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "fetchcore"
    for relative in (
        "projection.py",
        "projection_semantics.py",
        "embedding.py",
        "fusion.py",
        "reranking.py",
        "reranking_query.py",
        "reranking_runtime.py",
    ):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
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
        assert not any(name == "agentsorch" or name.startswith("agentsorch.") for name in imported_modules)
