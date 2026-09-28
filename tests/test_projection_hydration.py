from __future__ import annotations

import json

import pytest

from fetchcore.projection import (
    ProjectionV3CanonicalDocumentReader,
    ProjectionV3Compiler,
)
from fetchcore.repository.indexing import KnowledgeDocument, KnowledgeProvenance
from fetchcore.search import KnowledgeSearchError


def _document() -> KnowledgeDocument:
    source_ref = "repo://example/repo/src/example.py"
    chunk_ref = f"{source_ref}#symbol:example:0:segment:0"
    return KnowledgeDocument(
        id="kdoc-semantic-hydration",
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
        technical_role=None,
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


def test_projection_reader_rejects_corrupt_semantic_evidence_safely() -> None:
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    payload = json.loads(str(mapping["canonical_payload"]))
    payload["evidence"]["semantic"] = ["corrupt"]
    mapping["canonical_payload"] = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )

    with pytest.raises(KnowledgeSearchError) as raised:
        ProjectionV3CanonicalDocumentReader().read(mapping)

    assert raised.value.category == "response_parsing_failure"
