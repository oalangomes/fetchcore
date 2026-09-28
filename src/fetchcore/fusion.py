"""Backend-neutral fusion contracts and deterministic ranking kernel."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Literal


DEFAULT_RRF_K = 60
FUSION_NORMALIZATION_IDENTITY = "projection-v3-channel-scores-v1"
FusionStrategyId = Literal["rrf", "normalized-score"]


class FusionConfigurationError(ValueError):
    """Fail-closed error for invalid or unsupported fusion requests."""


@dataclass(frozen=True)
class FusionChannelSignal:
    """One candidate's participation in one retrieval channel."""

    rank: int
    normalized_score: float
    raw_score: float | None = None

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise FusionConfigurationError("fusion channel rank must be positive")
        if (
            not isfinite(self.normalized_score)
            or self.normalized_score < 0.0
            or self.normalized_score > 1.0
        ):
            raise FusionConfigurationError(
                "fusion channel normalized_score must be finite and within [0, 1]"
            )


@dataclass(frozen=True)
class FusionCandidate:
    """Backend-neutral candidate with explicit per-channel participation."""

    candidate_id: str
    source: str
    lexical: FusionChannelSignal | None = None
    dense: FusionChannelSignal | None = None

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise FusionConfigurationError("fusion candidate_id must not be empty")
        if self.lexical is None and self.dense is None:
            raise FusionConfigurationError(
                "fusion candidate must participate in at least one channel"
            )


@dataclass(frozen=True)
class FusionInput:
    """Bounded channel candidate set supplied to a fusion strategy."""

    candidates: tuple[FusionCandidate, ...]
    max_results: int
    normalization_identity: str = FUSION_NORMALIZATION_IDENTITY

    def __post_init__(self) -> None:
        if self.max_results < 1:
            raise FusionConfigurationError("fusion max_results must be positive")
        if not self.normalization_identity:
            raise FusionConfigurationError(
                "fusion normalization_identity must not be empty"
            )
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise FusionConfigurationError("fusion candidate_id values must be unique")

    @property
    def fingerprint(self) -> str:
        """Return a canonical fingerprint independent from backend iteration order."""

        return _fingerprint(self._fingerprint_mapping())

    def _fingerprint_mapping(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "max_results": self.max_results,
            "normalization_identity": self.normalization_identity,
            "candidates": [
                _candidate_fingerprint_mapping(candidate)
                for candidate in sorted(
                    self.candidates,
                    key=lambda item: item.candidate_id,
                )
            ],
        }

    def to_mapping(self, *, include_raw_scores: bool = True) -> dict[str, object]:
        """Serialize this bounded input for audit and deterministic replay."""

        return {
            "schema_version": 1,
            "max_results": self.max_results,
            "normalization_identity": self.normalization_identity,
            "candidates": [
                _candidate_mapping(
                    candidate,
                    include_raw_scores=include_raw_scores,
                )
                for candidate in sorted(
                    self.candidates,
                    key=lambda item: item.candidate_id,
                )
            ],
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> FusionInput:
        """Restore and verify a captured fusion input."""

        raw_candidates = value.get("candidates")
        if not isinstance(raw_candidates, list):
            raise FusionConfigurationError("fusion input candidates must be a list")
        candidates = tuple(
            _candidate_from_mapping(item)
            for item in raw_candidates
            if isinstance(item, Mapping)
        )
        if len(candidates) != len(raw_candidates):
            raise FusionConfigurationError("fusion input candidate entry is invalid")
        raw_max_results = value.get("max_results")
        if isinstance(raw_max_results, bool) or not isinstance(raw_max_results, int):
            raise FusionConfigurationError("fusion input max_results must be an integer")
        fusion_input = cls(
            candidates=candidates,
            max_results=raw_max_results,
            normalization_identity=str(value.get("normalization_identity", "")),
        )
        captured = value.get("fingerprint")
        if captured is not None and str(captured) != fusion_input.fingerprint:
            raise FusionConfigurationError(
                "fusion input fingerprint does not match captured content"
            )
        return fusion_input


@dataclass(frozen=True)
class FusionRequest:
    """Strategy selection over one immutable fusion input."""

    input: FusionInput
    strategy: FusionStrategyId = "rrf"
    parameters: Mapping[str, object] | None = None


@dataclass(frozen=True)
class FusedCandidate:
    """Deterministic ranking result preserving channel provenance."""

    candidate_id: str
    source: str
    lexical_rank: int | None
    dense_rank: int | None
    lexical_normalized_score: float | None
    dense_normalized_score: float | None
    lexical_raw_score: float | None
    dense_raw_score: float | None
    fused_score: float
    normalized_score: float
    fused_rank: int
    lexical_contribution: float
    dense_contribution: float
    returned: bool


@dataclass(frozen=True)
class FusionResult:
    """Pure fusion output tied to its immutable input identity."""

    candidates: tuple[FusedCandidate, ...]
    strategy: FusionStrategyId
    parameters: Mapping[str, object]
    input_fingerprint: str
    normalization_identity: str

    @property
    def output_fingerprint(self) -> str:
        """Fingerprint deterministic strategy output for replay validation."""

        return _fingerprint(
            {
                "schema_version": 1,
                "strategy": self.strategy,
                "parameters": dict(self.parameters),
                "input_fingerprint": self.input_fingerprint,
                "normalization_identity": self.normalization_identity,
                "candidates": [
                    {
                        "candidate_id": item.candidate_id,
                        "source": item.source,
                        "lexical_rank": item.lexical_rank,
                        "dense_rank": item.dense_rank,
                        "fused_score": item.fused_score,
                        "normalized_score": item.normalized_score,
                        "fused_rank": item.fused_rank,
                        "returned": item.returned,
                    }
                    for item in self.candidates
                ],
            }
        )

    def to_mapping(self) -> dict[str, object]:
        """Serialize deterministic fusion output for audit/replay artifacts."""

        return {
            "schema_version": 1,
            "strategy": self.strategy,
            "parameters": dict(self.parameters),
            "input_fingerprint": self.input_fingerprint,
            "normalization_identity": self.normalization_identity,
            "output_fingerprint": self.output_fingerprint,
            "candidates": [
                {
                    "candidate_id": item.candidate_id,
                    "source": item.source,
                    "lexical_rank": item.lexical_rank,
                    "dense_rank": item.dense_rank,
                    "lexical_normalized_score": item.lexical_normalized_score,
                    "dense_normalized_score": item.dense_normalized_score,
                    "lexical_raw_score": item.lexical_raw_score,
                    "dense_raw_score": item.dense_raw_score,
                    "fused_score": item.fused_score,
                    "normalized_score": item.normalized_score,
                    "fused_rank": item.fused_rank,
                    "lexical_contribution": item.lexical_contribution,
                    "dense_contribution": item.dense_contribution,
                    "returned": item.returned,
                }
                for item in self.candidates
            ],
        }


class FusionKernel:
    """Pure backend-neutral fusion kernel with deterministic semantics."""

    def fuse(self, request: FusionRequest) -> FusionResult:
        parameters = dict(request.parameters or {})
        ranked: list[tuple[float, int, str, FusionCandidate, float, float]] = []

        if request.strategy == "rrf":
            unknown = sorted(set(parameters) - {"k"})
            if unknown:
                raise FusionConfigurationError(
                    f"unsupported RRF parameters: {unknown}"
                )
            raw_k = parameters.get("k", DEFAULT_RRF_K)
            if isinstance(raw_k, bool) or not isinstance(raw_k, int) or raw_k < 1:
                raise FusionConfigurationError("RRF k must be a positive integer")
            k = raw_k
            result_parameters: Mapping[str, object] = {"k": k}
            maximum_score = 2.0 / (k + 1)

            for candidate in request.input.candidates:
                lexical_contribution = (
                    0.0
                    if candidate.lexical is None
                    else 1.0 / (k + candidate.lexical.rank)
                )
                dense_contribution = (
                    0.0
                    if candidate.dense is None
                    else 1.0 / (k + candidate.dense.rank)
                )
                score = lexical_contribution + dense_contribution
                ranked.append(
                    _ranked_candidate(
                        candidate,
                        score=score,
                        lexical_contribution=lexical_contribution,
                        dense_contribution=dense_contribution,
                    )
                )
        elif request.strategy == "normalized-score":
            if parameters:
                raise FusionConfigurationError(
                    "normalized-score does not accept strategy parameters"
                )
            result_parameters = {}
            maximum_score = 1.0

            for candidate in request.input.candidates:
                lexical_contribution = (
                    0.0
                    if candidate.lexical is None
                    else 0.5 * candidate.lexical.normalized_score
                )
                dense_contribution = (
                    0.0
                    if candidate.dense is None
                    else 0.5 * candidate.dense.normalized_score
                )
                score = lexical_contribution + dense_contribution
                ranked.append(
                    _ranked_candidate(
                        candidate,
                        score=score,
                        lexical_contribution=lexical_contribution,
                        dense_contribution=dense_contribution,
                    )
                )
        else:
            raise FusionConfigurationError(
                f"unsupported fusion strategy: {request.strategy!r}"
            )

        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        fused: list[FusedCandidate] = []
        for fused_rank, (
            score,
            _best_channel_rank,
            _candidate_id,
            candidate,
            lexical_contribution,
            dense_contribution,
        ) in enumerate(ranked, start=1):
            fused.append(
                FusedCandidate(
                    candidate_id=candidate.candidate_id,
                    source=candidate.source,
                    lexical_rank=(
                        None if candidate.lexical is None else candidate.lexical.rank
                    ),
                    dense_rank=(
                        None if candidate.dense is None else candidate.dense.rank
                    ),
                    lexical_normalized_score=(
                        None
                        if candidate.lexical is None
                        else candidate.lexical.normalized_score
                    ),
                    dense_normalized_score=(
                        None
                        if candidate.dense is None
                        else candidate.dense.normalized_score
                    ),
                    lexical_raw_score=(
                        None if candidate.lexical is None else candidate.lexical.raw_score
                    ),
                    dense_raw_score=(
                        None if candidate.dense is None else candidate.dense.raw_score
                    ),
                    fused_score=score,
                    normalized_score=_clamp(score / maximum_score),
                    fused_rank=fused_rank,
                    lexical_contribution=lexical_contribution,
                    dense_contribution=dense_contribution,
                    returned=fused_rank <= request.input.max_results,
                )
            )

        return FusionResult(
            candidates=tuple(fused),
            strategy=request.strategy,
            parameters=result_parameters,
            input_fingerprint=request.input.fingerprint,
            normalization_identity=request.input.normalization_identity,
        )


def _ranked_candidate(
    candidate: FusionCandidate,
    *,
    score: float,
    lexical_contribution: float,
    dense_contribution: float,
) -> tuple[float, int, str, FusionCandidate, float, float]:
    best_channel_rank = min(
        candidate.lexical.rank if candidate.lexical is not None else 10**9,
        candidate.dense.rank if candidate.dense is not None else 10**9,
    )
    return (
        score,
        best_channel_rank,
        candidate.candidate_id,
        candidate,
        lexical_contribution,
        dense_contribution,
    )


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _candidate_mapping(
    candidate: FusionCandidate,
    *,
    include_raw_scores: bool,
) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "source": candidate.source,
        "lexical": _channel_mapping(
            candidate.lexical,
            include_raw_score=include_raw_scores,
        ),
        "dense": _channel_mapping(
            candidate.dense,
            include_raw_score=include_raw_scores,
        ),
    }


def _channel_mapping(
    signal: FusionChannelSignal | None,
    *,
    include_raw_score: bool,
) -> dict[str, object] | None:
    if signal is None:
        return None
    payload: dict[str, object] = {
        "rank": signal.rank,
        "normalized_score": signal.normalized_score,
    }
    if include_raw_score:
        payload["raw_score"] = signal.raw_score
    return payload


def _candidate_from_mapping(value: Mapping[str, object]) -> FusionCandidate:
    return FusionCandidate(
        candidate_id=str(value.get("candidate_id", "")),
        source=str(value.get("source", "")),
        lexical=_channel_from_mapping(value.get("lexical")),
        dense=_channel_from_mapping(value.get("dense")),
    )


def _channel_from_mapping(value: object) -> FusionChannelSignal | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise FusionConfigurationError("fusion channel signal must be an object or null")
    raw_rank = value.get("rank")
    raw_score = value.get("normalized_score")
    if isinstance(raw_rank, bool) or not isinstance(raw_rank, int):
        raise FusionConfigurationError("fusion channel rank must be an integer")
    if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
        raise FusionConfigurationError(
            "fusion channel normalized_score must be numeric"
        )
    raw_raw_score = value.get("raw_score")
    if raw_raw_score is not None and (
        isinstance(raw_raw_score, bool)
        or not isinstance(raw_raw_score, (int, float))
    ):
        raise FusionConfigurationError("fusion channel raw_score must be numeric or null")
    return FusionChannelSignal(
        rank=raw_rank,
        normalized_score=float(raw_score),
        raw_score=None if raw_raw_score is None else float(raw_raw_score),
    )


def _candidate_fingerprint_mapping(candidate: FusionCandidate) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "source": candidate.source,
        "lexical": _channel_fingerprint_mapping(candidate.lexical),
        "dense": _channel_fingerprint_mapping(candidate.dense),
    }


def _channel_fingerprint_mapping(
    signal: FusionChannelSignal | None,
) -> dict[str, object] | None:
    if signal is None:
        return None
    # raw_score is deliberately diagnostic-only and does not affect replay identity.
    return {
        "rank": signal.rank,
        "normalized_score": signal.normalized_score,
    }


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
