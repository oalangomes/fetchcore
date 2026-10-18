"""Offline artifact validation must fail closed without creating a runtime/backend."""

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from fetchcore.cli import main
from fetchcore.application.index_artifacts import (
    compare_index_artifacts,
    load_index_artifact,
)

FIXTURES = Path(__file__).resolve().parents[3] / "tools/explorer/fixtures/index-runs"


def fixture(name="incremental"):
    return json.loads((FIXTURES / (name + ".json")).read_text())


def test_fixture_roundtrips_and_missing_telemetry():
    for path in FIXTURES.glob("*.json"):
        raw = path.read_text()
        artifact = load_index_artifact(raw)
        assert load_index_artifact(json.dumps(artifact.to_mapping())) == artifact
    value = fixture()
    value["command"]["result"]["execution"].pop("telemetry", None)
    assert "telemetry" not in load_index_artifact(json.dumps(value)).command["result"]["execution"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.update(schema_version=True),
        lambda v: v["command"]["result"]["execution"]["snapshot"].update(vector_dimensions=True),
        lambda v: v["command"]["result"]["execution"]["snapshot"].update(serializer_fingerprint={}),
        lambda v: v["command"]["result"].update(retrieval_mode="dense"),
        lambda v: v["command"].update(operation={}),
        lambda v: v["command"].update(status=[]),
        lambda v: v["command"]["result"].update(mode=[]),
        lambda v: v["command"]["result"]["execution"].update(mode={}),
        lambda v: v.update(schema_version=2),
        lambda v: v.update(unknown=1),
        lambda v: v.update(observed_at="2026-10-15"),
        lambda v: v.update(configuration_fingerprint="unknown"),
        lambda v: v.update(repository="other/repository"),
        lambda v: v["command"].update(operation="delete"),
        lambda v: v["command"]["result"].update(mode="fake"),
        lambda v: v["command"]["result"]["execution"].update(added=-1),
        lambda v: v["command"]["result"]["execution"].update(added=True),
        lambda v: v["command"]["result"]["execution"].update(duration_ms=float("nan")),
        lambda v: v["command"]["result"]["execution"]["snapshot"].update(repository="other"),
        lambda v: v["command"]["result"]["execution"]["snapshot"].update(
            projection_fingerprint="other"
        ),
        lambda v: v["command"]["result"]["execution"]["snapshot"].update(revision="other"),
        lambda v: v["command"]["result"].update(planned_mutations={}),
    ],
)
def test_invalid_artifacts_rejected(mutate):
    value = fixture()
    mutate(value)
    with pytest.raises(ValueError):
        load_index_artifact(json.dumps(value))


def test_observed_index_stage_timings_roundtrip_and_reject_invented_values():
    for artifact_name, location in (
        ("incremental", "execution"), ("delta-plan", "plan"),
    ):
        value = fixture(artifact_name)
        result = value["command"]["result"]
        stages = {"snapshot_load_ms": 3, "previous_documents_load_ms": 25}
        target = result["execution"] if location == "execution" else result
        target["stage_timings_ms"] = stages
        parsed = load_index_artifact(json.dumps(value))
        observed = parsed.command["result"]
        actual = observed["execution"] if location == "execution" else observed
        assert actual["stage_timings_ms"] == stages

        for invalid in (-1, True, None, 1.5):
            target["stage_timings_ms"] = {"snapshot_load_ms": invalid}
            with pytest.raises(ValueError):
                load_index_artifact(json.dumps(value))
        target["stage_timings_ms"] = {"invented_time_ms": 0}
        with pytest.raises(ValueError):
            load_index_artifact(json.dumps(value))


def test_duplicate_keys_and_size_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        load_index_artifact('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="2 MB"):
        load_index_artifact(" " * 2_000_001)


@pytest.mark.parametrize("field", ["repository", "backend", "target", "configuration_fingerprint"])
def test_identity_changes_block_comparison(field):
    before = load_index_artifact(json.dumps(fixture()))
    value = fixture()
    if field == "repository":
        value["command"]["result"]["repository"] = "other"
        value["command"]["result"]["execution"]["snapshot"]["repository"] = "other"
    value[field] = "b" * 64 if field == "configuration_fingerprint" else "other"
    report = compare_index_artifacts(before, load_index_artifact(json.dumps(value)))
    assert report["comparable"] is False
    assert field + "_changed" in report["reasons"]


def test_failure_and_unmeasured_embedding_identity_block_comparison():
    for name in ("failure", "initial-plan"):
        artifact = load_index_artifact(json.dumps(fixture(name)))
        assert compare_index_artifacts(artifact, artifact)["comparable"] is False


def test_offline_cli_wraps_json_with_configuration_identity(tmp_path):
    command = tmp_path / "command.json"
    config = tmp_path / "config.json"
    command.write_text(json.dumps(fixture()["command"]))
    config.write_text('{"backend":"solr","collection":"fixture"}')
    args = [
        "index",
        "artifact",
        "--input",
        str(command),
        "--configuration",
        str(config),
        "--run-id",
        "recorded",
        "--observed-at",
        "2026-10-15T12:00:00Z",
        "--repository",
        "acme/widgets",
        "--backend",
        "solr",
        "--target",
        "fixture",
    ]
    output = CliRunner().invoke(main, args)
    assert output.exit_code == 0, output.output
    artifact = load_index_artifact(output.output)
    assert artifact.command == fixture()["command"]
    assert "collection" not in output.output
    config.write_text('{"collection":"fixture", "backend":"solr"}')
    assert CliRunner().invoke(main, args).output == output.output
    config.write_text('{"collection":"changed", "backend":"solr"}')
    changed = load_index_artifact(CliRunner().invoke(main, args).output)
    assert changed.configuration_fingerprint != artifact.configuration_fingerprint
    command.write_text('{"operation":"status","operation":"sync"}')
    assert CliRunner().invoke(main, args).exit_code == 1


def test_fingerprints_and_embedding_changes_are_not_source_diffs():
    before = load_index_artifact(json.dumps(fixture()))
    for key in (
        "indexing_fingerprint",
        "projection_fingerprint",
        "serializer_fingerprint",
        "embedding_provider",
        "embedding_model",
        "vector_dimensions",
    ):
        value = fixture()
        snapshot = value["command"]["result"]["execution"]["snapshot"]
        snapshot[key] = 512 if key == "vector_dimensions" else "changed"
        if key in ("indexing_fingerprint", "projection_fingerprint"):
            value["command"]["result"][key] = "changed"
        report = compare_index_artifacts(before, load_index_artifact(json.dumps(value)))
        assert report["comparable"] is False
        assert report["source_revision_changed"] is False


def test_incompatible_and_rollback_failure_evidence():
    incompatible = load_index_artifact(json.dumps(fixture("incompatible-plan")))
    assert incompatible.command["result"]["mode"] == "rebuild_required"
    assert "rebuild_required" in compare_index_artifacts(incompatible, incompatible)["reasons"]
    failed = load_index_artifact(json.dumps(fixture("rollback-failure")))
    assert failed.command["error"]["category"] == "index_update_failure"
    assert "result" not in failed.command
    assert compare_index_artifacts(failed, failed)["source_revision_changed"] is None
