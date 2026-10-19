"""Attest canonical evidence offline; execute isolated backend arms only when authorized."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
from typing import Any

from fetchcore.adapters.embedding.ollama.provider import OllamaEmbeddingProvider, OllamaEmbeddingSettings
from fetchcore.adapters.retrieval.opensearch.backend import (
    OPENSEARCH_VECTOR_DIMENSIONS, OpenSearchIndexStore, OpenSearchSettings,
    UrllibOpenSearchTransport, _repository_alias as _opensearch_alias,
)
from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorIndexStore, QdrantVectorSettings, UrllibQdrantTransport, _writer_lock_collection
from fetchcore.adapters.retrieval.solr.backend import (
    SOLR_VECTOR_DIMENSIONS, SolrIndexStore, SolrSettings, UrllibSolrTransport,
)
from fetchcore.adapters.retrieval.solr.index_contract import bind_indexing_contract_store, snapshot_indexing_fingerprint, snapshot_mapping
from fetchcore.application.composition import BackendRegistry, EmbeddingProviderRegistry
from fetchcore.projection.core import ProjectionV3Compiler, corpus_checksum
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import RepositoryIndexConfig
from fetchcore.repository.postman_indexing import ApiContractRepositoryIndexer
from fetchcore.retrieval.config import RetrievalConfig
from fetchcore.retrieval.contracts import Anchor, ExactLookupBackend, SearchIntent, SearchScope
from fetchcore.retrieval.embedding import ProjectionV3EmbeddingSerializer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/acceptance/acquisition"))
from typed_knowledge import TRUTH_PATH, preflight
from metrics import evaluate


class RunEmbeddingCache:
    """Reuse identical serialized vectors within one fixed provider/model run."""

    def __init__(self, provider: OllamaEmbeddingProvider, *, provider_id: str, model_id: str) -> None:
        self.provider = provider
        self.provider_id = provider_id
        self.model_id = model_id
        self.vectors: dict[str, tuple[float, ...]] = {}
        self.generated = 0
        self.reused = 0
        self.duration_ms = 0.0

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        missing = tuple(dict.fromkeys(text for text in texts if text not in self.vectors))
        if missing:
            started = time.perf_counter()
            vectors = self.provider.embed(missing)
            self.duration_ms += (time.perf_counter() - started) * 1000
            if len(vectors) != len(missing):
                raise RuntimeError("embedding provider returned an incomplete vector set")
            self.vectors.update(zip(missing, vectors, strict=True))
            self.generated += len(missing)
        self.reused += len(texts) - len(missing)
        return tuple(self.vectors[text] for text in texts)

    def to_mapping(self) -> dict[str, object]:
        return {"provider": self.provider_id, "model": self.model_id,
                "generated": self.generated, "reused": self.reused,
                "cache_hit_rate": self.reused / (self.generated + self.reused) if self.generated + self.reused else None,
                "remote_duration_ms": round(self.duration_ms, 3),
                "cost": None}


def _http(method: str, url: str, body: object | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, method=method,
                      headers={"Content-Type": "application/json"} if data else {})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def _inventory(endpoints: dict[str, str]) -> dict[str, set[str]]:
    solr = _http("GET", endpoints["solr"] + "/admin/cores?action=STATUS&wt=json")
    opensearch = _http("GET", endpoints["opensearch"] + "/_cat/indices?format=json&h=index")
    qdrant = _http("GET", endpoints["qdrant"] + "/collections")
    aliases = _http("GET", endpoints["qdrant"] + "/aliases")
    return {
        "solr": set(solr["status"]),
        "opensearch": {str(item["index"]) for item in opensearch},
        "qdrant": {str(item["name"]) for item in qdrant["result"]["collections"]},
        "qdrant_aliases": {str(item["alias_name"]) for item in aliases["result"]["aliases"]},
    }


def _verify_ownership(config: dict[str, Any]) -> dict[str, object]:
    """Match exact local BENCH container identity and published endpoint ports."""
    approval = config["authorization"]
    required = ("reference", "approved_by", "owner", "scope", "compose_project")
    if approval.get("status") != "approved" or any(not approval.get(key) for key in required):
        raise RuntimeError("explicit operational write authorization is missing")
    if approval.get("service_class") != "BENCH":
        raise RuntimeError("authorization must identify BENCH services")
    endpoints = config["endpoints"]
    containers = config["containers"]
    observed: dict[str, object] = {}
    for backend in ("solr", "opensearch", "qdrant"):
        name = containers[backend]
        result = subprocess.run(["docker", "inspect", name], text=True,
                                capture_output=True, check=True)
        item = json.loads(result.stdout)[0]
        labels = item["Config"]["Labels"]
        if labels.get("com.docker.compose.project") != approval["compose_project"] or labels.get("com.docker.compose.service") != backend:
            raise RuntimeError(f"{backend} container ownership differs from authorization")
        parsed = urlparse(endpoints[backend])
        if parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise RuntimeError("remote endpoint cannot be tied to local BENCH container")
        published = {binding["HostPort"] for rows in item["NetworkSettings"]["Ports"].values()
                     if rows for binding in rows if binding["HostIp"] in {"127.0.0.1", "0.0.0.0", "::"}}
        if str(parsed.port) not in published:
            raise RuntimeError(f"{backend} endpoint port does not match inspected container")
        mounts = item.get("Mounts", [])
        if not mounts or not all(m.get("Name", "").startswith(approval["compose_project"] + "_")
                                 for m in mounts if m.get("Type") == "volume"):
            raise RuntimeError(f"{backend} volume ownership is not attested")
        observed[backend] = {"container_id": item["Id"], "image": item["Image"],
                             "mounts": [{"type": m.get("Type"), "name": m.get("Name"),
                                         "destination": m.get("Destination")} for m in mounts],
                             "endpoint": endpoints[backend]}
    return observed


def _create_solr_core(url: str, name: str, configset: str) -> None:
    payload = urlencode({"action": "CREATE", "name": name, "configSet": configset,
                         "wt": "json"}).encode("utf-8")
    request = Request(url + "/admin/cores", data=payload, method="POST",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(request, timeout=120) as response:
        result = json.load(response)
    if result.get("responseHeader", {}).get("status") != 0:
        raise RuntimeError("Solr did not create the isolated core")


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(fraction * len(ordered)) - 1)], 3)


def _retrieval_config(mode: str, embedding: dict[str, Any]) -> RetrievalConfig | None:
    if mode == "lexical":
        return None
    if mode not in {"dense", "hybrid"}:
        raise ValueError(f"unsupported retrieval mode: {mode}")
    return RetrievalConfig.from_mapping({
        "dense": {"enabled": True, "embedding": {"provider": embedding["provider"],
                  "model": embedding["model"], "dimensions": embedding["dimensions"]}},
        **({"hybrid": {"enabled": True, "strategy": "rrf"}} if mode == "hybrid" else {}),
    })


def _attest(store: Any, *, repository: str, revision: str, expected_documents: tuple[Any, ...],
            indexing_fingerprint: str, projection_fingerprint: str,
            embedding: dict[str, Any], backend: str) -> dict[str, object]:
    snapshot = store.load_snapshot(repository)
    actual = store.load_documents(repository)
    expected_set = {(d.id, d.checksum, d.path, d.revision, d.provenance.source_checksum)
                    for d in expected_documents}
    actual_set = {(d.id, d.checksum, d.path, d.revision, d.provenance.source_checksum)
                  for d in actual}
    if actual_set != expected_set or len(actual) != len(expected_documents):
        raise AssertionError(f"{backend} record set differs from canonical evidence")
    observed_indexing = snapshot_indexing_fingerprint(snapshot) if backend == "solr" else snapshot.indexing_fingerprint
    if observed_indexing != indexing_fingerprint:
        raise AssertionError(f"{backend} indexing fingerprint mismatch")
    if (snapshot.repository != repository or snapshot.revision != revision
            or snapshot.document_count != len(expected_documents)
            or snapshot.corpus_checksum != corpus_checksum(expected_documents)
            or snapshot.projection_fingerprint != projection_fingerprint
            or snapshot.embedding_provider != embedding["provider"]
            or snapshot.embedding_model != embedding["model"]
            or snapshot.vector_dimensions != embedding["dimensions"]):
        raise AssertionError(f"{backend} snapshot identity or vector contract mismatch")
    serializer_fingerprint = getattr(snapshot, "embedding_serializer_fingerprint", None)
    if serializer_fingerprint is None:
        serializer_fingerprint = getattr(snapshot, "serializer_fingerprint", None)
    if serializer_fingerprint != ProjectionV3EmbeddingSerializer().serializer_fingerprint:
        raise AssertionError(f"{backend} embedding serializer mismatch")
    mapping = snapshot_mapping(snapshot) if backend == "solr" else snapshot.to_mapping()
    if backend == "solr":
        generation = store.settings.collection
    elif backend == "qdrant-vector":
        generation = snapshot.active_collection
    else:
        generations = store._alias_indexes(_opensearch_alias(store.settings, repository))
        if len(generations) != 1:
            raise AssertionError("OpenSearch alias does not select one generation")
        generation = generations[0]
    return {"snapshot": mapping, "record_count": len(actual),
            "record_set_sha256": hashlib.sha256(json.dumps(sorted(actual_set), separators=(",", ":")).encode()).hexdigest(),
            "generation": generation}


def _query_arms(config: dict[str, Any], *, workspace: Path, truth: dict[str, Any],
                expected_ids: dict[str, str], resource: str,
                embedder: RunEmbeddingCache, output: dict[str, object]) -> None:
    registry = BackendRegistry(providers=EmbeddingProviderRegistry(
        factories={config["embedding"]["provider"]: lambda _embedding, _settings: embedder},
    ))
    cases = {case["id"]: {**case, "repository": truth["corpus"]["repository"]}
             for case in truth["acquisition_cases"] if case["expect"] == "present"}
    for arm in config["arms"]:
        backend_name, mode = arm["backend"], arm["mode"]
        backend = registry.create(backend_name)
        capabilities = backend.retrieval_capabilities
        supported = (capabilities.targeted_lookup if mode == "targeted" else
                     getattr(capabilities, mode, False))
        if not supported or (mode == "targeted" and not isinstance(backend, ExactLookupBackend)):
            raise RuntimeError(f"{backend_name} does not support {mode}")
        settings = dict(config["backend_settings"][backend_name])
        settings["url"] = config["endpoints"][backend_name if backend_name != "qdrant-vector" else "qdrant"]
        settings["collection" if backend_name in {"solr", "qdrant-vector"} else "index"] = resource
        if backend_name == "qdrant-vector":
            settings["embedding_url"] = config["endpoints"]["ollama"]
            settings["embedding_model"] = config["embedding"]["model"]
            settings["vector_dimensions"] = config["embedding"]["dimensions"]
        scope = SearchScope(workspace=workspace, revision=truth["corpus"]["revision"],
                            repository=truth["corpus"]["repository"],
                            knowledge_types=("code", "documentation", "configuration"),
                            max_results=config["measurement"]["k"], settings=settings,
                            retrieval=None if mode == "targeted" else
                            _retrieval_config(mode, config["embedding"]))
        observations = []
        durations = []
        for query in truth["ranking_ground_truth"]:
            case = cases[query["id"]]
            if mode == "targeted" and case["kind"] not in {"api_operation", "api_request"}:
                continue
            intent = SearchIntent(exact_terms=tuple(query["exact_terms"]),
                                  semantic_terms=tuple(query["semantic_terms"]))
            for repeat in range(config["measurement"]["repetitions"]):
                started = time.perf_counter()
                if mode == "targeted":
                    if not isinstance(backend, ExactLookupBackend):
                        raise RuntimeError("targeted lookup capability changed during execution")
                    response = backend.lookup_exact(Anchor("route", case["route"]), scope)
                else:
                    response = backend.search(intent, scope, ())
                durations.append((time.perf_counter() - started) * 1000)
                if response.status not in {"found", "not_found"}:
                    raise RuntimeError(f"{backend_name}/{mode} returned {response.status}")
                if any(hit.revision != truth["corpus"]["revision"] for hit in response.results):
                    raise AssertionError("retrieval returned a different revision")
                repository_prefix = "repository://" + quote(truth["corpus"]["repository"], safe="/:.@") + "/"
                if any(not hit.source_ref.startswith(repository_prefix) for hit in response.results):
                    raise AssertionError("retrieval returned a different repository")
                metrics = evaluate(response, case, expected_ids[query["id"]],
                                   k=config["measurement"]["k"])
                observations.append({"query_id": query["id"], "iteration": repeat + 1,
                                     "metrics": metrics,
                                     "ranked_ids": [hit.id for hit in response.results],
                                     "telemetry": response.telemetry.to_mapping() if response.telemetry else None})
        output[arm["id"]] = {"backend": backend_name, "mode": mode,
                              "effective_k": 2 if mode == "targeted" else config["measurement"]["k"],
                              "observations": observations,
                              "latency_ms": {"p50": _percentile(durations, .5),
                                             "p95": _percentile(durations, .95),
                                             "p99": _percentile(durations, .99)}}
def run(args: argparse.Namespace) -> dict[str, Any]:
    truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    canonical_summary, documents, expected_ids = preflight(args.corpus, args.ground_truth)
    record: dict[str, Any] = {
        "protocol": truth["protocol"], "status": "BLOCKED",
        "product_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "product_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
        "runner_sha256": hashlib.sha256((HERE / "run.py").read_bytes()).hexdigest(),
        "metrics_sha256": hashlib.sha256((HERE / "metrics.py").read_bytes()).hexdigest(),
        "ground_truth_sha256": canonical_summary["ground_truth_sha256"],
        "canonical": canonical_summary,
        "snapshot_attestation": None, "retrieval": None, "performance": None,
        "reason": "explicit operational authorization and isolated BENCH resources required",
    }
    if not args.execute_authorized:
        return record
    if args.config is None:
        raise RuntimeError("authorized execution requires an explicit configuration")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if config.get("cleanup_policy") != "retain":
        raise RuntimeError("only retain-and-review resource lifecycle is supported")
    if config["authorization"].get("resource_prefix") != "fc-typed-":
        raise RuntimeError("authorization does not cover generated typed resource names")
    if config["authorization"].get("embedding_endpoint") != config["endpoints"]["ollama"]:
        raise RuntimeError("embedding endpoint differs from authorization")
    arms = config["arms"]
    if not arms or len({arm["id"] for arm in arms}) != len(arms):
        raise RuntimeError("retrieval arms must be nonempty and uniquely identified")
    registry = BackendRegistry()
    for arm in arms:
        backend = registry.create(arm["backend"])
        mode = arm["mode"]
        supported = (backend.retrieval_capabilities.targeted_lookup if mode == "targeted"
                     else getattr(backend.retrieval_capabilities, mode, False))
        if mode not in {"lexical", "dense", "hybrid", "targeted"} or not supported:
            raise RuntimeError(f"unsupported retrieval arm: {arm['id']}")
    record["configuration_sha256"] = hashlib.sha256(args.config.read_bytes()).hexdigest()
    record["authorization_reference"] = config.get("authorization", {}).get("reference")
    record["service_identity"] = _verify_ownership(config)
    endpoints = {key: value.rstrip("/") for key, value in config["endpoints"].items()}
    before = _inventory(endpoints)
    resource = "fc-typed-" + uuid.uuid4().hex[:12]
    if any(name == resource or name.startswith(resource + "-")
           for names in before.values() for name in names):
        raise RuntimeError("new resource namespace collides with existing snapshot")
    record["resource"] = resource
    record["before_inventory"] = {key: sorted(names) for key, names in before.items()}
    repo, revision = truth["corpus"]["repository"], truth["corpus"]["revision"]
    indexer = ApiContractRepositoryIndexer(RepositoryIndexConfig(
        repository=repo, revision=revision,
        include_patterns=tuple(truth["corpus"]["source_sha256"])),
        resolve_local_references=True)
    canonical = indexer.index(args.corpus)
    if {(d.id, d.checksum) for d in canonical.documents} != {(d.id, d.checksum) for d in documents}:
        raise AssertionError("canonical corpus changed after read-only preflight")
    embedding = config["embedding"]
    if embedding["provider"] != "ollama":
        raise RuntimeError("configured embedding provider is unsupported by this runner")
    if (embedding["dimensions"] != SOLR_VECTOR_DIMENSIONS
            or embedding["dimensions"] != OPENSEARCH_VECTOR_DIMENSIONS):
        raise RuntimeError("embedding dimensions differ from configured backend vector schemas")
    provider = OllamaEmbeddingProvider(OllamaEmbeddingSettings(
        url=endpoints["ollama"], model=embedding["model"],
        timeout_seconds=embedding["timeout_seconds"],
        request_batch_size=embedding["batch_size"]))
    embedder = RunEmbeddingCache(provider, provider_id=embedding["provider"],
                                 model_id=embedding["model"])
    compiler = ProjectionV3Compiler()
    fingerprint = indexing_contract_fingerprint(indexer)
    record["projection_fingerprint"] = compiler.projection_fingerprint
    record["indexing_fingerprint"] = fingerprint
    record["embedding_serializer_fingerprint"] = ProjectionV3EmbeddingSerializer().serializer_fingerprint
    record["approval_scope"] = config["authorization"]["scope"]
    record["status"] = "FAIL"
    stores: dict[str, Any] = {}
    try:
        q_settings = QdrantVectorSettings.from_mapping({
            "url": endpoints["qdrant"], "collection": resource,
            "embedding_url": endpoints["ollama"], "embedding_model": embedding["model"],
            "vector_dimensions": embedding["dimensions"], "batch_size": config["index_batch_size"]})
        lock_collection = _writer_lock_collection(q_settings, repo)
        if lock_collection in before["qdrant"]:
            raise RuntimeError("Qdrant writer lock collection already exists")
        solr_settings = SolrSettings.from_mapping({"url": endpoints["solr"], "collection": resource})
        os_settings = OpenSearchSettings.from_mapping({"url": endpoints["opensearch"], "index": resource})
        _create_solr_core(endpoints["solr"], resource, config["solr_configset"])
        stores["solr"] = bind_indexing_contract_store(SolrIndexStore(
            UrllibSolrTransport(solr_settings), solr_settings, embedding_provider=embedder), indexer)
        stores["solr"].validate_schema(require_vectors=True)
        clock = lambda: datetime.now(UTC).isoformat()
        stores["opensearch"] = OpenSearchIndexStore(
            UrllibOpenSearchTransport(os_settings), os_settings, clock=clock,
            embedding_provider=embedder)
        stores["opensearch"].indexing_fingerprint = fingerprint
        stores["qdrant-vector"] = QdrantVectorIndexStore(
            UrllibQdrantTransport(q_settings), q_settings, embedder, clock=clock)
        stores["qdrant-vector"].indexing_fingerprint = fingerprint
        attestations: dict[str, object] = {}
        record["snapshot_attestation"] = attestations
        for name, store in stores.items():
            result = store.full_rebuild(canonical)
            attestation = _attest(store, repository=repo, revision=revision,
                                  expected_documents=documents, indexing_fingerprint=fingerprint,
                                  projection_fingerprint=compiler.projection_fingerprint,
                                  embedding=embedding, backend=name)
            duration_ms = result.duration_ms
            attestations[name] = {
                **attestation, "indexing_result": result.to_mapping(),
                "indexing_throughput_docs_per_second": (
                    round(len(documents) / (duration_ms / 1000), 3)
                    if duration_ms > 0 else None
                ),
            }
        record["retrieval"] = {}
        _query_arms(config, workspace=args.corpus, truth=truth,
                    expected_ids=expected_ids, resource=resource,
                    embedder=embedder, output=record["retrieval"])
        # Source deletion exercises incremental projection after ranking on the full corpus.
        with tempfile.TemporaryDirectory(prefix="typed-knowledge-") as temp:
            copy_root = Path(temp)
            for path in truth["corpus"]["source_sha256"]:
                target = copy_root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((args.corpus / path).read_bytes())
            removed_path = next(c["target_path"] for c in truth["relation_cases"]
                                if c["kind"] == "markdown_reference" and c["expect"] == "resolved")
            (copy_root / removed_path).unlink()
            changed = indexer.index(copy_root, previous_documents=documents)
            rebuilt = indexer.index(copy_root)
            if {(d.id, d.checksum) for d in changed.documents} != {(d.id, d.checksum) for d in rebuilt.documents}:
                raise AssertionError("incremental canonical state differs from rebuild")
            incremental: dict[str, object] = {}
            record["incremental"] = {"removed_source": removed_path,
                                      "canonical_removed": len(changed.changes.removed),
                                      "canonical_updated": len(changed.changes.updated),
                                      "backends": incremental}
            for name, store in stores.items():
                snapshot = store.load_snapshot(repo)
                outcome = (store.incremental_sync(changed) if name == "solr" else
                           store.incremental_sync(changed, expected_snapshot=snapshot))
                incremental[name] = {"result": outcome.to_mapping(),
                                     "attestation": _attest(store, repository=repo, revision=revision,
                                                             expected_documents=changed.documents,
                                                             indexing_fingerprint=fingerprint,
                                                             projection_fingerprint=compiler.projection_fingerprint,
                                                             embedding=embedding, backend=name)}
        record["status"] = "MEASURED"
        record["reason"] = "quality and performance promotion require reviewed thresholds and isolation evidence"
    except Exception as exc:
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        record["embedding"] = embedder.to_mapping()
        try:
            after = _inventory(endpoints)
            record["created_resources"] = {key: sorted(names - before[key]) for key, names in after.items()}
        except Exception as exc:  # noqa: BLE001 - persist inventory failure after a write
            record["inventory_error"] = {"type": type(exc).__name__, "message": str(exc)}
            record["status"] = "FAIL"
        record["performance_comparable"] = bool(config.get("isolation", {}).get("exclusive_host")
                                                and config.get("isolation", {}).get("evidence"))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--ground-truth", type=Path, default=TRUTH_PATH)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute-authorized", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output artifact already exists; choose a fresh path")
    try:
        result = run(args)
    except Exception as exc:
        if not args.output.exists():
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({"status": "FAIL", "error": {"type": type(exc).__name__, "message": str(exc)}}) + "\n")
        raise
    if not args.execute_authorized:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"status={result['status']} output={args.output}")
    return 0 if result["status"] in {"BLOCKED", "MEASURED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
