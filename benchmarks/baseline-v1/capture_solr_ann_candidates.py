#!/usr/bin/env python3
"""Capture ordered Solr dense acquisition IDs on an existing BENCH snapshot.

The frozen baseline artifact records top-20 result spans. This read-only sidecar
records the full 80-candidate acquisition window once per original query.
Keep the output outside the public repository when using a private corpus.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import run_bench


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True,
                        help="Completed paired solr-dense.json for vector identity checks")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ann-search-breadth", type=int)
    args = parser.parse_args()

    frozen = run_bench.frozen
    if args.output.resolve().is_relative_to(frozen.ROOT.parents[1]):
        parser.error("private acquisition IDs must be written outside the repository")
    suite = frozen.load_baseline_suite(frozen.GROUND_TRUTH_PATH)
    config = run_bench.load_run_config(args.run_config, suite.corpus)
    if config["provisioning"] != "reuse_only":
        parser.error("candidate capture requires reuse_only BENCH provisioning")
    workspace = args.workspace.resolve(strict=True)
    revision = str(suite.corpus["revision"])
    frozen._validate_workspace(workspace, revision)
    suite.validate_workspace_regions(workspace)
    effective = run_bench.configured_manifest(frozen._load_json(frozen.MANIFEST_PATH), config)
    if args.ann_search_breadth is not None:
        run_bench.configured_solr_ann_breadth(effective, args.ann_search_breadth)
    measurement = frozen._object(effective, "measurement")
    environment = frozen._object(effective, "environment")
    context = frozen.BenchmarkBackendContext(
        endpoints=frozen._object(environment, "service_endpoints"),
        embedding_identity=frozen._object(environment, "embedding"),
        settings=frozen._object(effective, "backend_settings"),
        diagnostic_candidate_window=int(str(measurement["diagnostic_candidate_window"])),
    )
    settings = frozen.benchmark_backend_adapters()["solr"].build_settings(context)
    artifact = frozen._load_json(args.artifact)
    if artifact["config"]["identity"]["backend_settings"] != settings:
        parser.error("paired artifact does not match effective Solr settings")
    if artifact["ground_truth_fingerprint"] != suite.fingerprint:
        parser.error("paired artifact does not match frozen Ground Truth")
    vector_by_query: dict[str, set[str]] = {}
    top20_by_query: dict[str, list[str]] = {}
    for observation in artifact["observations"]:
        query_id = str(observation["query_id"])
        vector_by_query.setdefault(query_id, set()).add(
            str(observation["telemetry"]["metadata"]["query_vector_sha256"])
        )
        top20 = [str(item["id"]) for item in observation["result_spans"]]
        if query_id in top20_by_query and top20_by_query[query_id] != top20:
            parser.error(f"paired artifact top-20 order is unstable for {query_id}")
        top20_by_query[query_id] = top20
    if len(vector_by_query) != len(suite.cases) or any(len(values) != 1 for values in vector_by_query.values()):
        parser.error("paired artifact query vectors are incomplete or unstable")

    embedding = frozen._object(environment, "embedding")
    retrieval = frozen._retrieval_for_mode(
        "dense", provider=str(embedding["provider"]), model=str(embedding["model"]),
    )
    scope = frozen.SearchScope(
        workspace=workspace,
        revision=revision,
        repository=str(suite.corpus["repository"]),
        knowledge_types=("code", "tests", "documentation", "configuration"),
        max_results=int(str(measurement["max_results"])),
        settings=settings,
        retrieval=retrieval,
    )
    backend = frozen.BackendRegistry(providers=frozen._embedding_providers(context)).create("solr")
    rows: list[dict[str, object]] = []
    for case in suite.cases:
        response = backend.search(case.intent, scope, ())
        if response.telemetry is None:
            raise RuntimeError("Solr response is missing retrieval telemetry")
        vector_sha = str(response.telemetry.metadata["query_vector_sha256"])
        if vector_by_query[case.query_id] != {vector_sha}:
            raise RuntimeError(f"query vector changed for {case.query_id}")
        order = response.metadata["candidate_order_before_cutoff"]
        if not isinstance(order, list):
            raise RuntimeError(f"Solr acquisition order is missing for {case.query_id}")
        ordered_ids = [str(item["id"]) for item in order]
        if ordered_ids[:20] != top20_by_query[case.query_id]:
            raise RuntimeError(f"acquisition top-20 changed for {case.query_id}")
        rows.append({
            "query_id": case.query_id,
            "query_vector_sha256": vector_sha,
            "ann_top_k_requested": response.metadata["ann_top_k_requested"],
            "candidate_window": response.metadata["dense_candidate_window"],
            "candidates_acquired": response.metadata["dense_candidates_acquired"],
            "ordered_ids": ordered_ids,
        })
    output = {
        "schema_version": 1,
        "fetchcore_revision": frozen._git_revision(frozen.ROOT.parents[1]),
        "corpus": suite.corpus,
        "ground_truth_fingerprint": suite.fingerprint,
        "snapshot_revision": revision,
        "backend_settings": settings,
        "queries": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"[solr-ann] captured {len(rows)} queries at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
