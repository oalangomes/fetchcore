"""Reproducible extraction and fixture-ranking benchmark for Markdown S4."""

from __future__ import annotations

import json
import math
import re
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import (
    KnowledgeDocument,
    RepositoryIndexConfig,
    RepositoryIndexer,
    RepositoryIndexResult,
)


_ROOT = Path(__file__).resolve().parent
_FIXTURES = _ROOT / "fixtures"
_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _tokens(value: str) -> set[str]:
    return {item.casefold() for item in _WORD.findall(value)}


def _ranked_documents(
    documents: tuple[KnowledgeDocument, ...],
    query: str,
) -> tuple[KnowledgeDocument, ...]:
    terms = _tokens(query)
    return tuple(
        sorted(
            documents,
            key=lambda document: (
                -len(terms & _tokens(document.content)),
                document.path,
                document.line_start,
                document.id,
            ),
        )
    )


def _nearest_rank(values: list[float], percentile: float) -> float:
    return values[max(0, math.ceil(percentile * len(values)) - 1)]


def _fixture_metrics(
    documents: tuple[KnowledgeDocument, ...],
    truth: dict[str, Any],
) -> dict[str, object]:
    case_results: list[dict[str, object]] = []
    recovered = 0
    exact_locators = 0
    reciprocal_ranks: list[float] = []
    recall_at_five: list[float] = []
    ndcg_at_five: list[float] = []
    evidence_density_at_three: list[float] = []

    for case in truth["cases"]:
        matching = [
            document
            for document in documents
            if document.path == case["path"] and case["evidence"] in document.content
        ]
        ranking = _ranked_documents(documents, case["query"])
        rank = next(
            (
                index
                for index, document in enumerate(ranking, start=1)
                if document in matching
            ),
            None,
        )
        if matching:
            recovered += 1
        exact = any(
            document.line_start == case["line_start"]
            and document.line_end == case["line_end"]
            for document in matching
        )
        if exact:
            exact_locators += 1

        reciprocal_ranks.append(0.0 if rank is None else 1.0 / rank)
        hit_at_five = rank is not None and rank <= 5
        recall_at_five.append(float(hit_at_five))
        ndcg_at_five.append(
            0.0 if not hit_at_five else 1.0 / math.log2(rank + 1)
        )
        top_three = ranking[:3]
        total_chars = sum(len(document.content) for document in top_three)
        relevant_chars = sum(
            len(document.content) for document in top_three if document in matching
        )
        evidence_density_at_three.append(
            0.0 if total_chars == 0 else relevant_chars / total_chars
        )
        case_results.append(
            {
                "id": case["id"],
                "ground_truth_recovered": bool(matching),
                "exact_locator": exact,
                "fixture_rank": rank,
            }
        )

    return {
        "ground_truth_cases": len(truth["cases"]),
        "ground_truth_recovered": recovered,
        "ground_truth_recovery": recovered / len(truth["cases"]),
        "exact_source_locators": exact_locators,
        "exact_source_locator_rate": exact_locators / len(truth["cases"]),
        "fixture_lexical_recall_at_5": statistics.mean(recall_at_five),
        "fixture_lexical_mrr": statistics.mean(reciprocal_ranks),
        "fixture_lexical_ndcg_at_5": statistics.mean(ndcg_at_five),
        "fixture_useful_evidence_density_at_3": statistics.mean(
            evidence_density_at_three
        ),
        "cases": case_results,
    }


def _indexer() -> RepositoryIndexer:
    return RepositoryIndexer(
        RepositoryIndexConfig(
            repository="fetchcore/markdown-s4-fixture",
            revision="synthetic-s4-fixture",
        )
    )


def _incremental_metrics() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="fetchcore-markdown-s4-") as temporary:
        workspace = Path(temporary) / "fixtures"
        shutil.copytree(_FIXTURES, workspace)
        indexer = _indexer()
        initial = indexer.index(workspace)
        unchanged = indexer.index(workspace, previous_documents=initial.documents)
        api_path = workspace / "docs" / "api.md"
        api_path.write_text(
            api_path.read_text(encoding="utf-8")
            + "\nA one-file incremental S4 benchmark edit.\n",
            encoding="utf-8",
        )
        started = time.perf_counter()
        changed = indexer.index(workspace, previous_documents=initial.documents)
        changed_duration_ms = (time.perf_counter() - started) * 1_000
        return {
            "unchanged_added": len(unchanged.changes.added),
            "unchanged_updated": len(unchanged.changes.updated),
            "unchanged_removed": len(unchanged.changes.removed),
            "unchanged_documents": len(unchanged.changes.unchanged),
            "one_file_edit_added": len(changed.changes.added),
            "one_file_edit_updated": len(changed.changes.updated),
            "one_file_edit_removed": len(changed.changes.removed),
            "one_file_edit_indexing_ms": round(changed_duration_ms, 3),
            "note": "A full canonical scan may still be required; these are document diffs.",
        }


def evaluate(*, repeats: int = 5) -> dict[str, object]:
    if repeats < 1:
        raise ValueError("benchmark repeats must be positive")
    truth = json.loads((_ROOT / "ground-truth.json").read_text(encoding="utf-8"))
    indexer = _indexer()
    result: RepositoryIndexResult | None = None
    durations: list[float] = []
    for attempt in range(repeats + 1):
        started = time.perf_counter()
        result = indexer.index(_FIXTURES)
        elapsed = (time.perf_counter() - started) * 1_000
        if attempt:
            durations.append(elapsed)
    assert result is not None

    documents = result.documents
    references = sum(
        len(getattr(document.evidence.section, "references", ()))
        for document in documents
        if document.evidence.section is not None
    )
    measurements = sorted(durations)
    return {
        "protocol": truth["protocol"],
        "corpus": truth["corpus"],
        "repository_revision": "synthetic-s4-fixture",
        "indexing_fingerprint": indexing_contract_fingerprint(indexer),
        "chunk_config": {
            "max_lines": indexer.config.max_chunk_lines,
            "overlap_lines": indexer.config.chunk_overlap_lines,
            "markdown_max_chars": getattr(
                indexer.config, "max_markdown_chunk_chars", None
            ),
        },
        "canonical_documents": len(documents),
        "content_characters": sum(len(document.content) for document in documents),
        "maximum_chunk_characters": max(
            (len(document.content) for document in documents),
            default=0,
        ),
        "observed_section_references": references,
        "indexing_ms_p50": round(statistics.median(measurements), 3),
        "indexing_ms_p95": round(_nearest_rank(measurements, 0.95), 3),
        "time_to_first_candidate_ms": None,
        "time_to_first_candidate_note": (
            "No retrieval backend is part of this extraction-only benchmark."
        ),
        **_fixture_metrics(documents, truth),
        "incremental": _incremental_metrics(),
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), indent=2, sort_keys=True))
