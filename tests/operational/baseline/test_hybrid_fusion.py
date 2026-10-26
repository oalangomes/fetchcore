"""Offline checks prove protocol behavior, never real retrieval quality/latency."""

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tracemalloc

import pytest

from fetchcore.baseline import (
    BaselineGroundTruth,
    BaselineEvidenceRequirement,
    BaselineEvidenceAlternative,
    BaselineEvidenceRegion,
)
from fetchcore.retrieval.fusion import FusionCandidate, FusionChannelSignal, FusionInput

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "hybrid_benchmark", ROOT / "benchmarks/hybrid-fusion/run.py"
)
BENCH = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BENCH
SPEC.loader.exec_module(BENCH)


def fixture():
    source = "repository://acme/widgets/file.py"
    truth = BaselineGroundTruth(
        "query",
        (
            BaselineEvidenceRequirement(
                "need", (BaselineEvidenceAlternative((BaselineEvidenceRegion(source, 2, 3),)),)
            ),
        ),
    )
    full = FusionInput(
        (
            FusionCandidate("irrelevant", "other.py", lexical=FusionChannelSignal(1, 0.9)),
            FusionCandidate(
                "evidence",
                "file.py",
                lexical=FusionChannelSignal(7, 0.6),
                dense=FusionChannelSignal(1, 0.8),
            ),
        ),
        20,
    )
    docs = [
        {
            "id": "irrelevant",
            "source": "other.py",
            "source_ref": "repository://acme/widgets/other.py",
            "knowledge_type": "code",
            "content": "a\nb\nc\n",
            "score": 0.9,
            "line_start": 1,
            "line_end": 3,
            "revision": "frozen",
            "checksum": "other",
        },
        {
            "id": "evidence",
            "source": "file.py",
            "source_ref": source,
            "knowledge_type": "code",
            "content": "a\nb\nc\n",
            "score": 0.8,
            "line_start": 1,
            "line_end": 3,
            "revision": "frozen",
            "checksum": "source",
        },
    ]
    return truth, {
        "query_id": "query",
        "iteration": 1,
        "input": full.to_mapping(),
        "evidence": docs,
        "lexical_acquired": 2,
        "dense_acquired": 1,
    }


def test_replay_deterministic_preserves_spans_and_has_no_retrieval_latency():
    truth, capture = fixture()
    a = BENCH.replay_row(capture, ("control", None, None, 60), 20, truth)
    b = BENCH.replay_row(capture, ("control", None, None, 60), 20, truth)
    assert a == b
    assert a["recovered_requirements"] == ["need"]
    assert (
        a["result_spans"][0]["source_ref"]
        == truth.requirements[0].alternatives[0].regions[0].source_ref
    )
    assert a["result_spans"][0]["line_start"] == 1
    assert "duration_ms" not in a and "telemetry" not in a
    assert "no retrieval latency" in a["timing_kind"]
    assert capture["input"]["candidates"][0]["dense"]["rank"] == 1
    assert a["fusion"]["candidates"][0]["lexical_rank"] == 7


def test_variants_are_separate_experiments_and_legacy_windows_are_backend_supplied():
    variants = BENCH.variants()
    assert len([v for v in variants if v[0].startswith("windows-")]) == 9
    assert all(v[3] == 60 for v in variants if v[0].startswith("windows-"))
    assert [(v[1], v[2]) for v in variants if v[0].startswith("rrf-")] == [(None, None)] * 4
    _, capture = fixture()
    full = FusionInput.from_mapping(capture["input"])
    assert BENCH.select_input(full, variants[0], 20).fingerprint == full.fingerprint
    changed = BENCH.select_input(full, ("cut", 1, 1, 60), 20)
    assert next(c for c in changed.candidates if c.candidate_id == "evidence").lexical is None


def test_artifact_seal_incomplete_and_corrupt_rejected(tmp_path):
    path = tmp_path / "artifact.json.gz"
    payload = {
        "protocol": BENCH.PROTOCOL,
        "schema_version": 1,
        "stage": "capture",
        "status": "blocked",
    }
    BENCH.write(path, payload)
    with pytest.raises(ValueError, match="incomplete"):
        BENCH.read(path)
    assert BENCH.read(path, complete=False) == payload
    path = tmp_path / "plain.json"
    BENCH.write(path, {**payload, "status": "complete"})
    edited = json.loads(path.read_text())
    edited["status"] = "fabricated"
    path.write_text(json.dumps(edited))
    with pytest.raises(ValueError, match="checksum"):
        BENCH.read(path)


def test_query_checkpoint_is_atomic_checksummed_and_identity_bound(tmp_path):
    import gzip

    path = BENCH.checkpoint_path(tmp_path, "solr", "query/with unsafe path")
    payload = {"observations": {"arm/control": [{"iteration": 1}]}, "captures": []}
    BENCH.save_checkpoint(
        path, identity_fingerprint="sha256:runner-a", backend="solr",
        query_id="query/with unsafe path", payload=payload,
    )
    assert BENCH.load_checkpoint(
        path, identity_fingerprint="sha256:runner-a", backend="solr",
        query_id="query/with unsafe path",
    ) == payload
    assert not list(path.parent.glob("*.tmp"))
    with pytest.raises(ValueError, match="identity mismatch"):
        BENCH.load_checkpoint(
            path, identity_fingerprint="sha256:runner-b", backend="solr",
            query_id="query/with unsafe path",
        )
    with pytest.raises(ValueError, match="identity mismatch"):
        BENCH.load_checkpoint(
            path, identity_fingerprint="sha256:runner-a", backend="opensearch",
            query_id="query/with unsafe path",
        )
    with pytest.raises(ValueError, match="identity mismatch"):
        BENCH.load_checkpoint(
            path, identity_fingerprint="sha256:runner-a", backend="solr", query_id="other-query"
        )
    damaged = bytearray(path.read_bytes())
    damaged[-5] ^= 1
    path.write_bytes(damaged)
    with pytest.raises(ValueError, match="invalid checkpoint|checksum mismatch"):
        BENCH.load_checkpoint(
            path, identity_fingerprint="sha256:runner-a", backend="solr",
            query_id="query/with unsafe path",
        )
    assert BENCH.checkpoint_path(tmp_path, "solr", "q", partition="solr-dense") != BENCH.checkpoint_path(
        tmp_path, "solr", "q", partition="solr-hybrid"
    )
    sealed_path = BENCH.checkpoint_path(tmp_path, "solr", "checksum-query")
    BENCH.save_checkpoint(
        sealed_path, identity_fingerprint="sha256:runner-a", backend="solr",
        query_id="checksum-query", payload={"value": "original"},
    )
    sealed = json.loads(gzip.decompress(sealed_path.read_bytes()))
    sealed["payload"]["value"] = "tampered"
    sealed_path.write_bytes(gzip.compress(json.dumps(sealed).encode(), mtime=0))
    with pytest.raises(ValueError, match="checksum mismatch"):
        BENCH.load_checkpoint(
            sealed_path, identity_fingerprint="sha256:runner-a", backend="solr",
            query_id="checksum-query",
        )


def test_resume_counter_runs_missing_queries_and_reuses_only_valid_ones(tmp_path):
    calls = []
    identity = "sha256:fixed-inputs"

    def execute(query_id, *, resume):
        path = BENCH.checkpoint_path(tmp_path, "solr", query_id, partition="solr-hybrid")
        payload = BENCH.reusable_checkpoint(
            path, resume=resume, identity_fingerprint=identity,
            backend="solr", query_id=query_id,
        )
        if payload is None:
            calls.append(query_id)
            payload = {"query_id": query_id, "measurement": len(calls)}
            BENCH.save_checkpoint(
                path, identity_fingerprint=identity, backend="solr", query_id=query_id,
                payload=payload,
            )
        return payload

    execute("q1", resume=False)
    execute("q2", resume=False)
    assert calls == ["q1", "q2"]
    execute("q1", resume=True)
    execute("q2", resume=True)
    assert calls == ["q1", "q2"]
    execute("q3", resume=True)  # absent means pending, so it runs
    assert calls == ["q1", "q2", "q3"]


def test_interrupted_run_can_resume_only_valid_completed_query_checkpoints(tmp_path):
    identity = "sha256:current-runner-and-inputs"
    first = BENCH.checkpoint_path(tmp_path, "opensearch", "q1")
    second = BENCH.checkpoint_path(tmp_path, "opensearch", "q2")
    BENCH.save_checkpoint(
        first, identity_fingerprint=identity, backend="opensearch", query_id="q1",
        payload={"observations": {"arm/control": [{"iteration": 1}]}, "captures": []},
    )
    assert BENCH.load_checkpoint(
        first, identity_fingerprint=identity, backend="opensearch", query_id="q1"
    )["observations"]["arm/control"][0]["iteration"] == 1
    assert not second.exists()  # interruption before q2: it must be measured on resume
    BENCH.save_checkpoint(
        second, identity_fingerprint=identity, backend="opensearch", query_id="q2",
        payload={"observations": {"arm/control": [{"iteration": 1}]}, "captures": []},
    )
    with pytest.raises(ValueError, match="identity mismatch"):
        BENCH.load_checkpoint(
            first, identity_fingerprint="sha256:changed-runner", backend="opensearch", query_id="q1"
        )


def test_replay_reads_query_shards_without_requiring_legacy_capture_array(tmp_path):
    _, row = fixture()
    row["query_id"] = "q1"
    rows = [{**row, "iteration": iteration} for iteration in range(1, 21)]
    identity = "sha256:capture-fingerprint"
    path = BENCH.checkpoint_path(tmp_path, "solr", "q1")
    BENCH.save_checkpoint(
        path, identity_fingerprint=identity, backend="solr", query_id="q1",
        payload={"captures": rows, "observations": {}},
    )
    capture = {
        "captures": {
            BENCH.arm_id("solr", "hybrid"): [BENCH.checkpoint_descriptor(path, "q1")]
        },
        "checkpoint_identities": {BENCH.arm_id("solr", "hybrid"): identity},
    }
    assert list(BENCH.iter_capture_rows(capture, "solr", ["q1"])) == rows
    path.write_bytes(path.read_bytes() + b"tampered shard")
    with pytest.raises(ValueError, match="checkpoint file checksum mismatch"):
        list(BENCH.iter_capture_rows(capture, "solr", ["q1"]))


def test_checkpoint_persistence_memory_tracks_one_query_not_total_run(tmp_path):
    tracemalloc.start()
    try:
        for index in range(12):
            query_id = f"q{index}"
            BENCH.save_checkpoint(
                BENCH.checkpoint_path(tmp_path, "qdrant-vector", query_id),
                identity_fingerprint="sha256:stable", backend="qdrant-vector",
                query_id=query_id,
                payload={"captures": [{"content": "x" * 750_000}], "observations": {}},
            )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 5_000_000


def test_consolidation_waits_for_all_three_backends(tmp_path):
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="exactly three"):
        BENCH.consolidate(
            SimpleNamespace(output=tmp_path / "combined.json.gz", backend_artifact=["one", "two"])
        )
    assert not (tmp_path / "combined.json.gz").exists()


def test_capture_consolidation_binds_control_snapshots_and_validates_query_shards(tmp_path):
    from types import SimpleNamespace

    query_ids = [f"q{i}" for i in range(20)]
    identity = {"corpus": {"repository": "acme/repo", "revision": "rev"}}
    embedding = {"provider": "ollama", "model": "embeddinggemma", "dimensions": 768}
    services = {"solr": "s1", "opensearch": "o1", "qdrant": "q1", "ollama": "v1"}
    environment = {
        "profile_id": "fetchcore-hybrid-fusion-v1", "python_version": "3.11.16",
        "platform_system": "Linux", "platform_release": "test", "machine": "x86_64",
        "cpu_count": 8, "embedding": embedding, "services": services,
    }
    snapshots = {
        BENCH.arm_id(backend, mode): {"backend": backend, "mode": mode}
        for backend in BENCH.BACKENDS for mode in ("dense", "hybrid")
    }
    observations = {
        BENCH.arm_id(backend, mode): [
            {"query_id": query_id, "iteration": iteration, "result_spans": []}
            for query_id in query_ids for iteration in range(1, 21)
        ]
        for backend in BENCH.BACKENDS for mode in ("dense", "hybrid")
    }
    control_path = tmp_path / "control.json.gz"
    BENCH.write(control_path, {
        "protocol": BENCH.PROTOCOL, "schema_version": 1, "stage": "control",
        "status": "complete", "identity": identity, "measurement": BENCH.FULL_MEASUREMENT,
        "measurement_kind": "real", "query_ids": query_ids, "observations": observations,
        "snapshots": snapshots, "snapshot_errors": {}, "environment": environment,
        "service_versions": services, "embedding_identity": embedding,
        "embedding_digest": "sha256:model", "consolidated_backends": list(BENCH.BACKENDS),
    })
    control_fingerprint = BENCH.digest(control_path)
    artifacts = []
    qdrant_capture_path = None
    qdrant_capture_payload = None
    truth, sample_capture = fixture()
    template = BENCH.replay_row(sample_capture, ("control", None, None, 60), 20, truth)
    for backend in BENCH.BACKENDS:
        arm = BENCH.arm_id(backend, "hybrid")
        checkpoint_identity = f"sha256:{backend}-capture"
        descriptors = []
        for query_id in query_ids:
            captures = [
                {"query_id": query_id, "iteration": iteration, "evidence": []}
                for iteration in range(1, 21)
            ]
            rows = [
                {**template, "query_id": query_id, "iteration": iteration}
                for iteration in range(1, 21)
            ]
            checkpoint = BENCH.checkpoint_path(
                tmp_path / f"{backend}.checkpoints", backend, query_id, partition=arm
            )
            BENCH.save_checkpoint(
                checkpoint, identity_fingerprint=checkpoint_identity,
                backend=backend, query_id=query_id,
                payload={"observations": {arm + "/control": rows}, "captures": captures},
            )
            descriptors.append(BENCH.checkpoint_descriptor(checkpoint, query_id))
        artifact_path = tmp_path / f"capture-{backend}.json.gz"
        observations = {arm + "/control": [
            {**template, "query_id": q, "iteration": i}
            for q in query_ids for i in range(1, 21)
        ]}
        BENCH.write(artifact_path, {
            "protocol": BENCH.PROTOCOL, "schema_version": 1, "stage": "capture",
            "status": "complete", "identity": identity, "measurement": BENCH.FULL_MEASUREMENT,
            "measurement_kind": "real", "query_ids": query_ids,
            "snapshots": {arm: snapshots[arm], BENCH.arm_id(backend, "dense"): snapshots[BENCH.arm_id(backend, "dense")]},
            "captures": {arm: descriptors}, "checkpoint_identities": {arm: checkpoint_identity},
            "observations": observations,
            "aggregates": {key: BENCH.summarize(rows) for key, rows in observations.items()},
            "control_artifact_fingerprint": control_fingerprint,
            "environment": environment, "embedding_identity": embedding,
            "embedding_digest": "sha256:model",
        })
        artifacts.append(artifact_path)
        if backend == "qdrant-vector":
            qdrant_capture_path = artifact_path
            qdrant_capture_payload = BENCH.read(artifact_path, stage="capture")
    output = tmp_path / "capture.json.gz"
    BENCH.consolidate(SimpleNamespace(
        output=output, backend_artifact=artifacts, control=control_path,
    ))
    result = BENCH.read(output, stage="capture")
    assert result["consolidated_backends"] == list(BENCH.BACKENDS)
    assert len(result["aggregates"]) == 3
    for artifact in artifacts:
        for arm, aggregate in BENCH.read(artifact)["aggregates"].items():
            assert result["aggregates"][arm] == aggregate
    assert all(len(result["captures"][BENCH.arm_id(b, "hybrid")]) == 20 for b in BENCH.BACKENDS)

    altered = BENCH.read(qdrant_capture_path, stage="capture")
    altered["snapshots"][BENCH.arm_id("qdrant-vector", "hybrid")] = {"snapshot": "other"}
    BENCH.write(qdrant_capture_path, altered)
    rejected = tmp_path / "rejected.json.gz"
    with pytest.raises(ValueError, match="snapshot differs"):
        BENCH.consolidate(SimpleNamespace(
            output=rejected, backend_artifact=artifacts, control=control_path,
        ))
    assert not rejected.exists()

    wrong_control = tmp_path / "different-control.json.gz"
    different = BENCH.read(control_path, stage="control")
    different["decision"] = "re-sealed but distinct control artifact"
    BENCH.write(wrong_control, different)
    altered = dict(qdrant_capture_payload)
    altered["control_artifact_fingerprint"] = BENCH.digest(wrong_control)
    BENCH.write(qdrant_capture_path, altered)
    with pytest.raises(ValueError, match="runner/input identities differ|different controls"):
        BENCH.consolidate(SimpleNamespace(
            output=tmp_path / "mixed-controls.json.gz",
            backend_artifact=artifacts, control=wrong_control,
        ))
    assert not (tmp_path / "mixed-controls.json.gz").exists()

    altered = dict(qdrant_capture_payload)
    altered["captures"] = dict(altered["captures"])
    altered["captures"][BENCH.arm_id("qdrant-vector", "hybrid")] = altered["captures"][
        BENCH.arm_id("qdrant-vector", "hybrid")
    ][:-1]
    BENCH.write(qdrant_capture_path, altered)
    with pytest.raises(ValueError, match="missing validated query checkpoints"):
        BENCH.consolidate(SimpleNamespace(
            output=tmp_path / "missing-shard.json.gz",
            backend_artifact=artifacts, control=control_path,
        ))
    assert not (tmp_path / "missing-shard.json.gz").exists()

    with pytest.raises(ValueError, match="duplicate backend artifact"):
        BENCH.consolidate(SimpleNamespace(
            output=tmp_path / "duplicate-backend.json.gz",
            backend_artifact=[artifacts[0], artifacts[0], artifacts[2]],
            control=control_path,
        ))
    assert not (tmp_path / "duplicate-backend.json.gz").exists()


def test_confirm_rejects_independent_backend_selection(tmp_path):
    import os
    import subprocess

    result = subprocess.run(
        [
            sys.executable, str(ROOT / "benchmarks/hybrid-fusion/run.py"), "confirm",
            "--backend", "solr", "--run-config", str(tmp_path / "config.json"),
            "--workspace", str(tmp_path / "workspace"), "--control", str(tmp_path / "control.json"),
            "--replay", str(tmp_path / "replay.json"), "--output", str(tmp_path / "out.json"),
        ],
        capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert result.returncode == 2
    assert "cross-backend stage" in result.stderr


def test_missing_materialization_and_tampered_fusion_input_fail_closed():
    truth, capture = fixture()
    changed = copy.deepcopy(capture)
    changed["evidence"].pop()
    with pytest.raises(ValueError, match="evidence identity"):
        BENCH.replay_row(changed, ("control", None, None, 60), 20, truth)
    changed = copy.deepcopy(capture)
    changed["input"]["max_results"] = 19
    with pytest.raises(ValueError, match="fingerprint"):
        BENCH.replay_row(changed, ("control", None, None, 60), 20, truth)


def test_incomplete_and_duplicate_measurements_rejected():
    with pytest.raises(ValueError, match="incomplete"):
        BENCH.validate_rows([{"query_id": "q", "iteration": 1}] * 20, ["q"], 20)


def test_evidence_identity_loss_cannot_hide_behind_equal_recall_or_aggregate_gain():
    truth, capture = fixture()
    control = BENCH.replay_row(capture, ("control", None, None, 60), 20, truth)
    candidate = copy.deepcopy(control)
    candidate["recovered_requirements"] = ["different-need"]
    candidate["evidence_reciprocal_rank"] = 1.0
    comparison = BENCH.compare([control], [candidate])
    assert comparison["nonregression"] is False
    assert comparison["lost_evidence"][0]["requirements"] == ["need"]
    assert BENCH.rank_finalists({"candidate": {b: comparison for b in BENCH.BACKENDS}}) == []


@pytest.mark.parametrize("budget", ("100", "250", "500", "1000"))
def test_context_yield_gate_protects_every_budget(budget):
    truth, capture = fixture()
    control = BENCH.replay_row(capture, ("control", None, None, 60), 20, truth)
    candidate = copy.deepcopy(control)
    candidate["budgets"][budget] -= 0.1
    assert BENCH.compare([control], [candidate])["nonregression"] is False


def test_qdrant_can_be_unchanged_but_all_backends_must_be_present():
    comparison = {
        "nonregression": True,
        "improved": True,
        "deltas": {"evidence_reciprocal_rank": 0.1},
        "context_yield_deltas": {"100": 0},
        "candidates_examined_mean": 40,
    }
    unchanged = {**comparison, "improved": False, "deltas": {"evidence_reciprocal_rank": 0}}
    rows = {"candidate": {b: comparison for b in BENCH.BACKENDS}}
    rows["candidate"]["qdrant-vector"] = unchanged
    assert BENCH.rank_finalists(rows) == ["candidate"]
    del rows["candidate"]["qdrant-vector"]
    assert BENCH.rank_finalists(rows) == []


def test_real_latency_comparability_and_candidate_budget_required():
    base = [{"end_to_end_ms": 100, "lexical_acquired": 80, "dense_acquired": 80}]
    candidate = [{"end_to_end_ms": 115, "lexical_acquired": 80, "dense_acquired": 80}]
    good = {"nonregression": True}
    assert BENCH.promotion_gate(base, candidate, good, performance_comparable=True)["passed"]
    assert not BENCH.promotion_gate(base, candidate, good, performance_comparable=False)["passed"]
    candidate[0]["end_to_end_ms"] = 115.01
    assert not BENCH.promotion_gate(base, candidate, good, performance_comparable=True)["passed"]
    candidate[0]["end_to_end_ms"] = 100
    candidate[0]["lexical_acquired"] = 81
    assert not BENCH.promotion_gate(base, candidate, good, performance_comparable=True)["passed"]
    assert not BENCH.promotion_gate(base, [{}], good, performance_comparable=True)["passed"]


def test_frozen_historical_and_confirmation_inputs_attest():
    evidence, _, suite, queries, identity = BENCH.inputs()
    assert len(suite.cases) == len(queries) == 20
    assert identity["ground_truth_fingerprint"] == suite.fingerprint
    assert evidence.PROTOCOL == "fetchcore.search-intent-v2-evidence"

    confirmation_dir = ROOT / "benchmarks/hybrid-fusion"
    confirmation_path = confirmation_dir / "confirmation-queries.json"
    review = json.loads((confirmation_dir / "confirmation-review.json").read_text())
    assert BENCH.digest(confirmation_path) == review["queries_sha256"]
    confirmation = BENCH.load_baseline_suite(confirmation_path)
    assert len(confirmation.cases) == 10
    assert all(case.intent.canonical_query for case in confirmation.cases)


def test_offline_aggregation_never_invents_latency():
    truth, capture = fixture()
    row = BENCH.replay_row(capture, ("control", None, None, 60), 20, truth)
    summary = BENCH.summarize([row])
    assert summary["duration_ms"] is None and summary["end_to_end_ms"] is None
    assert summary["stage_duration_ms"] == {}
    assert summary["lexical_acquired_mean"] == 2
    assert summary["context_yield"]["100"] == 1


def test_public_report_excludes_private_provider_metadata(tmp_path):
    import os
    import subprocess

    source = tmp_path / "private.json"
    destination = tmp_path / "public.json"
    diagnostics = tmp_path / "public-diagnostics.json"
    BENCH.write(
        source,
        {
            "protocol": BENCH.PROTOCOL,
            "schema_version": 1,
            "stage": "control",
            "status": "blocked",
            "identity": {
                "runtime": {"revision": "fixture", "private_path": "/secret/runner"},
                "private_path": "/private/config.json",
            },
            "error": "secret-private-endpoint",
            "captures": {"content": "private source"},
            "snapshot_errors": {
                "solr-hybrid": {
                    "type": "KnowledgeSearchError",
                    "category": "schema_mismatch",
                    "message": "private host",
                    "details": {
                        "url": "private endpoint",
                        "actual_projection_fingerprint": "sha256:old",
                        "actual": {
                            "projection_fingerprint": "sha256:old",
                            "url": "private endpoint",
                        },
                    },
                }
            },
        },
    )
    completed = subprocess.run(
        [
            sys.executable, str(ROOT / "benchmarks/hybrid-fusion/run.py"), "report",
            "--artifact", str(source), "--output", str(destination),
            "--diagnostics-output", str(diagnostics),
        ],
        capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert completed.returncode == 0, completed.stderr
    text = destination.read_text()
    diagnostics_text = diagnostics.read_text()
    assert (
        "private endpoint" not in text
        and "private host" not in text
        and "private source" not in text
    )
    assert "secret-private-endpoint" not in text
    assert "sha256:old" in text
    assert "/secret/runner" not in text + diagnostics_text
    assert "/private/config.json" not in text + diagnostics_text
    assert "private source" not in diagnostics_text
    assert "private endpoint" not in diagnostics_text
    assert BENCH.read(diagnostics, complete=False)["stage"] == "control"


def rebuild_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "run", BENCH)
    return BENCH.module("hybrid_rebuild", ROOT / "benchmarks/hybrid-fusion/rebuild.py")


def test_rebuild_cache_preserves_vectors_and_requires_exact_identity(tmp_path, monkeypatch):
    rebuild = rebuild_module(monkeypatch)

    class Provider:
        def __init__(self):
            self.calls = []

        def embed(self, texts):
            self.calls.append(texts)
            return tuple((0.1234567890123456,) * 768 for _ in texts)

    provider = Provider()
    cache = rebuild.SharedEmbeddingCache(provider, tmp_path / "vectors.sqlite", "identity-one")
    first = cache.embed(("same", "same", "different"))
    assert provider.calls == [("same", "different")]
    assert first[0][0] == 0.1234567890123456
    assert cache.embed(("same",)) == (first[0],)
    assert len(provider.calls) == 1
    other = rebuild.SharedEmbeddingCache(provider, tmp_path / "vectors.sqlite", "identity-two")
    assert other.embed(("same",)) == (first[0],)
    assert len(provider.calls) == 2
    cache.db.close()
    other.db.close()


def test_rebuild_rejects_incompatible_vectors_before_caching(tmp_path, monkeypatch):
    from types import SimpleNamespace

    rebuild = rebuild_module(monkeypatch)
    cache = rebuild.SharedEmbeddingCache(
        SimpleNamespace(embed=lambda texts: ((1.0,),)), tmp_path / "bad.sqlite", "identity"
    )
    with pytest.raises(ValueError, match="incompatible vector"):
        cache.embed(("source",))
    assert cache.db.execute("SELECT count(*) FROM vectors").fetchone()[0] == 0
    cache.db.close()


def test_rebuild_requires_explicit_execution_before_any_io(tmp_path):
    import subprocess
    import os

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "benchmarks/hybrid-fusion/rebuild.py"),
            "--run-config",
            str(tmp_path / "absent-config"),
            "--workspace",
            str(tmp_path / "absent-corpus"),
            "--output",
            str(tmp_path / "output.json"),
            "--embedding-cache",
            str(tmp_path / "cache.sqlite"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert result.returncode == 2
    assert "explicit --execute-rebuild required" in result.stderr
    assert not (tmp_path / "cache.sqlite").exists()


def test_complete_offline_replay_reuses_identical_acquisitions(tmp_path, monkeypatch):
    from types import SimpleNamespace

    _, _, suite, queries, identity = BENCH.inputs()
    _, row = fixture()
    for doc in row["evidence"]:
        doc["source_ref"] = "repository://" + suite.corpus["repository"] + "/" + doc["source"]
        doc["revision"] = suite.corpus["revision"]
    captures = {
        BENCH.arm_id(b, "hybrid"): [
            {**row, "query_id": q, "iteration": i} for q in queries for i in range(1, 21)
        ]
        for b in BENCH.BACKENDS
    }
    source = tmp_path / "capture.json.gz"
    outputs = []
    BENCH.write(
        source,
        {
            "protocol": BENCH.PROTOCOL,
            "schema_version": 1,
            "stage": "capture",
            "status": "complete",
            "identity": identity,
            "snapshots": {
                BENCH.arm_id(backend, mode): {
                    "backend": backend, "mode": mode, "projection": "frozen-shared"
                }
                for backend in BENCH.BACKENDS for mode in ("dense", "hybrid")
            },
            "measurement": BENCH.FULL_MEASUREMENT,
            "measurement_kind": "real",
            "captures": captures,
        },
    )
    original = BENCH.replay_row
    calls = []

    def checked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(BENCH, "replay_row", checked)
    for backend in BENCH.BACKENDS:
        output = tmp_path / f"replay-{backend}.json.gz"
        BENCH.replay(SimpleNamespace(capture=source, output=output, backend=backend))
        outputs.append(output)
    combined_output = tmp_path / "replay.json.gz"
    BENCH.consolidate(SimpleNamespace(output=combined_output, backend_artifact=outputs))
    output = combined_output
    result = BENCH.read(output, stage="replay")
    assert len(calls) == 3 * 20 * len(BENCH.variants())
    for backend in BENCH.BACKENDS:
        for variant in BENCH.variants():
            assert len(result["observations"][backend][variant[0]]) == 20
            first = result["observations"][backend][variant[0]][0]
            payload = BENCH.load_checkpoint(
                Path(first["path"]),
                identity_fingerprint=result["checkpoint_identities"][backend],
                backend=backend,
                query_id=first["query_id"],
            )
            assert len(payload["observations"][variant[0]]) == 20
            assert result["aggregates"][backend][variant[0]]["duration_ms"] is None
    assert result["finalists"] == []

    mismatched_references = BENCH.read(outputs[0], stage="replay")
    mismatched_references["observations"]["solr"][BENCH.variants()[1][0]][0]["sha256"] = "0" * 64
    mismatched_path = tmp_path / "replay-solr-mismatched-reference.json.gz"
    BENCH.write(mismatched_path, mismatched_references)
    with pytest.raises(ValueError, match="variants reference different query checkpoints"):
        BENCH.consolidate(SimpleNamespace(
            output=tmp_path / "mismatched-replay.json.gz",
            backend_artifact=[mismatched_path, outputs[1], outputs[2]],
        ))
    assert not (tmp_path / "mismatched-replay.json.gz").exists()

    resume_output = tmp_path / "resumed-solr-replay.json.gz"
    checkpoint_dir = Path(str(resume_output) + ".checkpoints")
    backend = "solr"
    query_id = next(iter(queries))
    checkpoint_identity = BENCH.mapping_fingerprint({
        "stage": "replay", "capture_fingerprint": BENCH.digest(source),
        "identity": identity, "backend": backend,
        "variants": [v[0] for v in BENCH.variants()],
    })
    query_captures = [
        capture_row for capture_row in captures[BENCH.arm_id(backend, "hybrid")]
        if capture_row["query_id"] == query_id
    ]
    replay_observations = {v[0]: [] for v in BENCH.variants()}
    comparison_rows = {v[0]: [] for v in BENCH.variants()}
    truth = next(case.truth for case in suite.cases if case.query_id == query_id)
    for capture_row in query_captures:
        for variant in BENCH.variants():
            replayed = BENCH.replay_row(capture_row, variant, 20, truth)
            replay_observations[variant[0]].append(replayed)
            comparison_rows[variant[0]].append(BENCH.compact_replay_row(replayed))
    checkpoint = BENCH.checkpoint_path(checkpoint_dir, backend, query_id)
    BENCH.save_checkpoint(
        checkpoint, identity_fingerprint=checkpoint_identity, backend=backend,
        query_id=query_id,
        payload={"observations": replay_observations, "comparison_rows": comparison_rows},
    )
    calls.clear()
    BENCH.replay(SimpleNamespace(
        capture=source, output=resume_output, backend=backend, resume=True,
    ))
    assert len(calls) == 19 * len(BENCH.variants())


def control_artifacts(tmp_path):
    truth, capture = fixture()
    template = BENCH.replay_row(capture, ('control', None, None, 60), 20, truth)
    query_ids = [f'q{i}' for i in range(20)]
    paths = []
    for backend in BENCH.BACKENDS:
        observations = {
            BENCH.arm_id(backend, mode): [
                {**template, 'query_id': q, 'iteration': i}
                for q in query_ids for i in range(1, 21)
            ] for mode in ('dense', 'hybrid')
        }
        payload = {
            'protocol': BENCH.PROTOCOL, 'schema_version': 1, 'stage': 'control',
            'status': 'complete', 'identity': {'runner_fingerprint': 'frozen'},
            'measurement': BENCH.FULL_MEASUREMENT, 'measurement_kind': 'real',
            'query_ids': query_ids, 'observations': observations,
            'snapshots': {arm: {'revision': 'frozen'} for arm in observations},
            'aggregates': {arm: BENCH.summarize(rows) for arm, rows in observations.items()},
        }
        path = tmp_path / f'{backend}.json.gz'
        BENCH.write(path, payload)
        paths.append(path)
    return paths


def test_control_consolidation_preserves_all_six_arms_and_is_order_independent(tmp_path):
    from types import SimpleNamespace

    paths = control_artifacts(tmp_path)
    originals = {path: path.read_bytes() for path in paths}
    outputs = [tmp_path / 'forward.gz', tmp_path / 'reverse.gz']
    for output, inputs in zip(outputs, (paths, paths[::-1])):
        BENCH.consolidate(SimpleNamespace(output=output, backend_artifact=inputs))
        combined = BENCH.read(output)
        assert len(combined['aggregates']) == len(combined['observations']) == 6
        assert sum(map(len, combined['observations'].values())) == 2400
        for path in paths:
            source = BENCH.read(path)
            assert combined['identity'] == source['identity']
            for arm, rows in source['observations'].items():
                assert combined['observations'][arm] == rows
                assert combined['aggregates'][arm] == source['aggregates'][arm]
                assert combined['aggregates'][arm] == BENCH.summarize(rows)
                assert combined['snapshots'][arm] == source['snapshots'][arm]
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert all(path.read_bytes() == original for path, original in originals.items())


@pytest.mark.parametrize('fault', [
    'missing_arm', 'missing_aggregate', 'missing_row', 'duplicate_row', 'duplicate_backend',
    'fingerprint', 'aggregate', 'corruption', 'foreign_arm', 'snapshot_collision',
    'missing_query', 'duplicate_query', 'missing_snapshot',
])
def test_control_consolidation_rejects_invalid_inputs_atomically(tmp_path, fault):
    from types import SimpleNamespace
    import gzip

    paths = control_artifacts(tmp_path)
    altered = BENCH.read(paths[0])
    arm = BENCH.arm_id('solr', 'dense')
    if fault == 'missing_arm':
        del altered['observations'][arm]
    elif fault == 'missing_aggregate':
        del altered['aggregates'][arm]
    elif fault == 'missing_row':
        altered['observations'][arm].pop()
    elif fault == 'duplicate_row':
        altered['observations'][arm][-1] = altered['observations'][arm][0]
    elif fault == 'duplicate_backend':
        paths[1] = paths[0]
    elif fault == 'missing_query':
        altered['query_ids'].pop()
    elif fault == 'duplicate_query':
        altered['query_ids'][-1] = altered['query_ids'][0]
    elif fault == 'missing_snapshot':
        del altered['snapshots'][arm]
    elif fault == 'fingerprint':
        altered['identity']['runner_fingerprint'] = 'incompatible'
    elif fault == 'aggregate':
        altered['aggregates'][arm]['evidence_recall_at_k'] = -1
    elif fault == 'foreign_arm':
        altered['observations']['unexpected-arm'] = altered['observations'][arm]
    elif fault == 'snapshot_collision':
        altered['snapshots'][BENCH.arm_id('opensearch', 'dense')] = {'revision': 'other'}
    BENCH.write(paths[0], altered)
    if fault == 'corruption':
        payload = json.loads(gzip.decompress(paths[0].read_bytes()))
        payload['observations'][arm][0]['revision'] = 'corrupt'
        paths[0].write_bytes(gzip.compress(json.dumps(payload).encode()))
    output = tmp_path / 'invalid.gz'
    with pytest.raises(ValueError):
        BENCH.consolidate(SimpleNamespace(output=output, backend_artifact=paths))
    assert not output.exists()


def test_published_reports_validate_seals_and_preserve_flagged_historical_files():
    directory = ROOT / 'benchmarks/hybrid-fusion/artifacts/runs/4d9332bf'
    checksums = {
        name: checksum
        for checksum, name in (
            line.split() for line in (directory / 'SHA256SUMS').read_text().splitlines()
        )
    }
    assert set(checksums) == {path.name for path in directory.glob('*.json')}
    assert all(BENCH.digest(directory / name) == checksum for name, checksum in checksums.items())
    historical = {
        'control-report.json': 'dc22cc40462e65daeb30e3602bef8a59d770c6eb6af7427b4e6edf0e52cf883a',
        'control-diagnostics.json': 'b0d7704ea7b329d6d8207b957e5722c54a398374775787bf233bc75c4ab8e1ce',
    }
    for path in directory.glob('*.json'):
        if path.name in historical:
            assert BENCH.digest(path) == historical[path.name]
            with pytest.raises(ValueError, match='artifact checksum mismatch'):
                BENCH.read(path)
        elif 'artifact_fingerprint' in json.loads(path.read_text()):
            BENCH.read(path, complete=False)
    for name in ('control-consolidated-v1-report.json', 'control-consolidated-v1-diagnostics.json'):
        assert len(BENCH.read(directory / name)['aggregates']) == 6
