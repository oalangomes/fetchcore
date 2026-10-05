"""Small capability gate and optional intent diagnostics; no query analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fetchcore.retrieval.config import RetrievalBackendCapabilities
from fetchcore.retrieval.contracts import KnowledgeSearchError, SearchIntent, SearchScope


TranslationStatus = Literal["preserved", "broadened", "partially_unsupported", "rejected"]


@dataclass(frozen=True)
class IntentTranslation:
    """Observation about a channel translation, never retrieval confidence."""

    channel: str
    status: TranslationStatus
    reasons: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.channel, str) or not self.channel:
            raise ValueError("translation channel must be a non-empty string")
        if self.status not in ("preserved", "broadened", "partially_unsupported", "rejected"):
            raise ValueError("invalid translation status")
        for values in (self.reasons, self.unsupported):
            if not isinstance(values, tuple) or any(not isinstance(x, str) for x in values):
                raise ValueError("translation observations require string tuples")

    def to_mapping(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "status": self.status,
            "reasons": list(self.reasons),
            "unsupported": list(self.unsupported),
        }

    @classmethod
    def from_mapping(cls, data: dict[str, object]) -> IntentTranslation:
        channel, status = data.get("channel"), data.get("status")
        if not isinstance(channel, str) or not channel:
            raise ValueError("translation channel must be a non-empty string")
        if status not in ("preserved", "broadened", "partially_unsupported", "rejected"):
            raise ValueError("invalid translation status")
        sequences = []
        for key in ("reasons", "unsupported"):
            value = data.get(key, [])
            if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                raise ValueError("translation reasons/unsupported must be string lists")
            sequences.append(tuple(value))
        return cls(channel, status, *sequences)


def validate_intent_translation(
    intent: SearchIntent,
    *,
    channel: str,
    capabilities: RetrievalBackendCapabilities,
) -> None:
    """The initial translation policy is reject; no implicit lossy fallback."""
    unsupported = []
    if intent.canonical_query and not capabilities.canonical_query:
        unsupported.append("canonical_query")
    if channel != "lexical" or not capabilities.lexical_requirements:
        if intent.anchors:
            unsupported.append("anchors")
        if intent.expression is not None:
            unsupported.append("expression")
    # A single embedding cannot guarantee recall-union expansion semantics.
    if channel == "dense" and intent.expansions:
        unsupported.append("expansions")
    if unsupported:
        observation = IntentTranslation(
            channel,
            "rejected",
            ("channel_does_not_preserve_declared_semantics",),
            tuple(unsupported),
        )
        raise KnowledgeSearchError(
            "channel cannot preserve declared intent under translation policy reject",
            category="unsupported_semantics",
            metadata={
                "translation_policy": "reject",
                "intent_translation": observation.to_mapping(),
            },
        )


def translation_diagnostics(
    intent: SearchIntent,
    scope: SearchScope,
    **representations: object,
) -> dict[str, object]:
    """Reuse response diagnostics, with full query text only on explicit opt-in."""
    if scope.settings.get("diagnostic_candidate_window") is None:
        return {}
    observations = []
    for channel, representation in representations.items():
        observation = IntentTranslation(
            channel,
            "broadened" if intent.expansions else "preserved",
            ("caller_supplied_expansions",) if intent.expansions else (),
        ).to_mapping()
        observation.update(
            canonical_query=intent.canonical_query,
            representation=representation,
            anchors_consumed=[x.to_mapping() for x in intent.anchors],
            expression_consumed=intent.expression.to_mapping() if intent.expression else None,
            signals_consumed=[x.to_mapping() for x in intent.signals],
            expansions=[x.to_mapping() for x in intent.expansions],
            translation_policy="reject",
        )
        observations.append(observation)
    return {"intent_translation": observations}
