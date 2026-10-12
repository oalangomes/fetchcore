"""Explicit, versioned Postman Collection 2.1 indexing over canonical knowledge.

This module composes S1 evidence contracts and S2 safe scanner admission.
Postman environments/globals, native v3 and raw source serving remain blocked.
"""

from __future__ import annotations

from fetchcore.repository.api_detection import detect_api_artifact
from fetchcore.repository.indexing import RepositoryIndexConfig
from fetchcore.repository.openapi_extraction import sanitize_openapi
from fetchcore.repository.openapi_indexing import (
    OpenApiEvidenceEnricher,
    OpenApiSourceInterpreter,
)
from fetchcore.repository.postman_extraction import sanitize_postman_collection
from fetchcore.repository.safe_scan import SafeSource, SanitizedApiExtraction
from fetchcore.repository.semantic_indexing import (
    KnowledgeSourceUnit,
    SemanticDocumentRepositoryIndexer,
)


class PostmanCollectionSourceInterpreter:
    """Claim only source-validated, sanitized Postman request units."""

    indexing_contract_id = "fetchcore.api-contract.postman-collection"
    indexing_contract_version = "1"

    def interpret(self, source: SafeSource) -> tuple[KnowledgeSourceUnit, ...] | None:
        if not source.api_units or source.api_units[0].format != "postman_collection":
            return None
        return tuple(
            KnowledgeSourceUnit(
                discriminator=f"{unit.kind}:{unit.pointer}",
                content=unit.content,
                line_start=1,
                line_end=max(1, source.original_line_count),
                language="postman",
                knowledge_type="configuration",
                strategy="sanitized-api-contract",
            )
            for unit in source.api_units
        )


def _api_contract_sanitizer(relative: str, raw: str) -> SanitizedApiExtraction | None:
    decision = detect_api_artifact(relative, raw)
    if decision.status != "supported":
        return None
    if decision.kind in {"openapi", "swagger"}:
        return sanitize_openapi(relative, raw)
    if decision.kind == "postman_collection":
        return sanitize_postman_collection(relative, raw)
    return None


class PostmanRepositoryIndexer(SemanticDocumentRepositoryIndexer):
    """Opt-in Postman 2.1 indexer; no OpenAPI enrollment and no environment read."""

    def __init__(self, config: RepositoryIndexConfig | None = None) -> None:
        super().__init__(
            config,
            source_interpreters=(PostmanCollectionSourceInterpreter(),),
            semantic_enrichers=(OpenApiEvidenceEnricher(),),
        )
        self._api_sanitizer = sanitize_postman_collection
        self._api_sanitizer_kinds = frozenset({"postman_collection"})


class ApiContractRepositoryIndexer(SemanticDocumentRepositoryIndexer):
    """Opt-in combined Swagger/OpenAPI + Postman 2.1 typed acquisition.

    Maintains a single canonical corpus and backend-neutral contract. A new
    interpreter and sanitizer requires an explicit fingerprint-compatible rebuild.
    """

    def __init__(self, config: RepositoryIndexConfig | None = None) -> None:
        super().__init__(
            config,
            source_interpreters=(
                OpenApiSourceInterpreter("openapi"),
                OpenApiSourceInterpreter("swagger"),
                PostmanCollectionSourceInterpreter(),
            ),
            semantic_enrichers=(OpenApiEvidenceEnricher(),),
        )
        self._api_sanitizer = _api_contract_sanitizer
        self._api_sanitizer_kinds = frozenset(
            {"openapi", "swagger", "postman_collection"}
        )
