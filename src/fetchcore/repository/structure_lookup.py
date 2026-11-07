"""Rebuildable exact lookup keys derived from canonical structural evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from fetchcore.knowledge.evidence import identify_declarations
from fetchcore.repository.indexing import KnowledgeDocument


STRUCTURAL_LOOKUP_VERSION = "1"
STRUCTURAL_LOOKUP_FIELDS = (
    "structural_names", "structural_qualified_names", "structural_identities",
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

    def to_mapping(self) -> dict[str, object]:
        return {
            "structural_names": list(self.names),
            "structural_qualified_names": list(self.qualified_names),
            "structural_identities": list(self.identities),
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
    )
