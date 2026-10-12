"""Reproducible synthetic API corpus baseline, without persisting source content.

Compare independently with an unchanged checkout:
  python benchmarks/api-extraction-s0/run.py --implementation-root /tmp/fetchcore-pre-s0
  python benchmarks/api-extraction-s0/run.py --implementation-root .

The first checkout must have the dependencies installed and contain the
pre-S0 implementation (specified in this protocol's README).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * quantile))], 3)


def measure(implementation_root: Path, fixture_root: Path, cases: list[dict[str, str]],
            sentinel: str, repeats: int) -> dict[str, object]:
    sys.path.insert(0, str(implementation_root / "src"))
    from fetchcore.adapters.retrieval.local.backend import LocalSemanticBackend
    from fetchcore.application.service import (
        RepositoryBinding, RetrievalApplicationService, SourceReadRequest,
    )
    from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
    from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
    from fetchcore.repository.safe_scan import scan_repository_text
    from fetchcore.retrieval.contracts import KnowledgeSearchError, SearchIntent, SearchScope

    config = RepositoryIndexConfig(
        repository="fetchcore/s0-synthetic",
        revision="fixture-revision",
    )
    indexer = RepositoryIndexer(config)
    durations: list[float] = []
    result: Any = None
    for _ in range(repeats + 1):
        started = time.perf_counter()
        result = indexer.index(fixture_root)
        elapsed = (time.perf_counter() - started) * 1000
        # First iteration is a warm-up; subsequent runs are measured.
        if _ > 0:
            durations.append(elapsed)

    assert result is not None
    scan = scan_repository_text(
        fixture_root,
        include_patterns=config.include_patterns,
        exclude_patterns=config.exclude_patterns,
        max_files=config.max_files,
        max_file_bytes=config.max_file_bytes,
        max_total_bytes=config.max_total_bytes,
    )
    backend = LocalSemanticBackend()
    scope = SearchScope(
        workspace=fixture_root,
        revision=None,
        repository="fetchcore/s0-synthetic",
        knowledge_types=(),
        max_results=5,
        settings={"max_matches": 5},
    )
    response_rows: list[dict[str, object]] = []
    for case in cases:
        response = backend.search(
            SearchIntent(canonical_query=case["query"]), scope, (),
        )
        matched = next(
            (rank for rank, hit in enumerate(response.results, 1)
             if hit.source.endswith(case["source"])), None,
        )
        response_rows.append({
            "id": case["id"],
            "expected_source": case["source"],
            "observed_rank": matched,
            "source_recall_at_5": int(matched is not None),
            "reciprocal_rank": round(1.0 / matched, 5) if matched else 0.0,
            "results_returned": len(response.results),
            "secret_in_results": sentinel in json.dumps(
                [hit.to_mapping() for hit in response.results], default=str
            ),
        })

    service = RetrievalApplicationService(
        {
            "fetchcore/s0-synthetic": RepositoryBinding(
                repository="fetchcore/s0-synthetic",
                workspace=fixture_root,
                backend="local-semantic",
                revision="fixture-revision",
            )
        }
    )
    read_rows: list[dict[str, object]] = []
    for case in ("openapi.json", "swagger.yaml",
                 "demo.postman_collection.json", "demo.postman_environment.json"):
        try:
            source = service.get_source(SourceReadRequest(
                repository="fetchcore/s0-synthetic",
                source_ref=f"repository://fetchcore/s0-synthetic/{case}",
                revision="fixture-revision",
            ))
            exposed = sentinel in source.content
            blocked = False
        except KnowledgeSearchError as exc:
            exposed = sentinel in str(exc)
            blocked = exc.category == "unsafe_source"
        read_rows.append({"file": case, "blocked": blocked, "secret_exposed": exposed})

    corpus_hash = hashlib.sha256(json.dumps(
        [(p.relative_to(fixture_root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
         for p in sorted(fixture_root.rglob("*")) if p.is_file()],
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "protocol": "fetchcore.api-extraction-s0",
        "fixture_fingerprint": "sha256:" + corpus_hash,
        "implementation_root": str(implementation_root),
        "indexing_fingerprint": indexing_contract_fingerprint(indexer),
        "indexed_files": sorted({doc.path for doc in result.documents}),
        "canonical_document_count": len(result.documents),
        "secret_in_canonical": sentinel in json.dumps(
            [doc.to_mapping() for doc in result.documents], default=str,
        ),
        "scan": {
            "files_read": len(scan.sources),
            "skipped": dict(sorted(scan.skipped.items())),
            "bytes_read": scan.bytes_read,
        },
        "indexing_latency_ms": {
            "repetitions": repeats,
            "p50": _percentile(durations, 0.5),
            "p95": _percentile(durations, 0.95),
            "p99": _percentile(durations, 0.99),
            "mean": round(statistics.mean(durations), 3),
        },
        "local_lexical_source_recall_at_5": round(
            statistics.mean(float(r["source_recall_at_5"]) for r in response_rows), 5,
        ),
        "local_lexical_source_mrr": round(
            statistics.mean(float(r["reciprocal_rank"]) for r in response_rows), 5,
        ),
        "queries": response_rows,
        "source_access": read_rows,
        "note": "Source-level synthetic diagnostic; not operation-level retrieval quality. "
                "S0 deliberately blocks API files until safe typed extractors arrive.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--implementation-root", type=Path, default=Path("."))
    parser.add_argument("--fixtures", type=Path,
                        default=Path("benchmarks/api-extraction-s0/fixtures"))
    parser.add_argument("--ground-truth", type=Path,
                        default=Path("benchmarks/api-extraction-s0/ground-truth.json"))
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    print(json.dumps(measure(
        args.implementation_root.resolve(), args.fixtures.resolve(),
        truth["queries"], truth["security_sentinel"], args.repeats,
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
