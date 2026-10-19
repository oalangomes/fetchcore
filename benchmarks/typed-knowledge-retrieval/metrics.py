"""Evaluate typed evidence IDs and exact Markdown spans on real ranked responses."""

from __future__ import annotations

import math
from typing import Any

from fetchcore.baseline import (
    BASELINE_LINE_BUDGETS,
    BaselineEvidenceAlternative,
    BaselineEvidenceRegion,
    BaselineEvidenceRequirement,
    BaselineGroundTruth,
    RetrievalResultSpan,
    evaluate_line_budgeted_evidence,
)
from fetchcore.retrieval.contracts import KnowledgeSearchResponse


def evaluate(response: KnowledgeSearchResponse, case: dict[str, Any], expected_id: str, *, k: int) -> dict[str, object]:
    """Score one reviewed locator; unavailable measurements remain null."""
    if k < 1:
        raise ValueError("k must be positive")
    ranked = response.results[:k]
    ranks = [n for n, hit in enumerate(ranked, 1) if hit.id == expected_id]
    if len(ranks) > 1:
        raise ValueError("backend returned a duplicate canonical ID")
    rank = ranks[0] if ranks else None
    matching_source = any(hit.source == case["path"] for hit in ranked)
    evidence_recall = int(rank is not None)
    metric: dict[str, object] = {
        "source_recall_at_k": int(matching_source),
        "evidence_recall_at_k": evidence_recall,
        "evidence_mrr": 0.0 if rank is None else round(1 / rank, 6),
        "evidence_ndcg_at_k": 0.0 if rank is None else round(1 / math.log2(rank + 1), 6),
        "expected_rank": rank,
        "span_coverage_at_k": None,
        "context_yield_by_line_budget": None,
        "useful_evidence_density_hits": round(evidence_recall / len(ranked), 6) if ranked else 0.0,
        "returned_chars": sum(len(hit.content) for hit in ranked),
        "returned_tokens": None,
        "returned_results": len(ranked),
        "candidates_examined": response.telemetry.candidates_examined if response.telemetry else None,
        "time_to_first_candidate_ms": response.telemetry.time_to_first_candidate_ms if response.telemetry else None,
    }
    if case["kind"] != "markdown_span":
        return metric
    source_ref = next((hit.source_ref for hit in ranked if hit.source == case["path"]), None)
    if source_ref is None:
        # Source refs are deterministic from corpus identity and relative path.
        from urllib.parse import quote
        source_ref = f"repository://{quote(case['repository'], safe='/:.@')}/{quote(case['path'], safe='/') }"
    truth = BaselineGroundTruth(
        query_id=case["id"],
        requirements=(BaselineEvidenceRequirement(
            id=case["id"],
            alternatives=(BaselineEvidenceAlternative((BaselineEvidenceRegion(
                source_ref=source_ref,
                line_start=case["line_start"], line_end=case["line_end"],
            ),)),),
        ),),
    )
    spans = tuple(RetrievalResultSpan(
        rank=n, id=hit.id, source_ref=hit.source_ref,
        line_start=hit.line_start, line_end=hit.line_end,
    ) for n, hit in enumerate(ranked, 1)
        if hit.line_start is not None and hit.line_end is not None)
    required = set(range(case["line_start"], case["line_end"] + 1))
    covered: set[int] = set()
    for span in spans:
        if span.source_ref == source_ref:
            covered.update(range(span.line_start, span.line_end + 1))
    metric["span_coverage_at_k"] = round(len(required & covered) / len(required), 6)
    if len(spans) == len(ranked):
        metric["context_yield_by_line_budget"] = {
            str(item.line_budget): item.to_mapping()
            for item in evaluate_line_budgeted_evidence(truth, spans, budgets=BASELINE_LINE_BUDGETS)
        }
    return metric
