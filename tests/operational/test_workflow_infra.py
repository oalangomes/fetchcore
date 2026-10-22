"""Static execution boundaries for hosted and persistent indexing callers."""
from pathlib import Path
import json
import os
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def test_operational_reuses_existing_snapshot_before_sync() -> None:
    caller = _workflow("operational-index.yml")["jobs"]["operational-index"]["with"]
    assert caller["solr_provision_mode"] == "reuse_only"
    assert caller["create_if_missing"] is False
    assert "== 'sync'" in caller["require_existing_snapshot"]
    assert "inputs.index_lifecycle || 'sync'" in caller["index_lifecycle"]
    assert "38983/solr" in caller["solr_url"]
    reusable = _workflow("repository-index.yml")["jobs"]["index"]["steps"]
    names = [step.get("name") for step in reusable]
    assert names.index("Index status before execution") < names.index(
        "Require compatible existing operational snapshot"
    ) < names.index("Synchronize repository index")
    gate = reusable[names.index("Require compatible existing operational snapshot")]
    assert "require_existing_snapshot" in gate["if"]
    assert "incremental_possible" in gate["run"]
    assert "projection_compatible" in gate["run"]


def test_hosted_consumers_keep_run_owned_fixture_services() -> None:
    dogfood = _workflow("self-dogfood.yml")["jobs"]["self-index"]["with"]
    assert dogfood["runner_labels_json"] == '["ubuntu-latest"]'
    assert dogfood["create_if_missing"] is True
    assert dogfood["solr_provision_mode"] == "provision_if_missing"
    assert dogfood.get("require_existing_snapshot", False) is False
    explorer = _workflow("explorer.yml")["jobs"]["explorer"]["steps"]
    assert any("docker run -d --name fetchcore-explorer-solr" in step.get("run", "")
               for step in explorer)
    assert not any("38983" in step.get("run", "") for step in explorer)


def test_existing_snapshot_gate_reads_cli_envelope_and_fails_closed(tmp_path: Path) -> None:
    steps = _workflow("repository-index.yml")["jobs"]["index"]["steps"]
    script = next(step["run"] for step in steps
                  if step.get("name") == "Require compatible existing operational snapshot")
    path = tmp_path / "status-before.json"
    environment = {**os.environ, "FETCHCORE_DOGFOOD_OUTPUT": str(tmp_path),
                   "EXPECTED_REPOSITORY": "oalangomes/fetchcore", "EXPECTED_BRANCH": "master"}
    compatible = {
        "repository": "oalangomes/fetchcore", "branch": "master",
        "mode": "noop", "currentness": "current", "incremental_possible": True,
        "schema_compatible": True, "projection_compatible": True,
        "indexed_revision": "old", "indexing_fingerprint": "sha256:index",
        "projection_fingerprint": "sha256:projection",
    }
    cases = [
        (compatible, True),
        ({**compatible, "mode": "incremental", "currentness": "stale"}, True),
        ({**compatible, "mode": "initial", "currentness": "absent",
          "indexed_revision": None, "incremental_possible": False}, False),
        ({**compatible, "mode": "rebuild_required", "currentness": "incompatible"}, False),
        ({**compatible, "repository": "other/repository"}, False),
        ({**compatible, "branch": "other"}, False),
        ({**compatible, "schema_compatible": False}, False),
        ({**compatible, "projection_compatible": False}, False),
        ({**compatible, "indexing_fingerprint": None}, False),
    ]
    for payload, allowed in cases:
        path.write_text(json.dumps({"status": "succeeded", "result": payload}))
        result = subprocess.run(["bash", "-c", script], env=environment,
                                capture_output=True, text=True, check=False)
        assert (result.returncode == 0) is allowed, result.stderr
