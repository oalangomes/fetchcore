"""Rebuildable exact lookup keys derived from canonical structural evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import get_args

from fetchcore.knowledge.evidence import identify_declarations
from fetchcore.knowledge.structure import (
    ExtractionCapabilities, StructuralCapability, StructuralCoverage,
)
from fetchcore.repository.indexing import KnowledgeDocument


STRUCTURAL_LOOKUP_VERSION = "2"
STRUCTURAL_LOOKUP_FIELDS = (
    "structural_names", "structural_qualified_names", "structural_identities",
    "structural_incoming_identities",
)


def structural_lookup_fingerprint() -> str:
    """Identify the derived key layout independently from Projection v3/embeddings."""

    payload = json.dumps(
        {"version": STRUCTURAL_LOOKUP_VERSION, "fields": STRUCTURAL_LOOKUP_FIELDS},
        sort_keys=True, separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StructuralLookupKeys:
    """Exact declarations carried beside one canonical projected document."""

    names: tuple[str, ...]
    qualified_names: tuple[str, ...]
    identities: tuple[str, ...]
    incoming_identities: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        return {
            "structural_names": list(self.names),
            "structural_qualified_names": list(self.qualified_names),
            "structural_identities": list(self.identities),
            "structural_incoming_identities": list(self.incoming_identities),
        }


def structural_lookup_keys(document: KnowledgeDocument) -> StructuralLookupKeys:
    """Derive exact keys; legacy evidence receives only its known declarations."""

    declarations = (
        document.evidence.declarations
        if document.evidence.extraction is not None else identify_declarations(
            tuple(dict.fromkeys((
                *((document.evidence.symbol,) if document.evidence.symbol else ()),
                *document.evidence.behavior.methods,
            ))),
            repository=document.repository, path=document.path, language=document.language,
        )
    )
    for item in declarations:
        if item.identity is None or (
            item.identity.repository != document.repository
            or item.identity.path != document.path
            or item.identity.language != document.language
        ):
            raise ValueError("structural lookup declaration conflicts with canonical source")
    return StructuralLookupKeys(
        names=tuple(sorted({item.name for item in declarations})),
        qualified_names=tuple(sorted({item.qualified_name or item.name for item in declarations})),
        identities=tuple(sorted({item.identity.key for item in declarations if item.identity})),
        incoming_identities=tuple(sorted({
            item.target_identity for item in document.evidence.relations.semantic
            if item.target_identity is not None and item.resolution in {"resolved", "exact-unique"}
        })),
    )


def structural_coverage_summary(documents: tuple[KnowledgeDocument, ...]) -> str:
    """Persist compact source-level coverage once when publishing a snapshot."""

    sources: dict[tuple[str | None, str], ExtractionCapabilities | None] = {}
    for document in documents:
        key = (document.language, document.source_ref)
        observed = document.evidence.extraction
        if key in sources and sources[key] != observed:
            raise ValueError("structural extraction coverage conflicts within one source")
        sources[key] = observed
    grouped: dict[str, list[ExtractionCapabilities | None]] = {"*": list(sources.values())}
    for (language, _), item in sources.items():
        grouped.setdefault(language or "", []).append(item)
    summary: dict[str, object] = {}
    for language, values in grouped.items():
        producers = sorted({item for item in values if item is not None},
                           key=lambda item: (item.language or "", item.producer,
                                             item.version, item.capabilities))
        summary[language] = {
            "sources": len(values),
            "unknown": sum(item is None for item in values),
            "supported": {
                capability: sum(item is not None and capability in item.capabilities
                                for item in values)
                for capability in get_args(StructuralCapability)
            },
            "producers": [item.to_mapping() for item in producers],
        }
    return json.dumps(summary, sort_keys=True, separators=(",", ":"))


def structural_coverage_from_summary(
    raw: str, capability: StructuralCapability, language: str | None = None,
) -> StructuralCoverage:
    """Read a snapshot-level coverage observation without loading documents."""

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("structural coverage summary is invalid")
    item = data.get(language if language is not None else "*", {})
    if not isinstance(item, dict):
        raise ValueError("structural language coverage is invalid")
    supported = item.get("supported", {})
    producers = item.get("producers", [])
    if not isinstance(supported, dict) or not isinstance(producers, list):
        raise ValueError("structural capability coverage is invalid")
    return StructuralCoverage(
        capability=capability,
        sources=int(item.get("sources", 0)),
        supported_sources=int(supported.get(capability, 0)),
        unknown_sources=int(item.get("unknown", 0)),
        producers=tuple(ExtractionCapabilities.from_mapping(value) for value in producers),
    )
