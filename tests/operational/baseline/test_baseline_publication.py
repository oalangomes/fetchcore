"""Offline safety tests for the baseline-v1 publication boundary."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PATH = ROOT / "benchmarks" / "baseline-v1" / "publish_run.py"
SPEC = importlib.util.spec_from_file_location("fetchcore_baseline_publish", PATH)
assert SPEC is not None and SPEC.loader is not None
PUBLISH = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PUBLISH
SPEC.loader.exec_module(PUBLISH)

REVISION = "a" * 40
CORPUS = {"corpus_id": "fixture", "repository": "owner/private", "revision": REVISION}
ARMS = [
    {"id": "solr-dense", "backend": "solr", "mode": "dense"},
    {"id": "qdrant-hybrid", "backend": "qdrant-vector", "mode": "hybrid"},
]


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")


def _source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    suite = tmp_path / "suite"
    suite.mkdir()
    monkeypatch.setattr(PUBLISH, "ROOT", suite)
    _write(suite / "manifest.json", {
        "arms": ARMS, "measurement": {"repetitions": 2}
    })
    _write(suite / "ground-truth.json", {
        "corpus": CORPUS, "queries": [{"id": "x"}, {"id": "y"}],
    })
    source = tmp_path / "runs" / "V0.4"
    source.mkdir(parents=True)
    run = {
        "schema_version": 1, "protocol": PUBLISH.PROTOCOL, "status": "complete",
        "run_version": "V0.4", "corpus": CORPUS, "fetchcore_revision": REVISION,
        "arms": ARMS, "artifacts": [f"{arm['id']}.json" for arm in ARMS],
        "prepare_index": True, "indexing_artifact": "indexing.json",
        "metrics_report": "README.md",
    }
    _write(source / "run.json", run)
    _write(source / "bench-run.json", {
        "schema_version": 1,
        "config": {
            "environment": "bench", "repository": CORPUS["repository"],
            "revision": CORPUS["revision"], "performance_comparable": False,
        },
        "ground_truth_fingerprint": "sha256:ground-truth",
        "observed_fingerprints": {"retrieval": {}},
    })
    for arm in ARMS:
        _write(source / f"{arm['id']}.json", {
            "protocol": PUBLISH.PROTOCOL, "arm": arm, "corpus": CORPUS,
            "ground_truth_fingerprint": "sha256:ground-truth",
            "metrics_fingerprint": "sha256:metrics",
            "config_fingerprint": f"sha256:{arm['id']}",
            "config": {"identity": {"fetchcore_revision": REVISION}},
            "observations": [{}, {}, {}, {}],
            "aggregate": {"run_count": 4},
        })
    _write(source / "indexing.json", {
        "schema_version": 1, "protocol": PUBLISH.PROTOCOL,
        "corpus": {"repository": CORPUS["repository"], "revision": CORPUS["revision"]},
        "fetchcore_revision": REVISION,
        "indexing_fingerprint": "sha256:indexing",
        "canonical": {"repository": CORPUS["repository"], "revision": CORPUS["revision"]},
        "backends": {
            backend: {
                "snapshot": {
                    "repository": CORPUS["repository"],
                    "snapshot_revision": CORPUS["revision"],
                },
                "telemetry": {"backend_writes": 1},
            }
            for backend in ("solr", "opensearch", "qdrant")
        },
    })
    _write(source / "context-yield.json", {
        "protocol": "fetchcore.baseline-v1-context-yield",
        "source_protocol": PUBLISH.PROTOCOL,
        "ground_truth_fingerprint": "sha256:ground-truth",
        "source_metrics_fingerprint": "sha256:metrics",
        "arms": [
            {"arm": arm, "source_config_fingerprint": f"sha256:{arm['id']}"}
            for arm in ARMS
        ],
    })
    (source / "README.md").write_text("# Report\n", encoding="utf-8")
    (source / "context-yield.md").write_text("# Context\n", encoding="utf-8")
    (source / ".run-state.json").write_text("machine local", encoding="utf-8")
    return source


def test_publish_is_offline_and_immutable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path, monkeypatch)
    published = tmp_path / "published"
    legacy = published / "V0.2" / "run.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"historical bytes")
    expected = {p.name: p.read_bytes() for p in source.iterdir()
                if p.name != ".run-state.json"}

    target = PUBLISH.publish(source, published)
    assert target == published / "V0.4"
    assert not target.exists()  # Dry-run by default

    PUBLISH.publish(source, published, write=True)
    assert {p.name: p.read_bytes() for p in target.iterdir()} == expected
    assert legacy.read_bytes() == b"historical bytes"
    assert (source / ".run-state.json").exists()
    assert not (target / ".run-state.json").exists()
    assert not (published / ".publication.lock").exists()
    with pytest.raises(PUBLISH.PublicationError, match="already exists"):
        PUBLISH.publish(source, published, write=True)


def test_rejects_incomplete_and_mismatched_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path, monkeypatch)
    artifact = source / "solr-dense.json"
    artifact.unlink()
    with pytest.raises(PUBLISH.PublicationError, match="missing or unsafe"):
        PUBLISH.validate(source)
    _write(artifact, {
        "protocol": PUBLISH.PROTOCOL, "arm": ARMS[0], "corpus": CORPUS,
        "ground_truth_fingerprint": "sha256:ground-truth",
        "config": {"identity": {"fetchcore_revision": REVISION}},
        "observations": [{}], "aggregate": {"run_count": 1},
    })
    with pytest.raises(PUBLISH.PublicationError, match="incomplete measurements"):
        PUBLISH.validate(source)


def test_rejects_tampered_revision_and_context_yield(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path, monkeypatch)
    run = json.loads((source / "run.json").read_text())
    run["status"] = "retrieval_complete"
    _write(source / "run.json", run)
    with pytest.raises(PUBLISH.PublicationError, match="incomplete"):
        PUBLISH.validate(source)

    run["status"] = "complete"
    _write(source / "run.json", run)
    _write(source / "context-yield.json", {"protocol": "wrong"})
    with pytest.raises(PUBLISH.PublicationError, match="context-yield"):
        PUBLISH.validate(source)


def test_rejects_stale_context_yield(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path, monkeypatch)
    derived = json.loads((source / "context-yield.json").read_text())
    derived["arms"][0]["source_config_fingerprint"] = "sha256:stale"
    _write(source / "context-yield.json", derived)
    with pytest.raises(PUBLISH.PublicationError, match="context-yield does not match"):
        PUBLISH.validate(source)


@pytest.mark.parametrize("invalid_field", [
    "schema_version", "protocol", "fetchcore_revision", "indexing_fingerprint",
    "corpus", "canonical", "backends", "snapshot", "telemetry",
])
def test_rejects_incompatible_indexing_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_field: str,
) -> None:
    source = _source(tmp_path, monkeypatch)
    indexing_path = source / "indexing.json"
    indexing = json.loads(indexing_path.read_text())
    if invalid_field in {"schema_version", "protocol", "fetchcore_revision",
                         "indexing_fingerprint"}:
        indexing[invalid_field] = None
    elif invalid_field in {"corpus", "canonical"}:
        indexing[invalid_field]["revision"] = "different"
    elif invalid_field == "backends":
        del indexing["backends"]["qdrant"]
    elif invalid_field == "snapshot":
        indexing["backends"]["solr"]["snapshot"]["snapshot_revision"] = "different"
    else:
        del indexing["backends"]["solr"]["telemetry"]
    _write(indexing_path, indexing)

    with pytest.raises(PUBLISH.PublicationError, match="indexing telemetry"):
        PUBLISH.validate(source)


def test_rejects_symlinked_measurements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path, monkeypatch)
    artifact = source / "solr-dense.json"
    outside = tmp_path / "outside.json"
    artifact.rename(outside)
    artifact.symlink_to(outside)
    with pytest.raises(PUBLISH.PublicationError, match="unsafe"):
        PUBLISH.validate(source)
