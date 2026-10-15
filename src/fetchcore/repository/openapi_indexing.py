"""Opt-in, fingerprinted OpenAPI/Swagger KnowledgeSourceInterpreter integration.

A safe scanner pre-claims raw API files and exposes only sanitized structural
units. Generic text scanning and raw source serving retain S0 quarantine.
"""

from __future__ import annotations

from fetchcore.repository.api_contracts import (
    ApiEvidenceV1,
    ApiSourceLocatorV1,
    attach_api_evidence,
)
from fetchcore.repository.indexing import KnowledgeDocument, RepositoryIndexConfig
from fetchcore.repository.openapi_extraction import sanitize_openapi
from fetchcore.repository.safe_scan import SafeSource
from fetchcore.repository.semantic_indexing import (
    KnowledgeSourceUnit,
    SemanticDocumentRepositoryIndexer,
)


class OpenApiSourceInterpreter:
    """Claim only sanitized OpenAPI/Swagger units for exactly one format."""

    indexing_contract_version = "1"

    def __init__(self, api_format: str) -> None:
        if api_format not in {"openapi", "swagger"}:
            raise ValueError("OpenAPI interpreter format is unsupported")
        self.api_format = api_format
        self.indexing_contract_id = f"fetchcore.api-contract.{api_format}"

    def interpret(self, source: SafeSource) -> tuple[KnowledgeSourceUnit, ...] | None:
        if not source.api_units or source.api_units[0].format != self.api_format:
            return None
        return tuple(
            KnowledgeSourceUnit(
                discriminator=f"{unit.kind}:{unit.pointer}",
                content=unit.content,
                line_start=1,
                line_end=max(1, source.original_line_count),
                language="openapi",
                knowledge_type="configuration",
                strategy="sanitized-api-contract",
            )
            for unit in source.api_units
        )


class OpenApiEvidenceEnricher:
    """Attach only source-observed v1 facts to their canonical indexed units."""

    indexing_contract_id = "fetchcore.api-contract.evidence"
    indexing_contract_version = "1"

    def enrich(self, source: SafeSource, document: KnowledgeDocument) -> KnowledgeDocument:
        if not source.api_units:
            return document
        discriminator = document.metadata.get("chunk_discriminator")
        matched = [
            unit for unit in source.api_units
            if f"{unit.kind}:{unit.pointer}" == discriminator
        ]
        if len(matched) != 1:
            raise ValueError("sanitized API source unit identity must match exactly once")
        unit = matched[0]
        fact = ApiEvidenceV1(
            kind=unit.kind,  # type: ignore[arg-type]
            format=unit.format,  # type: ignore[arg-type]
            format_version=unit.format_version,
            locator=ApiSourceLocatorV1(path=source.relative, pointer=unit.pointer),
            method=unit.method, route=unit.route, name=unit.name, target=unit.target,
        )
        return attach_api_evidence(document, fact)


class OpenApiRepositoryIndexer(SemanticDocumentRepositoryIndexer):
    """Explicit safe-mode entrypoint for OpenAPI/Swagger typed indexing.

    Source admission is opt-in to preserve S0 for existing installations. A
    versioned interpreter contract enters the normal indexing fingerprint.
    No extra persistence/index authority or backend adapter is introduced.
    """

    def __init__(self, config: RepositoryIndexConfig | None = None) -> None:
        super().__init__(
            config,
            source_interpreters=(
                OpenApiSourceInterpreter("openapi"),
                OpenApiSourceInterpreter("swagger"),
            ),
            semantic_enrichers=(OpenApiEvidenceEnricher(),),
        )
        self._api_sanitizer = sanitize_openapi
