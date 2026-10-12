from __future__ import annotations

import hashlib
import json

from fetchcore.projection import (
    PROJECTION_VERSION,
    ProjectionV3Compiler,
    projection_contract_fingerprint,
)
from fetchcore.repository.indexing import KnowledgeDocument, KnowledgeProvenance


FROZEN_PROJECTION_FINGERPRINT = (
    "sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039"
)
FROZEN_COMPILER_MAPPING_SHA256 = (
    "37189d179ed3d18be8a184fef70e7de0925c2237190a6f635106246ed8bb965d"
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


def test_projection_v3_fingerprint_is_preserved_during_extraction() -> None:
    assert PROJECTION_VERSION == "3"
    assert projection_contract_fingerprint() == FROZEN_PROJECTION_FINGERPRINT


def test_projection_v3_compiler_mapping_is_unchanged() -> None:
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    serialized = json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(serialized.encode()).hexdigest() == FROZEN_COMPILER_MAPPING_SHA256
