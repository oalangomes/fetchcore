"""Bounded hybrid admission and opt-in acquisition capture, independent of vendors."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from fetchcore.retrieval.contracts import SearchResult
from fetchcore.retrieval.fusion import FusionCandidate, FusionConfigurationError, FusionInput


def allocate_fusion_input(
    *,
    lexical: Sequence[FusionCandidate],
    dense: Sequence[FusionCandidate],
    lexical_window: int,
    dense_window: int,
    max_results: int,
) -> FusionInput:
    """Admit ordered prefixes without renumbering, then merge channel identities.

    Windows count eligible candidates, not original rank positions. In particular,
    sparse eligibility can leave rank gaps. Last occurrences within a channel
    retain the existing adapters' dictionary deduplication behavior.
    """
    for value in (lexical_window, dense_window, max_results):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise FusionConfigurationError(
                "fusion windows and output limit must be positive integers"
            )
    left = {item.candidate_id: item for item in lexical[:lexical_window]}
    right = {item.candidate_id: item for item in dense[:dense_window]}
    if any(item.lexical is None for item in left.values()):
        raise FusionConfigurationError("lexical admission requires lexical channel signals")
    if any(item.dense is None for item in right.values()):
        raise FusionConfigurationError("dense admission requires dense channel signals")
    return FusionInput(
        candidates=tuple(
            FusionCandidate(
                candidate_id=identity,
                source=(left[identity] if identity in left else right[identity]).source,
                lexical=left[identity].lexical if identity in left else None,
                dense=right[identity].dense if identity in right else None,
            )
            for identity in sorted(left.keys() | right.keys())
        ),
        max_results=max_results,
    )


@dataclass(frozen=True)
class HybridFusionCapture:
    """Full bounded acquisition before admission, with source-derived evidence.

    Capture is an injected observer, never part of a normal serving response.
    Evidence can contain private source; callers own storage/publication policy.
    Scores on evidence records are acquisition observations, not fused ranks.
    """

    input: FusionInput
    evidence: tuple[SearchResult, ...]
    lexical_acquired: int
    dense_acquired: int


FusionObserver = Callable[[HybridFusionCapture], None]
