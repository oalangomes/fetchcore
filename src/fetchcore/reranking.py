"""Backend-neutral post-acquisition reranking contracts and deterministic kernel."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Literal, Protocol


RERANK_INPUT_SCHEMA_VERSION = 1
RERANK_RESULT_SCHEMA_VERSION = 1
RerankParameterValue = str | int | float | bool
RerankingErrorCategory = Literal[
    "invalid_input",
    "scorer_mismatch",
    "scorer_failed",
    "invalid_scorer_output",
]


class RerankingConfigurationError(ValueError):
    """Fail-closed error for invalid reranking configuration or replay input."""

    def __init__(
        self,
        message: str,
        *,
        category: RerankingErrorCategory = "invalid_input",
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Initialize a configuration error with stable category and metadata."""
        super().__init__(message)
        self.category = category
        self.metadata = dict(metadata or {})


class RerankingExecutionError(RuntimeError):
    """Fail-closed error for scorer execution or scorer-output violations."""

    def __init__(
        self,
        message: str,
        *,
        category: RerankingErrorCategory,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Initialize an execution error with stable category and metadata."""
        super().__init__(message)
        self.category = category
        self.metadata = dict(metadata or {})


@dataclass(frozen=True)
class RerankCandidate:
    """One already-acquired candidate available to the post-acquisition reranker."""

    candidate_id: str
    source: str
    original_rank: int
    original_score: float
    original_score_provenance: str
    scoring_payload: str
    revision: str | None = None
    checksum: str | None = None

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise RerankingConfigurationError("rerank candidate_id must not be empty")
        if not self.source:
            raise RerankingConfigurationError("rerank candidate source must not be empty")
        if self.original_rank < 1:
            raise RerankingConfigurationError("rerank original_rank must be positive")
        if not isfinite(self.original_score):
            raise RerankingConfigurationError("rerank original_score must be finite")
        if not self.original_score_provenance:
            raise RerankingConfigurationError(
                "rerank original_score_provenance must not be empty"
            )
        if not self.scoring_payload:
            raise RerankingConfigurationError("rerank scoring_payload must not be empty")
        if self.revision is not None and not self.revision:
            raise RerankingConfigurationError("rerank revision must be non-empty or null")
        if self.checksum is not None and not self.checksum:
            raise RerankingConfigurationError("rerank checksum must be non-empty or null")

    def to_mapping(self) -> dict[str, object]:
        """Serialize the candidate without adding authority or ground-truth fields."""
        return {
            "candidate_id": self.candidate_id,
            "source": self.source,
            "original_rank": self.original_rank,
            "original_score": self.original_score,
            "original_score_provenance": self.original_score_provenance,
            "scoring_payload": self.scoring_payload,
            "revision": self.revision,
            "checksum": self.checksum,
        }


@dataclass(frozen=True)
class RerankInput:
    """Immutable bounded semantic input used for deterministic reranking and replay."""

    candidates: tuple[RerankCandidate, ...]
    scoring_query: str
    window_size: int
    scorer_id: str
    scorer_version: str
    scorer_parameters: tuple[tuple[str, RerankParameterValue], ...] = ()

    def __post_init__(self) -> None:
        if not self.candidates:
            raise RerankingConfigurationError("rerank input candidates must not be empty")
        if not self.scoring_query:
            raise RerankingConfigurationError("rerank scoring_query must not be empty")
        if self.window_size < 1 or self.window_size > len(self.candidates):
            raise RerankingConfigurationError(
                "rerank window_size must be within the captured candidate set"
            )
        if not self.scorer_id:
            raise RerankingConfigurationError("rerank scorer_id must not be empty")
        if not self.scorer_version:
            raise RerankingConfigurationError("rerank scorer_version must not be empty")

        ids = tuple(candidate.candidate_id for candidate in self.candidates)
        if len(ids) != len(set(ids)):
            raise RerankingConfigurationError("rerank candidate_id values must be unique")

        ranks = tuple(candidate.original_rank for candidate in self.candidates)
        expected_ranks = tuple(range(1, len(self.candidates) + 1))
        if ranks != expected_ranks:
            raise RerankingConfigurationError(
                "rerank candidates must preserve contiguous baseline ordering"
            )

        parameter_names = tuple(name for name, _value in self.scorer_parameters)
        if any(not name for name in parameter_names):
            raise RerankingConfigurationError("rerank scorer parameter names must not be empty")
        if len(parameter_names) != len(set(parameter_names)):
            raise RerankingConfigurationError("rerank scorer parameter names must be unique")
        for _name, value in self.scorer_parameters:
            if isinstance(value, float) and not isfinite(value):
                raise RerankingConfigurationError(
                    "rerank scorer float parameters must be finite"
                )

    @property
    def candidate_set_fingerprint(self) -> str:
        """Return identity for the captured candidate set before any reranker choice."""
        return _fingerprint(
            {
                "schema_version": RERANK_INPUT_SCHEMA_VERSION,
                "candidates": [candidate.to_mapping() for candidate in self.candidates],
            }
        )

    @property
    def fingerprint(self) -> str:
        """Return the canonical semantic identity of the scoring input and configuration."""
        return _fingerprint(self._fingerprint_mapping())

    def _fingerprint_mapping(self) -> dict[str, object]:
        return {
            "schema_version": RERANK_INPUT_SCHEMA_VERSION,
            "candidate_set_fingerprint": self.candidate_set_fingerprint,
            "scoring_query": self.scoring_query,
            "window_size": self.window_size,
            "scorer_id": self.scorer_id,
            "scorer_version": self.scorer_version,
            "scorer_parameters": {
                name: value for name, value in sorted(self.scorer_parameters)
            },
        }

    def to_mapping(self) -> dict[str, object]:
        """Serialize semantic input for capture, audit and deterministic replay."""
        return {
            "schema_version": RERANK_INPUT_SCHEMA_VERSION,
            "candidates": [candidate.to_mapping() for candidate in self.candidates],
            "scoring_query": self.scoring_query,
            "window_size": self.window_size,
            "scorer_id": self.scorer_id,
            "scorer_version": self.scorer_version,
            "scorer_parameters": {
                name: value for name, value in sorted(self.scorer_parameters)
            },
            "candidate_set_fingerprint": self.candidate_set_fingerprint,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> RerankInput:
        """Restore a captured input and reject drift, unknown fields or GT leakage."""
        allowed = {
            "schema_version",
            "candidates",
            "scoring_query",
            "window_size",
            "scorer_id",
            "scorer_version",
            "scorer_parameters",
            "candidate_set_fingerprint",
            "fingerprint",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise RerankingConfigurationError(
                "rerank input contains unsupported fields",
                metadata={"fields": unknown},
            )

        schema_version = _require_int(value.get("schema_version"), "schema_version")
        if schema_version != RERANK_INPUT_SCHEMA_VERSION:
            raise RerankingConfigurationError(
                "unsupported rerank input schema_version",
                metadata={"schema_version": schema_version},
            )

        raw_candidates = value.get("candidates")
        if not isinstance(raw_candidates, list):
            raise RerankingConfigurationError("rerank input candidates must be a list")
        candidates = tuple(_candidate_from_mapping(item) for item in raw_candidates)

        raw_parameters = value.get("scorer_parameters", {})
        if not isinstance(raw_parameters, Mapping):
            raise RerankingConfigurationError("rerank scorer_parameters must be an object")
        parameters = tuple(
            (str(name), _parameter_value(parameter, str(name)))
            for name, parameter in sorted(raw_parameters.items(), key=lambda item: str(item[0]))
        )

        restored = cls(
            candidates=candidates,
            scoring_query=_require_string(value.get("scoring_query"), "scoring_query"),
            window_size=_require_int(value.get("window_size"), "window_size"),
            scorer_id=_require_string(value.get("scorer_id"), "scorer_id"),
            scorer_version=_require_string(value.get("scorer_version"), "scorer_version"),
            scorer_parameters=parameters,
        )
        captured_candidate_set = value.get("candidate_set_fingerprint")
        if (
            captured_candidate_set is not None
            and str(captured_candidate_set) != restored.candidate_set_fingerprint
        ):
            raise RerankingConfigurationError(
                "rerank candidate-set fingerprint does not match captured content"
            )
        captured = value.get("fingerprint")
        if captured is not None and str(captured) != restored.fingerprint:
            raise RerankingConfigurationError(
                "rerank input fingerprint does not match captured content"
            )
        return restored


@dataclass(frozen=True)
class RerankScore:
    """One scorer-produced ordering signal for an existing candidate identity."""

    candidate_id: str
    score: float

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise RerankingExecutionError(
                "rerank score candidate_id must not be empty",
                category="invalid_scorer_output",
            )
        if not isfinite(self.score):
            raise RerankingExecutionError(
                "rerank score must be finite",
                category="invalid_scorer_output",
                metadata={"candidate_id": self.candidate_id},
            )


class RerankScorer(Protocol):
    """Explicit scoring dependency that cannot acquire or admit candidates itself."""

    scorer_id: str
    scorer_version: str

    def score(
        self,
        *,
        query: str,
        candidates: tuple[RerankCandidate, ...],
        parameters: tuple[tuple[str, RerankParameterValue], ...],
    ) -> tuple[RerankScore, ...]:
        """Score only the supplied bounded candidates for the supplied query."""
        ...


@dataclass(frozen=True)
class RerankedCandidate:
    """Auditable reranking result preserving original candidate evidence and ordering."""

    candidate_id: str
    source: str
    original_rank: int
    reranked_rank: int
    original_score: float
    original_score_provenance: str
    rerank_score: float | None
    in_rerank_window: bool
    revision: str | None = None
    checksum: str | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize the reranked candidate with original and reranked rank evidence."""
        return {
            "candidate_id": self.candidate_id,
            "source": self.source,
            "original_rank": self.original_rank,
            "reranked_rank": self.reranked_rank,
            "original_score": self.original_score,
            "original_score_provenance": self.original_score_provenance,
            "rerank_score": self.rerank_score,
            "in_rerank_window": self.in_rerank_window,
            "revision": self.revision,
            "checksum": self.checksum,
        }


@dataclass(frozen=True)
class RerankResult:
    """Deterministic post-acquisition ordering tied to one immutable rerank input."""

    candidates: tuple[RerankedCandidate, ...]
    scorer_id: str
    scorer_version: str
    window_size: int
    input_fingerprint: str
    candidate_set_fingerprint: str

    @property
    def output_fingerprint(self) -> str:
        """Return deterministic identity of the resulting scores and ordering."""
        return _fingerprint(
            {
                "schema_version": RERANK_RESULT_SCHEMA_VERSION,
                "scorer_id": self.scorer_id,
                "scorer_version": self.scorer_version,
                "window_size": self.window_size,
                "input_fingerprint": self.input_fingerprint,
                "candidate_set_fingerprint": self.candidate_set_fingerprint,
                "candidates": [candidate.to_mapping() for candidate in self.candidates],
            }
        )

    def to_mapping(self) -> dict[str, object]:
        """Serialize reranking output for audit and replay comparison."""
        return {
            "schema_version": RERANK_RESULT_SCHEMA_VERSION,
            "scorer_id": self.scorer_id,
            "scorer_version": self.scorer_version,
            "window_size": self.window_size,
            "input_fingerprint": self.input_fingerprint,
            "candidate_set_fingerprint": self.candidate_set_fingerprint,
            "output_fingerprint": self.output_fingerprint,
            "candidates": [candidate.to_mapping() for candidate in self.candidates],
        }


class RerankingKernel:
    """Pure bounded reranking kernel with explicit scorer and fail-closed semantics."""

    def rerank(self, rerank_input: RerankInput, scorer: RerankScorer) -> RerankResult:
        """Score and reorder only the captured rerank window without acquiring candidates."""
        if scorer.scorer_id != rerank_input.scorer_id:
            raise RerankingConfigurationError(
                "configured rerank scorer identity does not match runtime scorer",
                category="scorer_mismatch",
                metadata={
                    "configured": rerank_input.scorer_id,
                    "runtime": scorer.scorer_id,
                },
            )
        if scorer.scorer_version != rerank_input.scorer_version:
            raise RerankingConfigurationError(
                "configured rerank scorer version does not match runtime scorer",
                category="scorer_mismatch",
                metadata={
                    "configured": rerank_input.scorer_version,
                    "runtime": scorer.scorer_version,
                },
            )

        window = rerank_input.candidates[: rerank_input.window_size]
        try:
            scores = scorer.score(
                query=rerank_input.scoring_query,
                candidates=window,
                parameters=rerank_input.scorer_parameters,
            )
        except RerankingExecutionError:
            raise
        except Exception as exc:
            raise RerankingExecutionError(
                "rerank scorer execution failed",
                category="scorer_failed",
                metadata={
                    "scorer_id": rerank_input.scorer_id,
                    "scorer_version": rerank_input.scorer_version,
                },
            ) from exc

        score_by_id = _validate_scorer_output(window, scores)
        ranked_window = sorted(
            window,
            key=lambda candidate: (
                -score_by_id[candidate.candidate_id],
                candidate.original_rank,
                candidate.candidate_id,
            ),
        )
        ordered = (*ranked_window, *rerank_input.candidates[rerank_input.window_size :])
        reranked_candidates = tuple(
            RerankedCandidate(
                candidate_id=candidate.candidate_id,
                source=candidate.source,
                original_rank=candidate.original_rank,
                reranked_rank=reranked_rank,
                original_score=candidate.original_score,
                original_score_provenance=candidate.original_score_provenance,
                rerank_score=score_by_id.get(candidate.candidate_id),
                in_rerank_window=candidate.original_rank <= rerank_input.window_size,
                revision=candidate.revision,
                checksum=candidate.checksum,
            )
            for reranked_rank, candidate in enumerate(ordered, start=1)
        )
        return RerankResult(
            candidates=reranked_candidates,
            scorer_id=rerank_input.scorer_id,
            scorer_version=rerank_input.scorer_version,
            window_size=rerank_input.window_size,
            input_fingerprint=rerank_input.fingerprint,
            candidate_set_fingerprint=rerank_input.candidate_set_fingerprint,
        )


def _validate_scorer_output(
    window: tuple[RerankCandidate, ...],
    scores: tuple[RerankScore, ...],
) -> dict[str, float]:
    expected_ids = tuple(candidate.candidate_id for candidate in window)
    actual_ids = tuple(score.candidate_id for score in scores)
    if len(actual_ids) != len(set(actual_ids)):
        raise RerankingExecutionError(
            "rerank scorer returned duplicate candidate identities",
            category="invalid_scorer_output",
        )
    if set(actual_ids) != set(expected_ids):
        raise RerankingExecutionError(
            "rerank scorer must return exactly one score for each bounded candidate",
            category="invalid_scorer_output",
            metadata={
                "expected": sorted(expected_ids),
                "actual": sorted(actual_ids),
            },
        )
    return {score.candidate_id: score.score for score in scores}


def _candidate_from_mapping(value: object) -> RerankCandidate:
    if not isinstance(value, Mapping):
        raise RerankingConfigurationError("rerank candidate entry must be an object")
    allowed = {
        "candidate_id",
        "source",
        "original_rank",
        "original_score",
        "original_score_provenance",
        "scoring_payload",
        "revision",
        "checksum",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RerankingConfigurationError(
            "rerank candidate contains unsupported fields",
            metadata={"fields": unknown},
        )
    return RerankCandidate(
        candidate_id=_require_string(value.get("candidate_id"), "candidate_id"),
        source=_require_string(value.get("source"), "source"),
        original_rank=_require_int(value.get("original_rank"), "original_rank"),
        original_score=_require_number(value.get("original_score"), "original_score"),
        original_score_provenance=_require_string(
            value.get("original_score_provenance"), "original_score_provenance"
        ),
        scoring_payload=_require_string(value.get("scoring_payload"), "scoring_payload"),
        revision=_optional_string(value.get("revision"), "revision"),
        checksum=_optional_string(value.get("checksum"), "checksum"),
    )


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RerankingConfigurationError(f"rerank {field} must be a non-empty string")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field)


def _require_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RerankingConfigurationError(f"rerank {field} must be an integer")
    return value


def _require_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RerankingConfigurationError(f"rerank {field} must be numeric")
    number = float(value)
    if not isfinite(number):
        raise RerankingConfigurationError(f"rerank {field} must be finite")
    return number


def _parameter_value(value: object, field: str) -> RerankParameterValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    raise RerankingConfigurationError(
        "rerank scorer parameter values must be finite JSON scalars",
        metadata={"parameter": field},
    )
