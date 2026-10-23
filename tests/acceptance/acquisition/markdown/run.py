"""Reproducible extraction and fixture-ranking benchmark for Markdown S4."""

from __future__ import annotations

import json
import math
import re
import shutil
import statistics
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from fetchcore.knowledge.chunking import (
    MAX_MARKDOWN_EVIDENCE_BYTES,
    MAX_MARKDOWN_REFERENCES_PER_CHUNK,
    chunks_for_knowledge,
)
from fetchcore.projection.core import ProjectionV3Compiler
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
        if rank is None or rank > 5:
            ndcg_at_five.append(0.0)
        else:
            ndcg_at_five.append(1.0 / math.log2(rank + 1))
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


def _reference_integrity(
    documents: tuple[KnowledgeDocument, ...],
) -> dict[str, int]:
    occurrences: dict[tuple[str, str, int | None], list[KnowledgeDocument]] = defaultdict(list)
    invalid_provenance = 0
    invalid_retained_content = 0
    unresolved_only_violations = 0
    missing_code_languages = 0
    for document in documents:
        section = document.evidence.section
        if section is None:
            continue
        for language in section.code_languages:
            if f"```{language}" not in document.content and f"~~~{language}" not in document.content:
                missing_code_languages += 1
        for reference in section.references:
            occurrences[(document.path, reference.target, reference.line)].append(document)
            if reference.resolution != "observed":
                unresolved_only_violations += 1
            if (
                reference.line is None
                or not document.line_start <= reference.line <= document.line_end
            ):
                invalid_provenance += 1
            if reference.target not in document.content:
                invalid_retained_content += 1

    unjustified_duplicates = 0
    overlap_duplicates = 0
    for (_, target, line), matched_documents in occurrences.items():
        if len(matched_documents) < 2:
            continue
        extras = len(matched_documents) - 1
        if line is not None and all(
            document.line_start <= line <= document.line_end
            and target in document.content
            for document in matched_documents
        ):
            overlap_duplicates += extras
        else:
            unjustified_duplicates += extras
    return {
        "references_with_incorrect_provenance": invalid_provenance,
        "references_missing_from_retained_content": invalid_retained_content,
        "references_not_marked_observed": unresolved_only_violations,
        "code_languages_missing_from_retained_content": missing_code_languages,
        "unjustified_duplicate_reference_observations": unjustified_duplicates,
        "overlap_duplicated_reference_observations": overlap_duplicates,
    }


def _truncation_integrity() -> dict[str, int]:
    source = """# Truncated links

| Name | Reference |
| --- | --- |
| kept | [kept](kept.md) |
| discarded | [discarded](discarded.md) |
"""
    chunks = chunks_for_knowledge(
        source,
        language="markdown",
        knowledge_type="documentation",
        max_lines=3,
        overlap=0,
        max_chars=256,
    )
    propagated = 0
    for chunk in chunks:
        section = chunk.section
        if section is None:
            continue
        for reference in section.references:
            if (
                reference.line is None
                or not chunk.line_start <= reference.line <= chunk.line_end
                or reference.target not in chunk.content
            ):
                propagated += 1
        for language in section.code_languages:
            if f"```{language}" not in chunk.content and f"~~~{language}" not in chunk.content:
                propagated += 1
    return {"observations_from_discarded_regions": propagated}


def _oversized_evidence_metrics() -> dict[str, object]:
    heading_text = "H" * 100_000
    heading_chunk = chunks_for_knowledge(
        f"# {heading_text}\n\nbody\n",
        language="markdown",
        knowledge_type="documentation",
        max_lines=160,
        overlap=0,
        max_chars=100,
    )[0]
    heading_section = heading_chunk.section
    heading_evidence_bytes = (
        0
        if heading_section is None
        else len(
            json.dumps(
                heading_section.to_mapping(),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    )

    languages = "x" * 200
    language_chunk = chunks_for_knowledge(
        f"# Fence\n\n```{languages}\nvalue\n```\n",
        language="markdown",
        knowledge_type="documentation",
        max_lines=160,
        overlap=0,
        max_chars=512,
    )[0]
    language_section = language_chunk.section

    many_links = "\n".join(
        f"[reference {number}](ref-{number}.md)" for number in range(1_000)
    )
    link_chunk = chunks_for_knowledge(
        f"# Many links\n\n{many_links}\n",
        language="markdown",
        knowledge_type="documentation",
        max_lines=1_200,
        overlap=0,
        max_chars=48_000,
    )[0]
    link_section = link_chunk.section
    link_evidence_bytes = (
        0
        if link_section is None
        else len(
            json.dumps(
                link_section.to_mapping(),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    )

    with tempfile.TemporaryDirectory(prefix="fetchcore-markdown-s4-large-") as temporary:
        workspace = Path(temporary)
        (workspace / "README.md").write_text(
            f"# {heading_text}\n\nbody\n",
            encoding="utf-8",
        )
        indexer = RepositoryIndexer(
            RepositoryIndexConfig(
                repository="fetchcore/markdown-s4-large-fixture",
                revision="oversized-heading-fixture",
                max_markdown_chunk_chars=100,
            )
        )
        document = indexer.index(workspace).documents[0]
        payload = str(ProjectionV3Compiler().compile(document).fields["canonical_payload"])
        document_bytes = len(
            json.dumps(
                document.to_mapping(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        payload_bytes = len(payload.encode("utf-8"))

    return {
        "oversized_heading_source_chars": len(heading_text),
        "oversized_heading_chunk_chars": len(heading_chunk.content),
        "oversized_heading_metadata_name_chars": (
            0 if heading_section is None or heading_section.name is None else len(heading_section.name)
        ),
        "oversized_heading_evidence_bytes": heading_evidence_bytes,
        "oversized_heading_canonical_document_bytes": document_bytes,
        "oversized_heading_canonical_payload_bytes": payload_bytes,
        "long_fence_language_observed": bool(
            language_section is not None and language_section.code_languages
        ),
        "long_fence_language_omission_diagnostic": bool(
            language_section is not None
            and "markdown-code-language-omitted" in language_section.diagnostics
        ),
        "thousand_links_source_count": 1_000,
        "thousand_links_chunk_reference_count": (
            0 if link_section is None else len(link_section.references)
        ),
        "thousand_links_evidence_bytes": link_evidence_bytes,
        "thousand_links_budget_diagnostic": bool(
            link_section is not None
            and "markdown-references-budget-exceeded" in link_section.diagnostics
        ),
        "section_evidence_byte_limit": MAX_MARKDOWN_EVIDENCE_BYTES,
        "references_per_chunk_limit": MAX_MARKDOWN_REFERENCES_PER_CHUNK,
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
            "one_file_edit_unchanged": len(changed.changes.unchanged),
            "one_file_edit_changed_chunks": (
                len(changed.changes.added)
                + len(changed.changes.updated)
                + len(changed.changes.removed)
            ),
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
    references = 0
    section_evidence_bytes = []
    canonical_payload_bytes = []
    canonical_document_bytes = []
    for document in documents:
        section = document.evidence.section
        if section is not None:
            references += len(section.references)
            section_evidence_bytes.append(
                len(
                    json.dumps(
                        section.to_mapping(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
            )
        projection = ProjectionV3Compiler().compile(document)
        payload = str(projection.fields["canonical_payload"])
        canonical_payload_bytes.append(len(payload.encode("utf-8")))
        canonical_document_bytes.append(
            len(
                json.dumps(
                    document.to_mapping(),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            )
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
        "content_bytes": sum(
            len(document.content.encode("utf-8")) for document in documents
        ),
        "canonical_document_bytes_total": sum(canonical_document_bytes),
        "canonical_payload_bytes_total": sum(canonical_payload_bytes),
        "maximum_canonical_payload_bytes": max(canonical_payload_bytes, default=0),
        "maximum_section_evidence_bytes": max(section_evidence_bytes, default=0),
        "section_evidence_bytes_limit": MAX_MARKDOWN_EVIDENCE_BYTES,
        "maximum_chunk_characters": max(
            (len(document.content) for document in documents),
            default=0,
        ),
        "observed_section_references": references,
        **_reference_integrity(documents),
        **_truncation_integrity(),
        "oversized_evidence": _oversized_evidence_metrics(),
        "indexing_ms_p50": round(statistics.median(measurements), 3),
        "indexing_ms_p95": round(_nearest_rank(measurements, 0.95), 3),
        "indexing_ms_samples": [round(value, 3) for value in measurements],
        "time_to_first_candidate_ms": None,
        "time_to_first_candidate_note": (
            "No retrieval backend is part of this extraction-only benchmark."
        ),
        **_fixture_metrics(documents, truth),
        "incremental": _incremental_metrics(),
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), indent=2, sort_keys=True))
