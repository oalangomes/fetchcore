"""Small capability gate and optional intent diagnostics; no query analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fetchcore.retrieval.config import RetrievalBackendCapabilities
from fetchcore.retrieval.contracts import (
    AllOf, Anchor, AnyOf, KnowledgeSearchError, SearchExpression, SearchIntent, SearchScope,
)
from fetchcore.retrieval.signals import QuerySignal


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
            "unsupported_required": list(self.unsupported) if self.status == "rejected" else [],
            "unsupported_optional": list(self.unsupported) if self.status != "rejected" else [],
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
    """Reject unguaranteed required semantics; optional omissions are diagnostic.

    Structured capabilities apply to lexical acquisition only. The current dense
    and fusion paths cannot enforce hard constraints on their final candidates.
    """
    unsupported = []
    if intent.canonical_query and not capabilities.canonical_query:
        unsupported.append("canonical_query")
    supports_anchors = channel == "lexical" and capabilities.structured_anchors
    supports_expression = channel == "lexical" and capabilities.boolean_expressions
    if intent.anchors and not supports_anchors:
        unsupported.append("anchors")
    if intent.expression is not None and (
        not supports_expression
        or (not supports_anchors and _contains_anchor(intent.expression))
    ):
        unsupported.append("expression")
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


def _contains_anchor(expression: SearchExpression) -> bool:
    if isinstance(expression, Anchor):
        return True
    return isinstance(expression, (AllOf, AnyOf)) and any(
        _contains_anchor(child) for child in expression.children
    )


def translation_diagnostics(
    intent: SearchIntent,
    scope: SearchScope,
    *,
    consumed_signals: tuple[QuerySignal, ...] | None = None,
    consumed_hints: tuple[str, ...] | None = None,
    **representations: object,
) -> dict[str, object]:
    """Reuse response diagnostics, with full query text only on explicit opt-in."""
    if scope.settings.get("diagnostic_candidate_window") is None:
        return {}
    observations = []
    for channel, representation in representations.items():
        canonical_dense = channel == "dense" and bool(intent.canonical_query)
        consumed = () if canonical_dense else (
            intent.signals if consumed_signals is None else consumed_signals
        )
        expansions = () if channel == "dense" else intent.expansions
        hints = () if canonical_dense else (
            intent.hints if consumed_hints is None else consumed_hints
        )
        unsupported = tuple(
            f"signals[{index}]"
            for index, signal in enumerate(intent.signals) if signal not in consumed
        ) + tuple(
            name for name, supplied, used in (
                ("expansions", intent.expansions, expansions),
                ("exact_terms", intent.exact_terms, () if canonical_dense else intent.exact_terms),
                ("semantic_terms", intent.semantic_terms,
                 () if canonical_dense else intent.semantic_terms),
            ) if supplied and not used
        ) + tuple(f"hints[{index}]" for index, hint in enumerate(intent.hints) if hint not in hints)
        observation = IntentTranslation(
            channel,
            "partially_unsupported" if unsupported else "preserved",
            ("optional_semantics_not_consumed",) if unsupported else (),
            unsupported,
        ).to_mapping()
        observation.update(
            canonical_query=intent.canonical_query,
            canonical_query_consumed=bool(intent.canonical_query),
            representation=representation,
            anchors_consumed=[x.to_mapping() for x in intent.anchors],
            expression_consumed=intent.expression.to_mapping() if intent.expression else None,
            signals_supplied=[x.to_mapping() for x in intent.signals],
            signals_consumed=[x.to_mapping() for x in consumed],
            expansions=[x.to_mapping() for x in intent.expansions],
            expansions_consumed=[x.to_mapping() for x in expansions],
            hints_supplied=list(intent.hints),
            hints_consumed=list(hints),
            translation_policy="reject",
        )
        observations.append(observation)
    return {"intent_translation": observations}
