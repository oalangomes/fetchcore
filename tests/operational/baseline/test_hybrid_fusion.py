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
    damaged = bytearray(path.read_bytes())
    damaged[-5] ^= 1
    path.write_bytes(damaged)
    with pytest.raises(ValueError, match="invalid checkpoint|checksum mismatch"):
        BENCH.load_checkpoint(
            path, identity_fingerprint="sha256:runner-a", backend="solr",
            query_id="query/with unsafe path",
        )


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
            BENCH.arm_id("solr", "hybrid"): [{"query_id": "q1", "path": str(path)}]
        },
        "checkpoint_identities": {BENCH.arm_id("solr", "hybrid"): identity},
    }
    assert list(BENCH.iter_capture_rows(capture, "solr", ["q1"])) == rows


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


def test_offline_aggregation_never_invents_latency():
    truth, capture = fixture()
    row = BENCH.replay_row(capture, ("control", None, None, 60), 20, truth)
    summary = BENCH.summarize([row])
    assert summary["duration_ms"] is None and summary["end_to_end_ms"] is None
    assert summary["stage_duration_ms"] == {}
    assert summary["lexical_acquired_mean"] == 2
    assert summary["context_yield"]["100"] == 1


def test_public_report_excludes_private_provider_metadata(tmp_path):
    from types import SimpleNamespace

    source = tmp_path / "private.json"
    destination = tmp_path / "public.json"
    BENCH.write(
        source,
        {
            "protocol": BENCH.PROTOCOL,
            "schema_version": 1,
            "stage": "control",
            "status": "blocked",
            "identity": {"runtime": {"revision": "fixture"}},
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
    BENCH.report(SimpleNamespace(artifact=source, output=destination))
    text = destination.read_text()
    assert (
        "private endpoint" not in text
        and "private host" not in text
        and "private source" not in text
    )
    assert "secret-private-endpoint" not in text
    assert "sha256:old" in text


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
    output = tmp_path / "replay.json.gz"
    BENCH.write(
        source,
        {
            "protocol": BENCH.PROTOCOL,
            "schema_version": 1,
            "stage": "capture",
            "status": "complete",
            "identity": identity,
            "snapshots": {},
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
    BENCH.replay(SimpleNamespace(capture=source, output=output))
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
