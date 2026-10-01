"""Deterministic scoring-query serialization for backend-neutral reranking."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from fetchcore.retrieval.signals import QuerySignal, QuerySignalType
from fetchcore.retrieval.reranking import RerankingConfigurationError


RERANK_QUERY_SERIALIZER_ID = "agentsorch.rerank-query"
RERANK_QUERY_SERIALIZER_VERSION = "1"
RERANK_QUERY_GROUPS: tuple[QuerySignalType, ...] = (
    "exact",
    "reference",
    "semantic",
    "hint",
)


@dataclass(frozen=True)
class RerankQuerySerialization:
    """Immutable canonical query text plus protocol identity and fingerprint."""

    serializer_id: str
    serializer_version: str
    text: str
    fingerprint: str
    groups: tuple[tuple[QuerySignalType, tuple[str, ...]], ...]

    def to_mapping(self) -> dict[str, object]:
        """Return auditable serialization metadata without benchmark-only fields."""
        return {
            "serializer_id": self.serializer_id,
            "serializer_version": self.serializer_version,
            "fingerprint": self.fingerprint,
            "groups": {group: list(values) for group, values in self.groups},
            "text": self.text,
        }


class RerankQuerySerializer:
    """Serialize typed retrieval intent into one stable scorer-facing query string."""

    serializer_id = RERANK_QUERY_SERIALIZER_ID
    serializer_version = RERANK_QUERY_SERIALIZER_VERSION

    def serialize(
        self,
        *,
        exact_terms: tuple[str, ...] = (),
        semantic_terms: tuple[str, ...] = (),
        hints: tuple[str, ...] = (),
        signals: tuple[QuerySignal, ...] = (),
    ) -> RerankQuerySerialization:
        """Serialize typed signals first, then legacy groups, preserving stable order."""
        grouped: dict[QuerySignalType, list[str]] = {
            group: [] for group in RERANK_QUERY_GROUPS
        }
        seen: dict[QuerySignalType, set[str]] = {
            group: set() for group in RERANK_QUERY_GROUPS
        }

        for signal in signals:
            if signal.type not in grouped:
                raise RerankingConfigurationError(
                    "rerank query contains an unsupported signal type",
                    metadata={"signal_type": signal.type},
                )
            _append_value(grouped[signal.type], seen[signal.type], signal.value)

        legacy_groups: tuple[tuple[QuerySignalType, tuple[str, ...]], ...] = (
            ("exact", exact_terms),
            ("semantic", semantic_terms),
            ("hint", hints),
        )
        for group, values in legacy_groups:
            for value in values:
                _append_value(grouped[group], seen[group], value)

        if not any(grouped.values()):
            raise RerankingConfigurationError(
                "rerank scoring query requires at least one typed or legacy query signal"
            )

        canonical_groups = tuple(
            (group, tuple(grouped[group])) for group in RERANK_QUERY_GROUPS
        )
        payload = {
            "contract": self.serializer_id,
            "version": self.serializer_version,
            "groups": {
                group: list(values) for group, values in canonical_groups
            },
        }
        text = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        )
        fingerprint = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        return RerankQuerySerialization(
            serializer_id=self.serializer_id,
            serializer_version=self.serializer_version,
            text=text,
            fingerprint=fingerprint,
            groups=canonical_groups,
        )


def _append_value(target: list[str], seen: set[str], raw_value: str) -> None:
    value = raw_value.strip()
    if not value or value in seen:
        return
    seen.add(value)
    target.append(value)
