"""The #19 experiment reuses the frozen ruler and changes only intent representation."""

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fetchcore import baseline
from fetchcore.retrieval import RetrievalBackendCapabilities
from fetchcore.retrieval.contracts import (
    KnowledgeSearchError,
    KnowledgeSearchResponse,
    SearchIntent,
    SearchScope,
)
from fetchcore.telemetry import RetrievalTelemetry

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "intent_evidence", ROOT / "benchmarks/search-intent-v2/evidence.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize(("environment", "owner", "backend", "performance", "reason"), [
    ("dev", "operator", "solr", False, "BENCH routing"),
    ("bench", "", "solr", False, "--owner"),
    ("bench", "operator", "opensearch", False, "requires Solr"),
    ("bench", "operator", "solr", True, "dedicated BENCH"),
])
def test_new_search_intent_runs_fail_closed_before_retrieval(
    environment: str, owner: str, backend: str, performance: bool, reason: str,
) -> None:
    command = [sys.executable, str(ROOT / "benchmarks/search-intent-v2/run.py"),
               "--workspace", str(ROOT), "--revision", "0" * 40,
               "--solr-url", "http://127.0.0.1:18983/solr", "--collection", "fc-bench-test",
               "--scenarios", str(ROOT / "benchmarks/search-intent-v2/scenarios.yaml"),
               "--output", "/tmp/unused-search-intent.json", "--label", "guard-test"]
    if performance:
        command.append("--performance-comparable")
    result = subprocess.run(command, capture_output=True, text=True, check=False,
                            env={**os.environ, "FETCHCORE_ENVIRONMENT": environment,
                                 "FETCHCORE_BENCH_OWNER": owner, "FETCHCORE_BACKEND": backend,
                                 "FETCHCORE_INFRA_PROJECT": "fetchcore-shared",
                                 "FETCHCORE_BENCH_PERFORMANCE": "false"})
    assert result.returncode != 0
    assert reason in result.stderr


def test_frozen_baseline_bytes_protocol_and_measurement_remain_unchanged():
    for name, digest in MODULE.FROZEN_FILES.items():
        assert hashlib.sha256((MODULE.setup.ROOT / name).read_bytes()).hexdigest() == digest
    # The runner now uses the manifest Ollama endpoint and resumes validated arms;
    # the frozen measurement, Ground Truth and metric identities below are unchanged.
    assert hashlib.sha256((MODULE.setup.ROOT / "run.py").read_bytes()).hexdigest() == (
        "28dce2a372175862ed67375c5164de13c81865f37ec127521d3c19a66e4ebd93"
    )
    assert baseline.BASELINE_PROTOCOL == "fetchcore.baseline-v1"
    assert baseline.BASELINE_ARTIFACT_SCHEMA_VERSION == 2
    assert baseline.BASELINE_METRICS_SCHEMA_VERSION == 1
    assert baseline.mapping_fingerprint(baseline.BASELINE_V1_METRICS_SPEC) == (
        "sha256:3adba6b1021b0f16624d4759e642fcb1616cd00469aba8abcf7e5d2d0281e894"
    )
    manifest = MODULE.setup._load_json(MODULE.setup.MANIFEST_PATH)
    assert manifest["measurement"] == {
        "warmups": 1,
        "repetitions": 20,
        "k": 20,
        "max_results": 20,
        "diagnostic_candidate_window": 80,
    }
    assert MODULE.setup._manifest_arms(manifest) == baseline.BASELINE_V1_ARMS
    suite, queries, _ = MODULE.load_inputs()
    assert suite.fingerprint == (
        "sha256:a03733d1601097afcba5e32dc4b04834471758919300457fb88925fce481c29a"
    )
    assert len(queries) == len(suite.cases) == 20
    assert sum(len(c.requirements) for c in suite.cases) == 42
    assert set(queries) == {c.query_id for c in suite.cases}


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "unknown", "empty", "extra"])
def test_query_set_fails_closed(tmp_path, mutation):
    payload = json.loads((MODULE.ROOT / "evidence-queries.json").read_text())
    if mutation == "duplicate":
        payload["queries"].append(payload["queries"][0])
    elif mutation == "missing":
        payload["queries"].pop()
    elif mutation == "unknown":
        payload["queries"][0]["query_id"] = "unknown"
    elif mutation == "empty":
        payload["queries"][0]["canonical_query"] = "  "
    else:
        payload["queries"][0]["hints"] = ["invented"]
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(baseline.BaselineConfigurationError):
        MODULE.load_inputs(path)


def test_query_text_change_invalidates_protocol_identity(tmp_path):
    payload = json.loads((MODULE.ROOT / "evidence-queries.json").read_text())
    payload["queries"][0]["canonical_query"] += " Changed meaning."
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(payload))
    assert MODULE.load_inputs(path)[2] != MODULE.load_inputs()[2]


def test_representations_are_exact_without_invented_semantics():
    for query in MODULE.load_inputs()[1].values():
        assert MODULE.controlled_intent(query, "legacy") == SearchIntent(semantic_terms=(query,))
        assert MODULE.controlled_intent(query, "canonical") == SearchIntent(canonical_query=query)
    with pytest.raises(baseline.BaselineConfigurationError):
        MODULE.controlled_intent("query", "invented")


def test_evaluators_are_the_shared_functions():
    assert MODULE.evaluate_retrieval is baseline.evaluate_retrieval
    assert MODULE.evaluate_line_budgeted_evidence is baseline.evaluate_line_budgeted_evidence
    assert MODULE.build_baseline_artifact is baseline.build_baseline_artifact


class RecordingBackend:
    backend_id = "local-semantic"
    retrieval_capabilities = RetrievalBackendCapabilities(lexical=True, canonical_query=True)

    def __init__(self):
        self.calls = []

    def search(self, intent, scope, requirements):
        self.calls.append((intent, scope, requirements))
        return KnowledgeSearchResponse(
            backend=self.backend_id,
            status="not_found",
            results=(),
            duration_ms=1,
            telemetry=RetrievalTelemetry(
                mode="lexical",
                duration_ms=1,
                candidates_examined=0,
                candidates_returned=0,
                returned_chars=0,
            ),
        )


def measured_pair(tmp_path):
    backend = RecordingBackend()
    scope = SearchScope(
        workspace=tmp_path,
        repository="oalangomes/NeuroTrack_MS",
        revision="test",
        knowledge_types=(),
        max_results=20,
        result_namespace="fetchcore-serving",
    )
    suite, queries, fingerprint = MODULE.load_inputs()
    small = SimpleNamespace(
        cases=suite.cases[:2], corpus=suite.corpus, fingerprint=suite.fingerprint
    )
    identity = {
        "canonical_query_set_fingerprint": fingerprint,
        "runtime_source_fingerprint": "sha256:runtime",
        "runner_source_fingerprint": "sha256:runner",
    }
    pair = MODULE.run_arm(
        backend,
        baseline.BASELINE_V1_ARMS[0],
        scope,
        small,
        queries,
        representations=MODULE.REPRESENTATIONS,
        warmups=1,
        repetitions=2,
        identity=identity,
        environment={"test": True},
        snapshot={"revision": "test"},
    )
    return backend, scope, pair


def test_pair_shares_scope_config_and_serving_path(tmp_path):
    backend, scope, pair = measured_pair(tmp_path)
    assert len(backend.calls) == 14  # 2 queries × 2 representations × 3 passes + 2 gates
    assert all(actual is scope or actual == scope for _, actual, _ in backend.calls)
    assert all(requirements == () for _, _, requirements in backend.calls)
    assert pair[0]["config_fingerprint"] == pair[1]["config_fingerprint"]
    assert all(p["protocol"] == MODULE.PROTOCOL for p in pair)
    assert len(pair[1]["serving_equivalence"]) == 2
    comparison = MODULE.compare_pair(*pair)
    assert comparison["counts"] == {"improved": 0, "unchanged": 2, "regressed": 0}
    assert all(len(p["line_budget_aggregate"]) == 4 for p in pair)


@pytest.mark.parametrize("identity", MODULE.PAIR_IDENTITIES)
def test_pair_rejects_incompatible_provenance(tmp_path, identity):
    _, _, pair = measured_pair(tmp_path)
    pair[1][identity] = "incompatible"
    with pytest.raises(baseline.BaselineConfigurationError, match="incompatible pair"):
        MODULE.compare_pair(*pair)


def test_pair_rejects_missing_or_duplicate_observations(tmp_path):
    _, _, pair = measured_pair(tmp_path)
    altered = copy.deepcopy(pair)
    altered[1]["observations"].pop()
    with pytest.raises(baseline.BaselineConfigurationError, match="coverage"):
        MODULE.compare_pair(*altered)
    pair[1]["observations"].append(pair[1]["observations"][0])
    with pytest.raises(baseline.BaselineConfigurationError, match="duplicate"):
        MODULE.compare_pair(*pair)


def test_mixed_primary_changes_are_reported_as_regressions(tmp_path):
    _, _, pair = measured_pair(tmp_path)
    for row in pair[0]["observations"]:
        row["evidence_reciprocal_rank"] = 1
    for row in pair[1]["observations"]:
        row["evidence_recall_at_k"] = 1
    assert MODULE.compare_pair(*pair)["counts"]["regressed"] == 2


def test_unsupported_arm_fails_explicitly(tmp_path):
    with pytest.raises(baseline.BaselineConfigurationError, match="unknown"):
        MODULE.setup._select_arms(["qdrant-lexical"], baseline.BASELINE_V1_ARMS)
    with pytest.raises(baseline.BaselineConfigurationError, match="unsupported"):
        baseline.BaselineRunner(
            backend=RecordingBackend(),
            arm=baseline.BaselineArm("local-dense", "local-semantic", "dense"),
            scope=SearchScope(tmp_path, None, "repo", (), 20),
        )


def test_serving_gate_detects_config_divergence(tmp_path):
    backend = RecordingBackend()
    # A different namespace is an actual scope difference, not normalized away.
    scope = SearchScope(tmp_path, None, "repo", (), 20)
    response = backend.search(SearchIntent(canonical_query="query"), scope, ())
    with pytest.raises(baseline.BaselineConfigurationError, match="scope/configuration"):
        MODULE.serving_gate(backend, scope, "query", response)


def test_path_level_metrics_have_explicit_names():
    code = (MODULE.ROOT / "run.py").read_text()
    assert '"expected_path_recall_at_10"' in code
    assert '"expected_path_mrr"' in code
    assert '"source_recall_at_10"' not in code
    assert '"mrr"' not in code


def test_index_preparation_groups_modes_once_per_backend():
    class Adapter:
        backend_id = "solr"
        indexing_artifact_key = "solr"

        def __init__(self):
            self.calls = []

        def prepare_index(self, canonical, *, modes, retrieval, context):
            self.calls.append(modes)
            return {"index": "same-snapshot"}

    adapter = Adapter()
    context = SimpleNamespace(embedding_identity={"provider": "ollama", "model": "embeddinggemma"})
    arms = tuple(a for a in baseline.BASELINE_V1_ARMS if a.backend == "solr")
    result = MODULE.setup._prepare_backend_indexes(
        arms, canonical=object(), context=context, adapters={"solr": adapter}
    )
    assert adapter.calls == [frozenset({"lexical", "dense", "hybrid"})]
    assert result == {"solr": {"index": "same-snapshot"}}


def test_response_validation_rejects_missing_remote_fingerprints(tmp_path):
    scope = SearchScope(tmp_path, "revision", "repo", (), 20)
    response = RecordingBackend().search(SearchIntent(canonical_query="test"), scope, ())
    from dataclasses import replace

    response = replace(response, metadata={"snapshot_revision": "revision"})
    with pytest.raises(
        baseline.BaselineConfigurationError, match="missing applicable fingerprints"
    ):
        MODULE.validate_response(response, baseline.BASELINE_V1_ARMS[1], scope)


@pytest.mark.parametrize("backend_id", ["solr", "opensearch", "qdrant-vector"])
def test_embedding_endpoint_is_explicit_in_current_composition(backend_id):
    context = SimpleNamespace(endpoints={"ollama": "http://manifest-ollama:11434"})
    backend = MODULE.backend_registry(context).create(backend_id)
    retrieval = MODULE.setup._retrieval_for_mode("dense", provider="ollama", model="embeddinggemma")
    if backend_id == "qdrant-vector":
        provider = backend.embedding_factory(
            MODULE.setup.QdrantVectorSettings(), retrieval.dense.embedding
        )
    else:
        provider = backend.embedding_factory(retrieval)
    assert provider.url == context.endpoints["ollama"]
    assert provider.model_id == "embeddinggemma"


def _resume_run_fixture():
    return {
        "protocol": MODULE.PROTOCOL,
        "schema_version": MODULE.SCHEMA_VERSION,
        "status": "failed",
        "full_protocol": True,
        "arms": [arm.id for arm in baseline.BASELINE_V1_ARMS],
        "representations": list(MODULE.REPRESENTATIONS),
        "warmups": 1,
        "repetitions": 20,
        "k": 20,
        "corpus": {"repository": "repo", "revision": "rev"},
        "ground_truth_fingerprint": "sha256:gt",
        "canonical_query_set_fingerprint": "sha256:queries",
        "baseline_manifest_fingerprint": "sha256:manifest",
        "runtime_source_fingerprint": "sha256:runtime",
        "embedding_identity": {
            "provider": "ollama",
            "model": "embeddinggemma",
            "dimensions": 768,
        },
        "fusion_identity": "rrf; unchanged runtime defaults",
        "environment": {"profile_id": "test", "services": {"ollama": "test"}},
        "fetchcore_revision": "old-revision",
        "runner_source_fingerprint": "sha256:old-runner",
        "working_tree_dirty": False,
    }


def test_resume_contract_ignores_orchestration_only_provenance_changes():
    existing = _resume_run_fixture()
    expected = copy.deepcopy(existing)
    expected.update(
        status="running",
        fetchcore_revision="new-revision",
        runner_source_fingerprint="sha256:new-runner",
        working_tree_dirty=True,
    )
    MODULE.validate_resume_run(existing, expected)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runtime_source_fingerprint", "sha256:different-runtime"),
        ("ground_truth_fingerprint", "sha256:different-gt"),
        ("canonical_query_set_fingerprint", "sha256:different-queries"),
        ("repetitions", 19),
        ("environment", {"profile_id": "different"}),
    ],
)
def test_resume_contract_fails_closed_when_measurement_identity_changes(field, value):
    existing = _resume_run_fixture()
    expected = copy.deepcopy(existing)
    expected["status"] = "running"
    expected[field] = value
    with pytest.raises(baseline.BaselineConfigurationError, match="measurement contract"):
        MODULE.validate_resume_run(existing, expected)


def test_resume_rejects_completed_run():
    existing = _resume_run_fixture()
    existing["status"] = "complete"
    with pytest.raises(baseline.BaselineConfigurationError, match="does not need resume"):
        MODULE.validate_resume_run(existing, copy.deepcopy(existing))


def test_transient_arm_retry_restarts_whole_operation():
    calls = []

    def operation():
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise KnowledgeSearchError("provider reset", category="unavailable")
        return ["complete-arm"]

    result, attempts = MODULE.run_with_transient_arm_retry(
        operation,
        arm_id="opensearch-hybrid",
        retries=1,
        backoff_seconds=0,
    )
    assert result == ["complete-arm"]
    assert attempts == 2
    assert calls == [1, 2]


def test_non_transient_arm_failure_is_not_retried():
    calls = []

    def operation():
        calls.append(1)
        raise KnowledgeSearchError("schema mismatch", category="schema_mismatch")

    with pytest.raises(KnowledgeSearchError):
        MODULE.run_with_transient_arm_retry(
            operation,
            arm_id="opensearch-hybrid",
            retries=3,
            backoff_seconds=0,
        )
    assert len(calls) == 1


def test_resume_loads_only_complete_compatible_arm_checkpoint(tmp_path):
    _, _, pair = measured_pair(tmp_path)
    for artifact in pair:
        artifact["snapshot_unchanged_after_measurement"] = True
        MODULE.write_json(
            tmp_path / f"local-lexical-{artifact['representation']}.json.gz",
            artifact,
        )
    loaded = MODULE.load_arm_checkpoint(
        tmp_path,
        baseline.BASELINE_V1_ARMS[0],
        MODULE.REPRESENTATIONS,
        snapshot={"revision": "test"},
        identity={
            "canonical_query_set_fingerprint": pair[0]["canonical_query_set_fingerprint"],
            "runtime_source_fingerprint": pair[0]["runtime_source_fingerprint"],
            "runner_source_fingerprint": "ignored-for-resume",
        },
        environment={"test": True},
        warmups=1,
        repetitions=2,
    )
    assert loaded is not None
    assert [artifact["representation"] for artifact in loaded] == ["legacy", "canonical"]


def test_resume_checkpoint_fails_closed_on_snapshot_change(tmp_path):
    _, _, pair = measured_pair(tmp_path)
    for artifact in pair:
        artifact["snapshot_unchanged_after_measurement"] = True
        MODULE.write_json(
            tmp_path / f"local-lexical-{artifact['representation']}.json.gz",
            artifact,
        )
    with pytest.raises(baseline.BaselineConfigurationError, match="checkpoint identity"):
        MODULE.load_arm_checkpoint(
            tmp_path,
            baseline.BASELINE_V1_ARMS[0],
            MODULE.REPRESENTATIONS,
            snapshot={"revision": "different"},
            identity={
                "canonical_query_set_fingerprint": pair[0]["canonical_query_set_fingerprint"],
                "runtime_source_fingerprint": pair[0]["runtime_source_fingerprint"],
            },
            environment={"test": True},
            warmups=1,
            repetitions=2,
        )
