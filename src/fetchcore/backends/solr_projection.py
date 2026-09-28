"""Compatibility facade for the historical Solr Projection v3 API."""

from fetchcore.projection import (
    PROJECTION_VERSION,
    ProjectedKnowledgeRecord,
    ProjectionV3CanonicalDocumentReader,
    ProjectionV3Compiler,
    _identifiers_from_values,  # noqa: F401 - private compatibility export
    extract_content_identifiers,
    extract_identifiers,
    projection_contract_fingerprint,
)


SOLR_PROJECTION_VERSION = PROJECTION_VERSION
SolrProjectionCompiler = ProjectionV3Compiler
SolrCanonicalDocumentReader = ProjectionV3CanonicalDocumentReader

__all__ = [
    "SOLR_PROJECTION_VERSION",
    "ProjectedKnowledgeRecord",
    "SolrCanonicalDocumentReader",
    "SolrProjectionCompiler",
    "extract_content_identifiers",
    "extract_identifiers",
    "projection_contract_fingerprint",
]
