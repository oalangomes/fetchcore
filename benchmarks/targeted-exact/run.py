#!/usr/bin/env python3
"""Paired policy experiment on the frozen #18 corpus and evidence ruler."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import time
from urllib.parse import unquote

from fetchcore.application import RepositoryBinding, RetrievalApplicationService, SearchRequest
from fetchcore.application.composition import BackendRegistry
from fetchcore.adapters.retrieval.local.backend import LocalSemanticBackend
from fetchcore.baseline import load_baseline_suite, evaluate_retrieval
from fetchcore.retrieval import RetrievalConfig, DenseRetrievalConfig
from fetchcore.retrieval.contracts import Anchor, KnowledgeSearchError, SearchIntent

ROOT = Path(__file__).resolve().parents[2]


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_fingerprint():
    digest = hashlib.sha256()
    for path in sorted((ROOT / "src").rglob("*.py")):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def summarize(rows):
    values = sorted(x["wall_ms"] for x in rows)
    return {
        "samples": len(rows),
        "duration_ms": {
            name: values[max(0, math.ceil(len(values) * q) - 1)]
            for name, q in [("p50", 0.5), ("p95", 0.95), ("p99", 0.99)]
        },
        **{
            key + "_mean": statistics.mean(x[key] for x in rows)
            for key in (
                "evidence_recall_at_k",
                "candidates_examined",
                "returned_chars",
                "returned_line_count",
                "targeted_ms",
                "broad_calls",
                "files_read",
            )
        },
        "fallback_rate": statistics.mean(x["fallback_used"] for x in rows),
    }


class MeasuredLocal(LocalSemanticBackend):
    def __init__(self):
        super().__init__()
        self.broad_calls = 0

    def search(self, *args):
        self.broad_calls += 1
        return super().search(*args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--warmups", type=int, default=1)
    args = parser.parse_args()
    if args.repetitions < 1 or args.warmups < 1:
        parser.error("positive repetitions/warmups required")
    suite = load_baseline_suite(ROOT / "benchmarks/baseline-v1/ground-truth.json")
    revision, repository = suite.corpus["revision"], suite.corpus["repository"]
    if git(args.workspace, "rev-parse", "HEAD") != revision or git(
        args.workspace, "status", "--porcelain"
    ):
        parser.error("workspace must be clean and at frozen corpus revision")
    frozen = json.loads(
        (ROOT / "benchmarks/baseline-v1/artifacts/V0.1/local-lexical.json").read_text()
    )
    settings = frozen["config"]["identity"]["backend_settings"]
    queries = json.loads((ROOT / "benchmarks/search-intent-v2/evidence-queries.json").read_text())
    queries = {item["query_id"]: item["canonical_query"] for item in queries["queries"]}
    # Deterministic subset rule, selected before measurements: all GT requirements
    # and alternatives refer to exactly one source. Never choose by observed score.
    cases = []
    for case in suite.cases:
        if args.query and case.query_id not in args.query:
            continue
        refs = {
            region.source_ref
            for req in case.requirements
            for alternative in req.alternatives
            for region in alternative.regions
        }
        if len(refs) == 1:
            ref = next(iter(refs))
            path = unquote(ref.removeprefix(f"repository://{repository}/"))
            for kind, value in [("path", path), ("source_ref", ref)]:
                cases.append(
                    (
                        case,
                        kind,
                        SearchIntent(
                            canonical_query=queries[case.query_id], anchors=(Anchor(kind, value),)
                        ),
                    )
                )
    observations = []
    services = {}
    for policy in ("disabled", "exact_unique"):
        backend = MeasuredLocal()
        binding = RepositoryBinding(
            repository,
            args.workspace,
            backend.backend_id,
            revision=revision,
            settings=settings,
            retrieval=RetrievalConfig(targeted=policy),
        )
        services[policy] = (
            RetrievalApplicationService(
                {repository: binding},
                backends=BackendRegistry({backend.backend_id: lambda b=backend: b}),
            ),
            backend,
        )
    for case, kind, intent in cases:
        print(f"{case.query_id} {kind}", flush=True)
        for iteration in range(-args.warmups, args.repetitions):
            # Alternate policy order to reduce monotonic warm-cache/order bias.
            policies = (
                ("disabled", "exact_unique") if iteration % 2 else ("exact_unique", "disabled")
            )
            for policy in policies:
                app, backend = services[policy]
                before = backend.broad_calls
                started = time.perf_counter()
                response = app.search(SearchRequest(intent=intent, limit=20))
                wall_ms = (time.perf_counter() - started) * 1000
                if iteration < 0:
                    continue
                audit = response.metadata["targeted"]
                row = evaluate_retrieval(
                    response, case.truth, iteration=iteration + 1, k=20
                ).to_mapping()
                row.update(
                    policy=policy,
                    signal_kind=kind,
                    intent=intent.to_mapping(),
                    wall_ms=round(wall_ms, 3),
                    targeted_ms=audit["targeted_ms"],
                    fallback_used=audit["fallback_used"],
                    broad_calls=backend.broad_calls - before,
                    files_read=response.metadata["safe_scan"]["files_read"],
                )
                observations.append(row)
    safety = []
    app, backend = services["exact_unique"]
    for kind, value, expected in (
        ("path", "missing-for-issue-34.py", "not_found"),
        ("source_ref", f"repository://{repository}/missing-for-issue-34.py", "not_found"),
        ("path", "../outside.py", "unauthorized_source"),
    ):
        before = backend.broad_calls
        try:
            response = app.search(
                SearchRequest(
                    intent=SearchIntent(
                        canonical_query="medication", anchors=(Anchor(kind, value),)
                    )
                )
            )
            outcome = response.metadata["targeted"]["fallback_reason"]
            telemetry = response.telemetry.to_mapping()
        except KnowledgeSearchError as exc:
            outcome, telemetry = exc.category, None
        assert outcome == expected
        assert backend.broad_calls - before == (1 if expected == "not_found" else 0)
        safety.append(
            {
                "kind": kind,
                "value": value,
                "outcome": outcome,
                "broad_calls": backend.broad_calls - before,
                "telemetry": telemetry,
            }
        )
    # Dense avoidance control: same exact acquisition with dense selected. Local
    # broad entry is a tripwire, not a simulated dense latency/quality measurement.
    case, kind, intent = cases[0]
    binding = RepositoryBinding(
        repository,
        args.workspace,
        backend.backend_id,
        revision=revision,
        settings=settings,
        retrieval=RetrievalConfig(dense=DenseRetrievalConfig(enabled=True)),
    )
    dense_service = RetrievalApplicationService(
        {repository: binding}, backends=BackendRegistry({backend.backend_id: lambda: backend})
    )
    before = backend.broad_calls
    dense_response = dense_service.search(SearchRequest(intent=intent))
    assert dense_response.results and backend.broad_calls == before
    payload = {
        "schema_version": 1,
        "protocol": "fetchcore.targeted-exact-v1",
        "status": "complete",
        "corpus": suite.corpus,
        "ground_truth_fingerprint": suite.fingerprint,
        "query_set_sha256": fingerprint(ROOT / "benchmarks/search-intent-v2/evidence-queries.json"),
        "base_revision": git(ROOT, "merge-base", "HEAD", "origin/master"),
        "runtime_source_sha256": runtime_fingerprint(),
        "runner_sha256": fingerprint(Path(__file__)),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "backend": "local-semantic",
        "settings": settings,
        "k": 20,
        "repetitions": args.repetitions,
        "warmups": args.warmups,
        "projection": None,
        "embedding_identity": None,
        "subset_rule": "all evidence alternatives/requirements refer to exactly one source",
        "excluded_cases": [
            c.query_id for c in suite.cases if c.query_id not in {x[0].query_id for x in cases}
        ],
        "summary": {
            policy: summarize([x for x in observations if x["policy"] == policy])
            for policy in services
        },
        "by_query": {
            case.query_id + ":" + kind: {
                policy: summarize(
                    [
                        x
                        for x in observations
                        if x["query_id"] == case.query_id
                        and x["signal_kind"] == kind
                        and x["policy"] == policy
                    ]
                )
                for policy in services
            }
            for case, kind, _ in cases
        },
        "controls": safety,
        "dense_avoidance": {
            "selected_mode": "dense",
            "broad_calls": 0,
            "note": "call-count control; no dense timings fabricated",
        },
        "observations": observations,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload["summary"], indent=2), flush=True)


if __name__ == "__main__":
    main()
