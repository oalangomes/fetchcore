from __future__ import annotations

from fetchcore.reranking import RerankCandidate, RerankScore
from fetchcore.reranking_runtime import apply_reranking
from fetchcore.retrieval import RetrievalConfig
from fetchcore.search import SearchResult


class ReverseFixtureScorer:
    scorer_id = "reverse-fixture"
    scorer_version = "1"

    def score(
        self,
        *,
        query: str,
        candidates: tuple[RerankCandidate, ...],
        parameters: tuple[tuple[str, str | int | float | bool], ...],
    ) -> tuple[RerankScore, ...]:
        del query, parameters
        scores = {
            candidate.candidate_id: float(index)
            for index, candidate in enumerate(candidates, start=1)
        }
        return tuple(
            RerankScore(candidate_id=candidate.candidate_id, score=scores[candidate.candidate_id])
            for candidate in candidates
        )


def _result(candidate_id: str, score: float) -> SearchResult:
    return SearchResult(
        id=candidate_id,
        source="repository",
        source_ref=f"src/{candidate_id}.py",
        knowledge_type="code",
        content=f"implementation evidence for {candidate_id}",
        score=score,
        revision="rev-a",
        checksum=f"sha256:{candidate_id}",
        metadata={"score_provenance": "fixture:normalized"},
    )


def test_reranking_reorders_only_the_existing_candidate_set() -> None:
    candidates = (
        _result("alpha", 0.9),
        _result("beta", 0.8),
        _result("gamma", 0.7),
    )
    config = RetrievalConfig.from_mapping(
        {
            "reranking": {
                "enabled": True,
                "scorer": {"id": "reverse-fixture", "version": "1"},
                "window_size": 2,
            }
        }
    )

    application = apply_reranking(
        candidates,
        scoring_query="fixture query",
        backend_id="fixture",
        config=config,
        scorer=ReverseFixtureScorer(),
    )

    assert [item.id for item in application.candidates] == [
        "beta",
        "alpha",
        "gamma",
    ]
    assert {item.id for item in application.candidates} == {
        item.id for item in candidates
    }
    assert len(application.candidates) == len(candidates)
    assert application.metadata["effective_window_size"] == 2
