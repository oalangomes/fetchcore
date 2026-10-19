"""Paired construction-only benchmark for deterministic local S5 references."""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
import time
from pathlib import Path

from fetchcore.repository.indexing import RepositoryIndexConfig
from fetchcore.repository.local_references import LocalReferenceBudget, LocalReferenceResolver
from fetchcore.repository.postman_indexing import ApiContractRepositoryIndexer

HERE = Path(__file__).resolve().parent
GROUND = json.loads((HERE / "ground-truth.json").read_text(encoding="utf-8"))


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(percent * len(ordered)) - 1)], 3)


def observations(result) -> list[tuple[str, dict[str, object]]]:
    return [(doc.path, item.payload()) for doc in result.documents
            for item in doc.evidence.semantic
            if item.schema_id in {"local_reference/resolution@1",
                                  "local_reference/resolution@2"}]


def coverage(result) -> dict[str, object] | None:
    items = [item.payload() for doc in result.documents for item in doc.evidence.semantic
             if item.schema_id == "local_reference/coverage@1"]
    return items[0] if len(items) == 1 else None


def measure(root: Path, *, enabled: bool, runs: int = 11) -> tuple[object, list[float]]:
    indexer = ApiContractRepositoryIndexer(
        RepositoryIndexConfig(repository="benchmark/s5", revision="frozen"),
        resolve_local_references=enabled, structured_python=True,
    )
    times: list[float] = []
    result = indexer.index(root)
    for _ in range(runs):
        start = time.perf_counter()
        result = indexer.index(root)
        times.append((time.perf_counter() - start) * 1000)
    return result, times


def run() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="fetchcore-s5-bench-") as directory:
        root = Path(directory)
        for file in (HERE / "fixtures").iterdir():
            shutil.copyfile(file, root / file.name)
        baseline, base_ms = measure(root, enabled=False)
        resolved, s5_ms = measure(root, enabled=True)
        resolver = LocalReferenceResolver()
        resolution_ms: list[float] = []
        for _ in range(11):
            start = time.perf_counter()
            resolver.resolve(baseline.documents)
            resolution_ms.append((time.perf_counter() - start) * 1000)

        items = observations(resolved)
        pass_coverage = coverage(resolved)
        pressure = resolver.__class__(LocalReferenceBudget(max_references=1)).resolve(
            baseline.documents,
        )
        pressure_coverage = [item.payload() for doc in pressure for item in doc.evidence.semantic
                             if item.schema_id == "local_reference/coverage@1"]
        links = {(str(item["kind"]), source, str(item["target_path"]))
                 for source, item in items if item["status"] == "resolved"}
        positive = {tuple(row) for row in GROUND["positive"]}
        negatives = {(kind, target, path) for kind, target, path in GROUND["negative"]}
        false_links = sum(1 for _, declaration, target in negatives
                          if any(item["declaration"] == declaration
                                 and item["target_path"] == target
                                 and item["status"] == "resolved"
                                 for _, item in items))
        correct = len(links & positive)
        statuses = {status: sum(item["status"] == status for _, item in items)
                    for status in ("resolved", "unresolved", "ambiguous", "blocked",
                                   "truncated", "unsupported")}
        base_bytes = sum(len(json.dumps(doc.to_mapping(), sort_keys=True))
                         for doc in baseline.documents)
        s5_bytes = sum(len(json.dumps(doc.to_mapping(), sort_keys=True))
                       for doc in resolved.documents)
        proven_routes = sum(
            1 for doc in resolved.documents for route in doc.evidence.behavior.routes
            if route.resolution == "proven_binding"
        )
        positive_relations = sum(len(doc.evidence.relations.semantic)
                                 for doc in resolved.documents)
        before = resolved
        (root / "architecture.md").unlink()
        after = ApiContractRepositoryIndexer(
            RepositoryIndexConfig(repository="benchmark/s5", revision="frozen"),
            resolve_local_references=True, structured_python=True,
        ).index(root, previous_documents=before.documents)
        removed_link = not any(item["status"] == "resolved"
                               for source, item in observations(after)
                               if source == "README.md"
                               and item["declaration"] == "./architecture.md")

        # Larger deterministic corpus measures the full-scan cost. It uses
        # source copies with unique Markdown paths, avoiding a retrieval ranker.
        for index in range(100):
            (root / f"doc-{index:03}.md").write_text(
                f"# Document {index}\n\n[Next](./doc-{(index + 1) % 100:03}.md)\n",
                encoding="utf-8",
            )
        _, large_base = measure(root, enabled=False, runs=9)
        _, large_s5 = measure(root, enabled=True, runs=9)
        return {
            "protocol": "construction-only; no ranked retrieval",
            "python": sys.version.split()[0],
            "fixture_files": 5,
            "ground_truth_positive": len(positive),
            "ground_truth_negative": len(negatives),
            "references_observed": len(items),
            "statuses": statuses,
            "ground_truth_recovered": correct,
            "target_recall": round(correct / len(positive), 4),
            "relation_precision": round(correct / len(links), 4) if links else None,
            "false_positive_link_rate": round(false_links / len(negatives), 4),
            "candidates_examined": sum(int(item["candidate_count"]) for _, item in items),
            "declarations_examined": (pass_coverage["examined"] if pass_coverage else len(items)),
            "declarations_omitted_by_budget": (
                pass_coverage["omitted_by_budget"] if pass_coverage else None
            ),
            "resolution_complete": (
                pass_coverage["resolution_complete"] if pass_coverage else None
            ),
            "budget_exhaustion_rate": (
                (int(not bool(pass_coverage["resolution_complete"]))
                 + int(not bool(pressure_coverage[0]["resolution_complete"]))) / 2
                if pass_coverage and pressure_coverage else None
            ),
            "budget_pressure_omitted": (
                pressure_coverage[0]["omitted_by_budget"] if pressure_coverage else None
            ),
            "false_positive_contract_to_code_links": sum(
                item["status"] == "resolved" and item["kind"] == "route_correspondence"
                and item["target_path"] == "unrelated.py" for _, item in items
            ),
            "resolved_links_with_proven_receiver": sum(
                item["status"] == "resolved" and item["kind"] == "route_correspondence"
                and item["target_path"] == "app.py" for _, item in items
            ) if proven_routes else 0,
            "joins_inconclusive_missing_binding": sum(
                item["kind"] == "route_correspondence"
                and item["evidence_basis"] == "unproven_framework_receiver"
                for _, item in items
            ),
            "positive_relations_emitted": positive_relations,
            "resolution_p50_ms": percentile(resolution_ms, .5),
            "resolution_p95_ms": percentile(resolution_ms, .95),
            "baseline_index_p50_ms": percentile(base_ms, .5),
            "baseline_index_p95_ms": percentile(base_ms, .95),
            "s5_index_p50_ms": percentile(s5_ms, .5),
            "s5_index_p95_ms": percentile(s5_ms, .95),
            "relations_per_1000_canonical_chars": round(1000 * len(links) / s5_bytes, 4),
            "additional_canonical_bytes": s5_bytes - base_bytes,
            "deletion_removes_link": removed_link,
            "deletion_canonical_updated": len(after.changes.updated),
            "large_files": len(tuple(root.iterdir())),
            "large_baseline_index_p50_ms": percentile(large_base, .5),
            "large_s5_index_p50_ms": percentile(large_s5, .5),
            "large_baseline_index_p95_ms": percentile(large_base, .95),
            "large_s5_index_p95_ms": percentile(large_s5, .95),
        }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
