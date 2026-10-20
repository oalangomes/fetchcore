"""Issue #19 paired evidence experiment. The frozen baseline is read-only."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from statistics import mean
from time import sleep

from fetchcore.application import (
    BackendRegistry,
    RepositoryBinding,
    RetrievalApplicationService,
    SearchRequest,
)
from fetchcore.application.composition import EmbeddingProviderRegistry
from fetchcore.baseline import (
    BASELINE_CONTEXT_YIELD_METRICS_SPEC,
    BASELINE_LINE_BUDGETS,
    BaselineConfigurationError,
    BaselineRunner,
    build_baseline_artifact,
    capture_baseline_environment,
    evaluate_line_budgeted_evidence,
    evaluate_retrieval,
    load_baseline_suite,
    mapping_fingerprint,
)
from fetchcore.retrieval.contracts import KnowledgeSearchError, SearchIntent, SearchScope

ROOT = Path(__file__).resolve().parent
PROTOCOL = "fetchcore.search-intent-v2-evidence"
SCHEMA_VERSION = 1
REPRESENTATIONS = ("legacy", "canonical")
TRANSIENT_ARM_FAILURE_CATEGORIES = frozenset({"timeout", "unavailable"})
MEASUREMENT_CONTRACT_FIELDS = (
    "protocol",
    "schema_version",
    "arms",
    "representations",
    "warmups",
    "repetitions",
    "k",
    "corpus",
    "ground_truth_fingerprint",
    "canonical_query_set_fingerprint",
    "baseline_manifest_fingerprint",
    "runtime_source_fingerprint",
    "embedding_identity",
    "fusion_identity",
    "environment",
)
FROZEN_FILES = {
    "manifest.json": "d5f1a7f28c605fe54a2113c0b0d041b8004b0892b6d9ba6f27f4008289ae96d5",
    "ground-truth.json": "16a60143cd161e9d14d0a28016eb4ea58173a5e478b1ec6651210cdb3df0469f",
}


def load_frozen_runner():
    # Reuse the existing setup adapters without editing the frozen runner.
    name = "fetchcore_frozen_benchmark_setup"
    spec = importlib.util.spec_from_file_location(name, ROOT.parent / "baseline-v1/run.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


setup = load_frozen_runner()


def load_inputs(query_path=ROOT / "evidence-queries.json"):
    for filename, digest in FROZEN_FILES.items():
        if hashlib.sha256((setup.ROOT / filename).read_bytes()).hexdigest() != digest:
            raise BaselineConfigurationError(f"frozen baseline file changed: {filename}")
    suite = load_baseline_suite(setup.GROUND_TRUTH_PATH)
    payload = json.loads(query_path.read_text())
    if payload.get("schema_version") != 1 or not isinstance(payload.get("queries"), list):
        raise BaselineConfigurationError("invalid canonical query set schema")
    queries = {}
    for item in payload["queries"]:
        if not isinstance(item, dict) or set(item) != {"query_id", "canonical_query"}:
            raise BaselineConfigurationError("query entries require only query_id/canonical_query")
        key, query = item["query_id"], item["canonical_query"]
        if not isinstance(key, str) or key in queries:
            raise BaselineConfigurationError("invalid or duplicate query id")
        if not isinstance(query, str) or not query.strip() or query != query.strip():
            raise BaselineConfigurationError("canonical query must be nonempty, trimmed text")
        queries[key] = query
    if set(queries) != {case.query_id for case in suite.cases}:
        raise BaselineConfigurationError("missing or unknown canonical query ids")
    return suite, queries, mapping_fingerprint(payload)


def controlled_intent(query, representation):
    if representation == "legacy":
        return SearchIntent(semantic_terms=(query,))
    if representation == "canonical":
        return SearchIntent(canonical_query=query)
    raise BaselineConfigurationError(f"unknown representation: {representation}")


def backend_registry(context):
    # Current application composition resolves provider settings explicitly.
    # OLLAMA_URL alone is only consumed by the reused indexing helpers.
    return BackendRegistry(
        providers=EmbeddingProviderRegistry(
            settings={"ollama": {"url": context.endpoints["ollama"]}}
        )
    )


def source_identity():
    repo = ROOT.parents[1]

    def digest(paths):
        return mapping_fingerprint(
            {
                str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(paths)
            }
        )

    return {
        "fetchcore_revision": setup._git_revision(repo),
        "runtime_source_fingerprint": digest((repo / "src/fetchcore").rglob("*.py")),
        "runner_source_fingerprint": digest([Path(__file__), setup.ROOT / "run.py"]),
        "working_tree_dirty": bool(setup._git(repo, "status", "--porcelain").strip()),
    }


def snapshot_identity(arm, scope):
    """Read persisted snapshot metadata before/after; no backend writes here."""
    if arm.backend == "local-semantic":
        setup._validate_workspace(scope.workspace, scope.revision)
        return {
            "repository": scope.repository,
            "revision": scope.revision,
            "projection": "not_applicable_safe_scan",
        }
    if arm.backend == "solr":
        settings = setup.SolrSettings.from_mapping(scope.settings)
        store = setup.SolrIndexStore(setup.UrllibSolrTransport(settings), settings)
    elif arm.backend == "opensearch":
        settings = setup.OpenSearchSettings.from_mapping(scope.settings)
        store = setup.OpenSearchIndexStore(
            setup.UrllibOpenSearchTransport(settings), settings, clock=setup._utc_now
        )
    elif arm.backend == "qdrant-vector":
        settings = setup.QdrantVectorSettings.from_mapping(scope.settings)
        provider = setup.OllamaEmbeddingProvider(
            url=settings.embedding_url, model=settings.embedding_model
        )
        store = setup.QdrantVectorIndexStore(
            setup.UrllibQdrantTransport(settings), settings, provider, clock=setup._utc_now
        )
    else:
        raise BaselineConfigurationError(f"unsupported snapshot backend: {arm.backend}")
    snapshot = store.load_snapshot(scope.repository)
    if snapshot.revision != scope.revision:
        raise BaselineConfigurationError("snapshot corpus revision mismatch")
    return asdict(snapshot)


def validate_response(response, arm, scope, snapshot=None):
    if response.status not in {"found", "not_found", "success"}:
        raise BaselineConfigurationError(f"retrieval failed: {response.status}")
    if response.telemetry is None:
        raise BaselineConfigurationError("runtime telemetry is required")
    fingerprints = response.telemetry.to_mapping().get("fingerprints", {})
    required = set()
    if arm.backend != "local-semantic":
        if response.metadata.get("snapshot_revision") != scope.revision:
            raise BaselineConfigurationError("response snapshot revision mismatch")
        required.add("projection")
    if arm.mode != "lexical":
        required.add("embedding_serializer")
        if (
            response.metadata.get("embedding_provider"),
            response.metadata.get("embedding_model"),
            response.metadata.get("vector_dimensions"),
        ) != ("ollama", "embeddinggemma", 768):
            raise BaselineConfigurationError("embedding identity mismatch")
    if arm.mode == "hybrid":
        required.update(("fusion_input", "fusion_output"))
    if snapshot is not None and arm.backend != "local-semantic":
        if fingerprints.get("projection") != snapshot.get("projection_fingerprint"):
            raise BaselineConfigurationError("response projection differs from paired snapshot")
        serializer = snapshot.get(
            "embedding_serializer_fingerprint", snapshot.get("serializer_fingerprint")
        )
        if arm.mode != "lexical" and fingerprints.get("embedding_serializer") != serializer:
            raise BaselineConfigurationError("response embedding serializer differs from snapshot")
    if any(not fingerprints.get(key) for key in required):
        raise BaselineConfigurationError(f"missing applicable fingerprints: {required}")


def result_signature(response):
    # Include evidence identity/content hashes and rank observations, excluding timing.
    return [
        {
            "id": r.id,
            "source_ref": r.source_ref,
            "span": [r.line_start, r.line_end],
            "checksum": r.checksum,
            "content_sha256": hashlib.sha256(r.content.encode()).hexdigest(),
            "score": r.score,
        }
        for r in response.results
    ]


def serving_gate(backend, scope, query, direct):
    class CheckedBackend:
        backend_id = backend.backend_id
        retrieval_capabilities = backend.retrieval_capabilities
        calls = 0

        def search(self, intent, actual_scope, requirements):
            if intent != controlled_intent(query, "canonical"):
                raise BaselineConfigurationError("serving intent differs from controlled canonical")
            if actual_scope != scope or requirements != ():
                raise BaselineConfigurationError("serving scope/configuration differs")
            self.calls += 1
            return backend.search(intent, actual_scope, requirements)

    checked = CheckedBackend()
    binding = RepositoryBinding(
        repository=scope.repository,
        workspace=scope.workspace,
        backend=backend.backend_id,
        revision=scope.revision,
        settings=scope.settings,
        retrieval=scope.retrieval,
        knowledge_types=scope.knowledge_types,
    )
    service = RetrievalApplicationService(
        {scope.repository: binding}, backends=BackendRegistry({backend.backend_id: lambda: checked})
    )
    served = service.search(
        SearchRequest(query=query, repository=scope.repository, limit=scope.max_results)
    )
    if (
        served.status != direct.status
        or checked.calls != 1
        or result_signature(served) != result_signature(direct)
    ):
        raise BaselineConfigurationError("serving retrieval results differ from direct canonical")
    if served.metadata.get("intent_translation") != direct.metadata.get("intent_translation"):
        raise BaselineConfigurationError("serving translation differs")
    if served.telemetry.candidates_examined != direct.telemetry.candidates_examined:
        raise BaselineConfigurationError("serving candidate count differs")
    return {
        "intent_and_scope_equal": True,
        "ordered_results_equal": True,
        "translation_equal": True,
        "candidates_equal": True,
        "result_fingerprint": mapping_fingerprint({"results": result_signature(served)}),
    }


def run_arm(
    backend,
    arm,
    scope,
    suite,
    queries,
    *,
    representations,
    warmups,
    repetitions,
    identity,
    environment,
    snapshot,
):
    # Reuse the frozen capability/mode validation; measurement orchestration is local.
    BaselineRunner(
        backend=backend, arm=arm, scope=scope, warmups=warmups, repetitions=repetitions, k=20
    )
    observations = {rep: [] for rep in representations}
    diagnostics = {rep: [] for rep in representations}
    budgets = {rep: [] for rep in representations}
    gates = []
    for iteration in range(-warmups, repetitions):
        for case_index, case in enumerate(suite.cases):
            # Alternate first representation deterministically to reduce ordering bias.
            order = representations if (iteration + case_index) % 2 == 0 else representations[::-1]
            for rep in order:
                response = backend.search(controlled_intent(queries[case.query_id], rep), scope, ())
                validate_response(response, arm, scope, snapshot)
                if iteration < 0:
                    continue
                observation = evaluate_retrieval(
                    response, case.truth, iteration=iteration + 1, k=20
                )
                observations[rep].append(observation)
                diagnostics[rep].append(
                    {
                        "query_id": case.query_id,
                        "iteration": iteration + 1,
                        "channels": response.metadata.get("intent_translation", []),
                    }
                )
                budgets[rep].append(
                    {
                        "query_id": case.query_id,
                        "iteration": iteration + 1,
                        "metrics": [
                            m.to_mapping()
                            for m in evaluate_line_budgeted_evidence(
                                case.truth, observation.result_spans
                            )
                        ],
                    }
                )
                if rep == "canonical" and iteration == repetitions - 1:
                    gates.append(
                        {
                            "query_id": case.query_id,
                            **serving_gate(backend, scope, queries[case.query_id], response),
                        }
                    )
        print(f"[evidence] {arm.id} iteration={iteration + 1}/{repetitions}", flush=True)
    config = {
        "scope": scope.to_mapping(),
        "arm": arm.to_mapping(),
        "k": 20,
        "warmups": warmups,
        "repetitions": repetitions,
        "ordering": "alternate by iteration and query; serving gate after final canonical",
        "line_budgets": list(BASELINE_LINE_BUDGETS),
        "query_ids": [case.query_id for case in suite.cases],
        **identity,
    }
    artifacts = []
    for rep in representations:
        shared = build_baseline_artifact(
            arm=arm,
            corpus=suite.corpus,
            config=config,
            observations=observations[rep],
            ground_truth_fingerprint=suite.fingerprint,
            environment=environment,
        ).to_mapping()
        shared.update(
            protocol=PROTOCOL,
            schema_version=SCHEMA_VERSION,
            representation=rep,
            **identity,
            snapshot=snapshot,
            snapshot_fingerprint=mapping_fingerprint(snapshot),
            environment_fingerprint=mapping_fingerprint(environment),
            translation_diagnostics=diagnostics[rep],
            line_budget_observations=budgets[rep],
            serving_equivalence=gates if rep == "canonical" else [],
            context_yield_metrics_fingerprint=mapping_fingerprint(
                BASELINE_CONTEXT_YIELD_METRICS_SPEC
            ),
        )
        shared["aggregate"]["zero_result_rate"] = mean(
            o.candidates_returned == 0 for o in observations[rep]
        )
        shared["line_budget_aggregate"] = {
            str(budget): {
                key: mean(row["metrics"][i][key] for row in budgets[rep])
                for key in (
                    "context_yield",
                    "evidence_recall",
                    "consumed_lines",
                    "packed_result_count",
                )
            }
            for i, budget in enumerate(BASELINE_LINE_BUDGETS)
        }
        artifacts.append(shared)
    return artifacts


PAIR_IDENTITIES = (
    "protocol",
    "schema_version",
    "arm",
    "corpus",
    "ground_truth_fingerprint",
    "canonical_query_set_fingerprint",
    "config_fingerprint",
    "metrics_fingerprint",
    "context_yield_metrics_fingerprint",
    "snapshot_fingerprint",
    "environment_fingerprint",
    "runtime_source_fingerprint",
    "runner_source_fingerprint",
)
PRIORITY_METRICS = ("evidence_recall_at_k", "span_coverage_at_k", "evidence_reciprocal_rank")


def compare_pair(legacy, canonical):
    if legacy["representation"] != "legacy" or canonical["representation"] != "canonical":
        raise BaselineConfigurationError("pair requires legacy then canonical")
    for key in PAIR_IDENTITIES:
        if not legacy.get(key) or legacy[key] != canonical.get(key):
            raise BaselineConfigurationError(f"incompatible pair: {key}")

    def indexed(artifact):
        result = {}
        for row in artifact["observations"]:
            key = (row["query_id"], row["iteration"])
            if key in result:
                raise BaselineConfigurationError("duplicate paired observation")
            result[key] = row
        return result

    before, after = indexed(legacy), indexed(canonical)
    expected = {
        (q, i)
        for q in legacy["config"]["query_ids"]
        for i in range(1, legacy["config"]["repetitions"] + 1)
    }
    if not before or before.keys() != after.keys() or before.keys() != expected:
        raise BaselineConfigurationError("incompatible paired query/iteration coverage")
    per_query = []
    for query in sorted({key[0] for key in before}):
        keys = [key for key in before if key[0] == query]
        deltas = {
            metric: mean(after[k][metric] - before[k][metric] for k in keys)
            for metric in PRIORITY_METRICS
        }
        # Any negative primary metric is visible as regressed, even with gains elsewhere.
        status = (
            "regressed"
            if any(v < -1e-6 for v in deltas.values())
            else "improved"
            if any(v > 1e-6 for v in deltas.values())
            else "unchanged"
        )
        per_query.append(
            {
                "query_id": query,
                "status": status,
                "deltas": deltas,
                "before_after": {
                    metric: [
                        mean(before[k][metric] for k in keys),
                        mean(after[k][metric] for k in keys),
                    ]
                    for metric in PRIORITY_METRICS
                },
            }
        )
    a, b = legacy["aggregate"], canonical["aggregate"]
    metrics = (
        "evidence_recall_at_k_mean",
        "span_coverage_at_k_mean",
        "evidence_mrr_mean",
        "useful_context_line_ratio_mean",
        "candidates_examined_mean",
    )
    return {
        "arm": legacy["arm"]["id"],
        "delta_definition": "canonical - legacy",
        "deltas": {
            **{m: b[m] - a[m] for m in metrics},
            "p50_ms": b["duration_ms"]["p50"] - a["duration_ms"]["p50"],
        },
        "cost_before_after": {
            m: [a[m], b[m]]
            for m in (
                "candidates_examined_mean",
                "candidates_returned_mean",
                "returned_chars_mean",
                "returned_line_count_mean",
                "duration_ms",
            )
        },
        "per_query": per_query,
        "counts": {
            s: sum(q["status"] == s for q in per_query)
            for s in ("improved", "unchanged", "regressed")
        },
    }


def render_report(artifacts, comparisons, *, full):
    lines = [
        "# SearchIntent v2 evidence evaluation",
        "",
        f"Protocol: `{PROTOCOL}`. {'Full measurement protocol' if full else 'SMOKE / partial protocol'}.",
        "Delta = canonical - legacy. Quality is evidence recovery, not consumer satisfaction.",
        "Latency is backend-reported end-to-end milliseconds; indexing and serving smoke excluded.",
        "",
        "| Arm | Representation | SourceRecall | EvidenceRecall | SpanCoverage | EvidenceMRR | EvidenceNDCG | UsefulContext | Candidates | ZeroResult | p50 | p95 | p99 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for artifact in artifacts:
        a = artifact["aggregate"]
        vals = [
            a[k]
            for k in (
                "source_recall_at_k_mean",
                "evidence_recall_at_k_mean",
                "span_coverage_at_k_mean",
                "evidence_mrr_mean",
                "evidence_ndcg_at_k_mean",
                "useful_context_line_ratio_mean",
                "candidates_examined_mean",
                "zero_result_rate",
            )
        ]
        vals += [a["duration_ms"][k] for k in ("p50", "p95", "p99")]
        name = artifact["arm"]["id"] + "-" + artifact["representation"]
        lines.append(
            f"| [{artifact['arm']['id']}]({name}.json.gz) | {artifact['representation']} | "
            + " | ".join(f"{v:.6f}" for v in vals)
            + " |"
        )
    lines += [
        "",
        "## Operational volume",
        "",
        "| Arm | Representation | Returned candidates | Returned lines | Returned chars | Observations |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for artifact in artifacts:
        a = artifact["aggregate"]
        lines.append(
            f"| {artifact['arm']['id']} | {artifact['representation']} | "
            + " | ".join(
                f"{a[k]:.3f}"
                for k in (
                    "candidates_returned_mean",
                    "returned_line_count_mean",
                    "returned_chars_mean",
                    "run_count",
                )
            )
            + " |"
        )
    lines += [
        "",
        "## Paired changes",
        "",
        "| Arm | Δ EvidenceRecall | Δ SpanCoverage | Δ EvidenceMRR | Δ UsefulContext | Candidates before → after (Δ) | p50 before → after (Δ) |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for pair in comparisons:
        d, cost = pair["deltas"], pair["cost_before_after"]
        vals = [
            d[m]
            for m in (
                "evidence_recall_at_k_mean",
                "span_coverage_at_k_mean",
                "evidence_mrr_mean",
                "useful_context_line_ratio_mean",
            )
        ]
        c, t = cost["candidates_examined_mean"], cost["duration_ms"]
        lines.append(
            f"| {pair['arm']} | "
            + " | ".join(f"{v:+.6f}" for v in vals)
            + f" | {c[0]:.2f} → {c[1]:.2f} ({d['candidates_examined_mean']:+.2f})"
            + f" | {t[0]['p50']} → {t[1]['p50']} ({d['p50_ms']:+.0f}) |"
        )
    lines += [
        "",
        "## Context within line budgets",
        "",
        "| Arm | Representation | Budget | ContextYield | EvidenceRecall | Consumed lines | Packed results |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for artifact in artifacts:
        for budget, a in artifact["line_budget_aggregate"].items():
            lines.append(
                f"| {artifact['arm']['id']} | {artifact['representation']} | {budget} | "
                + " | ".join(
                    f"{a[k]:.6f}"
                    for k in (
                        "context_yield",
                        "evidence_recall",
                        "consumed_lines",
                        "packed_result_count",
                    )
                )
                + " |"
            )
    lines += [
        "",
        "## Query regressions",
        "",
        "Classification: any decrease in EvidenceRecall, SpanCoverage or EvidenceMRR is regressed;",
        "otherwise any increase is improved; otherwise unchanged. Mixed gains/losses remain regressed.",
        "",
    ]
    for pair in comparisons:
        lines.append(f"- **{pair['arm']}**: {pair['counts']}")
        for q in pair["per_query"]:
            if q["status"] == "regressed":
                lines.append(
                    f"  - `{q['query_id']}`: "
                    + ", ".join(f"Δ {k}={v:+.6f}" for k, v in q["deltas"].items())
                )
    lines += [
        "",
        "All query deltas and absolute operational costs: [comparison.json](comparison.json).",
        "Translation diagnostics, runtime telemetry and shared evaluator observations are separate artifact fields.",
        "No time-to-first-candidate metric is available from the current runtime.",
        "",
    ]
    return "\n".join(lines)


def write_json(path, payload):
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(gzip.compress(data, mtime=0) if path.suffix == ".gz" else data)


def read_json(path):
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaselineConfigurationError(f"invalid benchmark artifact JSON: {path}") from exc
    if not isinstance(value, dict):
        raise BaselineConfigurationError(f"benchmark artifact must be an object: {path}")
    return {str(key): item for key, item in value.items()}


def measurement_contract_fingerprint(run):
    return mapping_fingerprint({key: run.get(key) for key in MEASUREMENT_CONTRACT_FIELDS})


def validate_resume_run(existing, expected):
    if existing.get("protocol") != PROTOCOL or existing.get("schema_version") != SCHEMA_VERSION:
        raise BaselineConfigurationError("resume protocol/schema mismatch")
    if existing.get("status") == "complete":
        raise BaselineConfigurationError("completed benchmark run does not need resume")
    recorded = existing.get("measurement_contract_fingerprint")
    observed = measurement_contract_fingerprint(existing)
    if recorded is not None and recorded != observed:
        raise BaselineConfigurationError("resume run measurement contract fingerprint is invalid")
    if observed != measurement_contract_fingerprint(expected):
        raise BaselineConfigurationError("resume measurement contract mismatch")


def _checkpoint_identity_matches(artifact, *, arm, representation, snapshot, identity, environment):
    if artifact.get("protocol") != PROTOCOL or artifact.get("schema_version") != SCHEMA_VERSION:
        return False
    artifact_arm = artifact.get("arm")
    if not isinstance(artifact_arm, dict) or artifact_arm.get("id") != arm.id:
        return False
    if artifact.get("representation") != representation:
        return False
    for key in (
        "ground_truth_fingerprint",
        "canonical_query_set_fingerprint",
        "baseline_manifest_fingerprint",
        "runtime_source_fingerprint",
        "embedding_identity",
        "fusion_identity",
    ):
        if key in identity and artifact.get(key) != identity[key]:
            return False
    if artifact.get("snapshot_fingerprint") != mapping_fingerprint(snapshot):
        return False
    if artifact.get("environment_fingerprint") != mapping_fingerprint(environment):
        return False
    return artifact.get("snapshot_unchanged_after_measurement") is True


def load_arm_checkpoint(
    output,
    arm,
    representations,
    *,
    snapshot,
    identity,
    environment,
    warmups,
    repetitions,
):
    paths = [output / f"{arm.id}-{representation}.json.gz" for representation in representations]
    existing = [path.exists() for path in paths]
    if not any(existing):
        return None
    if not all(existing):
        print(f"[evidence] {arm.id} partial checkpoint ignored; restarting arm", flush=True)
        return None
    artifacts = [read_json(path) for path in paths]
    for representation, artifact in zip(representations, artifacts, strict=True):
        if not _checkpoint_identity_matches(
            artifact,
            arm=arm,
            representation=representation,
            snapshot=snapshot,
            identity=identity,
            environment=environment,
        ):
            raise BaselineConfigurationError(f"resume checkpoint identity mismatch: {arm.id}")
        config = artifact.get("config")
        if (
            not isinstance(config, dict)
            or config.get("warmups") != warmups
            or config.get("repetitions") != repetitions
            or config.get("k") != 20
        ):
            raise BaselineConfigurationError(f"resume checkpoint config mismatch: {arm.id}")
    if len(artifacts) == 2:
        compare_pair(*artifacts)
    return artifacts


def is_transient_arm_failure(exc):
    return (
        isinstance(exc, KnowledgeSearchError)
        and exc.category in TRANSIENT_ARM_FAILURE_CATEGORIES
    )


def run_with_transient_arm_retry(
    operation,
    *,
    arm_id,
    retries,
    backoff_seconds,
    on_retry=None,
):
    attempt = 1
    while True:
        try:
            return operation(), attempt
        except Exception as exc:
            if not is_transient_arm_failure(exc) or attempt > retries:
                raise
            if on_retry is not None:
                on_retry(attempt, exc)
            print(
                f"[evidence] {arm_id} transient failure on attempt {attempt}; "
                f"restarting whole arm after {backoff_seconds:.1f}s",
                flush=True,
            )
            if backoff_seconds:
                sleep(backoff_seconds)
            attempt += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--arm", action="append", default=[])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--representation", choices=REPRESENTATIONS)
    group.add_argument("--compare-representations", action="store_true")
    parser.add_argument("--prepare-index", action="store_true")
    parser.add_argument("--benchmark-infra-plan", type=Path,
                        help="Opt-in BENCH project/collection binding without editing frozen manifest")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--arm-retries", type=int, default=1)
    parser.add_argument("--retry-backoff-seconds", type=float, default=5.0)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/evidence")
    args = parser.parse_args()
    if args.warmups < 0 or args.repetitions < 1:
        parser.error("warmups must be nonnegative; repetitions must be positive")
    if args.arm_retries < 0 or args.retry_backoff_seconds < 0:
        parser.error("arm retries and retry backoff must be nonnegative")
    if args.resume is not None and args.prepare_index:
        parser.error("--resume cannot be combined with --prepare-index")
    suite, queries, query_fingerprint = load_inputs()
    manifest = setup._load_json(setup.MANIFEST_PATH)
    if args.benchmark_infra_plan is not None:
        sys.path.insert(0, str(ROOT.parent))
        from benchmark_infra import apply_search_intent_plan, verified_plan

        manifest = apply_search_intent_plan(
            manifest, verified_plan(args.benchmark_infra_plan)
        )
    workspace = args.workspace.resolve(strict=True)
    setup._validate_workspace(workspace, suite.corpus["revision"])
    suite.validate_workspace_regions(workspace)
    arms = setup._select_arms(args.arm, setup._manifest_arms(manifest))
    if len({arm.id for arm in arms}) != len(arms):
        parser.error("duplicate arms")
    reps = (args.representation,) if args.representation else REPRESENTATIONS
    context = setup.BenchmarkBackendContext(
        endpoints=manifest["environment"]["service_endpoints"],
        embedding_identity=manifest["environment"]["embedding"],
        settings=manifest["backend_settings"],
        diagnostic_candidate_window=80,
    )
    os.environ["OLLAMA_URL"] = context.endpoints["ollama"]
    adapters = setup.benchmark_backend_adapters()
    versions = setup._service_versions(arms, context=context, adapters=adapters)
    environment = capture_baseline_environment(
        manifest["environment"]["profile_id"],
        services=versions,
        embedding=context.embedding_identity,
    ).to_mapping()
    if any(arm.mode != "lexical" for arm in arms):
        tags = setup._http_json(context.endpoints["ollama"].rstrip("/") + "/api/tags")
        model = next(
            (m for m in tags["models"] if m["name"].split(":")[0] == "embeddinggemma"), None
        )
        if not model or not model.get("digest"):
            raise BaselineConfigurationError("embeddinggemma model digest unavailable")
        environment["embedding_model_digest"] = model["digest"]
    identity = {
        **source_identity(),
        "ground_truth_fingerprint": suite.fingerprint,
        "canonical_query_set_fingerprint": query_fingerprint,
        "baseline_manifest_fingerprint": mapping_fingerprint(manifest),
        "embedding_identity": context.embedding_identity,
        "fusion_identity": "rrf; unchanged runtime defaults",
    }
    full = len(arms) == 9 and len(reps) == 2 and args.repetitions == 20 and args.warmups == 1
    expected_run = {
        "protocol": PROTOCOL,
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "full_protocol": full,
        "arms": [a.id for a in arms],
        "representations": list(reps),
        "warmups": args.warmups,
        "repetitions": args.repetitions,
        "k": 20,
        "corpus": suite.corpus,
        "ground_truth_fingerprint": suite.fingerprint,
        "environment": environment,
        **identity,
    }
    expected_run["measurement_contract_fingerprint"] = measurement_contract_fingerprint(
        expected_run
    )
    if args.resume is None:
        version, output = setup._allocate_run_output_dir(args.output.resolve())
        run = {
            **expected_run,
            "run_version": version,
            "runner_source_fingerprints": [identity["runner_source_fingerprint"]],
            "completed_arms": [],
            "arm_attempts": {},
            "transient_failures": [],
        }
        write_json(output / "queries.json", {"queries": queries, "fingerprint": query_fingerprint})
    else:
        output = args.resume.resolve(strict=True)
        if not output.is_dir():
            parser.error("--resume must point to an existing run directory")
        run = read_json(output / "run.json")
        validate_resume_run(run, expected_run)
        query_record = read_json(output / "queries.json")
        if query_record.get("fingerprint") != query_fingerprint:
            raise BaselineConfigurationError("resume canonical query fingerprint mismatch")
        version = str(run.get("run_version") or output.name)
        previous_runner = run.get("runner_source_fingerprint")
        runner_fingerprints = list(run.get("runner_source_fingerprints") or [])
        if isinstance(previous_runner, str):
            runner_fingerprints.append(previous_runner)
        runner_fingerprints.append(identity["runner_source_fingerprint"])
        run.setdefault("initial_fetchcore_revision", run.get("fetchcore_revision"))
        run.setdefault("resume_history", []).append(
            {
                "resumed_at": setup._utc_now(),
                "previous_status": run.get("status"),
                "previous_error": run.get("error"),
                "runner_source_fingerprint": identity["runner_source_fingerprint"],
                "fetchcore_revision": identity["fetchcore_revision"],
            }
        )
        run.update(
            status="running",
            full_protocol=full,
            fetchcore_revision=identity["fetchcore_revision"],
            runner_source_fingerprint=identity["runner_source_fingerprint"],
            runner_source_fingerprints=sorted(set(runner_fingerprints)),
            working_tree_dirty=identity["working_tree_dirty"],
            measurement_contract_fingerprint=expected_run["measurement_contract_fingerprint"],
        )
        run.pop("error", None)
        run.pop("completed_at", None)
        run.setdefault("completed_arms", [])
        run.setdefault("arm_attempts", {})
        run.setdefault("transient_failures", [])
        print(f"[evidence] resuming run {version} from {output}", flush=True)
    write_json(output / "run.json", run)
    current_arm = None
    try:
        if args.prepare_index:
            print(f"[evidence] preparing indexes once; output={output}", flush=True)
            indexing = setup._prepare_indexes(
                arms,
                workspace=workspace,
                repository=suite.corpus["repository"],
                revision=suite.corpus["revision"],
                context=context,
                adapters=adapters,
                environment=environment,
                fetchcore_revision=identity["fetchcore_revision"],
            )
            indexing.update(protocol=PROTOCOL, **identity)
            write_json(output / "indexing.json", indexing)
        artifacts, pairs = [], []
        for arm in arms:
            current_arm = arm.id
            retrieval = setup._retrieval_for_mode(
                arm.mode, provider="ollama", model="embeddinggemma"
            )
            settings = setup._backend_settings(arm, context=context, adapters=adapters)
            settings["diagnostic_candidate_window"] = 80
            scope = SearchScope(
                workspace=workspace,
                revision=suite.corpus["revision"],
                repository=suite.corpus["repository"],
                max_results=20,
                knowledge_types=("code", "tests", "documentation", "configuration"),
                settings=settings,
                retrieval=retrieval,
                result_namespace="fetchcore-serving",
            )
            snapshot = snapshot_identity(arm, scope)
            if args.resume is not None:
                checkpoint = load_arm_checkpoint(
                    output,
                    arm,
                    reps,
                    snapshot=snapshot,
                    identity=identity,
                    environment=environment,
                    warmups=args.warmups,
                    repetitions=args.repetitions,
                )
                if checkpoint is not None:
                    print(f"[evidence] {arm.id} checkpoint valid; skipping completed arm", flush=True)
                    artifacts.extend(checkpoint)
                    if len(checkpoint) == 2:
                        pairs.append(compare_pair(*checkpoint))
                    if arm.id not in run["completed_arms"]:
                        run["completed_arms"].append(arm.id)
                    write_json(output / "comparison.json", pairs)
                    (output / "README.md").write_text(
                        render_report(artifacts, pairs, full=full)
                    )
                    write_json(output / "run.json", run)
                    continue

            def execute_arm(arm=arm, scope=scope, snapshot=snapshot):
                if snapshot_identity(arm, scope) != snapshot:
                    raise BaselineConfigurationError(
                        f"snapshot changed before measurement: {arm.id}"
                    )
                measured_artifacts = run_arm(
                    backend_registry(context).create(arm.backend),
                    arm,
                    scope,
                    suite,
                    queries,
                    representations=reps,
                    warmups=args.warmups,
                    repetitions=args.repetitions,
                    identity=identity,
                    environment=environment,
                    snapshot=snapshot,
                )
                if snapshot_identity(arm, scope) != snapshot:
                    raise BaselineConfigurationError(
                        f"snapshot changed during paired measurement: {arm.id}"
                    )
                return measured_artifacts

            def record_retry(attempt, exc, arm=arm):
                run["arm_attempts"][arm.id] = attempt
                run["failed_arm"] = arm.id
                run["transient_failures"].append(
                    {
                        "arm": arm.id,
                        "attempt": attempt,
                        "category": exc.category,
                        "message": str(exc),
                        "discarded": True,
                        "observed_at": setup._utc_now(),
                    }
                )
                write_json(output / "run.json", run)

            measured, attempts = run_with_transient_arm_retry(
                execute_arm,
                arm_id=arm.id,
                retries=args.arm_retries,
                backoff_seconds=args.retry_backoff_seconds,
                on_retry=record_retry,
            )
            run["arm_attempts"][arm.id] = attempts
            for artifact in measured:
                artifact["snapshot_unchanged_after_measurement"] = True
                write_json(output / f"{arm.id}-{artifact['representation']}.json.gz", artifact)
            if len(measured) == 2:
                pairs.append(compare_pair(*measured))
            artifacts.extend(measured)
            if arm.id not in run["completed_arms"]:
                run["completed_arms"].append(arm.id)
            run.pop("failed_arm", None)
            write_json(output / "comparison.json", pairs)
            (output / "README.md").write_text(render_report(artifacts, pairs, full=full))
            write_json(output / "run.json", run)
        setup._validate_workspace(workspace, suite.corpus["revision"])
        run.update(status="complete", completed_at=setup._utc_now())
        run.pop("failed_arm", None)
    except Exception as exc:
        run.update(
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            failed_arm=current_arm,
        )
        raise
    finally:
        write_json(output / "run.json", run)
    print(f"[evidence] report={output / 'README.md'}", flush=True)


if __name__ == "__main__":
    main()
