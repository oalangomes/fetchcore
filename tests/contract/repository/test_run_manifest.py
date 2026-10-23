"""Run composition preserves measured work and omits source/endpoint payloads."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from fetchcore.application.index_artifacts import IndexRunArtifact
from fetchcore.application.run_manifest import OperationalRunIdentity, compose_run_manifest

FIXTURES = Path(__file__).resolve().parents[3] / "tools/explorer/fixtures/index-runs"


def artifact(name):
    return IndexRunArtifact.from_mapping(json.loads((FIXTURES / (name + ".json")).read_text()))


def manifest(observation, outcome="success"):
    identity = OperationalRunIdentity(**{key: getattr(observation, key) for key in (
        "run_id", "repository", "backend", "target", "configuration_fingerprint",
    )})
    revision = observation.command.get("result", {}).get("target_revision", "unmeasured-revision")
    return compose_run_manifest([observation], identity=identity, requested="sync",
                                revision=revision, outcome=outcome)


def test_incremental_and_noop_keep_actual_counts_without_invented_cost():
    changed = manifest(artifact("incremental"))["observations"][0]
    noop = manifest(artifact("noop"))["observations"][0]
    assert changed["discovery"]["changed_paths"] == 1
    assert changed["execution"]["added"] == 1
    assert noop["mode"] == "noop"
    assert noop["execution"]["added"] == noop["execution"]["updated"] == 0
    assert noop["execution"]["telemetry"]["backend_writes"] == 0
    assert noop["execution"]["telemetry"]["embedding_requests"] == 0
    assert changed["previous_indexed_revision"] == noop["target_revision"]
    assert "telemetry" not in changed["execution"]
    assert "stage_timings_ms" not in changed["execution"]
    assert manifest(artifact("noop")) == manifest(artifact("noop"))


def test_run_manifest_excludes_raw_paths_and_preserves_fingerprints():
    observation = artifact("incremental")
    result = manifest(observation)
    encoded = json.dumps(result)
    assert "/tmp/pytest" not in encoded
    assert '"source"' not in encoded
    snapshot = result["observations"][0]["execution"]["snapshot"]
    assert snapshot["corpus_checksum"] == observation.command["result"]["execution"]["snapshot"]["corpus_checksum"]
    assert snapshot["indexing_fingerprint"] == observation.command["result"]["indexing_fingerprint"]


def test_full_discovery_does_not_claim_rebuild_or_unmeasured_file_deltas():
    observation = artifact("incremental")
    result = observation.command["result"]
    result["execution"]["discovery"] = {"mode": "full-scan", "reason": "history_unavailable"}
    result["indexing_telemetry"] = {"files_scanned": 10, "documents_per_source": {
        "/private/path": 4,
    }}
    result["execution"]["stage_timings_ms"] = {"canonical_full_index_ms": 24}
    observed = manifest(observation)["observations"][0]
    assert observed["mode"] == "incremental"
    assert observed["execution"]["mode"] == "incremental"
    assert observed["discovery"] == {"mode": "full-scan", "reason": "history_unavailable"}
    assert observed["indexing_telemetry"] == {"files_scanned": 10}
    assert observed["execution"]["stage_timings_ms"] == {"canonical_full_index_ms": 24}


def test_manifest_cli_composes_available_commands_after_failure(tmp_path):
    import subprocess
    import sys

    observation = artifact("incremental")
    failed = {"operation": "sync", "status": "failed", "error": {
        "category": "rebuild_required", "message": "/private/operator/config.yaml",
    }}
    directory = tmp_path / "evidence"
    directory.mkdir()
    (directory / "execution.json").write_text(json.dumps(failed))
    config = tmp_path / "effective.json"
    config.write_text(json.dumps({"runtime": {"workspace": "/private/operator"}}))
    root = FIXTURES.parents[3]
    output = directory / "run-manifest.json"
    subprocess.run([
        sys.executable, str(root / "scripts/ci/build-run-manifest.py"),
        "--repository", observation.repository, "--backend", observation.backend,
        "--revision", "abc", "--target", observation.target, "--run-id", "run-1",
        "--observed-at", observation.observed_at, "--lifecycle", "sync", "--outcome", "failure",
        "--configuration", str(config), "--directory", str(directory), "--output", str(output),
    ], check=True)
    value = json.loads(output.read_text())
    assert value["outcome"] == "failure"
    assert value["observations"][0]["error"]["category"] == "rebuild_required"
    assert "/private/operator" not in output.read_text()


def test_failure_is_observable_and_cannot_become_success():
    observed = artifact("incremental")
    failed = replace(observed, command={"operation": "sync", "status": "failed", "error": {
        "category": "rebuild_required", "message": "private path /secret/operator",
    }})
    result = manifest(failed, "failure")
    assert result["observations"][0]["error"] == {"category": "rebuild_required"}
    assert "/secret" not in json.dumps(result)
    with pytest.raises(ValueError, match="successful run"):
        manifest(failed)


def test_composition_rejects_mixed_destinations_or_revision():
    observed = artifact("incremental")
    identity = OperationalRunIdentity(observed.run_id, observed.repository, observed.backend,
                                      observed.target, observed.configuration_fingerprint)
    with pytest.raises(ValueError, match="different runs"):
        compose_run_manifest([observed, replace(observed, backend="opensearch")], identity=identity,
                             requested="sync", revision=observed.command["result"]["target_revision"],
                             outcome="success")
    with pytest.raises(ValueError, match="revision mismatch"):
        compose_run_manifest([observed], identity=identity, requested="sync", revision="other",
                             outcome="success")


def test_preflight_failure_has_empty_observations_instead_of_synthetic_measurements():
    identity = OperationalRunIdentity("run-1", "acme/widgets", "solr", "isolated", "a" * 64)
    result = compose_run_manifest([], identity=identity, requested="sync", revision="abc",
                                  outcome="failure")
    assert result["outcome"] == "failure"
    assert result["observations"] == []
    with pytest.raises(ValueError, match="requires command evidence"):
        compose_run_manifest([], identity=identity, requested="sync", revision="abc",
                             outcome="success")
