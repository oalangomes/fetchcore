#!/usr/bin/env python3
"""Issue #84: immutable acquisition, offline fusion replay, and real confirmation."""

from __future__ import annotations

import argparse
from dataclasses import replace
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from statistics import mean
import subprocess
import sys
import tempfile
import time
import zlib

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
BACKEND_ALIASES = {"solr": "solr", "opensearch": "opensearch", "qdrant": "qdrant-vector"}
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


def checkpoint_path(directory, backend, query_id, *, partition=None):
    """Return a stable, path-safe checkpoint name for one backend/query pair."""
    key = hashlib.sha256(query_id.encode("utf-8")).hexdigest()
    scope = directory / backend
    if partition is not None:
        scope = scope / partition
    return scope / (key + ".json.gz")


def checkpoint_descriptor(path, query_id):
    return {"query_id": query_id, "path": str(path), "sha256": digest(path)}


def verify_checkpoint_descriptor(descriptor):
    path = Path(descriptor["path"])
    if descriptor.get("sha256") != digest(path):
        raise ValueError(f"checkpoint file checksum mismatch: {path.name}")
    return path


def save_checkpoint(path, *, identity_fingerprint, backend, query_id, payload):
    """Atomically persist a checksummed query checkpoint."""
    body = {
        "checkpoint_version": 1,
        "identity_fingerprint": identity_fingerprint,
        "backend": backend,
        "query_id": query_id,
        "payload": payload,
    }
    sealed = {**body, "checksum": mapping_fingerprint(body)}
    raw = json.dumps(sealed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with open(fd, "wb", closefd=True) as stream:
            stream.write(gzip.compress(raw, mtime=0))
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_checkpoint(path, *, identity_fingerprint, backend, query_id):
    """Load only a complete, untampered checkpoint bound to this run."""
    try:
        sealed = json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError, zlib.error) as exc:
        raise ValueError(f"invalid checkpoint: {path.name}") from exc
    checksum = sealed.pop("checksum", None)
    if checksum != mapping_fingerprint(sealed):
        raise ValueError(f"checkpoint checksum mismatch: {path.name}")
    if (
        sealed.get("checkpoint_version") != 1
        or sealed.get("identity_fingerprint") != identity_fingerprint
        or sealed.get("backend") != backend
        or sealed.get("query_id") != query_id
        or not isinstance(sealed.get("payload"), dict)
    ):
        raise ValueError(f"checkpoint identity mismatch: {path.name}")
    return sealed["payload"]


def reusable_checkpoint(path, *, resume, identity_fingerprint, backend, query_id):
    """Distinguish absence (execute) from invalid presence (fail closed)."""
    if not resume or not path.exists():
        return None
    return load_checkpoint(
        path, identity_fingerprint=identity_fingerprint, backend=backend, query_id=query_id
    )


def validate_query_checkpoint(payload, *, query_id, observation_keys, capture=False):
    observations = payload.get("observations")
    if not isinstance(observations, dict) or set(observations) != set(observation_keys):
        raise ValueError("checkpoint observation arms differ")
    for rows in observations.values():
        validate_rows(rows, [query_id], 20)
    captures = payload.get("captures")
    if not isinstance(captures, list):
        raise ValueError("checkpoint captures must be a list")
    if capture:
        validate_rows(captures, [query_id], 20)
    elif captures:
        raise ValueError("unexpected capture payload in this checkpoint stage")


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


def arm_id(backend, mode):
    """Resolve public arm identity from the existing manifest, never from a vendor ID."""
    arms = json.loads((ROOT / "benchmarks/baseline-v1/manifest.json").read_text())["arms"]
    matches = [a["id"] for a in arms if a["backend"] == backend and a["mode"] == mode]
    if len(matches) != 1:
        raise ValueError("exactly one manifest arm required for " + backend + "/" + mode)
    return matches[0]


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


def verify_control(control, identity, backends=BACKENDS):
    if control["identity"] != identity or control["measurement"] != FULL_MEASUREMENT:
        raise ValueError("control measurement/corpus/runtime identity mismatch")
    if control.get("measurement_kind") != "real":
        raise ValueError("real control required")
    for backend in backends:
        for mode in ("dense", "hybrid"):
            rows = control["observations"].get(arm_id(backend, mode), [])
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


def environment_matches_backend(expected, actual):
    """Compare the host/model identity and the service subset measured this run."""
    host_fields = (
        "profile_id", "python_version", "platform_system", "platform_release",
        "machine", "cpu_count", "embedding",
    )
    if any(expected.get(key) != actual.get(key) for key in host_fields):
        return False
    expected_services = expected.get("services", {})
    return all(expected_services.get(key) == value for key, value in actual.get("services", {}).items())


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


def iter_capture_rows(capture, backend, queries):
    """Yield legacy rows or validated query-shard rows one query at a time."""
    captures = capture["captures"][arm_id(backend, "hybrid")]
    if not captures or not (isinstance(captures[0], dict) and "path" in captures[0]):
        validate_rows(captures, queries, 20)
        yield from captures
        return
    seen_queries = set()
    expected_fp = capture["checkpoint_identities"][arm_id(backend, "hybrid")]
    for descriptor in captures:
        query_id = descriptor["query_id"]
        if query_id in seen_queries:
            raise ValueError("duplicate query checkpoint in capture manifest")
        seen_queries.add(query_id)
        checkpoint_file = verify_checkpoint_descriptor(descriptor)
        saved = load_checkpoint(
            checkpoint_file, identity_fingerprint=expected_fp,
            backend=backend, query_id=query_id,
        )
        query_rows = saved["captures"]
        validate_rows(query_rows, [query_id], 20)
        yield from query_rows
    if seen_queries != set(queries):
        raise ValueError("capture is missing query checkpoints")


def iter_capture_groups(capture, backend, queries):
    """Yield one query's captures, keeping new shard-based inputs bounded in memory."""
    captures = capture["captures"][arm_id(backend, "hybrid")]
    if captures and isinstance(captures[0], dict) and "path" in captures[0]:
        expected_fp = capture["checkpoint_identities"][arm_id(backend, "hybrid")]
        seen = set()
        for descriptor in captures:
            query_id = descriptor["query_id"]
            if query_id in seen:
                raise ValueError("duplicate query checkpoint in capture manifest")
            seen.add(query_id)
            checkpoint_file = verify_checkpoint_descriptor(descriptor)
            payload = load_checkpoint(
                checkpoint_file, identity_fingerprint=expected_fp,
                backend=backend, query_id=query_id,
            )
            validate_rows(payload["captures"], [query_id], 20)
            yield query_id, payload["captures"]
        if seen != set(queries):
            raise ValueError("capture is missing query checkpoints")
        return
    grouped = {query_id: [] for query_id in queries}
    for row in captures:
        grouped[row["query_id"]].append(row)
    for query_id in queries:
        validate_rows(grouped[query_id], [query_id], 20)
        yield query_id, grouped[query_id]


def compact_replay_row(row):
    keys = ("query_id", "iteration", *QUALITY, "budgets", "recovered_requirements", "candidates_examined",
            "candidates_returned", "returned_chars", "returned_line_count",
            "lexical_acquired", "dense_acquired", "lexical_admitted", "dense_admitted",
            "useful_hit_density")
    return {key: row[key] for key in keys if key in row}


def validate_replay_checkpoint(payload, *, query_id, variant_names):
    observations = payload.get("observations")
    compact = payload.get("comparison_rows")
    if not isinstance(observations, dict) or set(observations) != set(variant_names):
        raise ValueError("replay checkpoint variants differ")
    if not isinstance(compact, dict) or set(compact) != set(variant_names):
        raise ValueError("replay checkpoint comparison variants differ")
    for name in variant_names:
        validate_rows(observations[name], [query_id], 20)
        validate_rows(compact[name], [query_id], 20)


def replay_observation(replay, backend, variant, query_id, iteration):
    observations = replay["observations"][backend][variant]
    if observations and isinstance(observations[0], dict) and "path" in observations[0]:
        descriptor = next(item for item in observations if item["query_id"] == query_id)
        checkpoint_file = verify_checkpoint_descriptor(descriptor)
        payload = load_checkpoint(
            checkpoint_file,
            identity_fingerprint=replay["checkpoint_identities"][backend],
            backend=backend, query_id=query_id,
        )
        rows = payload["observations"][variant]
    else:
        rows = observations
    return next(row for row in rows if row["iteration"] == iteration)


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
    if args.resume:
        if args.output.exists():
            raise ValueError("cannot resume a completed artifact")
        if args.backend is None and args.command != "confirm":
            raise ValueError("--resume requires one explicit --backend")
        if args.output.resolve().is_relative_to(ROOT):
            raise ValueError("raw artifacts must be outside repository (private source)")
    else:
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
    if args.backend is not None:
        selected_backend = BACKEND_ALIASES[args.backend]
        arms = [a for a in arms if a.backend == selected_backend]
        if not arms:
            raise ValueError("no benchmark arms for backend " + args.backend)
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
        "embedding_identity": context.embedding_identity,
        "query_ids": list(queries),
        "snapshots": {},
        "snapshot_errors": {},
        "observations": {},
        "captures": {},
        "checkpoint_identities": {},
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
            if control.get("consolidated_backends") != list(BACKENDS):
                raise ValueError("capture/confirm requires a three-backend consolidated control")
            verify_control(
                control,
                identity,
                (BACKEND_ALIASES[args.backend],) if args.backend else BACKENDS,
            )
            if not environment_matches_backend(control["environment"], run["environment"]):
                raise ValueError("environment changed since control")
            expected_snapshots = {
                key: value for key, value in control["snapshots"].items()
                if key in run["snapshots"]
            }
            if expected_snapshots != run["snapshots"] or control["embedding_digest"] != run["embedding_digest"]:
                raise ValueError("snapshots/model changed since control")
            if control.get("embedding_identity") != run["embedding_identity"]:
                raise ValueError("embedding provider/model/dimensions changed since control")
            if args.command == "capture":
                run["control_artifact_fingerprint"] = digest(args.control)
        selected_variants = [("control", None, None, 60)]
        if args.command == "confirm":
            replay = read(args.replay, stage="replay")
            if replay.get("consolidated_backends") != list(BACKENDS):
                raise ValueError("confirm requires replay validated for all three backends")
            if replay["identity"] != identity or replay["snapshots"] != run["snapshots"]:
                raise ValueError("replay identity mismatch")
            selected_variants += [
                v for key in replay["finalists"] for v in variants() if v[0] == key
            ]
        checkpoint_dir = args.checkpoint_dir or Path(str(args.output) + ".checkpoints")
        if checkpoint_dir.resolve().is_relative_to(ROOT):
            raise ValueError("checkpoint directory must be outside repository")
        if args.resume:
            if not checkpoint_dir.is_dir():
                raise ValueError("checkpoint directory does not exist")
        elif checkpoint_dir.exists():
            raise ValueError("checkpoint directory exists; use --resume or choose a new output")
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
            checkpoint_identity = mapping_fingerprint({
                "stage": args.command, "identity": identity, "snapshots": run["snapshots"],
                "environment": run["environment"], "embedding_digest": run["embedding_digest"],
                "measurement": FULL_MEASUREMENT, "backend": arm.backend,
                "arm": arm.id,
                "variants": [v[0] for v in selected_variants],
                "workspace": str(args.workspace.resolve()),
                "control_artifact": digest(args.control) if args.control else None,
                "replay_artifact": digest(args.replay) if args.replay else None,
            })
            run["checkpoint_identities"][arm.id] = checkpoint_identity
            for qi, case in enumerate(suite.cases):
                checkpoint = checkpoint_path(
                    checkpoint_dir, arm.backend, case.query_id, partition=arm.id
                )
                saved = reusable_checkpoint(
                    checkpoint, resume=args.resume,
                    identity_fingerprint=checkpoint_identity,
                    backend=arm.backend, query_id=case.query_id,
                )
                if saved is not None:
                    expected_observations = [arm.id + "/" + v[0] for v in selected_variants]
                    validate_query_checkpoint(
                        saved, query_id=case.query_id,
                        observation_keys=expected_observations,
                        capture=args.command == "capture",
                    )
                    for key, rows in saved["observations"].items():
                        run["observations"].setdefault(key, []).extend(rows)
                    if args.command == "capture":
                        run["captures"][arm.id].append(checkpoint_descriptor(checkpoint, case.query_id))
                    print(arm.id, case.query_id, "checkpoint", flush=True)
                    continue
                query_observations = {arm.id + "/" + v[0]: [] for v in selected_variants}
                query_captures = []
                for iteration in range(-1, 20):
                    order = selected_variants if (qi + iteration) % 2 == 0 else selected_variants[::-1]
                    for variant in order:
                        scope = scopes[arm.id]
                        if variant[0] != "control":
                            hybrid = replace(
                                scope.retrieval.hybrid,
                                lexical_fusion_window=variant[1],
                                dense_fusion_window=variant[2], rrf_k=variant[3],
                            )
                            scope = replace(scope, retrieval=replace(scope.retrieval, hybrid=hybrid))
                        binding = RepositoryBinding(
                            scope.repository, scope.workspace, arm.backend, scope.revision,
                            scope.settings, scope.retrieval, scope.knowledge_types,
                        )
                        service = RetrievalApplicationService(
                            {scope.repository: binding},
                            backends=BackendRegistry({arm.backend: lambda backend=backend: backend}),
                        )
                        captured.clear()
                        call_started = time.perf_counter()
                        response = service.search(SearchRequest(
                            query=queries[case.query_id], repository=scope.repository, limit=20,
                        ))
                        end_to_end_ms = (time.perf_counter() - call_started) * 1000
                        evidence.validate_response(response, arm, scope, run["snapshots"][arm.id])
                        if iteration < 0:
                            continue
                        measured = observation(response, case.truth, iteration + 1)
                        measured["end_to_end_ms"] = end_to_end_ms
                        query_observations[arm.id + "/" + variant[0]].append(measured)
                        if args.command in ("capture", "confirm"):
                            if len(captured) != 1:
                                raise ValueError("exactly one acquisition capture required")
                            c = captured[0]
                            if max(c.lexical_acquired, c.dense_acquired) > 80:
                                raise ValueError("acquisition exceeded 80")
                            row = {
                                "query_id": case.query_id, "iteration": iteration + 1,
                                "input": c.input.to_mapping(),
                                "evidence": [d.to_mapping() for d in c.evidence],
                                "lexical_acquired": c.lexical_acquired,
                                "dense_acquired": c.dense_acquired,
                            }
                            replayed = replay_row(
                                row, variant, 80 if arm.backend == "qdrant-vector" else 20, case.truth,
                            )
                            if replayed["result_spans"] != measured["result_spans"]:
                                raise ValueError("capture replay differs from real materialization")
                            if replayed["fusion"]["output_fingerprint"] != response.metadata[
                                "fusion_output_fingerprint"
                            ]:
                                raise ValueError("capture replay fusion fingerprint differs from runtime")
                            row["real_fusion_output_fingerprint"] = response.metadata[
                                "fusion_output_fingerprint"
                            ]
                            if args.command == "capture":
                                query_captures.append(row)
                            else:
                                expected = replay_observation(
                                    replay, arm.backend, variant[0], case.query_id, iteration + 1
                                )
                                same_acquisition = expected["full_acquisition_fingerprint"] == c.input.fingerprint
                                measured["same_acquisition_as_replay"] = same_acquisition
                                measured["full_acquisition_fingerprint"] = c.input.fingerprint
                                if same_acquisition and (
                                    expected["fusion"]["output_fingerprint"] != replayed["fusion"]["output_fingerprint"]
                                    or expected["result_spans"] != replayed["result_spans"]
                                    or expected["evidence_provenance"] != replayed["evidence_provenance"]
                                ):
                                    raise ValueError("real confirmation differs for identical acquired candidates")
                save_checkpoint(
                    checkpoint, identity_fingerprint=checkpoint_identity,
                    backend=arm.backend, query_id=case.query_id,
                    payload={"observations": query_observations, "captures": query_captures},
                )
                for key, rows in query_observations.items():
                    run["observations"].setdefault(key, []).extend(rows)
                if args.command == "capture":
                    run["captures"][arm.id].append(checkpoint_descriptor(checkpoint, case.query_id))
                print(arm.id, case.query_id, "checkpointed", flush=True)
            if evidence.snapshot_identity(arm, scopes[arm.id]) != run["snapshots"][arm.id]:
                raise ValueError("snapshot changed during measurement")
        if args.command == "control":
            run["observations"] = {
                key.split("/")[0]: value for key, value in run["observations"].items()
            }
            verify_control(run, identity, tuple(sorted({a.backend for a in arms})))
        if args.command == "confirm":
            run["comparisons"] = {
                v[0]: {
                    b: compare(
                        run["observations"][arm_id(b, "hybrid") + "/control"],
                        run["observations"][arm_id(b, "hybrid") + "/" + v[0]],
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
                        run["observations"][arm_id(b, "hybrid") + "/control"],
                        run["observations"][arm_id(b, "hybrid") + "/" + v[0]],
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
                    holdout_identity = mapping_fingerprint({
                        "stage": "confirm-holdout", "identity": identity,
                        "holdout_fingerprint": holdout.fingerprint,
                        "control_artifact": digest(args.control),
                        "replay_artifact": digest(args.replay),
                        "snapshot": run["snapshots"][arm.id],
                        "embedding_digest": run["embedding_digest"],
                        "config": mapping_fingerprint(config),
                        "backend": backend_id, "selected": selected,
                    })
                    for qi, case in enumerate(holdout.cases):
                        checkpoint = checkpoint_path(
                            checkpoint_dir / "holdout", backend_id, case.query_id,
                            partition="confirmation",
                        )
                        saved = reusable_checkpoint(
                            checkpoint, resume=args.resume,
                            identity_fingerprint=holdout_identity,
                            backend=backend_id, query_id=case.query_id,
                        )
                        expected_keys = ("control", selected)
                        if saved is not None:
                            validate_query_checkpoint(
                                saved, query_id=case.query_id,
                                observation_keys=expected_keys,
                            )
                            query_measured = saved["observations"]
                        else:
                            query_measured = {key: [] for key in expected_keys}
                            for iteration in range(-1, 20):
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
                                        scope.repository, scope.workspace, backend_id,
                                        scope.revision, scope.settings, retrieval,
                                        scope.knowledge_types,
                                    )
                                    service = RetrievalApplicationService(
                                        {scope.repository: binding},
                                        backends=BackendRegistry(
                                            {backend_id: lambda backend=backend: backend}
                                        ),
                                    )
                                    response = service.search(SearchRequest(
                                        query=case.intent.canonical_query,
                                        repository=scope.repository, limit=20,
                                    ))
                                    evidence.validate_response(
                                        response, arm,
                                        replace(scope, retrieval=retrieval),
                                        run["snapshots"][arm.id],
                                    )
                                    if iteration >= 0:
                                        query_measured[v[0]].append(
                                            observation(response, case.truth, iteration + 1)
                                        )
                            save_checkpoint(
                                checkpoint, identity_fingerprint=holdout_identity,
                                backend=backend_id, query_id=case.query_id,
                                payload={"observations": query_measured, "captures": []},
                            )
                        for key, rows in query_measured.items():
                            measured[key].extend(rows)
                    validate_rows(measured["control"], [c.query_id for c in holdout.cases], 20)
                    validate_rows(measured[selected], [c.query_id for c in holdout.cases], 20)
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
        blocked_path = args.output.with_name(args.output.name + ".blocked.json.gz")
        if not blocked_path.exists():
            write(blocked_path, run)
        raise
    write(args.output, run)


def replay(args):
    if getattr(args, "resume", False):
        if args.output.exists() or not args.capture:
            raise ValueError("cannot resume a completed or unbound replay")
        if args.output.resolve().is_relative_to(ROOT):
            raise ValueError("raw artifacts must be outside repository (private source)")
    else:
        private_output(args.output)
    _, _, suite, queries, identity = inputs()
    capture = read(args.capture, stage="capture")
    if capture.get("measurement") != FULL_MEASUREMENT or capture.get("measurement_kind") != "real":
        raise ValueError("full real acquisition measurement required for replay")
    if any(capture["identity"][key] != value for key, value in identity.items()):
        raise ValueError("capture corpus/query identity mismatch")
    observations = {}
    comparison_rows = {}
    available = {
        backend for backend in BACKENDS
        if arm_id(backend, "hybrid") in capture["captures"]
    }
    requested_backend = getattr(args, "backend", None)
    selected = (
        {requested_backend if requested_backend in BACKENDS else BACKEND_ALIASES[requested_backend]}
        if requested_backend else available
    )
    if not selected or not selected <= available:
        raise ValueError("capture does not contain the requested backend(s)")
    checkpoint_dir = getattr(args, "checkpoint_dir", None) or Path(str(args.output) + ".checkpoints")
    if checkpoint_dir.resolve().is_relative_to(ROOT):
        raise ValueError("checkpoint directory must be outside repository")
    if getattr(args, "resume", False):
        if not checkpoint_dir.is_dir():
            raise ValueError("replay checkpoint directory does not exist")
    elif checkpoint_dir.exists():
        raise ValueError("replay checkpoint directory exists; use --resume or choose a new output")
    capture_fingerprint = digest(args.capture)
    checkpoint_identities = {}
    for backend in sorted(selected):
        observations[backend] = {v[0]: [] for v in variants()}
        comparison_rows[backend] = {v[0]: [] for v in variants()}
        truths = {c.query_id: c.truth for c in suite.cases}
        checkpoint_identity = mapping_fingerprint({
            "stage": "replay", "capture_fingerprint": capture_fingerprint,
            "identity": capture["identity"], "backend": backend,
            "variants": [v[0] for v in variants()],
        })
        checkpoint_identities[backend] = checkpoint_identity
        for query_id, query_captures in iter_capture_groups(capture, backend, queries):
            checkpoint = checkpoint_path(checkpoint_dir, backend, query_id)
            saved = reusable_checkpoint(
                checkpoint, resume=getattr(args, "resume", False),
                identity_fingerprint=checkpoint_identity, backend=backend, query_id=query_id,
            )
            if saved is None:
                per_variant = {v[0]: [] for v in variants()}
                compact = {v[0]: [] for v in variants()}
                query_cache = {}
                for row in query_captures:
                    if max(row["lexical_acquired"], row["dense_acquired"]) > 80:
                        raise ValueError("captured acquisition exceeds budget")
                    for doc in row["evidence"]:
                        if doc.get("revision") != suite.corpus["revision"] or not doc.get(
                            "source_ref", ""
                        ).startswith("repository://" + suite.corpus["repository"] + "/"):
                            raise ValueError("captured evidence provenance differs from corpus")
                    acquisition_key = mapping_fingerprint({
                        "input": row["input"], "evidence": row["evidence"],
                        "lexical_acquired": row["lexical_acquired"],
                        "dense_acquired": row["dense_acquired"],
                    })
                    for variant in variants():
                        cache_key = (acquisition_key, variant[0])
                        if cache_key not in query_cache:
                            query_cache[cache_key] = replay_row(
                                row, variant, 80 if backend == "qdrant-vector" else 20,
                                truths[query_id],
                            )
                        replayed = {**query_cache[cache_key], "iteration": row["iteration"]}
                        per_variant[variant[0]].append(replayed)
                        compact[variant[0]].append(compact_replay_row(replayed))
                saved = {"observations": per_variant, "comparison_rows": compact}
                save_checkpoint(
                    checkpoint, identity_fingerprint=checkpoint_identity,
                    backend=backend, query_id=query_id, payload=saved,
                )
            validate_replay_checkpoint(
                saved, query_id=query_id, variant_names=[v[0] for v in variants()]
            )
            descriptor = checkpoint_descriptor(checkpoint, query_id)
            for variant in variants():
                observations[backend][variant[0]].append(descriptor)
                comparison_rows[backend][variant[0]].extend(
                    saved["comparison_rows"][variant[0]]
                )
        for variant in variants():
            validate_rows(comparison_rows[backend][variant[0]], queries, 20)
    comparisons = {
        v[0]: {
            b: compare(comparison_rows[b]["control"], comparison_rows[b][v[0]])
            for b in BACKENDS
        }
        for v in variants()[1:]
    } if selected == set(BACKENDS) else {}
    axis_pairs = [
        (a[0], b[0])
        for a in variants()[1:10]
        for b in variants()[1:10]
        if a[0] < b[0] and ((a[1] == b[1]) != (a[2] == b[2]))
    ]
    pairs = {
        a + "/" + b: {
            backend: compare(comparison_rows[backend][a], comparison_rows[backend][b])
            for backend in BACKENDS
        }
        for a, b in axis_pairs
    } if selected == set(BACKENDS) else {}
    write(
        args.output,
        {
            "protocol": PROTOCOL,
            "schema_version": 1,
            "stage": "replay",
            "status": "complete",
            "identity": capture["identity"],
            "snapshots": capture["snapshots"],
            "backend_artifacts": sorted(selected),
            "consolidated_backends": list(BACKENDS) if selected == set(BACKENDS) else [],
            "checkpoint_identities": checkpoint_identities,
            "measurement": capture["measurement"],
            "capture_fingerprint": digest(args.capture),
            "environment": capture.get("environment"),
            "embedding_identity": capture.get("embedding_identity"),
            "embedding_digest": capture.get("embedding_digest"),
            "query_ids": list(queries),
            "observations": observations,
            "aggregates": {
                backend: {
                    key: summarize(comparison_rows[backend][key]) for key in arms
                } for backend, arms in observations.items()
            },
            "comparisons": comparisons,
            "one_axis_comparisons": pairs,
            "finalists": rank_finalists(comparisons) if comparisons else [],
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
            "one_axis_comparisons",
            "finalists",
            "performance_comparable",
            "performance_reason",
            "aggregates",
            "consolidation",
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
    safe.update(identity=public_identity(artifact["identity"]), raw_artifact_sha256=digest(args.artifact))
    safe["stage"] = "public-report"
    write(args.output, safe)
    if getattr(args, "diagnostics_output", None):
        write(args.diagnostics_output, public_diagnostics(artifact, digest(args.artifact)))


def public_identity(identity):
    """Keep reproducibility identifiers while dropping unrecognized/private fields."""
    fields = (
        "ground_truth_fingerprint", "metrics_fingerprint",
        "context_yield_metrics_fingerprint", "query_fingerprint",
        "confirmation_fingerprint", "historical_reference_fingerprint",
        "effective_config_fingerprint",
    )
    result = {key: identity[key] for key in fields if key in identity}
    corpus = identity.get("corpus")
    if isinstance(corpus, dict):
        result["corpus"] = {
            key: corpus[key] for key in ("repository", "revision") if key in corpus
        }
    runtime = identity.get("runtime")
    if isinstance(runtime, dict):
        result["runtime"] = {
            key: runtime[key]
            for key in ("revision", "runtime_fingerprint", "runner_fingerprint", "dirty")
            if key in runtime
        }
    return result


def public_diagnostics(artifact, raw_artifact_sha256):
    """Project existing safe report evidence; never serialize raw captures/errors."""
    keys = (
        "protocol", "schema_version", "stage", "status", "measurement", "decision",
        "comparisons", "one_axis_comparisons", "finalists", "aggregates",
        "promotion_gates", "selected", "consolidation",
    )
    result = {key: artifact[key] for key in keys if key in artifact}
    result["identity"] = public_identity(artifact.get("identity", {}))
    result["raw_artifact_sha256"] = raw_artifact_sha256
    if artifact.get("error"):
        result["error"] = (
            artifact["error"]
            if artifact["error"] ==
            "ValueError: snapshot attestation failed; no retrieval or rebuild performed"
            else "Run stopped; inspect the private artifact for failure details."
        )
    if artifact.get("independent_confirmation"):
        result["independent_confirmation"] = {
            backend: {
                "comparison": value["comparison"],
                "aggregates": {
                    name: summarize(rows) for name, rows in value["observations"].items()
                },
            }
            for backend, value in artifact["independent_confirmation"].items()
        }
    return result


def consolidate(args):
    """Seal a multi-backend artifact only after every backend artifact validates."""
    private_output(args.output)
    if len(args.backend_artifact) != len(BACKENDS):
        raise ValueError("consolidation requires exactly three backend artifacts")
    artifacts = [read(path, complete=True) for path in args.backend_artifact]
    stage = artifacts[0]["stage"]
    if stage not in ("control", "capture", "replay"):
        raise ValueError("only control, capture, or replay artifacts can be consolidated")
    if any(a["stage"] != stage for a in artifacts):
        raise ValueError("backend artifact stages differ")
    if any(a["identity"] != artifacts[0]["identity"] for a in artifacts[1:]):
        raise ValueError("backend artifact runner/input fingerprints differ")
    for field in ("measurement", "measurement_kind", "query_ids"):
        if any(a.get(field) != artifacts[0].get(field) for a in artifacts[1:]):
            raise ValueError("backend artifact measurement/query manifests differ")
    backend_by_artifact = {}
    for artifact in artifacts:
        if stage == "replay":
            # Per-backend replay artifacts carry the full common snapshot map
            # from consolidated capture, so identify them by their explicit
            # backend manifest instead of mistaking shared snapshots for three
            # measured backends.
            backends = set(artifact.get("backend_artifacts", []))
        else:
            backends = {
                backend for backend in BACKENDS
                if any(
                    key.startswith(arm_id(backend, "hybrid"))
                    for key in artifact.get("observations", {})
                )
                or arm_id(backend, "hybrid") in artifact.get("captures", {})
                or arm_id(backend, "hybrid") in artifact.get("snapshots", {})
            }
        if len(backends) != 1:
            raise ValueError("each input must contain exactly one backend")
        backend = next(iter(backends))
        if backend in backend_by_artifact:
            raise ValueError("duplicate backend artifact")
        backend_by_artifact[backend] = artifact
    if set(backend_by_artifact) != set(BACKENDS):
        raise ValueError("consolidation requires Solr, OpenSearch, and Qdrant")
    # Canonical backend order makes the sealed result independent of CLI order.
    artifacts = [backend_by_artifact[backend] for backend in BACKENDS]
    merged = dict(artifacts[0])
    for field in ("snapshots", "snapshot_errors", "observations", "captures", "checkpoint_identities"):
        merged[field] = {}
        for artifact in artifacts:
            for key, value in artifact.get(field, {}).items():
                if key in merged[field] and (field != "snapshots" or merged[field][key] != value):
                    raise ValueError("duplicate or incompatible consolidated " + field)
                merged[field][key] = value
    if stage in ("control", "capture"):
        merged["aggregates"] = {}
        for backend in BACKENDS:
            artifact = backend_by_artifact[backend]
            expected_arms = (
                {arm_id(backend, mode) for mode in ("dense", "hybrid")}
                if stage == "control" else {arm_id(backend, "hybrid") + "/control"}
            )
            if set(artifact.get("observations", {})) != expected_arms:
                raise ValueError("backend observation arms are missing or incompatible")
            if set(artifact.get("aggregates", {})) != expected_arms:
                raise ValueError("backend aggregate arms are missing or incompatible")
            for arm in sorted(expected_arms):
                rows = artifact["observations"][arm]
                validate_rows(rows, artifact["query_ids"], 20)
                aggregate = summarize(rows)
                if aggregate != artifact["aggregates"][arm]:
                    raise ValueError("backend aggregate differs from observations")
                merged["aggregates"][arm] = aggregate
    if stage == "control":
        services = {}
        reference_environment = artifacts[0].get("environment", {})
        host_fields = (
            "profile_id", "python_version", "platform_system", "platform_release",
            "machine", "cpu_count", "embedding",
        )
        for artifact in backend_by_artifact.values():
            environment = artifact.get("environment", {})
            if any(environment.get(key) != reference_environment.get(key) for key in host_fields):
                raise ValueError("control backend runtime/environment identities differ")
            if (
                artifact.get("embedding_identity") != artifacts[0].get("embedding_identity")
                or artifact.get("embedding_digest") != artifacts[0].get("embedding_digest")
            ):
                raise ValueError("control backend embedding model identities differ")
            for service, version in artifact.get("service_versions", {}).items():
                if service in services and services[service] != version:
                    raise ValueError("control backend service versions differ")
                services[service] = version
        merged["service_versions"] = services
        merged["environment"] = {**reference_environment, "services": dict(sorted(services.items()))}
        verify_control(merged, merged["identity"])
    elif stage == "capture":
        if args.control is None:
            raise ValueError("capture consolidation requires --control consolidated-control.json.gz")
        control = read(args.control, stage="control")
        if control.get("consolidated_backends") != list(BACKENDS):
            raise ValueError("capture consolidation requires a validated three-backend control")
        verify_control(control, control["identity"])
        expected_control_fingerprint = digest(args.control)
        if control["identity"] != merged["identity"]:
            raise ValueError("capture/control runner and input identities differ")
        merged["environment"] = control["environment"]
        merged["service_versions"] = control["service_versions"]
        merged["embedding_identity"] = control["embedding_identity"]
        merged["embedding_digest"] = control["embedding_digest"]
        for backend in BACKENDS:
            arm = arm_id(backend, "hybrid")
            if arm not in merged["captures"] or len(merged["captures"][arm]) != len(merged["query_ids"]):
                raise ValueError("capture backend is missing validated query checkpoints")
            artifact = backend_by_artifact[backend]
            if artifact.get("control_artifact_fingerprint") != expected_control_fingerprint:
                raise ValueError("capture backends were measured against different controls")
            if (
                artifact.get("embedding_identity") != control.get("embedding_identity")
                or artifact.get("embedding_digest") != control.get("embedding_digest")
                or not environment_matches_backend(control["environment"], artifact["environment"])
            ):
                raise ValueError("capture backend environment/model differs from control")
            if artifact["snapshots"].get(arm) != control["snapshots"].get(arm):
                raise ValueError("capture backend snapshot differs from its control snapshot")
            expected_fp = merged["checkpoint_identities"][arm]
            query_ids = [descriptor["query_id"] for descriptor in merged["captures"][arm]]
            if len(set(query_ids)) != len(query_ids) or set(query_ids) != set(merged["query_ids"]):
                raise ValueError("capture backend has duplicate or missing query checkpoints")
            for descriptor in merged["captures"][arm]:
                checkpoint_file = verify_checkpoint_descriptor(descriptor)
                checkpoint = load_checkpoint(
                    checkpoint_file, identity_fingerprint=expected_fp,
                    backend=backend, query_id=descriptor["query_id"],
                )
                validate_query_checkpoint(
                    checkpoint, query_id=descriptor["query_id"],
                    observation_keys=[arm + "/control"], capture=True,
                )
                corpus = merged["identity"].get("corpus", {})
                for row in checkpoint["captures"]:
                    for document in row["evidence"]:
                        if (
                            document.get("revision") != corpus.get("revision")
                            or not document.get("source_ref", "").startswith(
                                "repository://" + corpus.get("repository", "") + "/"
                            )
                        ):
                            raise ValueError("captured evidence provenance differs from corpus")
    else:
        merged["observations"] = {}
        comparison_rows = {}
        merged["capture_fingerprints"] = {}
        for backend, artifact in backend_by_artifact.items():
            merged["observations"][backend] = artifact["observations"][backend]
            merged["capture_fingerprints"][backend] = artifact["capture_fingerprint"]
            comparison_rows[backend] = {variant[0]: [] for variant in variants()}
            expected_fp = artifact["checkpoint_identities"][backend]
            expected_query_ids = set(artifact["query_ids"])
            descriptors_by_variant = {}
            for variant in variants():
                descriptors = artifact["observations"][backend][variant[0]]
                actual_query_ids = [descriptor["query_id"] for descriptor in descriptors]
                if len(set(actual_query_ids)) != len(actual_query_ids) or set(actual_query_ids) != expected_query_ids:
                    raise ValueError("replay manifest has duplicate or missing query shards")
                descriptors_by_variant[variant[0]] = {
                    descriptor["query_id"]: descriptor for descriptor in descriptors
                }
            for descriptor in artifact["observations"][backend]["control"]:
                checkpoint_file = verify_checkpoint_descriptor(descriptor)
                payload = load_checkpoint(
                    checkpoint_file, identity_fingerprint=expected_fp,
                    backend=backend, query_id=descriptor["query_id"],
                )
                validate_replay_checkpoint(
                    payload, query_id=descriptor["query_id"],
                    variant_names=[v[0] for v in variants()],
                )
                for variant, _lexical, _dense, _rrf in variants():
                    if descriptors_by_variant[variant][descriptor["query_id"]] != descriptor:
                        raise ValueError("replay variants reference different query checkpoints")
                for variant, rows in payload["comparison_rows"].items():
                    comparison_rows[backend][variant].extend(rows)
        capture_fingerprints = {a.get("capture_fingerprint") for a in artifacts}
        if len(capture_fingerprints) != 1:
            raise ValueError("replay backends consumed different capture artifacts")
        if any(a.get("snapshots") != artifacts[0].get("snapshots") for a in artifacts[1:]):
            raise ValueError("replay backend snapshot identities differ")
        if any(
            a.get("embedding_identity") != artifacts[0].get("embedding_identity")
            or a.get("embedding_digest") != artifacts[0].get("embedding_digest")
            or a.get("environment") != artifacts[0].get("environment")
            for a in artifacts[1:]
        ):
            raise ValueError("replay backend model/environment identities differ")
        merged["capture_fingerprint"] = next(iter(capture_fingerprints))
        merged["backend_artifacts"] = list(BACKENDS)
        merged["query_ids"] = artifacts[0]["query_ids"]
        merged["aggregates"] = {
            backend: {
                key: summarize(comparison_rows[backend][key])
                for key in merged["observations"][backend]
            }
            for backend in BACKENDS
        }
        merged["comparisons"] = {
            variant[0]: {
                backend: compare(
                    comparison_rows[backend]["control"],
                    comparison_rows[backend][variant[0]],
                ) for backend in BACKENDS
            } for variant in variants()[1:]
        }
        axis_pairs = [
            (a[0], b[0])
            for a in variants()[1:10]
            for b in variants()[1:10]
            if a[0] < b[0] and ((a[1] == b[1]) != (a[2] == b[2]))
        ]
        merged["one_axis_comparisons"] = {
            a + "/" + b: {
                backend: compare(
                    comparison_rows[backend][a], comparison_rows[backend][b]
                ) for backend in BACKENDS
            } for a, b in axis_pairs
        }
        merged["finalists"] = rank_finalists(merged["comparisons"])
    merged["consolidated_backends"] = list(BACKENDS)
    merged["consolidation"] = {
        "version": 1,
        "processor_runner_sha256": digest(Path(__file__)),
        "inputs": sorted(
            ({"sha256": digest(path)} for path in args.backend_artifact),
            key=lambda item: item["sha256"],
        ),
    }
    write(args.output, merged)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("control", "capture", "replay", "confirm", "report", "consolidate"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--performance-attestation", type=Path)
    parser.add_argument("--diagnostics-output", type=Path)
    parser.add_argument("--backend", choices=tuple(BACKEND_ALIASES))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--backend-artifact", type=Path, action="append", default=[])
    for option in ("run-config", "workspace", "control", "capture", "replay", "artifact"):
        parser.add_argument("--" + option, type=Path)
    args = parser.parse_args()
    needed = {
        "control": ("run_config", "workspace"),
        "capture": ("run_config", "workspace", "control"),
        "confirm": ("run_config", "workspace", "control", "replay"),
        "replay": ("capture",),
        "report": ("artifact",),
        "consolidate": (),
    }
    if any(getattr(args, key) is None for key in needed[args.command]):
        parser.error("missing required inputs for " + args.command)
    if args.command == "consolidate":
        if not args.backend_artifact:
            parser.error("consolidate requires --backend-artifact three times")
        consolidate(args)
    elif args.command in ("control", "capture", "confirm"):
        if args.command == "confirm" and args.backend is not None:
            parser.error("confirm is a cross-backend stage and cannot select one --backend")
        if args.resume and args.command in ("control", "capture") and args.backend is None:
            parser.error("--resume requires an explicit --backend")
        online(args)
    elif args.command == "replay":
        replay(args)
    else:
        report(args)


if __name__ == "__main__":
    main()
