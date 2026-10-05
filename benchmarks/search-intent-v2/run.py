#!/usr/bin/env python3
"""Measure the serving translation on one frozen self-dogfood Solr projection.

Run the same file with PYTHONPATH pointing to the before/after source trees.
No indexing, tuning or natural-language analysis is performed by this runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

import yaml

from fetchcore.application import RepositoryBinding, RetrievalApplicationService, SearchRequest
from fetchcore.retrieval import contracts


def percentile(values, percentile):
    return sorted(values)[max(0, math.ceil(len(values) * percentile) - 1)]


def git(workspace, *args):
    return subprocess.check_output(["git", "-C", str(workspace), *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--solr-url", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repetitions", type=int, default=20)
    args = parser.parse_args()
    if args.warmups < 1 or args.repetitions < 1:
        parser.error("warmups and repetitions must be positive")
    workspace = args.workspace.resolve()
    if git(workspace, "rev-parse", "HEAD") != git(workspace, "rev-parse", args.revision):
        parser.error("corpus HEAD must equal requested revision")
    if git(workspace, "status", "--porcelain", "--untracked-files=no"):
        parser.error("corpus must be clean")
    payload = yaml.safe_load(args.scenarios.read_text())
    repository = payload["repository"]
    service = RetrievalApplicationService(
        {
            repository: RepositoryBinding(
                repository=repository,
                workspace=workspace,
                backend="solr",
                revision=args.revision,
                settings={
                    "url": args.solr_url,
                    "collection": args.collection,
                    "projection_version": "3",
                },
            )
        }
    )
    observations = []
    for scenario in payload["scenarios"]:
        request = SearchRequest(
            scenario["query"], repository=repository, limit=payload["defaults"]["limit"]
        )
        expected = scenario["expected_paths"]
        for repetition in range(-args.warmups, args.repetitions):
            started = time.perf_counter()
            response = service.search(request)
            duration_ms = (time.perf_counter() - started) * 1000
            if response.status not in ("found", "not_found", "success"):
                raise RuntimeError(response.status)
            if response.metadata["snapshot_revision"] != args.revision:
                raise RuntimeError("snapshot changed during measurement")
            if repetition < 0:
                continue
            sources = [item.source for item in response.results]
            matched = set(sources) & set(expected)
            rank = next((rank for rank, path in enumerate(sources, 1) if path in expected), None)
            observations.append(
                {
                    "scenario": scenario["id"],
                    "repetition": repetition,
                    "hit": bool(matched),
                    "source_recall_at_10": len(matched) / len(set(expected)),
                    "reciprocal_rank": 1 / rank if rank else 0,
                    "first_expected_rank": rank,
                    "zero_results": not sources,
                    "duration_ms": round(duration_ms, 3),
                    "backend_duration_ms": response.duration_ms,
                    "candidates_examined": response.telemetry.candidates_examined
                    if response.telemetry
                    else None,
                    "candidates_returned": len(response.results),
                    "ranked_sources": sources,
                }
            )
    durations = [x["duration_ms"] for x in observations]
    source_root = Path(contracts.__file__).resolve().parents[1]
    tree = hashlib.sha256()
    for path in sorted(source_root.rglob("*.py")):
        tree.update(path.relative_to(source_root).as_posix().encode())
        tree.update(path.read_bytes())
    result = {
        "protocol": "fetchcore.search-intent-v2.serving-comparison",
        "schema_version": 1,
        "label": args.label,
        "source_tree_sha256": tree.hexdigest(),
        "corpus_revision": git(workspace, "rev-parse", "HEAD"),
        "snapshot_revision": args.revision,
        "repository": repository,
        "collection": args.collection,
        "scenarios_sha256": hashlib.sha256(args.scenarios.read_bytes()).hexdigest(),
        "python": sys.version,
        "platform": platform.platform(),
        "warmups": args.warmups,
        "repetitions": args.repetitions,
        "k": 10,
        "summary": {
            "expected_path_hit_rate": statistics.mean(x["hit"] for x in observations),
            "source_recall_at_10": statistics.mean(x["source_recall_at_10"] for x in observations),
            "mrr": statistics.mean(x["reciprocal_rank"] for x in observations),
            "zero_result_rate": statistics.mean(x["zero_results"] for x in observations),
            "latency_ms": {
                "p50": percentile(durations, 0.5),
                "p95": percentile(durations, 0.95),
                "p99": percentile(durations, 0.99),
            },
            "mean_candidates_examined": statistics.mean(
                x["candidates_examined"] for x in observations
            ),
        },
        "observations": observations,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
