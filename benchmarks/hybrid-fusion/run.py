#!/usr/bin/env python3
"""Issue #84: immutable acquisition, offline fusion replay, and real confirmation."""

from __future__ import annotations

import argparse
from dataclasses import replace
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
from statistics import mean
import subprocess
import sys
import time

from fetchcore.application import (
    BackendRegistry,
    RepositoryBinding,
    RetrievalApplicationService,
    SearchRequest,
)
from fetchcore.baseline import (
    BASELINE_LINE_BUDGETS,
    BASELINE_V1_METRICS_SPEC,
    BASELINE_CONTEXT_YIELD_METRICS_SPEC,
    capture_baseline_environment,
    BaselineGroundTruth,
    evaluate_retrieval,
    evaluate_line_budgeted_evidence,
    load_baseline_suite,
    mapping_fingerprint,
    percentile,
)
from fetchcore.retrieval.contracts import KnowledgeSearchResponse, SearchResult, SearchScope
from fetchcore.retrieval.fusion import FusionInput, FusionKernel, FusionRequest
from fetchcore.retrieval.hybrid import allocate_fusion_input

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROTOCOL = "fetchcore.hybrid-fusion-v1"
BACKENDS = ("solr", "opensearch", "qdrant-vector")
FULL_MEASUREMENT = {
    "queries": 20,
    "k": 20,
    "warmups": 1,
    "repetitions": 20,
    "acquisition_window": 80,
}
QUALITY = (
    "evidence_reciprocal_rank",
    "evidence_recall_at_k",
    "span_coverage_at_k",
    "evidence_ndcg_at_k",
    "useful_context_line_ratio",
)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    sealed = {**payload, "artifact_fingerprint": mapping_fingerprint(payload)}
    raw = (json.dumps(sealed, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(gzip.compress(raw, mtime=0) if path.suffix == ".gz" else raw)
    temporary.replace(path)


def read(path, *, stage=None, complete=True):
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    result = json.loads(raw)
    fingerprint = result.pop("artifact_fingerprint")
    if mapping_fingerprint(result) != fingerprint:
        raise ValueError("artifact checksum mismatch")
    if result.get("protocol") != PROTOCOL or result.get("schema_version") != 1:
        raise ValueError("artifact protocol/schema mismatch")
    if stage is not None and result.get("stage") != stage:
        raise ValueError("artifact stage mismatch")
    if complete and result.get("status") != "complete":
        raise ValueError("incomplete/blocked artifact cannot be used as evidence")
    return result


def runtime_identity():
    files = sorted((ROOT / "src/fetchcore").rglob("*.py"))
    return {
        "revision": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "runtime_fingerprint": mapping_fingerprint(
            {str(p.relative_to(ROOT)): digest(p) for p in files}
        ),
        "runner_fingerprint": digest(Path(__file__)),
        "dirty": bool(
            subprocess.check_output(
                ["git", "-C", str(ROOT), "status", "--porcelain"], text=True
            ).strip()
        ),
    }


def inputs():
    historical = HERE / "artifacts/historical-checksums.json"
    checks = json.loads(historical.read_text())
    for name, expected in checks.items():
        if digest(ROOT / "benchmarks/search-intent-v2/artifacts/evidence/V0.2" / name) != expected:
            raise ValueError("historical reference changed: " + name)
    evidence = module("hybrid84_evidence", ROOT / "benchmarks/search-intent-v2/evidence.py")
    wrapper = module("hybrid84_wrapper", ROOT / "benchmarks/baseline-v1/run_bench.py")
    suite, queries, query_fingerprint = evidence.load_inputs()
    review = json.loads((HERE / "confirmation-review.json").read_text())
    if digest(HERE / "confirmation-queries.json") != review["queries_sha256"]:
        raise ValueError("confirmation queries changed after source review")
    identity = {
        "corpus": suite.corpus,
        "ground_truth_fingerprint": suite.fingerprint,
        "metrics_fingerprint": mapping_fingerprint(BASELINE_V1_METRICS_SPEC),
        "context_yield_metrics_fingerprint": mapping_fingerprint(
            BASELINE_CONTEXT_YIELD_METRICS_SPEC
        ),
        "query_fingerprint": query_fingerprint,
        "confirmation_fingerprint": review["queries_sha256"],
        "historical_reference_fingerprint": digest(historical),
    }
    return evidence, wrapper, suite, queries, identity


def variants():
    return (
        [("control", None, None, 60)]
        + [
            (f"windows-{left}-{right}", left, right, 60)
            for left in (20, 40, 80)
            for right in (20, 40, 80)
        ]
        + [(f"rrf-{k}", None, None, k) for k in (10, 30, 60, 120)]
    )


def select_input(full, variant, default_window):
    _, left, right, _ = variant
    lexical = sorted(
        (c for c in full.candidates if c.lexical is not None), key=lambda c: c.lexical.rank
    )
    dense = sorted((c for c in full.candidates if c.dense is not None), key=lambda c: c.dense.rank)
    return allocate_fusion_input(
        lexical=lexical,
        dense=dense,
        lexical_window=left or default_window,
        dense_window=right or default_window,
        max_results=full.max_results,
    )


def recovered(truth, response):
    # Reuse the canonical evaluator per requirement; do not duplicate evidence matching.
    return [
        r.id
        for r in truth.requirements
        if evaluate_retrieval(
            response,
            BaselineGroundTruth(truth.query_id, (r,)),
            iteration=1,
            k=20,
        ).evidence_ground_truth_recovered
        == 1
    ]


def observation(response, truth, iteration, *, offline=False):
    obs = evaluate_retrieval(response, truth, iteration=iteration, k=20)
    payload = obs.to_mapping()
    payload["recovered_requirements"] = recovered(truth, response)
    if response.telemetry is not None:
        for stage in response.telemetry.stages:
            if stage.name in ("lexical", "dense"):
                payload[stage.name + "_acquired"] = stage.metadata.get(
                    "raw_candidates", stage.candidates_out
                )
    payload["fusion_output_fingerprint"] = response.metadata.get("fusion_output_fingerprint")
    if "fusion_input" in response.metadata:
        admitted = FusionInput.from_mapping(response.metadata["fusion_input"])
        payload["lexical_admitted"] = sum(c.lexical is not None for c in admitted.candidates)
        payload["dense_admitted"] = sum(c.dense is not None for c in admitted.candidates)
    payload["evidence_provenance"] = [
        {
            "id": r.id,
            "source_ref": r.source_ref,
            "revision": r.revision,
            "checksum": r.checksum,
            "span": [r.line_start, r.line_end],
            "content_sha256": hashlib.sha256(r.content.encode()).hexdigest(),
        }
        for r in response.results
    ]
    payload["budgets"] = {
        str(m.line_budget): m.context_yield
        for m in evaluate_line_budgeted_evidence(truth, obs.result_spans)
    }
    payload["useful_hit_density"] = (
        mean(
            [
                evaluate_retrieval(
                    replace(response, results=(r,), telemetry=None),
                    truth,
                    iteration=iteration,
                    k=20,
                ).span_coverage_at_k
                > 0
                for r in response.results
            ]
        )
        if response.results
        else 0.0
    )
    if offline:
        payload.pop("duration_ms")
        payload.pop("telemetry")
        payload["timing_kind"] = "offline-replay; no retrieval latency measured"
    return payload


def replay_row(capture, variant, default_window, truth):
    full = FusionInput.from_mapping(capture["input"])
    selected = select_input(full, variant, default_window)
    fused = FusionKernel().fuse(FusionRequest(selected, parameters={"k": variant[3]}))
    documents = {d["id"]: d for d in capture["evidence"]}
    if set(documents) != {c.candidate_id for c in full.candidates}:
        raise ValueError("capture evidence identity mismatch")
    result_fields = (
        "id",
        "source",
        "source_ref",
        "knowledge_type",
        "content",
        "score",
        "line_start",
        "line_end",
        "revision",
        "checksum",
        "source_type",
    )
    results = tuple(
        SearchResult(
            **{
                **{
                    k: documents[c.candidate_id][k]
                    for k in result_fields
                    if k in documents[c.candidate_id]
                },
                "score": c.normalized_score,
            }
        )
        for c in fused.candidates
        if c.returned
    )
    response = KnowledgeSearchResponse("offline-fusion-replay", "ok", results)
    row = observation(response, truth, capture["iteration"], offline=True)
    row.update(
        candidates_examined=len(selected.candidates),
        fusion=fused.to_mapping(),
        full_acquisition_fingerprint=full.fingerprint,
        lexical_acquired=capture["lexical_acquired"],
        dense_acquired=capture["dense_acquired"],
        lexical_admitted=sum(c.lexical is not None for c in selected.candidates),
        dense_admitted=sum(c.dense is not None for c in selected.candidates),
        excluded_lexical=[
            c.candidate_id
            for c in full.candidates
            if c.lexical is not None
            and not any(
                s.candidate_id == c.candidate_id and s.lexical is not None
                for s in selected.candidates
            )
        ],
        excluded_dense=[
            c.candidate_id
            for c in full.candidates
            if c.dense is not None
            and not any(
                s.candidate_id == c.candidate_id and s.dense is not None
                for s in selected.candidates
            )
        ],
    )
    return row


def validate_rows(rows, query_ids, repetitions):
    expected = {(q, n) for q in query_ids for n in range(1, repetitions + 1)}
    keys = [(r["query_id"], r["iteration"]) for r in rows]
    if len(keys) != len(expected) or set(keys) != expected:
        raise ValueError("incomplete or duplicate observations")


def summarize(rows):
    """Aggregate the existing evidence observations; absent times remain unavailable."""
    result = {key: mean(r[key] for r in rows) for key in QUALITY}
    result["context_yield"] = {
        str(b): mean(r["budgets"][str(b)] for r in rows) for b in BASELINE_LINE_BUDGETS
    }
    for key in (
        "candidates_examined",
        "candidates_returned",
        "returned_chars",
        "returned_line_count",
        "lexical_acquired",
        "dense_acquired",
        "lexical_admitted",
        "dense_admitted",
        "useful_hit_density",
    ):
        result[key + "_mean"] = (
            mean(r[key] for r in rows) if all(r.get(key) is not None for r in rows) else None
        )
    for key in ("duration_ms", "end_to_end_ms"):
        result[key] = (
            {f"p{n}": percentile([r[key] for r in rows], n / 100) for n in (50, 95, 99)}
            if all(key in r for r in rows)
            else None
        )
    stages = {}
    for row in rows:
        for stage in row.get("telemetry", {}).get("stages", []):
            stages.setdefault(stage["name"], []).append(stage["duration_ms"])
    result["stage_duration_ms"] = {
        key: {f"p{n}": percentile(values, n / 100) for n in (50, 95, 99)}
        for key, values in stages.items()
    }
    return result


def compare(control, candidate):
    if len(control) != len(candidate) or not control:
        raise ValueError("comparison requires paired observations")
    before = {(r["query_id"], r["iteration"]): r for r in control}
    after = {(r["query_id"], r["iteration"]): r for r in candidate}
    if before.keys() != after.keys() or len(before) != len(control) or len(after) != len(candidate):
        raise ValueError("comparison observation identities differ")
    deltas = {k: mean(r[k] for r in candidate) - mean(r[k] for r in control) for k in QUALITY}
    yields = {
        str(b): mean(r["budgets"][str(b)] for r in candidate)
        - mean(r["budgets"][str(b)] for r in control)
        for b in BASELINE_LINE_BUDGETS
    }
    lost = [
        {
            "query_id": key[0],
            "iteration": key[1],
            "requirements": sorted(
                set(before[key]["recovered_requirements"])
                - set(after[key]["recovered_requirements"])
            ),
        }
        for key in before
        if not set(before[key]["recovered_requirements"])
        <= set(after[key]["recovered_requirements"])
    ]
    return {
        "deltas": deltas,
        "context_yield_deltas": yields,
        "lost_evidence": lost,
        "nonregression": not lost
        and deltas["evidence_reciprocal_rank"] >= -1e-9
        and all(d >= -1e-9 for d in yields.values()),
        "improved": deltas["evidence_reciprocal_rank"] > 1e-9
        or any(d > 1e-9 for d in yields.values()),
        "candidates_examined_mean": mean(r["candidates_examined"] for r in candidate),
        "per_query": {
            q: {
                k: mean(after[key][k] - before[key][k] for key in before if key[0] == q)
                for k in QUALITY
            }
            for q in sorted({key[0] for key in before})
        },
    }


def rank_finalists(comparisons):
    eligible = []
    for variant, arms in comparisons.items():
        if (
            set(arms) != set(BACKENDS)
            or not all(a["nonregression"] for a in arms.values())
            or not any(a["improved"] for a in arms.values())
        ):
            continue
        eligible.append(
            (
                variant,
                mean(a["deltas"]["evidence_reciprocal_rank"] for a in arms.values()),
                mean(d for a in arms.values() for d in a["context_yield_deltas"].values()),
                mean(a["candidates_examined_mean"] for a in arms.values()),
            )
        )
    return [r[0] for r in sorted(eligible, key=lambda r: (-r[1], -r[2], r[3], r[0]))]


def verify_control(control, identity):
    if control["identity"] != identity or control["measurement"] != FULL_MEASUREMENT:
        raise ValueError("control measurement/corpus/runtime identity mismatch")
    if control.get("measurement_kind") != "real":
        raise ValueError("real control required")
    for backend in BACKENDS:
        for mode in ("dense", "hybrid"):
            rows = control["observations"].get(backend + "-" + mode, [])
            validate_rows(rows, control["query_ids"], 20)
            for query in control["query_ids"]:
                ranks = {
                    mapping_fingerprint({"spans": r["result_spans"]})
                    for r in rows
                    if r["query_id"] == query
                }
                if len(ranks) != 1:
                    raise ValueError(
                        "control ranked spans are not repeatable: " + backend + "/" + query
                    )


def promotion_gate(control, candidate, comparison, *, performance_comparable):
    reasons = []
    if not comparison["nonregression"]:
        reasons.append("quality-or-evidence-regression")
    if not all(
        isinstance(r.get(key), int) and not isinstance(r[key], bool) and r[key] >= 0
        for r in candidate
        for key in ("lexical_acquired", "dense_acquired")
    ):
        reasons.append("acquisition-counts-required")
    if any(
        max(r.get("lexical_acquired") or 0, r.get("dense_acquired") or 0) > 80 for r in candidate
    ):
        reasons.append("acquisition-budget-exceeded")
    latency_ratio = None
    if not performance_comparable:
        reasons.append("performance-comparability-unattested")
    elif not all("end_to_end_ms" in r for r in control + candidate):
        reasons.append("real-end-to-end-latency-required")
    else:

        def p95(rows):
            return percentile([r["end_to_end_ms"] for r in rows], 0.95)

        baseline = p95(control)
        if baseline <= 0:
            reasons.append("invalid-control-latency")
        else:
            latency_ratio = p95(candidate) / baseline
            if latency_ratio > 1.15:
                reasons.append("p95-exceeds-15-percent")
    return {"passed": not reasons, "reasons": reasons, "p95_ratio": latency_ratio}


def performance_window(path, started, finished, config):
    if path is None:
        return False
    attestation = json.loads(path.read_text())
    # An explicit operator attestation must bind observed isolation and this resource configuration.
    return bool(
        attestation.get("dedicated") is True
        and attestation.get("exclusive_host") is True
        and config["dedicated"]
        and config["exclusive_host"]
        and attestation.get("config_fingerprint") == mapping_fingerprint(config)
        and isinstance(attestation.get("observations"), list)
        and attestation["observations"]
        and isinstance(attestation.get("owner"), str)
        and attestation["owner"].strip()
        and attestation.get("started_unix", float("inf")) <= started
        and attestation.get("finished_unix", 0) >= finished
    )


def private_output(path):
    if path.resolve().is_relative_to(ROOT):
        raise ValueError("raw artifacts must be outside repository (private source)")
    if path.exists():
        raise ValueError("output exists; artifacts are immutable")


def context_for(args, *, rebuild=False):
    evidence, wrapper, suite, queries, identity = inputs()
    config = wrapper.load_run_config(args.run_config, suite.corpus)
    provisioning = "explicit_rebuild" if rebuild else "reuse_only"
    if config["provisioning"] != provisioning or not config["dedicated"]:
        raise ValueError("this operation requires dedicated BENCH " + provisioning + " resources")
    manifest = wrapper.configured_manifest(
        wrapper.frozen._load_json(wrapper.frozen.MANIFEST_PATH), config
    )
    context = evidence.setup.BenchmarkBackendContext(
        manifest["environment"]["service_endpoints"],
        manifest["environment"]["embedding"],
        manifest["backend_settings"],
        80,
    )
    evidence.setup._validate_workspace(args.workspace, suite.corpus["revision"])
    suite.validate_workspace_regions(args.workspace)
    review = json.loads((HERE / "confirmation-review.json").read_text())
    for path, checksum in review["source_sha256"].items():
        if digest(args.workspace / path) != checksum:
            raise ValueError("confirmation source checksum mismatch")
    # Validate the running resource bindings, never start/reconcile/provision them here.
    names = [
        config["infrastructure_project"] + "-" + b + "-1" for b in ("solr", "opensearch", "qdrant")
    ]
    containers = json.loads(subprocess.check_output(["docker", "inspect", *names]))
    for container in containers:
        labels = container["Config"]["Labels"]
        service = labels["com.docker.compose.service"]
        port = {"solr": "8983/tcp", "opensearch": "9200/tcp", "qdrant": "6333/tcp"}[service]
        from urllib.parse import urlsplit

        endpoint = urlsplit(config["endpoints"][service])
        bindings = container["HostConfig"]["PortBindings"][port]
        if (
            labels.get("com.docker.compose.project") != config["infrastructure_project"]
            or labels.get("io.fetchcore.owner") != "fetchcore-shared-infra-v1"
            or not container["State"]["Running"]
            or endpoint.hostname not in ("127.0.0.1", "localhost")
            or not any(
                b["HostIp"] == "127.0.0.1" and int(b["HostPort"]) == endpoint.port for b in bindings
            )
        ):
            raise ValueError("BENCH ownership/endpoint/readiness mismatch")
        for mount in container["Mounts"]:
            if mount["Type"] == "volume":
                volume = json.loads(
                    subprocess.check_output(["docker", "volume", "inspect", mount["Name"]])
                )[0]
                if (
                    volume["Labels"].get("com.docker.compose.project")
                    != config["infrastructure_project"]
                ):
                    raise ValueError("BENCH volume ownership mismatch")
    identity.update(
        runtime=runtime_identity(), effective_config_fingerprint=mapping_fingerprint(config)
    )
    return evidence, suite, queries, identity, context, config


def online(args):
    private_output(args.output)
    evidence, suite, queries, identity, context, config = context_for(args)
    started = time.time()
    arms = [
        a
        for a in evidence.setup._manifest_arms(
            evidence.setup._load_json(evidence.setup.MANIFEST_PATH)
        )
        if a.backend in BACKENDS and a.mode in ("dense", "hybrid")
    ]
    adapters = evidence.setup.benchmark_backend_adapters()
    scopes = {
        a.id: SearchScope(
            workspace=args.workspace,
            revision=suite.corpus["revision"],
            repository=suite.corpus["repository"],
            max_results=20,
            knowledge_types=("code", "tests", "documentation", "configuration"),
            settings=evidence.setup._backend_settings(a, context=context, adapters=adapters),
            retrieval=evidence.setup._retrieval_for_mode(
                a.mode, provider="ollama", model="embeddinggemma"
            ),
            result_namespace="fetchcore-serving",
        )
        for a in arms
    }
    run = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "stage": args.command,
        "status": "running",
        "identity": identity,
        "measurement": FULL_MEASUREMENT,
        "measurement_kind": "real",
        "query_ids": list(queries),
        "snapshots": {},
        "snapshot_errors": {},
        "observations": {},
        "captures": {},
        "performance_comparable": False,
        "performance_reason": "No independently attested exclusive measurement window; config alone is insufficient.",
    }
    try:
        # Attest EVERY arm before the first retrieval; a mismatch on one blocks all.
        for arm in arms:
            try:
                run["snapshots"][arm.id] = evidence.snapshot_identity(arm, scopes[arm.id])
            except (ValueError, OSError, RuntimeError) as exc:
                run["snapshot_errors"][arm.id] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "category": getattr(exc, "category", "failed"),
                    "details": getattr(exc, "metadata", {}),
                }
        if run["snapshot_errors"]:
            raise ValueError("snapshot attestation failed; no retrieval or rebuild performed")
        run["service_versions"] = evidence.setup._service_versions(
            arms, context=context, adapters=adapters
        )
        run["environment"] = capture_baseline_environment(
            "fetchcore-hybrid-fusion-v1",
            services=run["service_versions"],
            embedding=context.embedding_identity,
        ).to_mapping()
        tags = evidence.setup._http_json(context.endpoints["ollama"].rstrip("/") + "/api/tags")
        run["embedding_digest"] = next(
            t["digest"] for t in tags["models"] if t["name"].split(":")[0] == "embeddinggemma"
        )
        if args.command != "control":
            control = read(args.control, stage="control")
            verify_control(control, identity)
            if control["environment"] != run["environment"]:
                raise ValueError("environment changed since control")
            if (
                control["snapshots"] != run["snapshots"]
                or control["embedding_digest"] != run["embedding_digest"]
            ):
                raise ValueError("snapshots/model changed since control")
        selected_variants = [("control", None, None, 60)]
        if args.command == "confirm":
            replay = read(args.replay, stage="replay")
            if replay["identity"] != identity or replay["snapshots"] != run["snapshots"]:
                raise ValueError("replay identity mismatch")
            selected_variants += [
                v for key in replay["finalists"] for v in variants() if v[0] == key
            ]
        for arm in arms:
            if args.command != "control" and arm.mode != "hybrid":
                continue
            backend = evidence.backend_registry(context).create(arm.backend)
            captured = []
            if args.command in ("capture", "confirm"):
                backend.fusion_observer = captured.append
            for variant in selected_variants:
                run["observations"][arm.id + "/" + variant[0]] = []
            run["captures"][arm.id] = []
            for iteration in range(-1, 20):
                for qi, case in enumerate(suite.cases):
                    order = (
                        selected_variants if (qi + iteration) % 2 == 0 else selected_variants[::-1]
                    )
                    for variant in order:
                        scope = scopes[arm.id]
                        if variant[0] != "control":
                            hybrid = replace(
                                scope.retrieval.hybrid,
                                lexical_fusion_window=variant[1],
                                dense_fusion_window=variant[2],
                                rrf_k=variant[3],
                            )
                            scope = replace(
                                scope, retrieval=replace(scope.retrieval, hybrid=hybrid)
                            )
                        binding = RepositoryBinding(
                            scope.repository,
                            scope.workspace,
                            arm.backend,
                            scope.revision,
                            scope.settings,
                            scope.retrieval,
                            scope.knowledge_types,
                        )
                        service = RetrievalApplicationService(
                            {scope.repository: binding},
                            backends=BackendRegistry(
                                {arm.backend: lambda backend=backend: backend}
                            ),
                        )
                        captured.clear()
                        call_started = time.perf_counter()
                        response = service.search(
                            SearchRequest(
                                query=queries[case.query_id], repository=scope.repository, limit=20
                            )
                        )
                        end_to_end_ms = (time.perf_counter() - call_started) * 1000
                        evidence.validate_response(response, arm, scope, run["snapshots"][arm.id])
                        if iteration < 0:
                            continue
                        measured = observation(response, case.truth, iteration + 1)
                        measured["end_to_end_ms"] = end_to_end_ms
                        run["observations"][arm.id + "/" + variant[0]].append(measured)
                        if args.command in ("capture", "confirm"):
                            if len(captured) != 1:
                                raise ValueError("exactly one acquisition capture required")
                            c = captured[0]
                            if max(c.lexical_acquired, c.dense_acquired) > 80:
                                raise ValueError("acquisition exceeded 80")
                            row = {
                                "query_id": case.query_id,
                                "iteration": iteration + 1,
                                "input": c.input.to_mapping(),
                                "evidence": [d.to_mapping() for d in c.evidence],
                                "lexical_acquired": c.lexical_acquired,
                                "dense_acquired": c.dense_acquired,
                            }
                            replayed = replay_row(
                                row,
                                variant,
                                80 if arm.backend == "qdrant-vector" else 20,
                                case.truth,
                            )
                            if (
                                replayed["result_spans"]
                                != run["observations"][arm.id + "/" + variant[0]][-1][
                                    "result_spans"
                                ]
                            ):
                                raise ValueError("capture replay differs from real materialization")
                            if (
                                replayed["fusion"]["output_fingerprint"]
                                != response.metadata["fusion_output_fingerprint"]
                            ):
                                raise ValueError(
                                    "capture replay fusion fingerprint differs from runtime"
                                )
                            row["real_fusion_output_fingerprint"] = response.metadata[
                                "fusion_output_fingerprint"
                            ]
                            if args.command == "capture":
                                run["captures"][arm.id].append(row)
                            else:
                                expected = next(
                                    r
                                    for r in replay["observations"][arm.backend][variant[0]]
                                    if (r["query_id"], r["iteration"])
                                    == (case.query_id, iteration + 1)
                                )
                                same_acquisition = (
                                    expected["full_acquisition_fingerprint"] == c.input.fingerprint
                                )
                                measured["same_acquisition_as_replay"] = same_acquisition
                                measured["full_acquisition_fingerprint"] = c.input.fingerprint
                                if same_acquisition and (
                                    expected["fusion"]["output_fingerprint"]
                                    != replayed["fusion"]["output_fingerprint"]
                                    or expected["result_spans"] != replayed["result_spans"]
                                    or expected["evidence_provenance"]
                                    != replayed["evidence_provenance"]
                                ):
                                    raise ValueError(
                                        "real confirmation differs for identical acquired candidates"
                                    )
                print(arm.id, "iteration", iteration + 1, flush=True)
            if evidence.snapshot_identity(arm, scopes[arm.id]) != run["snapshots"][arm.id]:
                raise ValueError("snapshot changed during measurement")
        if args.command == "control":
            run["observations"] = {
                key.split("/")[0]: value for key, value in run["observations"].items()
            }
            verify_control(run, identity)
        if args.command == "confirm":
            run["comparisons"] = {
                v[0]: {
                    b: compare(
                        run["observations"][b + "-hybrid/control"],
                        run["observations"][b + "-hybrid/" + v[0]],
                    )
                    for b in BACKENDS
                }
                for v in selected_variants[1:]
            }
            run["performance_comparable"] = performance_window(
                args.performance_attestation, started, time.time(), config
            )
            run["promotion_gates"] = {
                v[0]: {
                    b: promotion_gate(
                        run["observations"][b + "-hybrid/control"],
                        run["observations"][b + "-hybrid/" + v[0]],
                        run["comparisons"][v[0]][b],
                        performance_comparable=run["performance_comparable"],
                    )
                    for b in BACKENDS
                }
                for v in selected_variants[1:]
            }
            finalists = rank_finalists(run["comparisons"])
            # Freeze selection using historical cases; do not select/retune on the holdout.
            selected = finalists[0] if finalists else None
            run["selected"] = selected
            run["independent_confirmation"] = {}
            if selected is not None:
                variant = next(v for v in variants() if v[0] == selected)
                holdout = load_baseline_suite(HERE / "confirmation-queries.json")
                holdout.validate_workspace_regions(args.workspace)
                for backend_id in BACKENDS:
                    arm = next(a for a in arms if a.backend == backend_id and a.mode == "hybrid")
                    scope = scopes[arm.id]
                    backend = evidence.backend_registry(context).create(backend_id)
                    measured = {"control": [], selected: []}
                    for iteration in range(-1, 20):
                        for qi, case in enumerate(holdout.cases):
                            order = [("control", None, None, 60), variant]
                            if (qi + iteration) % 2:
                                order.reverse()
                            for v in order:
                                retrieval = replace(
                                    scope.retrieval,
                                    hybrid=replace(
                                        scope.retrieval.hybrid,
                                        lexical_fusion_window=v[1],
                                        dense_fusion_window=v[2],
                                        rrf_k=v[3],
                                    ),
                                )
                                binding = RepositoryBinding(
                                    scope.repository,
                                    scope.workspace,
                                    backend_id,
                                    scope.revision,
                                    scope.settings,
                                    retrieval,
                                    scope.knowledge_types,
                                )
                                service = RetrievalApplicationService(
                                    {scope.repository: binding},
                                    backends=BackendRegistry(
                                        {backend_id: lambda backend=backend: backend}
                                    ),
                                )
                                response = service.search(
                                    SearchRequest(
                                        query=case.intent.canonical_query,
                                        repository=scope.repository,
                                        limit=20,
                                    )
                                )
                                evidence.validate_response(
                                    response,
                                    arm,
                                    replace(scope, retrieval=retrieval),
                                    run["snapshots"][arm.id],
                                )
                                if iteration >= 0:
                                    measured[v[0]].append(
                                        observation(response, case.truth, iteration + 1)
                                    )
                    if evidence.snapshot_identity(arm, scope) != run["snapshots"][arm.id]:
                        raise ValueError("snapshot changed during independent confirmation")
                    run["independent_confirmation"][backend_id] = {
                        "observations": measured,
                        "comparison": compare(measured["control"], measured[selected]),
                    }
            holds = selected is not None and all(
                x["comparison"]["nonregression"] for x in run["independent_confirmation"].values()
            )
            gates = selected is not None and all(
                g["passed"] for g in run["promotion_gates"][selected].values()
            )
            run["decision"] = (
                "not-promoted"
                if not holds
                else "pending-performance-comparability"
                if not run["performance_comparable"]
                else "experimental-opt-in; validated only on studied corpus"
                if gates
                else "not-promoted"
            )
        run["aggregates"] = {key: summarize(rows) for key, rows in run["observations"].items()}
        run["status"] = "complete"
    except Exception as exc:
        run.update(status="blocked", error=f"{type(exc).__name__}: {exc}", decision="not-promoted")
        write(args.output, run)
        raise
    write(args.output, run)


def replay(args):
    private_output(args.output)
    _, _, suite, queries, identity = inputs()
    capture = read(args.capture, stage="capture")
    if capture.get("measurement") != FULL_MEASUREMENT or capture.get("measurement_kind") != "real":
        raise ValueError("full real acquisition measurement required for replay")
    if any(capture["identity"][key] != value for key, value in identity.items()):
        raise ValueError("capture corpus/query identity mismatch")
    observations = {}
    replay_cache = {}
    for backend in BACKENDS:
        rows = capture["captures"][backend + "-hybrid"]
        validate_rows(rows, queries, 20)
        for row in rows:
            if max(row["lexical_acquired"], row["dense_acquired"]) > 80:
                raise ValueError("captured acquisition exceeds budget")
            for doc in row["evidence"]:
                if doc.get("revision") != suite.corpus["revision"] or not doc.get(
                    "source_ref", ""
                ).startswith("repository://" + suite.corpus["repository"] + "/"):
                    raise ValueError("captured evidence provenance differs from corpus")
        observations[backend] = {v[0]: [] for v in variants()}
        truths = {c.query_id: c.truth for c in suite.cases}
        for row in rows:
            acquisition_key = mapping_fingerprint(
                {
                    "input": row["input"],
                    "evidence": row["evidence"],
                    "lexical_acquired": row["lexical_acquired"],
                    "dense_acquired": row["dense_acquired"],
                }
            )
            ordered = (
                variants()
                if (row["iteration"] + list(queries).index(row["query_id"])) % 2 == 0
                else variants()[::-1]
            )
            for variant in ordered:
                # Repeated identical acquisitions share immutable diagnostic payloads.
                # Include raw scores/materialization in identity, not just the kernel fingerprint.
                cache_key = (backend, row["query_id"], acquisition_key, variant[0])
                if cache_key not in replay_cache:
                    replay_cache[cache_key] = replay_row(
                        row,
                        variant,
                        80 if backend == "qdrant-vector" else 20,
                        truths[row["query_id"]],
                    )
                observations[backend][variant[0]].append(
                    {
                        **replay_cache[cache_key],
                        "iteration": row["iteration"],
                    }
                )
    comparisons = {
        v[0]: {b: compare(observations[b]["control"], observations[b][v[0]]) for b in BACKENDS}
        for v in variants()[1:]
    }
    axis_pairs = [
        (a[0], b[0])
        for a in variants()[1:10]
        for b in variants()[1:10]
        if a[0] < b[0] and ((a[1] == b[1]) != (a[2] == b[2]))
    ]
    pairs = {
        a + "/" + b: {
            backend: compare(observations[backend][a], observations[backend][b])
            for backend in BACKENDS
        }
        for a, b in axis_pairs
    }
    write(
        args.output,
        {
            "protocol": PROTOCOL,
            "schema_version": 1,
            "stage": "replay",
            "status": "complete",
            "identity": capture["identity"],
            "snapshots": capture["snapshots"],
            "measurement": capture["measurement"],
            "capture_fingerprint": digest(args.capture),
            "observations": observations,
            "aggregates": {
                backend: {key: summarize(rows) for key, rows in arms.items()}
                for backend, arms in observations.items()
            },
            "comparisons": comparisons,
            "one_axis_comparisons": pairs,
            "finalists": rank_finalists(comparisons),
            "decision": "offline-selection-only; real confirmation and comparable latency required",
        },
    )


def report(args):
    artifact = read(args.artifact, complete=False)
    # An allowlist deliberately excludes source payloads, endpoint configuration and snapshots.
    safe = {
        key: artifact[key]
        for key in (
            "protocol",
            "schema_version",
            "stage",
            "status",
            "measurement",
            "decision",
            "error",
            "snapshot_errors",
            "comparisons",
            "finalists",
            "performance_comparable",
            "performance_reason",
            "aggregates",
            "promotion_gates",
            "selected",
        )
        if key in artifact
    }
    if "snapshot_errors" in safe:

        def schema_details(details):
            allowed = {
                "projection_version",
                "projection_fingerprint",
                "embedding_model",
                "embedding_provider",
                "serializer_version",
                "serializer_mode",
                "serializer_fingerprint",
                "sparse_vector_name",
                "sparse_analyzer_version",
                "sparse_analyzer_fingerprint",
                "sparse_modifier",
                "expected_projection_version",
                "actual_projection_version",
                "expected_projection_fingerprint",
                "actual_projection_fingerprint",
                "rebuild_required",
                "vector_dimensions",
            }
            result = {key: value for key, value in details.items() if key in allowed}
            for key in ("actual", "expected"):
                if isinstance(details.get(key), dict):
                    result[key] = schema_details(details[key])
            return result

        safe["snapshot_errors"] = {
            key: {
                "type": value["type"],
                "category": value["category"],
                "details": schema_details(value.get("details", {})),
            }
            for key, value in safe["snapshot_errors"].items()
        }
    if (
        "error" in safe
        and safe["error"]
        != "ValueError: snapshot attestation failed; no retrieval or rebuild performed"
    ):
        safe["error"] = "Run stopped; inspect the private artifact for failure details."
    if artifact.get("independent_confirmation"):
        safe["independent_confirmation"] = {
            key: {
                "comparison": value["comparison"],
                "aggregates": {
                    variant: summarize(rows) for variant, rows in value["observations"].items()
                },
            }
            for key, value in artifact["independent_confirmation"].items()
        }
    safe.update(identity=artifact["identity"], raw_artifact_sha256=digest(args.artifact))
    safe["stage"] = "public-report"
    write(args.output, safe)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("control", "capture", "replay", "confirm", "report"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--performance-attestation", type=Path)
    for option in ("run-config", "workspace", "control", "capture", "replay", "artifact"):
        parser.add_argument("--" + option, type=Path)
    args = parser.parse_args()
    needed = {
        "control": ("run_config", "workspace"),
        "capture": ("run_config", "workspace", "control"),
        "confirm": ("run_config", "workspace", "control", "replay"),
        "replay": ("capture",),
        "report": ("artifact",),
    }
    if any(getattr(args, key) is None for key in needed[args.command]):
        parser.error("missing required inputs for " + args.command)
    if args.command in ("control", "capture", "confirm"):
        online(args)
    elif args.command == "replay":
        replay(args)
    else:
        report(args)


if __name__ == "__main__":
    main()
