#!/usr/bin/env python3
"""Explicitly rebuild only the owned #84 BENCH resources before measurement."""

from __future__ import annotations

import argparse
import hashlib
import math
from pathlib import Path
import sqlite3
import struct
import time

import run as benchmark

from fetchcore.application.index_operations import prepare_repository_index_runtime
from fetchcore.adapters.embedding.ollama.provider import OllamaEmbeddingProvider
from fetchcore.adapters.retrieval.solr.backend import (
    SolrIndexStore,
    SolrSettings,
    UrllibSolrTransport,
)
from fetchcore.adapters.retrieval.opensearch.backend import (
    OpenSearchIndexStore,
    OpenSearchSettings,
    UrllibOpenSearchTransport,
)
from fetchcore.adapters.retrieval.qdrant.backend import (
    QdrantVectorIndexStore,
    QdrantVectorSettings,
    UrllibQdrantTransport,
)
from fetchcore.projection import projection_contract_fingerprint
from fetchcore.retrieval.embedding import (
    ProjectionEmbeddingMode,
    vector_embedding_serializer_fingerprint,
)


class SharedEmbeddingCache:
    """Preparation-only exact-input cache; never used in measured retrieval."""

    provider_id = "ollama"
    model_id = "embeddinggemma"

    def __init__(self, provider, path, identity):
        self.provider = provider
        self.identity = identity
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS vectors (identity TEXT, text_hash TEXT, vector BLOB, PRIMARY KEY(identity,text_hash))"
        )
        self.hits = self.misses = 0

    def embed(self, texts):
        keys = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
        existing = {
            key: self.db.execute(
                "SELECT vector FROM vectors WHERE identity=? AND text_hash=?", (self.identity, key)
            ).fetchone()
            for key in set(keys)
        }
        missing = list(dict.fromkeys(key for key in keys if existing[key] is None))
        source = dict(zip(keys, texts, strict=True))
        vectors = self.provider.embed(tuple(source[key] for key in missing)) if missing else ()
        if len(vectors) != len(missing):
            raise ValueError("embedding batch incomplete")
        for key, vector in zip(missing, vectors, strict=True):
            if len(vector) != 768 or any(not math.isfinite(v) for v in vector):
                raise ValueError("incompatible vector; cache write rejected")
            blob = struct.pack("<768d", *vector)
            self.db.execute("INSERT INTO vectors VALUES (?,?,?)", (self.identity, key, blob))
            existing[key] = (blob,)
        self.db.commit()
        self.misses += len(missing)
        self.hits += len(texts) - len(missing)
        print(f"embedding cache: new={self.misses} reused={self.hits}", flush=True)
        return tuple(struct.unpack("<768d", existing[key][0]) for key in keys)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--embedding-cache", type=Path, required=True)
    parser.add_argument("--execute-rebuild", action="store_true")
    args = parser.parse_args()
    if not args.execute_rebuild:
        parser.error("explicit --execute-rebuild required; no writes performed")
    benchmark.private_output(args.output)
    evidence, suite, _, identity, context, _ = benchmark.context_for(args, rebuild=True)
    tags = evidence.setup._http_json(context.endpoints["ollama"].rstrip("/") + "/api/tags")
    digest = next(
        m["digest"] for m in tags["models"] if m["name"].split(":")[0] == "embeddinggemma"
    )
    embedding_identity = {
        "provider": "ollama",
        "model": "embeddinggemma",
        "dimensions": 768,
        "digest": digest,
        "projection": projection_contract_fingerprint(),
        "serializer": vector_embedding_serializer_fingerprint(
            ProjectionEmbeddingMode.PROJECTION_V3
        ),
    }
    args.embedding_cache.parent.mkdir(parents=True, exist_ok=True)
    provider = SharedEmbeddingCache(
        OllamaEmbeddingProvider(
            url=context.endpoints["ollama"], model="embeddinggemma", timeout_seconds=120
        ),
        args.embedding_cache,
        benchmark.mapping_fingerprint(embedding_identity),
    )
    report = {
        "protocol": benchmark.PROTOCOL,
        "schema_version": 1,
        "stage": "authorized-rebuild",
        "status": "running",
        "identity": identity,
        "authorization": "User explicitly authorized BENCH projection rebuild for issue #84.",
        "embedding_identity": embedding_identity,
        "before": {},
        "backends": {},
        "snapshots": {},
    }
    try:
        adapters = evidence.setup.benchmark_backend_adapters()
        arms = [
            a
            for a in evidence.setup._manifest_arms(
                evidence.setup._load_json(evidence.setup.MANIFEST_PATH)
            )
            if a.mode == "hybrid"
        ]
        scopes = {
            a.backend: evidence.SearchScope(
                workspace=args.workspace,
                revision=suite.corpus["revision"],
                repository=suite.corpus["repository"],
                knowledge_types=(),
                max_results=20,
                settings=evidence.setup._backend_settings(a, context=context, adapters=adapters),
                retrieval=evidence.setup._retrieval_for_mode(
                    "hybrid", provider="ollama", model="embeddinggemma"
                ),
            )
            for a in arms
        }
        # Preserve typed old compatibility diagnostics before any index writes.
        for arm in arms:
            try:
                report["before"][arm.backend] = evidence.snapshot_identity(arm, scopes[arm.backend])
            except ValueError as exc:
                report["before"][arm.backend] = {
                    "error": str(exc),
                    "category": getattr(exc, "category", "failed"),
                    "details": getattr(exc, "metadata", {}),
                }
        runtime = prepare_repository_index_runtime(
            args.workspace,
            repository=suite.corpus["repository"],
            revision=suite.corpus["revision"],
            branch=None,
        )
        canonical = runtime.indexer.index(args.workspace)
        report["canonical"] = canonical.telemetry.to_mapping()
        report["indexing_fingerprint"] = runtime.fingerprint
        print("canonical indexed", len(canonical.documents), "documents", flush=True)
        stores = {
            "solr": SolrIndexStore(
                UrllibSolrTransport(SolrSettings.from_mapping(scopes["solr"].settings)),
                SolrSettings.from_mapping(scopes["solr"].settings),
                embedding_provider=provider,
            ),
            "opensearch": OpenSearchIndexStore(
                UrllibOpenSearchTransport(
                    OpenSearchSettings.from_mapping(scopes["opensearch"].settings)
                ),
                OpenSearchSettings.from_mapping(scopes["opensearch"].settings),
                embedding_provider=provider,
                clock=evidence.setup._utc_now,
            ),
            "qdrant-vector": QdrantVectorIndexStore(
                UrllibQdrantTransport(
                    QdrantVectorSettings.from_mapping(scopes["qdrant-vector"].settings)
                ),
                QdrantVectorSettings.from_mapping(scopes["qdrant-vector"].settings),
                provider,
                clock=evidence.setup._utc_now,
            ),
        }
        stores["solr"].validate_schema(require_vectors=True)
        for arm in arms:
            store = stores[arm.backend]
            store.indexing_fingerprint = runtime.fingerprint
            print("rebuilding", arm.backend, flush=True)
            started = time.monotonic()
            result = store.full_rebuild(canonical)
            report["backends"][arm.backend] = result.to_mapping()
            snapshot = evidence.snapshot_identity(arm, scopes[arm.backend])
            if snapshot["projection_fingerprint"] != embedding_identity["projection"] or snapshot[
                "document_count"
            ] != len(canonical.documents):
                raise ValueError("rebuilt snapshot differs from canonical projection")
            report["snapshots"][arm.backend] = snapshot
            print("ready", arm.backend, "seconds", round(time.monotonic() - started, 2), flush=True)
        checksums = {s["corpus_checksum"] for s in report["snapshots"].values()}
        if len(checksums) != 1:
            raise ValueError("rebuilt corpus checksums disagree")
        report.update(
            status="complete",
            embedding_cache_hits=provider.hits,
            embedding_cache_misses=provider.misses,
        )
    except Exception as exc:
        report.update(
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            details=getattr(exc, "metadata", {}),
        )
        benchmark.write(args.output, report)
        raise
    benchmark.write(args.output, report)


if __name__ == "__main__":
    main()
