"""Small client-side structural lookup cost comparison using an indexed transport.

The transport simulates exact keyword lookup, not OpenSearch server latency. It
counts protocol requests and hydrated records through the real operational
OpenSearch store. Run with PYTHONPATH=. .venv/bin/python scripts/bench/structural_lookup.py.
"""

from __future__ import annotations

import json
import statistics
import tempfile
import time
import tracemalloc
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fetchcore.adapters.retrieval.opensearch.backend import OpenSearchIndexStore, OpenSearchSettings
from fetchcore.knowledge.evidence import KnowledgeEvidence
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.application.navigation import RepositoryKnowledgeNavigationService
from fetchcore.repository.navigation import StructuralNavigationError
from fetchcore.adapters.retrieval.solr.backend import corpus_checksum


REPOSITORY = "bench/structure"
REVISION = "rev-1"


class IndexedTransport:
    """Deterministic keyword postings for measuring client transfer only."""

    def __init__(self, records: tuple[dict[str, object], ...]) -> None:
        self.records = tuple(sorted(records, key=lambda row: str(row["id"])))
        self.by_field: dict[tuple[str, str], tuple[dict[str, object], ...]] = {}
        start = time.perf_counter()
        postings: dict[tuple[str, str], list[dict[str, object]]] = {}
        for row in self.records:
            for field in ("structural_names", "structural_qualified_names",
                          "structural_identities", "structural_incoming_identities",
                          "source_ref"):
                raw = row.get(field, ())
                values = raw if isinstance(raw, list) else (raw,)
                for value in values:
                    if isinstance(value, str):
                        postings.setdefault((field, value), []).append(row)
        self.by_field = {key: tuple(value) for key, value in postings.items()}
        self.build_ms = (time.perf_counter() - start) * 1000
        self.requests = 0
        self.transferred = 0
        self.examined = 0

    def request(self, method: str, path: str, payload: object | None = None) -> dict[str, object]:
        assert method == "POST" and path.endswith("/_search") and isinstance(payload, dict)
        self.requests += 1
        filters = payload["query"]["bool"]["filter"]
        terms = {key: value for clause in filters for key, value in clause["term"].items()}
        selector = next((field for field in ("structural_names", "structural_qualified_names",
                                               "structural_identities", "structural_incoming_identities",
                                               "source_ref")
                         if field in terms), None)
        rows = self.records if selector is None else self.by_field.get(
            (selector, str(terms[selector])), (),
        )
        after = payload.get("search_after")
        if isinstance(after, list):
            rows = tuple(row for row in rows if str(row["id"]) > str(after[0]))
        self.examined += len(rows)
        page = tuple(row for row in rows if all(
            value in row.get(key, []) if isinstance(row.get(key), list)
            else row.get(key) == value for key, value in terms.items()
        ))[: int(payload["size"])]
        self.transferred += len(page)
        return {"hits": {"hits": [{"_source": row, "sort": [row["id"]]} for row in page]}}

    def reset(self) -> None:
        self.requests = self.transferred = self.examined = 0


def _documents(count: int) -> tuple[Any, ...]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "needle.py").write_text("def needle():\n    return 1\n")
        base = RepositoryIndexer(RepositoryIndexConfig(
            repository=REPOSITORY, revision=REVISION,
        )).index(root).documents[0]
    output = [base]
    for number in range(count - 1):
        path = f"noise/{number:05d}.txt"
        source_ref = f"repository://{REPOSITORY}/{path}"
        output.append(replace(
            base, id=f"synthetic-{number:05d}", path=path,
            filename=Path(path).name, source_ref=source_ref,
            chunk_ref=f"{source_ref}#chunk:0", language=None,
            content="unrelated technical text", symbols=(), symbol_types=(),
            evidence=KnowledgeEvidence(), metadata={},
            provenance=replace(base.provenance, path=path, source_ref=source_ref,
                               chunk_ref=f"{source_ref}#chunk:0"),
        ))
    return tuple(output)


def _summary(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {"p50_ms": round(statistics.median(samples), 3),
            "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * .95))], 3)}


def run(count: int, samples: int = 10) -> dict[str, object]:
    documents = _documents(count)
    store = OpenSearchIndexStore(
        None, OpenSearchSettings(index="bench"),  # type: ignore[arg-type]
        clock=lambda: "benchmark",
    )
    projected_start = time.perf_counter()
    records = store._project_documents(documents)
    projection_ms = (time.perf_counter() - projected_start) * 1000
    transport = IndexedTransport(records)
    store.transport = transport  # type: ignore[assignment]
    # This historical client-cost experiment deliberately excludes snapshot reads.
    store.load_snapshot = lambda _repository: SimpleNamespace(revision=REVISION)  # type: ignore[method-assign]
    results: dict[str, object] = {"documents": count,
                                  "projection_build_ms": round(projection_ms, 2),
                                  "keyword_postings_build_ms": round(transport.build_ms, 2)}
    for operation in ("find_symbol", "get_related"):
        for mode in ("previous", "targeted"):
            timings: list[float] = []
            candidates_examined = 0
            transport.reset()
            tracemalloc.start()
            for _ in range(samples):
                started = time.perf_counter()
                if mode == "previous":
                    candidates = store.load_documents(REPOSITORY)
                    corpus_checksum(candidates)
                else:
                    selectors = (("name", "needle"), ("qualified_name", "needle")) if (
                        operation == "find_symbol"
                    ) else (("source_ref", f"repository://{REPOSITORY}/needle.py"),)
                    pages = tuple(store.lookup_structural_documents(
                        REPOSITORY, REVISION, selector=selector, value=value, limit=128,
                    ) for selector, value in selectors)
                    candidates = tuple({document.id: document for page in pages
                                        for document in page.documents}.values())
                navigation = RepositoryKnowledgeNavigationService(candidates)
                candidates_examined += len(candidates)
                if operation == "find_symbol":
                    navigation.find_symbol("needle", limit=10)
                else:
                    try:
                        navigation.get_related(f"repository://{REPOSITORY}/needle.py", limit=10)
                    except StructuralNavigationError as exc:
                        if exc.category != "unsupported_capability":
                            raise
                timings.append((time.perf_counter() - started) * 1000)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            results[f"{operation}_{mode}"] = {
                **_summary(timings), "backend_requests": transport.requests // samples,
                "documents_transferred": transport.transferred // samples,
                "candidates_examined_by_navigation": candidates_examined // samples,
                "peak_tracemalloc_mib": round(peak / (1024 * 1024), 2),
            }
    update_started = time.perf_counter()
    last = dict(records[-1])
    store._project_documents((documents[-1],))
    transport.by_field[("structural_names", "replacement")] = (last,)
    results["one_document_projection_update_ms"] = round(
        (time.perf_counter() - update_started) * 1000, 4,
    )
    return results


if __name__ == "__main__":
    print(json.dumps({"method": "simulated keyword index, real OpenSearch adapter hydration; snapshot reads excluded",
                      "scales": [run(1_000), run(10_000)]}, indent=2))
