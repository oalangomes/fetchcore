"""Isolated BENCH projection and retrieval run; removes only its own resources."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from fetchcore.adapters.embedding.ollama.provider import (
    OllamaEmbeddingProvider, OllamaEmbeddingSettings,
)
from fetchcore.adapters.retrieval.opensearch.backend import (
    OpenSearchIndexStore, OpenSearchSettings, UrllibOpenSearchTransport,
)
from fetchcore.adapters.retrieval.qdrant.backend import (
    QdrantVectorIndexStore, QdrantVectorSettings, UrllibQdrantTransport,
    _writer_lock_collection,
)
from fetchcore.adapters.retrieval.solr.backend import (
    SolrIndexStore, SolrSettings, UrllibSolrTransport,
)
from fetchcore.adapters.retrieval.solr.index_contract import (
    bind_indexing_contract_store, snapshot_indexing_fingerprint, snapshot_mapping,
)
from fetchcore.application.composition import BackendRegistry, EmbeddingProviderRegistry
from fetchcore.projection.core import ProjectionV3Compiler
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.retrieval.config import RetrievalConfig
from fetchcore.retrieval.contracts import SearchIntent, SearchScope
from fetchcore.retrieval.embedding import ProjectionV3EmbeddingSerializer

from neurotrack_acceptance import TRUTH, preflight


class RunEmbeddingCache:
    """One bounded Ollama vector set reused across the three BENCH projections."""

    provider_id = "ollama"
    model_id = "embeddinggemma"

    def __init__(self, provider: OllamaEmbeddingProvider) -> None:
        self.provider = provider
        self.vectors: dict[str, tuple[float, ...]] = {}
        self.remote_vectors = 0
        self.remote_batches = 0
        self.cache_hits = 0
        self.remote_duration_ms = 0.0

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        missing = list(dict.fromkeys(text for text in texts if text not in self.vectors))
        for offset in range(0, len(missing), 4):
            batch = tuple(missing[offset:offset + 4])
            started = time.perf_counter()
            vectors = self.provider.embed(batch)
            self.remote_duration_ms += (time.perf_counter() - started) * 1000
            self.remote_batches += 1
            self.remote_vectors += len(vectors)
            self.vectors.update(zip(batch, vectors, strict=True))
            if offset + 4 < len(missing):
                time.sleep(.05)
        self.cache_hits += len(texts) - len(missing)
        return tuple(self.vectors[text] for text in texts)

    def summary(self) -> dict[str, object]:
        return {
            "provider": self.provider_id,
            "model": self.model_id,
            "remote_vectors": self.remote_vectors,
            "remote_batches": self.remote_batches,
            "remote_duration_ms": round(self.remote_duration_ms, 3),
            "cache_hits": self.cache_hits,
            "unique_cached_vectors": len(self.vectors),
            "provider_batch_size": 4,
            "inter_batch_delay_ms": 50,
        }


def _http(method: str, url: str, body: object | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode()
    request = Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urlopen(request, timeout=120) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("backend returned a non-object response")
    return value


def _solr_cores(url: str) -> set[str]:
    payload = _http("GET", url + "/admin/cores?action=STATUS&wt=json")
    return set(payload["status"])


def _os_indexes(url: str) -> set[str]:
    request = Request(url + "/_cat/indices?format=json&h=index")
    with urlopen(request, timeout=30) as response:
        rows = json.load(response)
    return {str(item["index"]) for item in rows}


def _qdrant_collections(url: str) -> set[str]:
    payload = _http("GET", url + "/collections")
    return {str(item["name"]) for item in payload["result"]["collections"]}


def _qdrant_aliases(url: str) -> set[str]:
    payload = _http("GET", url + "/aliases")
    return {str(item["alias_name"]) for item in payload["result"]["aliases"]}


def _create_solr_core(url: str, resource: str) -> None:
    params = urlencode({"action": "CREATE", "name": resource,
                        "configSet": "fetchcore", "wt": "json"}).encode()
    request = Request(
        url + "/admin/cores", data=params, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(request, timeout=120) as response:
        result = json.load(response)
    if result.get("responseHeader", {}).get("status") != 0:
        raise RuntimeError("Solr did not create the isolated core")


def _cleanup(*, solr_url: str, opensearch_url: str, qdrant_url: str,
             resource: str, lock_collection: str,
             prior_qdrant: set[str]) -> dict[str, Any]:
    errors = []
    removed: dict[str, list[str]] = {"solr": [], "opensearch": [], "qdrant": []}
    try:
        if resource in _solr_cores(solr_url):
            params = urlencode({"action": "UNLOAD", "core": resource,
                                "deleteIndex": "true", "deleteDataDir": "true",
                                "deleteInstanceDir": "true", "wt": "json"}).encode()
            request = Request(
                solr_url + "/admin/cores", data=params, method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urlopen(request, timeout=120) as response:
                result = json.load(response)
            if result.get("responseHeader", {}).get("status") != 0:
                raise RuntimeError("Solr core unload failed")
            removed["solr"].append(resource)
    except Exception as exc:  # noqa: BLE001 - cleanup must continue on backend failure
        errors.append(f"solr: {type(exc).__name__}: {exc}")
    try:
        for index in sorted(_os_indexes(opensearch_url)):
            if index.startswith(resource + "-"):
                _http("DELETE", opensearch_url + "/" + quote(index))
                removed["opensearch"].append(index)
    except Exception as exc:  # noqa: BLE001 - cleanup must continue on backend failure
        errors.append(f"opensearch: {type(exc).__name__}: {exc}")
    try:
        for alias in sorted(_qdrant_aliases(qdrant_url)):
            if alias.startswith(resource + "-"):
                _http("POST", qdrant_url + "/aliases", {
                    "actions": [{"delete_alias": {"alias_name": alias}}],
                })
        for collection in sorted(_qdrant_collections(qdrant_url)):
            if (collection.startswith(resource + "-")
                    or (collection == lock_collection and collection not in prior_qdrant)):
                _http("DELETE", qdrant_url + "/collections/" + quote(collection))
                removed["qdrant"].append(collection)
    except Exception as exc:  # noqa: BLE001 - cleanup must continue on backend failure
        errors.append(f"qdrant: {type(exc).__name__}: {exc}")
    remaining: dict[str, list[str]] = {}
    checks: dict[str, Callable[[], list[str]]] = {
        "solr": lambda: sorted(name for name in _solr_cores(solr_url)
                               if name == resource),
        "opensearch": lambda: sorted(name for name in _os_indexes(opensearch_url)
                                     if name.startswith(resource + "-")),
        "qdrant": lambda: sorted(name for name in _qdrant_collections(qdrant_url)
                                 if name.startswith(resource + "-")
                                 or name == lock_collection and name not in prior_qdrant),
        "qdrant_aliases": lambda: sorted(name for name in _qdrant_aliases(qdrant_url)
                                         if name.startswith(resource + "-")),
    }
    for name, check in checks.items():
        try:
            remaining[name] = check()
        except Exception as exc:  # noqa: BLE001 - persist cleanup diagnostics
            remaining[name] = []
            errors.append(f"{name} verification: {type(exc).__name__}: {exc}")
    if any(remaining.values()):
        errors.append("issue-owned resources remain after cleanup")
    return {"removed": removed, "remaining": remaining, "errors": errors}


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(fraction * len(ordered)) - 1)], 3)


def _retrieval(mode: str) -> RetrievalConfig | None:
    if mode == "lexical":
        return None
    return RetrievalConfig.from_mapping({
        "dense": {"enabled": True,
                  "embedding": {"provider": "ollama", "model": "embeddinggemma",
                                "dimensions": 768}},
        **({"hybrid": {"enabled": True, "strategy": "rrf"}}
           if mode == "hybrid" else {}),
    })


def _measure_retrieval(*, workspace: Path, resource: str, endpoints: dict[str, str],
                       expected_ids: dict[str, str], repeats: int,
                       embedder: RunEmbeddingCache) -> dict[str, object]:
    registry = BackendRegistry(providers=EmbeddingProviderRegistry(
        factories={"ollama": lambda _embedding, _settings: embedder},
    ))
    arms = {
        "solr": ("lexical", "dense", "hybrid"),
        "opensearch": ("lexical", "dense", "hybrid"),
        "qdrant-vector": ("dense", "hybrid"),
    }
    settings = {
        "solr": {"url": endpoints["solr"], "collection": resource,
                 "diagnostic_candidate_window": 80},
        "opensearch": {"url": endpoints["opensearch"], "index": resource,
                       "diagnostic_candidate_window": 80},
        "qdrant-vector": {"url": endpoints["qdrant"], "collection": resource,
                           "embedding_url": endpoints["ollama"],
                           "embedding_model": "embeddinggemma",
                           "vector_dimensions": 768,
                           "diagnostic_candidate_window": 80},
    }
    output: dict[str, object] = {}
    for backend_name, modes in arms.items():
        backend = registry.create(backend_name)
        for mode in modes:
            scope = SearchScope(
                workspace=workspace, revision=str(TRUTH["revision"]),
                repository=str(TRUTH["repository"]),
                knowledge_types=("code", "documentation", "configuration"),
                max_results=20, settings=settings[backend_name],
                retrieval=_retrieval(mode),
            )
            cases = []
            durations: list[float] = []
            for case in TRUTH["queries"]:
                intent = SearchIntent(
                    exact_terms=tuple(case["exact_terms"]),
                    semantic_terms=tuple(case["semantic_terms"]),
                )
                observations = []
                for _ in range(repeats):
                    began = time.perf_counter()
                    response = backend.search(intent, scope, ())
                    durations.append((time.perf_counter() - began) * 1000)
                    ranking = [hit.id for hit in response.results]
                    expected = expected_ids[str(case["id"])]
                    rank = ranking.index(expected) + 1 if expected in ranking else None
                    total_chars = sum(len(hit.content) for hit in response.results)
                    relevant_chars = sum(len(hit.content) for hit in response.results
                                         if hit.id == expected)
                    budgeted = []
                    used = 0
                    for hit in response.results:
                        if used + len(hit.content) > 4000:
                            break
                        budgeted.append(hit)
                        used += len(hit.content)
                    budgeted_useful = sum(len(hit.content) for hit in budgeted
                                          if hit.id == expected)
                    coverage = None
                    if "line_start" in case:
                        required = set(range(case["line_start"], case["line_end"] + 1))
                        covered: set[int] = set()
                        for hit in response.results:
                            if (hit.source == case["path"] and hit.line_start is not None
                                    and hit.line_end is not None):
                                covered.update(range(hit.line_start, hit.line_end + 1))
                        coverage = len(required & covered) / len(required)
                    observations.append({
                        "rank": rank,
                        "recall_at_20": int(rank is not None),
                        "mrr": 0 if rank is None else 1 / rank,
                        "ndcg_at_20": 0 if rank is None else 1 / math.log2(rank + 1),
                        "span_coverage_at_20": coverage,
                        "useful_evidence_density": relevant_chars / total_chars if total_chars else 0,
                        "context_yield_4000_chars": budgeted_useful / used if used else 0,
                        "candidates_examined": (
                            response.telemetry.candidates_examined if response.telemetry else None
                        ),
                        "returned_chars": total_chars,
                    })
                cases.append({"id": case["id"], "observations": observations})
            output[f"{backend_name}-{mode}"] = {
                "cases": cases,
                "latency_ms": {"p50": _percentile(durations, .5),
                               "p95": _percentile(durations, .95),
                               "p99": _percentile(durations, .99)},
                "repetitions_per_case": repeats,
            }
    return output


def run(args: argparse.Namespace) -> dict[str, object]:
    workspace = args.workspace.resolve(strict=True)
    preflight_summary, canonical, indexer, expected_ids = preflight(workspace)
    resource = "fc-issue181-" + uuid.uuid4().hex[:8]
    endpoints = {
        "solr": args.solr_url.rstrip("/"),
        "opensearch": args.opensearch_url.rstrip("/"),
        "qdrant": args.qdrant_url.rstrip("/"),
        "ollama": args.ollama_url.rstrip("/"),
    }
    before = {
        "solr": _solr_cores(endpoints["solr"]),
        "opensearch": _os_indexes(endpoints["opensearch"]),
        "qdrant": _qdrant_collections(endpoints["qdrant"]),
        "qdrant_aliases": _qdrant_aliases(endpoints["qdrant"]),
    }
    if any(name.startswith("fc-issue181-") for names in before.values() for name in names):
        raise RuntimeError("prior issue-181 resource exists; inspect before another run")
    qsettings = QdrantVectorSettings.from_mapping({
        "url": endpoints["qdrant"], "collection": resource,
        "embedding_url": endpoints["ollama"], "embedding_model": "embeddinggemma",
        "vector_dimensions": 768, "batch_size": 200,
    })
    lock_collection = _writer_lock_collection(qsettings, str(TRUTH["repository"]))
    record: dict[str, Any] = {
        "protocol": TRUTH["protocol"],
        "resource": resource,
        "preflight": preflight_summary,
        "endpoints": endpoints,
        "performance_comparable": False,
        "indexing": {},
        "retrieval": {},
    }
    error: BaseException | None = None
    provider = OllamaEmbeddingProvider(OllamaEmbeddingSettings(
        url=endpoints["ollama"], model="embeddinggemma",
        timeout_seconds=120, request_batch_size=4,
    ))
    embedder = RunEmbeddingCache(provider)
    try:
        serializer = ProjectionV3EmbeddingSerializer()
        compiler = ProjectionV3Compiler()
        texts = tuple(serializer.serialize_record(compiler.compile(document))
                      for document in canonical.documents)
        print(f"embedding=precompute documents={len(texts)}", flush=True)
        embedder.embed(texts)
        record["embedding"] = embedder.summary()
        _create_solr_core(endpoints["solr"], resource)
        ssettings = SolrSettings.from_mapping({
            "url": endpoints["solr"], "collection": resource,
            "timeout_seconds": 120,
        })
        solr = bind_indexing_contract_store(SolrIndexStore(
            UrllibSolrTransport(ssettings), ssettings, embedding_provider=embedder,
        ), indexer)
        solr.validate_schema(require_vectors=True)
        osettings = OpenSearchSettings.from_mapping({
            "url": endpoints["opensearch"], "index": resource,
            "timeout_seconds": 120,
        })
        clock = lambda: datetime.now(UTC).isoformat()
        opensearch = OpenSearchIndexStore(
            UrllibOpenSearchTransport(osettings), osettings,
            clock=clock, embedding_provider=embedder,
        )
        opensearch.indexing_fingerprint = indexing_contract_fingerprint(indexer)
        qdrant = QdrantVectorIndexStore(
            UrllibQdrantTransport(qsettings), qsettings, embedder, clock=clock,
        )
        qdrant.indexing_fingerprint = indexing_contract_fingerprint(indexer)
        stores: dict[str, Any] = {
            "solr": solr, "opensearch": opensearch, "qdrant": qdrant,
        }
        for name, store in stores.items():
            print(f"indexing={name}", flush=True)
            result = store.full_rebuild(canonical)
            snapshot = store.load_snapshot(str(TRUTH["repository"]))
            actual = store.load_documents(str(TRUTH["repository"]))
            expected_set = {(doc.id, doc.checksum, doc.path, doc.revision)
                            for doc in canonical.documents}
            actual_set = {(doc.id, doc.checksum, doc.path, doc.revision)
                          for doc in actual}
            if actual_set != expected_set:
                raise AssertionError(f"{name} record set differs from canonical")
            observed_fingerprint = (
                snapshot_indexing_fingerprint(snapshot) if name == "solr"
                else snapshot.indexing_fingerprint
            )
            if observed_fingerprint != indexing_contract_fingerprint(indexer):
                raise AssertionError(f"{name} snapshot has wrong indexing fingerprint")
            record["indexing"][name] = {
                "result": result.to_mapping(),
                "snapshot": snapshot_mapping(snapshot) if name == "solr"
                else snapshot.to_mapping(),
                "record_count": len(actual),
                "record_set_sha256": hashlib.sha256(json.dumps(
                    sorted(actual_set), separators=(",", ":"),
                ).encode()).hexdigest(),
            }
        print("retrieval=begin", flush=True)
        record["retrieval"] = _measure_retrieval(
            workspace=workspace, resource=resource, endpoints=endpoints,
            expected_ids=expected_ids, repeats=args.repeats, embedder=embedder,
        )
    except BaseException as exc:  # noqa: BLE001 - persist failure and always clean up
        error = exc
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        record["embedding"] = embedder.summary()
        record["cleanup"] = _cleanup(
            solr_url=endpoints["solr"], opensearch_url=endpoints["opensearch"],
            qdrant_url=endpoints["qdrant"], resource=resource,
            lock_collection=lock_collection, prior_qdrant=before["qdrant"],
        )
        record["status"] = (
            "PASS" if error is None and not record["cleanup"]["errors"] else "FAIL"
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, indent=2, sort_keys=True, default=str) + "\n")
        print(f"result={args.output} status={record['status']}", flush=True)
    if error is not None:
        raise error
    if record["cleanup"]["errors"]:
        raise RuntimeError(f"issue-owned resource cleanup failed: {record['cleanup']['errors']}")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--solr-url", required=True)
    parser.add_argument("--opensearch-url", required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--ollama-url", required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--authorized-temporary-write", action="store_true")
    args = parser.parse_args()
    if not args.authorized_temporary_write:
        parser.error("temporary backend writes require explicit operator authorization")
    if not 1 <= args.repeats <= 30:
        parser.error("repeats must be between 1 and 30")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
