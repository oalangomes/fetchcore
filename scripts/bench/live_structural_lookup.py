"""Measure structural navigation through a live Solr, OpenSearch, or Qdrant service.

Run with ``python -m scripts.bench.live_structural_lookup --backend opensearch``
after starting the corresponding existing ``infra/compose`` service. The corpus
is synthetic Python source, indexed by the normal canonical indexer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fetchcore.adapters.retrieval.solr.backend import (
    SolrIndexStore, SolrSettings, UrllibSolrTransport,
)
from fetchcore.adapters.retrieval.solr.index_contract import bind_indexing_contract_store
from fetchcore.adapters.retrieval.opensearch.backend import (
    OpenSearchIndexStore, OpenSearchSettings, UrllibOpenSearchTransport,
)
from fetchcore.adapters.retrieval.qdrant.backend import (
    QdrantVectorIndexStore, QdrantVectorSettings, UrllibQdrantTransport,
)
from fetchcore.application import (
    FindSymbolRequest, GetRelatedRequest, RepositoryBinding, RetrievalApplicationService,
)
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.application.index_operations import prepare_repository_index_runtime


class _Embedder:
    provider_id = "structural-benchmark"
    model_id = "sha256-4"

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(tuple((hashlib.sha256(text.encode()).digest()[i] + 1) / 256
                           for i in range(4)) for text in texts)


class _MeasuredTransport:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls = 0
        self.backend_ms = 0.0
        self.documents = 0

    def request(
        self, method: str, path: str, payload: object | bytes | None = None,
        *, content_type: str | None = None,
    ) -> dict[str, object]:
        started = time.perf_counter()
        response: dict[str, object] = (
            self.inner.request(method, path, payload)
            if content_type is None else self.inner.request(
                method, path, payload, content_type=content_type,
            )
        )
        self._observe(response, started)
        return response

    def get(self, path: str, params: object | None = None) -> dict[str, object]:
        started = time.perf_counter()
        response: dict[str, object] = self.inner.get(path, params)
        self._observe(response, started)
        return response

    def post(self, path: str, payload: object, params: object | None = None) -> dict[str, object]:
        started = time.perf_counter()
        response: dict[str, object] = self.inner.post(path, payload, params)
        self._observe(response, started)
        return response

    def _observe(self, response: dict[str, object], started: float) -> None:
        self.backend_ms += (time.perf_counter() - started) * 1_000
        self.calls += 1
        hits = response.get("hits")
        rows: object = hits.get("hits") if isinstance(hits, dict) else None
        if rows is None:
            solr = response.get("response")
            rows = solr.get("docs") if isinstance(solr, dict) else None
        if rows is None:
            result = response.get("result")
            rows = result.get("points") if isinstance(result, dict) else result
        if isinstance(rows, list):
            self.documents += sum(
                isinstance(item, dict) and isinstance(
                    item.get("_source", item.get("payload", item)), dict,
                ) and item.get("_source", item.get("payload", item)).get("record_kind") == "knowledge"
                for item in rows
            )

    def reset(self) -> None:
        self.calls = 0
        self.backend_ms = 0.0
        self.documents = 0


def _store(backend: str, url: str):
    token = uuid4().hex[:10]
    clock = lambda: datetime.now(UTC).isoformat()
    if backend == "opensearch":
        settings = OpenSearchSettings.from_mapping({
            "url": url, "index": f"fetchcore-structural-bench-{token}",
        })
        return OpenSearchIndexStore(UrllibOpenSearchTransport(settings), settings, clock=clock)
    if backend == "solr":
        settings = SolrSettings.from_mapping({
            "url": url, "collection": "fetchcore", "projection_version": "3",
        })
        return SolrIndexStore(UrllibSolrTransport(settings), settings, clock=clock)
    settings = QdrantVectorSettings.from_mapping({
        "url": url, "collection": f"fetchcore-structural-bench-{token}",
        "embedding_model": "sha256-4", "vector_dimensions": 4,
    })
    return QdrantVectorIndexStore(
        UrllibQdrantTransport(settings), settings, _Embedder(), clock=clock,
    )


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50_ms": round(statistics.median(values), 3),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * .95))], 3),
    }


def run(backend: str, url: str, size: int, samples: int) -> dict[str, object]:
    repository = "bench/structure"
    revision = "rev-1"
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        target_path = workspace / "target.py"
        target_path.write_text("def run():\n    return 1\n", encoding="utf-8")
        (workspace / "caller.py").write_text(
            "from target import run as execute\ndef invoke():\n    execute()\n",
            encoding="utf-8",
        )
        for number in range(997):
            functions = 1 if size == 1_000 else 10 + (number >= 970)
            (workspace / f"work_{number:03}.py").write_text("".join(
                f"def task_{number}_{item}(value):\n    return value + {item}\n\n"
                for item in range(functions)
            ), encoding="utf-8")
        runtime = prepare_repository_index_runtime(
            workspace, repository=repository, branch=None, revision=revision,
        )
        indexer = runtime.indexer
        result = indexer.index(workspace)
        assert len(result.documents) == size
        store = _store(backend, url)
        if backend == "solr":
            store = bind_indexing_contract_store(store, indexer)
        store.indexing_fingerprint = indexing_contract_fingerprint(indexer)
        started = time.perf_counter()
        published = store.full_rebuild(result)
        build_ms = round((time.perf_counter() - started) * 1_000, 2)
        measured = _MeasuredTransport(store.transport)
        store.transport = measured  # type: ignore[assignment]
        app = RetrievalApplicationService(
            {repository: RepositoryBinding(
                repository, workspace,
                "qdrant-vector" if backend == "qdrant" else backend,
                revision=revision,
            )},
            navigation_store=lambda _binding: store,
        )
        target = app.find_symbol(FindSymbolRequest("target.run", repository)).results[0]
        caller = app.find_symbol(FindSymbolRequest("caller.invoke", repository)).results[0]
        output: dict[str, object] = {
            "documents": size,
            "full_projection_and_publication_ms": build_ms,
            "publication_telemetry": published.telemetry.to_mapping(),
        }
        for operation, request in (
            ("find_symbol", FindSymbolRequest("target.run", repository)),
            ("get_related", GetRelatedRequest(
                caller.identity, repository, relation_types=("calls",),
                require_resolution=True,
            )),
            ("get_related_incoming", GetRelatedRequest(
                target.identity, repository, relation_types=("calls",),
                direction="incoming",
            )),
        ):
            cold = []
            repeated = []
            backend_times = []
            app_only = []
            calls = []
            transferred = []
            tracemalloc.start()
            for index in range(samples + 1):
                measured.reset()
                started = time.perf_counter()
                response = (app.find_symbol(request) if isinstance(request, FindSymbolRequest)
                            else app.get_related(request))
                elapsed = (time.perf_counter() - started) * 1_000
                assert len(response.results) == 1, (operation, response.to_mapping())
                (cold if index == 0 else repeated).append(elapsed)
                if index > 0:
                    backend_times.append(measured.backend_ms)
                    app_only.append(max(0.0, elapsed - measured.backend_ms))
                    calls.append(measured.calls)
                    transferred.append(measured.documents)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            output[operation] = {
                "cold_ms": round(cold[0], 3),
                "repeated": _percentiles(repeated),
                "backend_transport": _percentiles(backend_times),
                "application_hydration": _percentiles(app_only),
                "backend_calls_per_query": sorted(set(calls)),
                "knowledge_documents_transferred_per_query": sorted(set(transferred)),
                "candidate_count": 1,
                "time_to_first_candidate_upper_bound_ms": _percentiles(repeated),
                "peak_tracemalloc_mib": round(peak / (1024 * 1024), 2),
            }
        target_path.write_text("def replacement():\n    return 2\n", encoding="utf-8")
        started = time.perf_counter()
        updated = indexer.index(workspace, previous_documents=result.documents)
        canonical_update_ms = (time.perf_counter() - started) * 1_000
        started = time.perf_counter()
        incremental = store.incremental_sync(updated)
        projection_update_ms = (time.perf_counter() - started) * 1_000
        assert not app.find_symbol(FindSymbolRequest("target.run", repository)).results
        assert app.find_symbol(FindSymbolRequest("replacement", repository)).results
        assert not app.get_related(GetRelatedRequest(
            caller.identity, repository, relation_types=("calls",),
            require_resolution=True,
        )).results
        output["incremental_update"] = {
            "canonical_reindex_ms": round(canonical_update_ms, 2),
            "projection_and_publication_ms": round(projection_update_ms, 2),
            "changed_documents": updated.telemetry.to_mapping(),
            "publication_telemetry": incremental.telemetry.to_mapping(),
            "stale_symbol_and_relation_removed": True,
        }
        return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("solr", "opensearch", "qdrant"), required=True)
    parser.add_argument("--url")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 2:
        parser.error("--samples must be at least 2")
    effective_url = args.url or {
        "solr": "http://127.0.0.1:8983/solr",
        "opensearch": "http://127.0.0.1:9200",
        "qdrant": "http://127.0.0.1:6333",
    }[args.backend]
    payload = {
        "method": "live server and HTTP transport; synthetic Python source indexed canonically",
        "backend": args.backend,
        "samples": args.samples,
        "scales": [run(args.backend, effective_url, size, args.samples)
                   for size in (1_000, 10_000)],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(args.output)
