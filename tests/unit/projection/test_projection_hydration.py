from __future__ import annotations

import json
from dataclasses import replace

import pytest

from fetchcore.knowledge.evidence import KnowledgeEvidence, StructureEvidence, SymbolEvidence
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


def _document_with_symbol() -> KnowledgeDocument:
    return replace(
        _document(),
        evidence=KnowledgeEvidence(
            symbol=SymbolEvidence(name="example", kind="function", line_start=1, line_end=2),
            structure=StructureEvidence(exports=("example",)),
        ),
    )


def _mapping_with_payload_change(
    field: str, value: object, *, provenance: bool = False
) -> dict[str, object]:
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    payload = json.loads(str(mapping["canonical_payload"]))
    target = payload["provenance"] if provenance else payload
    target[field] = value
    mapping["canonical_payload"] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return mapping


def test_projection_compiler_reader_round_trip_preserves_canonical_document() -> None:
    document = _document_with_symbol()
    mapping = ProjectionV3Compiler().compile(document).to_mapping()

    hydrated = ProjectionV3CanonicalDocumentReader().read(mapping)

    assert hydrated == document
    assert hydrated.to_mapping() == document.to_mapping()


def test_projection_reader_rejects_empty_nested_symbol() -> None:
    mapping = ProjectionV3Compiler().compile(_document_with_symbol()).to_mapping()
    payload = json.loads(str(mapping["canonical_payload"]))
    payload["evidence"]["symbol"] = {}
    mapping["canonical_payload"] = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    with pytest.raises(ProjectionParsingError) as raised:
        ProjectionV3CanonicalDocumentReader().read(mapping)

    assert raised.value.metadata == {"field": "evidence.symbol.name"}


@pytest.mark.parametrize("field", ["kind", "qualified_name"])
def test_projection_reader_rejects_missing_nested_symbol_field(field: str) -> None:
    mapping = ProjectionV3Compiler().compile(_document_with_symbol()).to_mapping()
    payload = json.loads(str(mapping["canonical_payload"]))
    del payload["evidence"]["symbol"][field]
    mapping["canonical_payload"] = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    with pytest.raises(ProjectionParsingError) as raised:
        ProjectionV3CanonicalDocumentReader().read(mapping)

    assert raised.value.metadata == {"field": f"evidence.symbol.{field}"}


def test_projection_reader_accepts_explicit_none_in_nested_symbol() -> None:
    document = _document_with_symbol()
    mapping = ProjectionV3Compiler().compile(document).to_mapping()
    payload = json.loads(str(mapping["canonical_payload"]))
    assert payload["evidence"]["symbol"]["qualified_name"] is None

    assert ProjectionV3CanonicalDocumentReader().read(mapping) == document


@pytest.mark.parametrize(
    ("field", "value", "provenance"),
    [
        ("repository", 123, False),
        ("path", False, False),
        ("source_ref", [], True),
        ("branch", 123, False),
    ],
)
def test_projection_reader_rejects_invalid_string_representation(
    field: str, value: object, provenance: bool
) -> None:
    mapping = _mapping_with_payload_change(field, value, provenance=provenance)

    with pytest.raises(ProjectionParsingError) as raised:
        ProjectionV3CanonicalDocumentReader().read(mapping)

    expected_field = f"provenance.{field}" if provenance else field
    assert raised.value.metadata == {
        "field": expected_field,
        "expected_type": "str",
        "actual_type": type(value).__name__,
    }


@pytest.mark.parametrize("value", ["10", 10.0, True])
@pytest.mark.parametrize("provenance", [False, True])
def test_projection_reader_rejects_invalid_integer_representation(
    value: object, provenance: bool
) -> None:
    mapping = _mapping_with_payload_change("line_start", value, provenance=provenance)

    with pytest.raises(ProjectionParsingError) as raised:
        ProjectionV3CanonicalDocumentReader().read(mapping)

    assert raised.value.metadata == {
        "field": "provenance.line_start" if provenance else "line_start",
        "expected_type": "int",
        "actual_type": type(value).__name__,
    }


@pytest.mark.parametrize("value", [["example", 123], "example", 123])
def test_projection_reader_rejects_invalid_symbol_collection(value: object) -> None:
    mapping = _mapping_with_payload_change("symbols", value)

    with pytest.raises(ProjectionParsingError):
        ProjectionV3CanonicalDocumentReader().read(mapping)


def test_projection_reader_rejects_nested_evidence_member_coercion() -> None:
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    payload = json.loads(str(mapping["canonical_payload"]))
    payload["evidence"]["structure"]["exports"] = ["example", 123]
    mapping["canonical_payload"] = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    with pytest.raises(ProjectionParsingError) as raised:
        ProjectionV3CanonicalDocumentReader().read(mapping)

    assert raised.value.metadata["field"] == "evidence.structure.exports[1]"


def test_projection_reader_wraps_non_finite_nested_integer() -> None:
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    payload = json.loads(str(mapping["canonical_payload"]))
    payload["evidence"]["symbol"] = {
        "name": "example",
        "kind": "function",
        "line_start": float("inf"),
        "line_end": 2,
    }
    mapping["canonical_payload"] = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    with pytest.raises(ProjectionParsingError, match="Canonical payload is invalid"):
        ProjectionV3CanonicalDocumentReader().read(mapping)


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


def test_projection_reader_requires_rebuild_for_prior_route_contract() -> None:
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    mapping["projection_fingerprint"] = (
        "sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039"
    )

    with pytest.raises(ProjectionCompatibilityError) as raised:
        ProjectionV3CanonicalDocumentReader().read(mapping)

    assert raised.value.metadata["rebuild_required"] is True


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
