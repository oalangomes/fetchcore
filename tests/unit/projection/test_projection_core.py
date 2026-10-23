from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from fetchcore.projection import (
    PROJECTION_VERSION,
    ProjectionParsingError,
    ProjectionV3Compiler,
    projection_contract_fingerprint,
)
from fetchcore.repository.api_contracts import (
    ApiEvidenceV1,
    ApiSourceLocatorV1,
    attach_api_evidence,
)
from fetchcore.repository.indexing import KnowledgeDocument, KnowledgeProvenance
from fetchcore.repository.semantic import SemanticEvidenceEnvelope


FROZEN_PROJECTION_FINGERPRINT = (
    "sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039"
)
CURRENT_PROJECTION_FINGERPRINT = (
    "sha256:80d19b9400141cd7bb73f7ba7f64de414cd5a896b87c2dc1713959804696cef2"
)
FROZEN_COMPILER_MAPPING_SHA256 = (
    "37189d179ed3d18be8a184fef70e7de0925c2237190a6f635106246ed8bb965d"
)
CURRENT_COMPILER_MAPPING_SHA256 = (
    "1cdc84f37fe40ed9ca88a25fdd7b259079070162cddd73b608c270f3b89b3806"
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


def test_projection_v3_route_semantics_change_fingerprint() -> None:
    assert PROJECTION_VERSION == "3"
    assert projection_contract_fingerprint() == CURRENT_PROJECTION_FINGERPRINT
    assert projection_contract_fingerprint() != FROZEN_PROJECTION_FINGERPRINT


def test_projection_v3_compiler_mapping_changes_only_its_fingerprint_for_plain_code() -> None:
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    serialized = json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(serialized.encode()).hexdigest() == CURRENT_COMPILER_MAPPING_SHA256
    mapping["projection_fingerprint"] = FROZEN_PROJECTION_FINGERPRINT
    historical = json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(historical.encode()).hexdigest() == FROZEN_COMPILER_MAPPING_SHA256


@pytest.mark.parametrize(
    ("kind", "format", "format_version", "pointer"),
    [
        ("api_operation", "openapi", "3.0.0", "#/paths/~1health/get"),
        ("api_request", "postman_collection", "2.1.0", "#/item/0"),
    ],
)
def test_projection_indexes_validated_api_route_facts(
    kind: str, format: str, format_version: str, pointer: str,
) -> None:
    document = attach_api_evidence(
        _document(),
        ApiEvidenceV1(
            kind=kind,  # type: ignore[arg-type]
            format=format,  # type: ignore[arg-type]
            format_version=format_version,
            locator=ApiSourceLocatorV1(path="src/example.py", pointer=pointer),
            method="GET",
            route="/Health",
        ),
    )
    projected = ProjectionV3Compiler().compile(document).to_mapping()
    assert projected["route_methods"] == ["get"]
    assert projected["route_paths"] == ["/health"]
    assert projected["route_mechanisms"] == []


def test_projection_rejects_incompatible_api_route_evidence() -> None:
    document = _document()
    envelope = SemanticEvidenceEnvelope.from_payload(
        namespace="api_contract", kind="api_operation", version="2",
        payload={"method": "GET", "route": "/health"},
    )
    document = replace(
        document,
        evidence=replace(document.evidence, semantic=(envelope,)),
    )
    with pytest.raises(ProjectionParsingError, match="API route evidence is invalid"):
        ProjectionV3Compiler().compile(document)
