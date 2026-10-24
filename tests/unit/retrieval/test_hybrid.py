import pytest

from fetchcore.adapters.retrieval.solr.backend import SolrBackend
from fetchcore.adapters.retrieval.opensearch.backend import OpenSearchBackend
from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorBackend
from fetchcore.retrieval.config import (
    HybridRetrievalConfig,
    RetrievalConfig,
    RetrievalConfigurationError,
)
from fetchcore.retrieval.contracts import SearchIntent, SearchScope
from fetchcore.retrieval.fusion import (
    FusionCandidate,
    FusionChannelSignal,
    FusionInput,
    FusionKernel,
    FusionRequest,
)
from fetchcore.retrieval.hybrid import allocate_fusion_input


@pytest.mark.parametrize("name", ["lexical_fusion_window", "dense_fusion_window", "rrf_k"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "20", [], {}])
def test_invalid_config_rejected_for_mapping_and_direct_construction(name, value):
    for build in (
        lambda: HybridRetrievalConfig(**{name: value}),
        lambda: HybridRetrievalConfig.from_mapping({name: value}),
    ):
        with pytest.raises(RetrievalConfigurationError):
            build()


def test_legacy_config_serialization_and_round_trip():
    old = {"enabled": True, "strategy": "rrf"}
    assert HybridRetrievalConfig.from_mapping(old).to_mapping() == old
    assert HybridRetrievalConfig(enabled=True, rrf_k=60).to_mapping() == old
    configured = {**old, "lexical_fusion_window": 40, "dense_fusion_window": 80, "rrf_k": 30}
    assert HybridRetrievalConfig.from_mapping(configured).to_mapping() == configured
    with pytest.raises(RetrievalConfigurationError):
        HybridRetrievalConfig(rrf_k=None)


@pytest.mark.parametrize("backend_type", [SolrBackend, OpenSearchBackend, QdrantVectorBackend])
def test_impossible_window_rejected_before_backend_or_embedding_io(backend_type, tmp_path):
    def forbidden(*args):
        pytest.fail("invalid window reached I/O")

    backend = backend_type(transport_factory=forbidden, embedding_factory=forbidden)
    retrieval = RetrievalConfig.from_mapping(
        {"dense": {"enabled": True}, "hybrid": {"enabled": True, "lexical_fusion_window": 81}}
    )
    with pytest.raises(RetrievalConfigurationError, match="acquisition budget"):
        backend.search(
            SearchIntent(canonical_query="value"),
            SearchScope(
                workspace=tmp_path,
                repository="acme/repo",
                revision=None,
                knowledge_types=(),
                max_results=20,
                settings={"diagnostic_candidate_window": 80},
                retrieval=retrieval,
            ),
            (),
        )


def candidate(identity, *, lexical=None, dense=None):
    return FusionCandidate(
        identity,
        identity + ".py",
        lexical=FusionChannelSignal(lexical, 0.7) if lexical is not None else None,
        dense=FusionChannelSignal(dense, 0.8) if dense is not None else None,
    )


def test_prefix_admission_preserves_sparse_rank_gaps_and_channel_provenance():
    left = (
        candidate("same", lexical=2),
        candidate("sparse", lexical=17),
        candidate("excluded", lexical=29),
    )
    right = (candidate("dense", dense=1), candidate("same", dense=2))
    result = allocate_fusion_input(
        lexical=left, dense=right, lexical_window=2, dense_window=2, max_results=1
    )
    assert len(result.candidates) == 3
    by_id = {c.candidate_id: c for c in result.candidates}
    assert by_id["sparse"].lexical.rank == 17
    assert by_id["same"].lexical.rank == 2 and by_id["same"].dense.rank == 2
    assert "excluded" not in by_id
    assert left[1].lexical.rank == 17
    fused = FusionKernel().fuse(FusionRequest(result))
    assert sum(c.returned for c in fused.candidates) == 1
    assert fused.candidates[0].candidate_id == "same"
    assert (
        fused.candidates[0].lexical_contribution > 0 and fused.candidates[0].dense_contribution > 0
    )


def test_sparse_availability_is_not_the_acquisition_budget():
    config = HybridRetrievalConfig(lexical_fusion_window=80)
    config.validate_acquisition_budget(80)
    result = allocate_fusion_input(
        lexical=(candidate("only", lexical=42),),
        dense=(),
        lexical_window=80,
        dense_window=80,
        max_results=20,
    )
    assert result.candidates[0].lexical.rank == 42
    with pytest.raises(RetrievalConfigurationError):
        config.validate_acquisition_budget(79)


def test_default_admission_preserves_existing_input_and_output_fingerprints():
    full = (candidate("a", lexical=1, dense=2), candidate("b", lexical=2), candidate("c", dense=1))
    old = FusionInput(full, 20)
    admitted = allocate_fusion_input(
        lexical=(full[0], full[1]),
        dense=(full[2], full[0]),
        lexical_window=20,
        dense_window=20,
        max_results=20,
    )
    assert old.fingerprint == admitted.fingerprint
    assert (
        FusionKernel().fuse(FusionRequest(old)).to_mapping()
        == FusionKernel().fuse(FusionRequest(admitted)).to_mapping()
    )
    changed = FusionKernel().fuse(FusionRequest(admitted, parameters={"k": 10}))
    assert changed.input_fingerprint == old.fingerprint
    assert changed.output_fingerprint != FusionKernel().fuse(FusionRequest(old)).output_fingerprint


def test_channel_deduplication_does_not_duplicate_rrf_contribution():
    first = candidate("a", lexical=1)
    last = candidate("a", lexical=3)
    admitted = allocate_fusion_input(
        lexical=(first, last),
        dense=(candidate("a", dense=1),),
        lexical_window=2,
        dense_window=1,
        max_results=20,
    )
    assert len(admitted.candidates) == 1
    assert admitted.candidates[0].lexical.rank == 3
    fused = FusionKernel().fuse(FusionRequest(admitted))
    assert fused.candidates[0].fused_score == 1 / 63 + 1 / 61


@pytest.mark.parametrize("window", [0, True, -1])
def test_invalid_admission_window_fails_closed(window):
    with pytest.raises(ValueError):
        allocate_fusion_input(
            lexical=(), dense=(), lexical_window=window, dense_window=20, max_results=20
        )
