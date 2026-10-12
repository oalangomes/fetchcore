from __future__ import annotations

import json

import pytest

from fetchcore.projection import (
    ProjectionCompatibilityError,
    ProjectionParsingError,
    ProjectionV3CanonicalDocumentReader,
    ProjectionV3Compiler,
)
from fetchcore.repository.indexing import KnowledgeDocument, KnowledgeProvenance


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

    with pytest.raises(ProjectionParsingError, match="Canonical payload is invalid"):
        ProjectionV3CanonicalDocumentReader().read(mapping)


def test_projection_reader_rejects_incompatible_version_with_metadata() -> None:
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    mapping["projection_version"] = "2"

    with pytest.raises(ProjectionCompatibilityError) as raised:
        ProjectionV3CanonicalDocumentReader().read(mapping)

    assert raised.value.metadata == {
        "expected_projection_version": "3",
        "actual_projection_version": "2",
    }


def test_projection_reader_rejects_incompatible_fingerprint_with_metadata() -> None:
    reader = ProjectionV3CanonicalDocumentReader()
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    mapping["projection_fingerprint"] = "sha256:incompatible"

    with pytest.raises(ProjectionCompatibilityError) as raised:
        reader.read(mapping)

    assert raised.value.metadata == {
        "expected_projection_fingerprint": reader.projection_fingerprint,
        "actual_projection_fingerprint": "sha256:incompatible",
        "rebuild_required": True,
    }


def test_projection_reader_rejects_corrupt_canonical_payload() -> None:
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    mapping["canonical_payload"] = "{invalid"

    with pytest.raises(ProjectionParsingError, match="Canonical payload is invalid") as raised:
        ProjectionV3CanonicalDocumentReader().read(mapping)

    assert isinstance(raised.value.__cause__, json.JSONDecodeError)


def test_projection_reader_rejects_missing_canonical_payload() -> None:
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    del mapping["canonical_payload"]

    with pytest.raises(
        ProjectionParsingError,
        match="Knowledge projection record has no canonical payload",
    ):
        ProjectionV3CanonicalDocumentReader().read(mapping)


def test_projection_reader_rejects_non_knowledge_record() -> None:
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    mapping["record_kind"] = "snapshot"

    with pytest.raises(
        ProjectionParsingError,
        match="Projection control record cannot be reconstructed as knowledge",
    ):
        ProjectionV3CanonicalDocumentReader().read(mapping)
