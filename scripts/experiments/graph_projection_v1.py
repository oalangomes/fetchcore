"""Compare exact one-hop evidence on the pinned FetchCore corpus.

Run only against a disposable SolrCloud with both FetchCore configsets loaded.
All collections and aliases created here carry a unique ``fc30_bench`` prefix
and are removed in ``finally``. Existing collections are never modified.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import resource
import statistics
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import yaml

from fetchcore.adapters.retrieval.solr.backend import SolrSettings, UrllibSolrTransport
from fetchcore.adapters.retrieval.solr.cloud_lifecycle import (
    SolrCloudGenerationManager, UrllibSolrCloudCollectionsApi,
)
from fetchcore.adapters.retrieval.solr.index_contract import IndexContractSolrIndexStore
from fetchcore.application.graph_navigation import NavigateGraphRequest
from fetchcore.application.graph_publication import GraphPublicationService
from fetchcore.application.index_operations import prepare_repository_index_runtime
from fetchcore.application.navigation import GetRelatedRequest, RepositoryKnowledgeNavigationService
from fetchcore.application.service import RepositoryBinding, RetrievalApplicationService
from fetchcore.knowledge.graph_projection import project_graph
from fetchcore.repository.navigation import RelatedEvidence


PINNED_REVISION = "fa94f4ee456710f348d05f16425c5b5a016c867c"
REPOSITORY = "oalangomes/fetchcore"


def _latency(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50_ms": statistics.median(ordered),
        "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * .95))],
        "p99_ms": ordered[min(len(ordered) - 1, int(len(ordered) * .99))],
    }


def run(workspace: Path, solr_url: str, scenarios: Path, repetitions: int) -> dict[str, object]:
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True,
    ).strip()
    if revision != PINNED_REVISION:
        raise ValueError("benchmark workspace is not the pinned baseline revision")
    cases = yaml.safe_load(scenarios.read_text(encoding="utf-8"))["scenarios"]
    truth_case = next(case for case in cases if case["id"] == "resolved-call-exact-target")
    truth = truth_case["expected_relationships"][0]
    seed = truth_case["source"]
    suffix = uuid4().hex[:10]
    knowledge_alias = "fc30_bench_knowledge_" + suffix
    knowledge_collection = knowledge_alias + "_physical"
    graph_alias = knowledge_alias + "_graph"
    settings = SolrSettings(
        url=solr_url, collection=knowledge_alias, deployment_mode="solrcloud",
        configset="fetchcore-projection-v3",
    )
    api = UrllibSolrCloudCollectionsApi(settings)
    created: list[str] = []
    graph_collection: str | None = None

    def knowledge_store(collection: str) -> IndexContractSolrIndexStore:
        physical = replace(settings, collection=collection)
        store = IndexContractSolrIndexStore(UrllibSolrTransport(physical), physical)
        store.indexing_fingerprint = runtime.fingerprint
        return store

    try:
        runtime = prepare_repository_index_runtime(
            workspace, repository=REPOSITORY, branch=None, revision=None,
        )
        start = time.monotonic()
        indexed = runtime.indexer.index(workspace)
        extraction_seconds = time.monotonic() - start
        start = time.monotonic()
        projection = project_graph(
            indexed.documents, generation="graph-" + suffix,
            indexing_fingerprint=runtime.fingerprint,
        )
        projection_seconds = time.monotonic() - start
        observations = tuple(relation for document in indexed.documents
                             for relation in document.evidence.relations.semantic)
        observed_kinds = Counter(relation.kind for relation in observations)
        resolved_observations = sum(
            relation.resolution in {"resolved", "exact-unique"}
            and relation.target_identity is not None
            for relation in observations
        )
        declarations = sum(len(document.evidence.declarations)
                           for document in indexed.documents)
        exact = tuple(edge for edge in projection.edges
                      if edge.source_identity == seed and edge.relation_kind == truth["kind"])
        expected = tuple(edge for edge in exact
                         if edge.target_identity == truth["target_identity"]
                         and edge.source_ref == truth["source_ref"]
                         and edge.line == truth["line"])
        if len(expected) != 1:
            raise AssertionError("pinned ground truth does not match exactly one graph edge")
        canonical = RepositoryKnowledgeNavigationService(indexed.documents)
        seed_definition = canonical.find_symbol(seed, limit=2)
        assert len(seed_definition) == 1
        canonical_related = canonical.get_related(
            seed_definition[0], relation_types=("calls",), limit=100,
        )
        canonical_matches = sum(
            isinstance(item, RelatedEvidence)
            and item.target_identity == truth["target_identity"] and item.line == truth["line"]
            for item in canonical_related
        )

        api.create_collection(knowledge_collection, settings.configset)
        created.append(knowledge_collection)
        start = time.monotonic()
        knowledge_store(knowledge_collection).full_rebuild(indexed)
        knowledge_write_seconds = time.monotonic() - start
        api.set_alias(knowledge_alias, knowledge_collection)
        manager = SolrCloudGenerationManager(
            settings, api=api, store_factory=knowledge_store,
        )
        publisher = GraphPublicationService(
            manager, replace(settings, collection=graph_alias,
                             configset="fetchcore-graph-v1"), api=api,
        )
        start = time.monotonic()
        publication = publisher.publish(projection)
        publication_seconds = time.monotonic() - start
        graph_collection = publication.active_collection
        application = RetrievalApplicationService(
            {REPOSITORY: RepositoryBinding(
                REPOSITORY, workspace, "solr", revision=revision,
                settings={"url": solr_url, "collection": knowledge_alias,
                          "deployment_mode": "solrcloud",
                          "configset": "fetchcore-projection-v3"},
            )},
            navigation_store=lambda _binding: knowledge_store(knowledge_collection),
            graph_publication=lambda _binding: publisher,
        )
        baseline_times: list[float] = []
        graph_times: list[float] = []
        baseline_result = None
        graph_result = None
        for _ in range(repetitions):
            start = time.monotonic()
            baseline_result = application.get_related(GetRelatedRequest(
                seed, repository=REPOSITORY, revision=revision,
                relation_types=("calls",), require_resolution=True,
            ))
            baseline_times.append((time.monotonic() - start) * 1000)
            start = time.monotonic()
            graph_result = application.navigate_graph(NavigateGraphRequest(
                seed, repository=REPOSITORY, revision=revision,
                relation_types=("calls",), depth=1,
            ))
            graph_times.append((time.monotonic() - start) * 1000)
        assert baseline_result is not None and graph_result is not None
        baseline_hits = sum(
            isinstance(item, RelatedEvidence)
            and item.target_identity == truth["target_identity"] and item.line == truth["line"]
            for item in baseline_result.results
        )
        graph_hits = sum(edge.id == expected[0].id for edge in graph_result.edges)
        if baseline_hits != 1 or graph_hits != 1:
            raise AssertionError("baseline or graph failed independent pinned ground truth")
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        return {
            "corpus": REPOSITORY, "revision": revision,
            "ground_truth": {"seed": seed, "target": truth["target_identity"],
                             "source_ref": truth["source_ref"], "line": truth["line"]},
            "canonical": {"documents": len(indexed.documents),
                          "declarations": declarations,
                          "semantic_observations": len(observations),
                          "resolved_observations": resolved_observations,
                          "inconclusive_observations": len(observations) - resolved_observations,
                          "observation_kinds": dict(sorted(observed_kinds.items())),
                          "one_hop_observations": len(canonical_related),
                          "ground_truth_matches": canonical_matches},
            "projection": {"edges": len(projection.edges),
                           "seed_edges": len(exact), "serialized_bytes": len(projection.serialize()),
                           "build_seconds": projection_seconds},
            "baseline_one_hop": {"ground_truth_matches": baseline_hits,
                                 "returned": len(baseline_result.results),
                                 "status": baseline_result.status,
                                 "latency": _latency(baseline_times)},
            "graph_one_hop": {"ground_truth_matches": graph_hits,
                              "returned": len(graph_result.edges),
                              "status": graph_result.status,
                              "backend_queries": graph_result.backend_queries,
                              "backend_qtime_ms": graph_result.backend_qtime_ms,
                              "paths": len(graph_result.paths),
                              "inconclusive_in_map": len(graph_result.inconclusive_observations),
                              "diagnostics": list(graph_result.diagnostics),
                              "response_bytes": len(json.dumps(graph_result.to_mapping()).encode()),
                              "latency": _latency(graph_times)},
            "cost": {"extraction_seconds": extraction_seconds,
                     "knowledge_write_seconds": knowledge_write_seconds,
                     "graph_publication_seconds": publication_seconds,
                     "process_peak_rss_bytes": peak},
            "isolation": {"knowledge_collection": knowledge_collection,
                          "graph_collection": graph_collection},
        }
    finally:
        aliases = api.list_aliases()
        for alias in (graph_alias, knowledge_alias):
            if alias in aliases:
                api.delete_alias(alias)
        if graph_collection is not None:
            created.append(graph_collection)
        for collection in created:
            if collection in api.list_collections():
                api.delete_collection(collection)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--solr-url", required=True)
    parser.add_argument("--scenarios", type=Path, default=Path("dogfood/scenarios/fetchcore-related.yaml"))
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    result = run(args.workspace.resolve(), args.solr_url, args.scenarios, args.repetitions)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
