"""Post-acquisition reranking binding and application over FetchCore search results."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from fetchcore.retrieval.contracts import SearchResult
from fetchcore.retrieval.reranking import (
    RerankCandidate,
    RerankInput,
    RerankResult,
    RerankScorer,
    RerankingConfigurationError,
    RerankingExecutionError,
    RerankingKernel,
)
from fetchcore.retrieval import RerankingConfig
from fetchcore.telemetry import elapsed_ms


class RerankScorerUnavailableError(LookupError):
    """Raised when an explicitly configured scorer binding is not registered."""

    def __init__(self, scorer_id: str, scorer_version: str) -> None:
        """Initialize the error with the exact missing scorer identity."""
        super().__init__(
            f"rerank scorer {scorer_id!r} version {scorer_version!r} is unavailable"
        )
        self.category = "unavailable"
        self.metadata: dict[str, object] = {
            "capability": "reranking",
            "owner": "post_acquisition",
            "scorer_id": scorer_id,
            "scorer_version": scorer_version,
        }


class RerankScorerExperimentalError(RerankScorerUnavailableError):
    """Raised when product runtime tries to consume an unpromoted scorer binding."""

    def __init__(self, scorer_id: str, scorer_version: str) -> None:
        """Initialize an explicit fail-closed experimental-capability error."""
        LookupError.__init__(
            self,
            f"rerank scorer {scorer_id!r} version {scorer_version!r} is experimental and not promoted for product runtime",
        )
        self.category = "unsupported_capability"
        self.metadata = {
            "capability": "reranking",
            "owner": "post_acquisition",
            "scorer_id": scorer_id,
            "scorer_version": scorer_version,
            "state": "experimental_not_promoted",
        }


class RerankingApplicationError(RuntimeError):
    """Provider-facing reranking failure that preserves stable error evidence."""

    def __init__(
        self,
        message: str,
        *,
        category: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Initialize the error with a stable category and metadata."""
        super().__init__(message)
        self.category = category
        self.metadata = dict(metadata or {})


@dataclass(frozen=True)
class RerankingApplication:
    """Reordered candidate tuple plus auditable post-acquisition evidence."""

    candidates: tuple[SearchResult, ...]
    result: RerankResult | None
    metadata: dict[str, object]


_RERANK_SCORERS: dict[tuple[str, str], RerankScorer] = {}


def register_rerank_scorer(
    scorer: RerankScorer,
    *,
    replace: bool = False,
) -> None:
    """Register one scorer implementation by exact backend-neutral identity/version."""
    key = (scorer.scorer_id, scorer.scorer_version)
    if not scorer.scorer_id or not scorer.scorer_version:
        raise ValueError("rerank scorer identity and version must not be empty")
    if not replace and key in _RERANK_SCORERS:
        raise ValueError(
            "rerank scorer "
            f"{scorer.scorer_id!r} version {scorer.scorer_version!r} already registered"
        )
    _RERANK_SCORERS[key] = scorer


def get_rerank_scorer(
    scorer_id: str,
    scorer_version: str,
    *,
    allow_experimental: bool = False,
) -> RerankScorer:
    """Resolve one scorer without silently promoting experimental product behavior.

    Product callers use the default and fail closed while #120 remains an
    experiment. Controlled benchmark/replay code may opt in explicitly after
    freezing its protocol by passing ``allow_experimental=True``.
    """
    if not allow_experimental:
        raise RerankScorerExperimentalError(scorer_id, scorer_version)
    try:
        return _RERANK_SCORERS[(scorer_id, scorer_version)]
    except KeyError as exc:
        raise RerankScorerUnavailableError(scorer_id, scorer_version) from exc


def apply_reranking(
    candidates: tuple[SearchResult, ...],
    *,
    scoring_query: str,
    backend_id: str,
    config: RerankingConfig,
    scorer: RerankScorer,
    timer: Callable[[], float] = time.monotonic,
) -> RerankingApplication:
    """Rerank only the already bounded candidates while preserving their original scores."""
    started = timer()
    if not config.enabled:
        return RerankingApplication(
            candidates=candidates,
            result=None,
            metadata={
                "enabled": False,
                "applied": False,
                "duration_ms": elapsed_ms(started, timer()),
            },
        )
    if not candidates:
        return RerankingApplication(
            candidates=candidates,
            result=None,
            metadata={
                "enabled": True,
                "applied": False,
                "reason": "empty_candidate_set",
                "scorer_id": scorer.scorer_id,
                "scorer_version": scorer.scorer_version,
                "duration_ms": elapsed_ms(started, timer()),
            },
        )

    scorer_id = config.scorer_id
    scorer_version = config.scorer_version
    configured_window = config.window_size
    if scorer_id is None or scorer_version is None or configured_window is None:
        raise RerankingApplicationError(
            "enabled reranking requires a fully resolved scorer configuration",
            category="invalid_input",
            metadata={"capability": "reranking", "owner": "post_acquisition"},
        )

    effective_window = min(configured_window, len(candidates))
    rerank_input = _build_rerank_input(
        candidates,
        scoring_query=scoring_query,
        backend_id=backend_id,
        scorer_id=scorer_id,
        scorer_version=scorer_version,
        window_size=effective_window,
        parameters=config.parameters,
    )
    try:
        result = RerankingKernel().rerank(rerank_input, scorer)
    except (RerankingConfigurationError, RerankingExecutionError) as exc:
        raise RerankingApplicationError(
            str(exc),
            category=exc.category,
            metadata={
                "capability": "reranking",
                "owner": "post_acquisition",
                **exc.metadata,
            },
        ) from exc

    by_id = {candidate.id: candidate for candidate in candidates}
    reordered = tuple(by_id[item.candidate_id] for item in result.candidates)
    duration_ms = elapsed_ms(started, timer())
    return RerankingApplication(
        candidates=reordered,
        result=result,
        metadata={
            "enabled": True,
            "applied": True,
            "scorer_id": result.scorer_id,
            "scorer_version": result.scorer_version,
            "configured_window_size": configured_window,
            "effective_window_size": result.window_size,
            "candidate_set_fingerprint": result.candidate_set_fingerprint,
            "input_fingerprint": result.input_fingerprint,
            "output_fingerprint": result.output_fingerprint,
            "duration_ms": duration_ms,
            "ordering": [
                {
                    "candidate_id": item.candidate_id,
                    "original_rank": item.original_rank,
                    "reranked_rank": item.reranked_rank,
                    "rerank_score": item.rerank_score,
                    "in_rerank_window": item.in_rerank_window,
                }
                for item in result.candidates
            ],
        },
    )


def _build_rerank_input(
    candidates: tuple[SearchResult, ...],
    *,
    scoring_query: str,
    backend_id: str,
    scorer_id: str,
    scorer_version: str,
    window_size: int,
    parameters: tuple[tuple[str, str | int | float | bool], ...],
) -> RerankInput:
    return RerankInput(
        candidates=tuple(
            RerankCandidate(
                candidate_id=item.id,
                source=item.source_identity,
                original_rank=rank,
                original_score=item.score,
                original_score_provenance=_score_provenance(item, backend_id),
                scoring_payload=item.content,
                revision=item.revision,
                checksum=item.checksum,
            )
            for rank, item in enumerate(candidates, start=1)
        ),
        scoring_query=scoring_query,
        window_size=window_size,
        scorer_id=scorer_id,
        scorer_version=scorer_version,
        scorer_parameters=parameters,
    )


def _score_provenance(candidate: SearchResult, backend_id: str) -> str:
    raw = candidate.metadata.get("score_provenance")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return f"candidate_acquisition:{backend_id}"
