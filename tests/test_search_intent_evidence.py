"""The #19 experiment reuses the frozen ruler and changes only intent representation."""

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fetchcore import baseline
from fetchcore.retrieval import RetrievalBackendCapabilities
from fetchcore.retrieval.contracts import KnowledgeSearchResponse, SearchIntent, SearchScope
from fetchcore.telemetry import RetrievalTelemetry

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "intent_evidence", ROOT / "benchmarks/search-intent-v2/evidence.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_frozen_baseline_bytes_protocol_and_measurement_remain_unchanged():
    for name, digest in MODULE.FROZEN_FILES.items():
        assert hashlib.sha256((MODULE.setup.ROOT / name).read_bytes()).hexdigest() == digest
    assert hashlib.sha256((MODULE.setup.ROOT / "run.py").read_bytes()).hexdigest() == (
        "fafa5219a4c8aa33e8d055dae8521c32a66ac81c570f1a2fca1d4d8d367bcd71"
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
