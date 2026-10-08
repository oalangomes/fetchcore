from __future__ import annotations

from dataclasses import fields, replace

import pytest

from fetchcore.reranking import (
    RerankCandidate,
    RerankInput,
    RerankParameterValue,
    RerankingConfigurationError,
    RerankingExecutionError,
    RerankingKernel,
    RerankScore,
)


class DeterministicFakeScorer:
    scorer_id = "deterministic-fixture"
    scorer_version = "1"

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.calls: list[
            tuple[
                str,
                tuple[str, ...],
                tuple[tuple[str, RerankParameterValue], ...],
            ]
        ] = []

    def score(
        self,
        *,
        query: str,
        candidates: tuple[RerankCandidate, ...],
        parameters: tuple[tuple[str, RerankParameterValue], ...],
    ) -> tuple[RerankScore, ...]:
        self.calls.append(
            (query, tuple(candidate.candidate_id for candidate in candidates), parameters)
        )
        return tuple(
            RerankScore(candidate_id=candidate.candidate_id, score=self.scores[candidate.candidate_id])
            for candidate in candidates
        )


class FailingFakeScorer(DeterministicFakeScorer):
    def score(
        self,
        *,
        query: str,
        candidates: tuple[RerankCandidate, ...],
        parameters: tuple[tuple[str, RerankParameterValue], ...],
    ) -> tuple[RerankScore, ...]:
        raise OSError("fixture scorer unavailable")


class InvalidOutputFakeScorer(DeterministicFakeScorer):
    def score(
        self,
        *,
        query: str,
        candidates: tuple[RerankCandidate, ...],
        parameters: tuple[tuple[str, RerankParameterValue], ...],
    ) -> tuple[RerankScore, ...]:
        return (RerankScore(candidate_id=candidates[0].candidate_id, score=1.0),)


def _candidate(candidate_id: str, rank: int, score: float) -> RerankCandidate:
    return RerankCandidate(
        candidate_id=candidate_id,
        source=f"src/{candidate_id}.py",
        original_rank=rank,
        original_score=score,
        original_score_provenance="solr:normalized_score",
        scoring_payload=f"implementation evidence for {candidate_id}",
        revision="fixture-revision",
        checksum=f"sha256:{candidate_id}",
    )


def _input(*, window_size: int = 3) -> RerankInput:
    return RerankInput(
        candidates=(
            _candidate("alpha", 1, 0.91),
            _candidate("beta", 2, 0.84),
            _candidate("gamma", 3, 0.73),
            _candidate("delta", 4, 0.62),
        ),
        scoring_query="find the implementation and its tests",
        window_size=window_size,
        scorer_id="deterministic-fixture",
        scorer_version="1",
        scorer_parameters=(("temperature", 0.0),),
    )


def test_kernel_reranks_only_bounded_window_and_preserves_tail() -> None:
    rerank_input = _input(window_size=3)
    scorer = DeterministicFakeScorer(
        {"alpha": 0.2, "beta": 0.95, "gamma": 0.5, "delta": 1.0}
    )

    result = RerankingKernel().rerank(rerank_input, scorer)

    assert [candidate.candidate_id for candidate in result.candidates] == [
        "beta",
        "gamma",
        "alpha",
        "delta",
    ]
    assert scorer.calls == [
        (
            rerank_input.scoring_query,
            ("alpha", "beta", "gamma"),
            rerank_input.scorer_parameters,
        )
    ]
    assert result.candidates[-1].rerank_score is None
    assert result.candidates[-1].in_rerank_window is False
    assert len(result.candidates) == len(rerank_input.candidates)
    assert {item.candidate_id for item in result.candidates} == {
        item.candidate_id for item in rerank_input.candidates
    }


def test_kernel_preserves_original_rank_score_source_and_provenance() -> None:
    rerank_input = _input()
    scorer = DeterministicFakeScorer({"alpha": 0.1, "beta": 0.9, "gamma": 0.5})

    result = RerankingKernel().rerank(rerank_input, scorer)
    by_id = {candidate.candidate_id: candidate for candidate in result.candidates}

    for original in rerank_input.candidates:
        reranked = by_id[original.candidate_id]
        assert reranked.source == original.source
        assert reranked.original_rank == original.original_rank
        assert reranked.original_score == original.original_score
        assert reranked.original_score_provenance == original.original_score_provenance
        assert reranked.revision == original.revision
        assert reranked.checksum == original.checksum


def test_equal_scores_use_original_rank_as_stable_tie_break() -> None:
    rerank_input = _input()
    scorer = DeterministicFakeScorer({"alpha": 0.7, "beta": 0.7, "gamma": 0.7})

    first = RerankingKernel().rerank(rerank_input, scorer)
    second = RerankingKernel().rerank(rerank_input, scorer)

    assert [item.candidate_id for item in first.candidates[:3]] == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert first.output_fingerprint == second.output_fingerprint


def test_capture_round_trip_preserves_semantic_fingerprints() -> None:
    rerank_input = _input()

    restored = RerankInput.from_mapping(rerank_input.to_mapping())

    assert restored == rerank_input
    assert restored.candidate_set_fingerprint == rerank_input.candidate_set_fingerprint
    assert restored.fingerprint == rerank_input.fingerprint


def test_candidate_set_identity_is_independent_from_reranker_choice() -> None:
    baseline = _input()
    treatment = replace(
        baseline,
        scorer_id="another-neutral-scorer",
        scorer_version="2026-09",
        scorer_parameters=(("threshold", 0.25),),
    )

    assert baseline.candidate_set_fingerprint == treatment.candidate_set_fingerprint
    assert baseline.fingerprint != treatment.fingerprint


def test_semantic_input_changes_invalidate_replay_identity() -> None:
    rerank_input = _input()
    changed_query = replace(rerank_input, scoring_query="different semantic query")
    changed_window = replace(rerank_input, window_size=2)
    changed_payload_candidates = (
        replace(rerank_input.candidates[0], scoring_payload="changed evidence"),
        *rerank_input.candidates[1:],
    )
    changed_payload = replace(rerank_input, candidates=changed_payload_candidates)

    assert changed_query.fingerprint != rerank_input.fingerprint
    assert changed_window.fingerprint != rerank_input.fingerprint
    assert changed_payload.candidate_set_fingerprint != rerank_input.candidate_set_fingerprint
    assert changed_payload.fingerprint != rerank_input.fingerprint


def test_capture_rejects_ground_truth_or_other_unknown_runtime_fields() -> None:
    payload = _input().to_mapping()
    payload["ground_truth"] = ["beta"]

    with pytest.raises(
        RerankingConfigurationError,
        match="unsupported fields",
    ):
        RerankInput.from_mapping(payload)

    assert "ground_truth" not in {field.name for field in fields(RerankInput)}
    assert "expected_rank" not in {field.name for field in fields(RerankCandidate)}


def test_captured_fingerprint_drift_fails_closed() -> None:
    payload = _input().to_mapping()
    payload["fingerprint"] = "sha256:stale"

    with pytest.raises(
        RerankingConfigurationError,
        match="fingerprint does not match",
    ):
        RerankInput.from_mapping(payload)


def test_runtime_scorer_identity_must_match_captured_contract() -> None:
    rerank_input = _input()
    scorer = DeterministicFakeScorer({"alpha": 1.0, "beta": 0.5, "gamma": 0.2})
    scorer.scorer_id = "wrong-scorer"

    with pytest.raises(RerankingConfigurationError) as exc_info:
        RerankingKernel().rerank(rerank_input, scorer)

    assert exc_info.value.category == "scorer_mismatch"


def test_scorer_failure_is_explicit_and_never_falls_back_to_baseline() -> None:
    rerank_input = _input()

    with pytest.raises(RerankingExecutionError) as exc_info:
        RerankingKernel().rerank(rerank_input, FailingFakeScorer({}))

    assert exc_info.value.category == "scorer_failed"
    assert exc_info.value.metadata == {
        "scorer_id": rerank_input.scorer_id,
        "scorer_version": rerank_input.scorer_version,
    }


def test_scorer_must_return_exactly_one_score_per_window_candidate() -> None:
    rerank_input = _input()

    with pytest.raises(RerankingExecutionError) as exc_info:
        RerankingKernel().rerank(rerank_input, InvalidOutputFakeScorer({}))

    assert exc_info.value.category == "invalid_scorer_output"


def test_input_rejects_non_contiguous_baseline_order() -> None:
    with pytest.raises(
        RerankingConfigurationError,
        match="contiguous baseline ordering",
    ):
        replace(
            _input(),
            candidates=(
                _candidate("alpha", 1, 0.9),
                _candidate("beta", 3, 0.8),
            ),
            window_size=2,
        )
