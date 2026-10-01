"""Stable canonical indexing identity, independent of backend storage."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict

from fetchcore.repository.indexing import RepositoryIndexer


INDEXING_CONTRACT_VERSION = "23"


def indexing_contract_fingerprint(indexer: RepositoryIndexer) -> str:
    """Fingerprint corpus-producing policy without repository identity/revision.

    Semantic implementation changes that are not represented by configuration
    or extractor identity must bump ``INDEXING_CONTRACT_VERSION``.  Repository
    policy added in later deliveries is automatically included when it becomes
    part of ``RepositoryIndexConfig``.
    """

    config = asdict(indexer.config)
    for identity_field in ("repository", "branch", "revision"):
        config.pop(identity_field, None)
    extractors = {
        language: {
            "type": f"{type(extractor).__module__}.{type(extractor).__qualname__}",
            "version": str(getattr(extractor, "indexing_contract_version", "1")),
        }
        for language, extractor in sorted(indexer.symbol_extractors.items())
    }
    raw_document_contract = getattr(indexer, "document_indexing_contract", None)
    document_contract = (
        dict(raw_document_contract)
        if isinstance(raw_document_contract, Mapping)
        else None
    )
    payload = {
        "contract_version": INDEXING_CONTRACT_VERSION,
        "config": config,
        "extractors": extractors,
        "document_sources": document_contract,
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()
