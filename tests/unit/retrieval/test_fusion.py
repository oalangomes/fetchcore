from __future__ import annotations

import pytest

from fetchcore.fusion import (
    DEFAULT_RRF_K,
    FusionCandidate,
    FusionChannelSignal,
    FusionConfigurationError,
    FusionInput,
    FusionKernel,
    FusionRequest,
)


def _signal(rank: int, score: float, raw: float | None = None) -> FusionChannelSignal:
    return FusionChannelSignal(rank=rank, normalized_score=score, raw_score=raw)


def test_rrf_kernel_preserves_frozen_backend_ordering_semantics() -> None:
    fusion_input = FusionInput(
        candidates=(
            FusionCandidate(
                candidate_id="beta",
                source="beta.py",
                lexical=_signal(2, 0.5, 5.0),
                dense=_signal(1, 0.95, 0.95),
            ),
            FusionCandidate(
                candidate_id="alpha",
                source="alpha.py",
                lexical=_signal(1, 1.0, 10.0),
            ),
            FusionCandidate(
                candidate_id="gamma",
                source="gamma.py",
                dense=_signal(2, 0.90, 0.90),
            ),
        ),
        max_results=3,
    )

    result = FusionKernel().fuse(
        FusionRequest(
            input=fusion_input,
            strategy="rrf",
            parameters={"k": DEFAULT_RRF_K},
        )
    )

    assert [candidate.candidate_id for candidate in result.candidates] == [
        "beta",
        "alpha",
        "gamma",
    ]
    beta, alpha, gamma = result.candidates
    assert (beta.lexical_rank, beta.dense_rank, beta.fused_rank) == (2, 1, 1)
    assert beta.lexical_contribution > 0
    assert beta.dense_contribution > 0
    assert (alpha.lexical_rank, alpha.dense_rank, alpha.fused_rank) == (1, None, 2)
    assert (gamma.lexical_rank, gamma.dense_rank, gamma.fused_rank) == (None, 2, 3)
    assert result.parameters == {"k": 60}


def test_rrf_tie_break_is_deterministic_and_backend_order_independent() -> None:
    left = FusionCandidate(
        candidate_id="a",
        source="a.py",
        lexical=_signal(1, 0.5),
    )
    right = FusionCandidate(
        candidate_id="b",
        source="b.py",
        dense=_signal(1, 0.5),
    )
    kernel = FusionKernel()
    first = kernel.fuse(FusionRequest(input=FusionInput((right, left), 2)))
    second = kernel.fuse(FusionRequest(input=FusionInput((left, right), 2)))

    assert [item.candidate_id for item in first.candidates] == ["a", "b"]
    assert [item.candidate_id for item in second.candidates] == ["a", "b"]
    assert first.input_fingerprint == second.input_fingerprint


def test_fusion_input_fingerprint_ignores_diagnostic_raw_scores() -> None:
    first = FusionInput(
        (
            FusionCandidate(
                candidate_id="a",
                source="a.py",
                dense=_signal(1, 0.8, 0.81),
            ),
        ),
        10,
    )
    second = FusionInput(
        (
            FusionCandidate(
                candidate_id="a",
                source="a.py",
                dense=_signal(1, 0.8, 999.0),
            ),
        ),
        10,
    )

    assert first.fingerprint == second.fingerprint


def test_fusion_input_fingerprint_changes_when_ordering_input_changes() -> None:
    first = FusionInput(
        (
            FusionCandidate(
                candidate_id="a",
                source="a.py",
                dense=_signal(1, 0.8),
            ),
        ),
        10,
    )
    second = FusionInput(
        (
            FusionCandidate(
                candidate_id="a",
                source="a.py",
                dense=_signal(2, 0.8),
            ),
        ),
        10,
    )

    assert first.fingerprint != second.fingerprint


def test_fusion_kernel_fails_closed_for_invalid_input_or_strategy() -> None:
    with pytest.raises(FusionConfigurationError):
        FusionInput(
            (
                FusionCandidate(
                    candidate_id="a",
                    source="a.py",
                    dense=_signal(1, 0.8),
                ),
                FusionCandidate(
                    candidate_id="a",
                    source="other.py",
                    lexical=_signal(2, 0.7),
                ),
            ),
            10,
        )

    fusion_input = FusionInput(
        (
            FusionCandidate(
                candidate_id="a",
                source="a.py",
                dense=_signal(1, 0.8),
            ),
        ),
        10,
    )
    with pytest.raises(FusionConfigurationError):
        FusionKernel().fuse(
            FusionRequest(
                input=fusion_input,
                strategy="weighted-score",  # type: ignore[arg-type]
            )
        )


def test_fusion_input_round_trip_preserves_fingerprint() -> None:
    original = FusionInput(
        (
            FusionCandidate(
                candidate_id="a",
                source="a.py",
                lexical=_signal(2, 0.7, 12.0),
                dense=_signal(1, 0.9, 0.91),
            ),
        ),
        10,
    )

    restored = FusionInput.from_mapping(original.to_mapping())

    assert restored == original
    assert restored.fingerprint == original.fingerprint


def test_fusion_result_output_fingerprint_is_deterministic() -> None:
    fusion_input = FusionInput(
        (
            FusionCandidate(
                candidate_id="a",
                source="a.py",
                dense=_signal(1, 0.9),
            ),
        ),
        10,
    )
    kernel = FusionKernel()

    first = kernel.fuse(FusionRequest(input=fusion_input, parameters={"k": 60}))
    second = kernel.fuse(FusionRequest(input=fusion_input, parameters={"k": 60}))

    assert first.output_fingerprint == second.output_fingerprint
    assert first.to_mapping()["output_fingerprint"] == first.output_fingerprint


def test_fusion_input_round_trip_fails_closed_on_tampered_fingerprint() -> None:
    original = FusionInput(
        (
            FusionCandidate(
                candidate_id="a",
                source="a.py",
                dense=_signal(1, 0.9),
            ),
        ),
        10,
    )
    captured = original.to_mapping()
    captured["max_results"] = 9

    with pytest.raises(FusionConfigurationError):
        FusionInput.from_mapping(captured)


def test_normalized_score_uses_fixed_equal_channel_weights() -> None:
    fusion_input = FusionInput(
        candidates=(
            FusionCandidate(
                candidate_id="both",
                source="both.py",
                lexical=_signal(2, 0.90),
                dense=_signal(4, 0.60),
            ),
            FusionCandidate(
                candidate_id="dense-only",
                source="dense.py",
                dense=_signal(1, 0.95),
            ),
            FusionCandidate(
                candidate_id="lexical-only",
                source="lexical.py",
                lexical=_signal(1, 1.0),
            ),
        ),
        max_results=3,
    )

    result = FusionKernel().fuse(
        FusionRequest(
            input=fusion_input,
            strategy="normalized-score",
        )
    )

    assert [item.candidate_id for item in result.candidates] == [
        "both",
        "lexical-only",
        "dense-only",
    ]
    both, lexical_only, dense_only = result.candidates
    assert both.fused_score == pytest.approx(0.75)
    assert both.lexical_contribution == pytest.approx(0.45)
    assert both.dense_contribution == pytest.approx(0.30)
    assert lexical_only.fused_score == pytest.approx(0.50)
    assert lexical_only.dense_contribution == 0.0
    assert dense_only.fused_score == pytest.approx(0.475)
    assert dense_only.lexical_contribution == 0.0
    assert result.parameters == {}


def test_normalized_score_rejects_parameters_and_invalid_channel_scores() -> None:
    fusion_input = FusionInput(
        (
            FusionCandidate(
                candidate_id="a",
                source="a.py",
                dense=_signal(1, 0.8),
            ),
        ),
        10,
    )

    with pytest.raises(FusionConfigurationError):
        FusionKernel().fuse(
            FusionRequest(
                input=fusion_input,
                strategy="normalized-score",
                parameters={"dense_weight": 0.7},
            )
        )

    with pytest.raises(FusionConfigurationError):
        FusionChannelSignal(rank=1, normalized_score=1.01)

    with pytest.raises(FusionConfigurationError):
        FusionChannelSignal(rank=1, normalized_score=float("nan"))
