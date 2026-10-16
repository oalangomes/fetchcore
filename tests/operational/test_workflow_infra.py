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
    assert caller["require_existing_snapshot"] is True
    assert caller["index_lifecycle"] == "sync"
    assert "38983/solr" in caller["solr_url"]
    reusable = _workflow("repository-index.yml")["jobs"]["index"]["steps"]
    names = [step.get("name") for step in reusable]
    assert names.index("Index status before execution") < names.index(
        "Require compatible existing operational snapshot"
    ) < names.index("Synchronize repository index")
    gate = reusable[names.index("Require compatible existing operational snapshot")]
    assert "require_existing_snapshot" in gate["if"]
    assert "snapshot_fingerprint_matches" in gate["run"]


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
    environment = {**os.environ, "FETCHCORE_DOGFOOD_OUTPUT": str(tmp_path)}
    for state, fingerprint, expected in (("changes_pending", True, 0),
                                          ("not_indexed", True, 1),
                                          ("ready", False, 1)):
        path.write_text(json.dumps({"status": "succeeded", "result": {
            "state": state, "snapshot": {"revision": "old"},
            "snapshot_fingerprint_matches": fingerprint,
        }}))
        result = subprocess.run(["bash", "-c", script], env=environment,
                                capture_output=True, text=True, check=False)
        assert (result.returncode == 0) is (expected == 0), result.stderr
