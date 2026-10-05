"""Deterministic sparse lexical vectors derived from backend-neutral Projection v3."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from fetchcore.retrieval.contracts import SearchIntent
from fetchcore.retrieval.config import RetrievalBackendCapabilities
from fetchcore.retrieval.translation import validate_intent_translation


SPARSE_VECTOR_NAME = "lexical"
SPARSE_ANALYZER_VERSION = "projection-v3-hashed-lexical-v1"
SPARSE_MODIFIER = "idf"

_PROJECTION_FIELDS = (
    "identifiers_exact",
    "content_identifiers_exact",
    "symbols",
    "filename",
    "filename_exact",
    "path",
    "path_text",
    "path_segments",
    "technical_role",
    "route_methods",
    "route_paths",
    "route_mechanisms",
    "import_targets",
    "dependency_targets",
    "relation_targets",
    "documentation_text",
    "content",
)
_TOKEN_PATTERN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+")
_CAMEL_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_WORD_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


def _analyzer_fingerprint() -> str:
    payload = {
        "version": SPARSE_ANALYZER_VERSION,
        "projection_fields": list(_PROJECTION_FIELDS),
        "tokenizer": "latin-alnum-plus-camel-boundaries-v1",
        "term_index": "sha256-first-u32-big-endian",
        "document_weight": "binary",
        "query_weight": "binary",
        "qdrant_modifier": SPARSE_MODIFIER,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


SPARSE_ANALYZER_FINGERPRINT = _analyzer_fingerprint()


@dataclass(frozen=True)
class DeterministicSparseVector:
    """Auditable Qdrant sparse vector plus the lexical terms that produced it."""

    indices: tuple[int, ...]
    values: tuple[float, ...]
    terms: tuple[str, ...]
    term_indices: tuple[tuple[str, int], ...]

    def to_qdrant(self) -> dict[str, object]:
        """Return the Qdrant REST sparse-vector representation."""
        return {"indices": list(self.indices), "values": list(self.values)}


def projection_sparse_terms(document: Mapping[str, object]) -> tuple[str, ...]:
    """Extract deterministic lexical terms from canonical Projection v3 fields."""
    terms: set[str] = set()
    for field_name in _PROJECTION_FIELDS:
        for value in _string_values(document.get(field_name)):
            terms.update(_analyze_value(value))
    return tuple(sorted(terms))


def intent_sparse_terms(intent: SearchIntent) -> tuple[str, ...]:
    """Extract deterministic lexical terms from the backend-neutral search intent."""
    validate_intent_translation(
        intent, channel="sparse", capabilities=RetrievalBackendCapabilities(canonical_query=True),
    )
    values: list[str] = []
    values.extend(signal.value for signal in intent.signals)
    if intent.canonical_query:
        values.append(intent.canonical_query)
    values.extend(item.value for item in intent.expansions)
    values.extend(intent.exact_terms)
    values.extend(intent.semantic_terms)
    values.extend(intent.hints)
    terms: set[str] = set()
    for value in values:
        terms.update(_analyze_value(value))
    return tuple(sorted(terms))


def projection_sparse_vector(document: Mapping[str, object]) -> DeterministicSparseVector:
    """Build the binary sparse document vector used with Qdrant's IDF modifier."""
    return sparse_vector_from_terms(projection_sparse_terms(document))


def intent_sparse_vector(intent: SearchIntent) -> DeterministicSparseVector:
    """Build the binary sparse query vector for one search intent."""
    return sparse_vector_from_terms(intent_sparse_terms(intent))


def matched_sparse_terms(
    intent: SearchIntent,
    document: Mapping[str, object],
) -> tuple[str, ...]:
    """Return only terms that are present in both query and indexed projection."""
    query = set(intent_sparse_terms(intent))
    if not query:
        return ()
    return tuple(sorted(query.intersection(projection_sparse_terms(document))))


def sparse_vector_from_terms(terms: Iterable[str]) -> DeterministicSparseVector:
    """Hash unique terms into Qdrant uint32 indices, failing on local collisions."""
    normalized = tuple(sorted({term.casefold().strip() for term in terms if term.strip()}))
    by_index: dict[int, str] = {}
    for term in normalized:
        index = _term_index(term)
        prior = by_index.get(index)
        if prior is not None and prior != term:
            raise ValueError(
                "deterministic sparse lexical hash collision: "
                f"{prior!r} and {term!r} -> {index}"
            )
        by_index[index] = term
    ordered = tuple(sorted((index, term) for index, term in by_index.items()))
    return DeterministicSparseVector(
        indices=tuple(index for index, _term in ordered),
        values=tuple(1.0 for _ in ordered),
        terms=tuple(term for _index, term in ordered),
        term_indices=tuple((term, index) for index, term in ordered),
    )


def _term_index(term: str) -> int:
    digest = hashlib.sha256(term.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], byteorder="big", signed=False)


def _analyze_value(value: str) -> tuple[str, ...]:
    raw = value.strip()
    if not raw:
        return ()
    expanded = _CAMEL_ACRONYM_BOUNDARY.sub(r"\1 \2", raw)
    expanded = _CAMEL_WORD_BOUNDARY.sub(r"\1 \2", expanded)
    terms = {item.casefold() for item in _TOKEN_PATTERN.findall(expanded) if item}
    compact = "".join(_TOKEN_PATTERN.findall(raw)).casefold()
    if compact:
        terms.add(compact)
    return tuple(sorted(terms))


def _string_values(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),)
