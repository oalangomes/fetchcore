"""Bounded exact acquisition before the existing backend search boundary."""

from __future__ import annotations

import time
from typing import Literal
from dataclasses import replace

from fetchcore.retrieval.contracts import (
    Anchor,
    ExactLookupBackend,
    KnowledgeSearchBackend,
    KnowledgeSearchError,
    KnowledgeSearchResponse,
    SearchIntent,
    SearchResult,
    SearchScope,
)
from fetchcore.retrieval.evidence import EvidenceObservation, QueryMatchEvidence
from fetchcore.retrieval.signals import QuerySignal
from fetchcore.retrieval.targeted import (
    TargetedCandidate,
    TargetedOutcome,
    matches_target,
    targetable_anchors,
    validate_target,
)
from fetchcore.retrieval.translation import validate_intent_translation
from fetchcore.telemetry import (
    RetrievalStageTelemetry,
    RetrievalTelemetry,
    elapsed_ms,
    returned_chars,
)


def _annotate(result: SearchResult, targets: tuple[TargetedCandidate, ...]) -> SearchResult:
    observations = list(result.observations)
    matches = list(result.query_matches)
    for target in targets:
        anchor = target.anchor
        observations.append(
            EvidenceObservation(
                category="path" if anchor.kind == "path" else "reference",
                subtype="file" if anchor.kind == "path" else "direct_reference",
                value=anchor.value,
                source_ref=result.source_ref,
                revision=result.revision,
                checksum=result.checksum,
                line_start=result.line_start,
                line_end=result.line_end,
                provenance="exact_source_identity",
            )
        )
        matches.append(
            QueryMatchEvidence(
                signal=QuerySignal(
                    value=anchor.value,
                    type="reference",
                    classification="path" if anchor.kind == "path" else "direct_reference",
                    priority=0,
                    weight=0.0,
                    origin=anchor.origin,
                    source="SearchIntent.anchors",
                    section="anchors",
                    required=True,
                ),
                match_type="exact_structural",
                channels=("targeted",),
                matched_values=(anchor.value,),
                source_ref=result.source_ref,
                provenance="exact_source_identity",
            )
        )
    return replace(result, observations=tuple(observations), query_matches=tuple(matches))


def search_with_targeted(
    backend: KnowledgeSearchBackend,
    intent: SearchIntent,
    scope: SearchScope,
) -> KnowledgeSearchResponse:
    """Return a unique exact source or search with the original intent."""
    started = time.monotonic()
    mode = scope.retrieval.mode if scope.retrieval else "lexical"
    if scope.retrieval is not None and scope.retrieval.targeted == "disabled":
        _validate_broad(intent, mode, backend)
        return backend.search(intent, scope, ())
    anchors = targetable_anchors(intent)
    targets = tuple(validate_target(anchor, scope) for anchor in anchors)
    if not targets:
        _validate_broad(intent, mode, backend)
        return backend.search(intent, scope, ())

    attempted = False
    lookup = None
    examined = 0
    reason: TargetedOutcome | Literal["additional_required_semantics"] = (
        "unsupported"
    )
    if len(intent.anchors) != 1 or intent.expression is not None:
        reason = "additional_required_semantics"
    elif backend.retrieval_capabilities.targeted_lookup:
        if not isinstance(backend, ExactLookupBackend):
            raise KnowledgeSearchError(
                "backend advertises exact lookup without implementing it",
                category="schema_mismatch",
            )
        attempted = True
        lookup = backend.lookup_exact(anchors[0], scope)
        if (
            lookup.status not in ("success", "found", "not_found")
            or type(lookup.metadata.get("output_truncated")) is not bool
        ):
            raise KnowledgeSearchError(
                "exact lookup returned an incomplete protocol response",
                category="response_parsing_failure",
            )
        if len(lookup.results) > 2:
            raise KnowledgeSearchError(
                "exact lookup exceeded two-record probe", category="response_parsing_failure"
            )
        for result in lookup.results:
            _validate_hit(result, targets, scope)
        examined = lookup.telemetry.candidates_examined if lookup.telemetry else len(lookup.results)
        if lookup.metadata["output_truncated"] or len(lookup.results) > 1:
            reason = "exact_ambiguous"
        elif lookup.results:
            reason = "exact_unique"
        else:
            reason = "not_found"
    targeted_ms = (time.monotonic() - started) * 1000
    early = reason == "exact_unique"
    audit: dict[str, object] = {
        "schema_version": 1,
        "targeted_attempted": attempted,
        "targeted_signal_kind": anchors[0].kind if len(anchors) == 1 else "multiple",
        "targeted_hits": len(lookup.results) if lookup else 0,
        "targeted_ms": round(targeted_ms, 3),
        "fallback_used": not early,
        "fallback_reason": None if early else reason,
        "outcome": reason,
        "lookup_candidates_examined": examined,
        "candidates_examined": examined,
    }
    if early:
        assert lookup is not None
        response = replace(lookup, results=tuple(_annotate(x, targets) for x in lookup.results))
    else:
        try:
            _validate_broad(intent, mode, backend)
            response = backend.search(intent, scope, ())
        except KnowledgeSearchError as exc:
            exc.metadata["targeted"] = audit
            raise
        broad_examined = (
            response.telemetry.candidates_examined if response.telemetry else len(response.results)
        )
    duration = elapsed_ms(started, time.monotonic())
    original = response.telemetry
    total_examined = examined if early else examined + broad_examined
    audit["candidates_examined"] = total_examined
    stage = RetrievalStageTelemetry(
        name="targeted",
        duration_ms=round(targeted_ms),
        candidates_out=len(lookup.results) if lookup else 0,
        metadata=audit,
    )
    telemetry = replace(
        original
        or RetrievalTelemetry(
            mode=mode, duration_ms=0, candidates_examined=0, candidates_returned=0, returned_chars=0
        ),
        duration_ms=duration,
        candidates_examined=total_examined,
        candidates_returned=len(response.results),
        returned_chars=returned_chars(x.content for x in response.results),
        stages=(stage,) + (original.stages if original and not early else ()),
        metadata={**(original.metadata if original else {}), "targeted": audit},
    )
    return replace(
        response,
        telemetry=telemetry,
        duration_ms=duration,
        metadata={**response.metadata, "targeted": audit},
    )


def _validate_broad(intent: SearchIntent, mode: str, backend: KnowledgeSearchBackend) -> None:
    channels = ("lexical", "dense") if mode == "hybrid" else (mode,)
    for channel in channels:
        validate_intent_translation(
            intent, channel=channel, capabilities=backend.retrieval_capabilities
        )


def _validate_hit(
    result: SearchResult, targets: tuple[TargetedCandidate, ...], scope: SearchScope
) -> None:
    # Validate repository identity even when matching by path.
    identity = validate_target(Anchor("source_ref", result.source_ref), scope)
    if identity.path != result.source:
        raise KnowledgeSearchError(
            "result path and source_ref disagree", category="response_parsing_failure"
        )
    if not all(matches_target(result, target) for target in targets):
        raise KnowledgeSearchError(
            "exact adapter returned a different source", category="response_parsing_failure"
        )
    if scope.revision is not None and result.revision != scope.revision:
        raise KnowledgeSearchError(
            "exact result revision does not match binding", category="snapshot_revision_mismatch"
        )
    if scope.knowledge_types and result.knowledge_type not in scope.knowledge_types:
        raise KnowledgeSearchError(
            "exact result is outside knowledge type scope", category="response_parsing_failure"
        )
