"""Execute workflow shell boundaries with canned CLI envelopes, never services."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml


WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"
REVISION = "a" * 40
LIFECYCLE = "${{ github.event_name == 'workflow_dispatch' && inputs.index_lifecycle || 'sync' }}"


def _workflow(backend):
    name = "operational-index.yml" if backend == "solr" else f"operational-{backend}.yml"
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _expression(value, **contexts):
    if not isinstance(value, str):
        return value
    expression = value.removeprefix("${{").removesuffix("}}").strip()
    # Evaluate only checked-in workflow boolean expressions with fixed fixtures.
    return eval(expression.replace("&&", " and ").replace("||", " or "),
                {"__builtins__": {}}, contexts)


@pytest.mark.parametrize("backend", ["solr", "opensearch", "qdrant"])
@pytest.mark.parametrize("event,requested,expected", [
    ("workflow_run", "", "sync"),
    ("workflow_run", "rebuild", "sync"),
    ("workflow_dispatch", "plan", "plan"),
    ("workflow_dispatch", "sync", "sync"),
    ("workflow_dispatch", "rebuild", "rebuild"),
])
def test_manual_actions_never_change_automatic_sync(backend, event, requested, expected):
    workflow = _workflow(backend)
    choice = workflow[True]["workflow_dispatch"]["inputs"]["index_lifecycle"]
    assert choice["type"] == "choice"
    assert choice["required"] is True
    assert choice["default"] == "sync"
    assert choice["options"] == ["plan", "sync", "rebuild"]
    job = next(iter(workflow["jobs"].values()))
    expression = (job["with"]["index_lifecycle"] if backend == "solr"
                  else job["env"]["FC_INDEX_LIFECYCLE"])
    assert expression == LIFECYCLE
    contexts = {
        "github": SimpleNamespace(event_name=event),
        "inputs": SimpleNamespace(index_lifecycle=requested),
    }
    assert _expression(expression, **contexts) == expected
    if backend == "solr":
        assert _expression(job["with"]["require_existing_snapshot"], **contexts) is (
            expected == "sync"
        )
        assert job["with"]["create_if_missing"] is False
        assert job["with"]["solr_provision_mode"] == "reuse_only"
    else:
        steps = job["steps"]
        preflight = next(step for step in steps if step["name"].startswith("Preflight"))
        assert "--create-if-missing" not in preflight["run"]
        assert "if" not in preflight
        assert "continue-on-error" not in preflight
        assert next(i for i, step in enumerate(steps) if step == preflight) < next(
            i for i, step in enumerate(steps) if step["name"] == "Plan index lifecycle"
        )


@pytest.mark.parametrize("backend", ["solr", "opensearch", "qdrant"])
def test_manual_operational_actions_require_master_workflow(backend):
    condition = next(iter(_workflow(backend)["jobs"].values()))["if"]
    for ref, allowed in (("refs/heads/master", True), ("refs/heads/untrusted", False)):
        github = SimpleNamespace(
            event_name="workflow_dispatch", ref=ref,
            event=SimpleNamespace(workflow_run=SimpleNamespace(conclusion="")),
        )
        assert _expression(condition, github=github) is allowed


def _status(state):
    current = state == "current"
    absent = state == "absent"
    return {
        "repository": "oalangomes/fetchcore", "branch": "master",
        "target_revision": REVISION, "indexed_revision": None if absent else REVISION,
        "mode": "noop" if current else "initial" if absent else "rebuild_required",
        "currentness": "current" if current else "absent" if absent else "incompatible",
        "reason": "up_to_date" if current else "snapshot_missing" if absent else "indexing_contract_changed",
        "incremental_possible": current, "schema_compatible": state != "schema",
        "projection_compatible": None if absent else True,
        "indexing_fingerprint": "sha256:index", "projection_fingerprint": "sha256:projection",
        "snapshot_indexing_fingerprint": None if absent else "sha256:index" if current else "old",
        "snapshot_projection_fingerprint": None if absent else "sha256:projection",
    }


def _run_steps(tmp_path, backend, lifecycle, state, *, after_override=None):
    """Run the actual YAML scripts after resource preflight with a fake CLI port."""
    bin_dir = tmp_path / "bin"
    evidence = tmp_path / "evidence"
    bin_dir.mkdir()
    evidence.mkdir()
    log = tmp_path / "commands.jsonl"
    configuration = {
        "before": _status(state), "after": {**_status("current"), **(after_override or {})},
        "state": state,
    }
    config_path = tmp_path / "envelopes.json"
    config_path.write_text(json.dumps(configuration))
    cli = bin_dir / "fetchcore"
    cli.write_text(f"#!{sys.executable}\n" + '''
import json
import os
from pathlib import Path
import sys

config = json.loads(Path(os.environ["FIXTURE_ENVELOPES"]).read_text())
args = sys.argv[1:]
operation = ("readiness" if args[0] == "readiness" else "register" if args[0] == "repo"
             else next(value for value in args if value in {"bootstrap", "status", "plan", "sync", "rebuild"}))
log = Path(os.environ["FIXTURE_LOG"])
with log.open("a") as output:
    output.write(json.dumps(operation) + "\\n")
executed = Path(os.environ["FIXTURE_EXECUTED"])
result = {}
if config["state"] == "schema" and operation in {"bootstrap", "sync", "rebuild"}:
    print(json.dumps({"status": "failed", "error": {"category": "schema_mismatch"}}))
    sys.exit(1)
if config["state"] == "collision" and operation == "status":
    print(json.dumps({"status": "failed", "error": {"category": "repository_resource_collision"}}))
    sys.exit(1)
if operation == "status":
    result = config["after"] if executed.exists() else config["before"]
elif operation == "bootstrap":
    result = {"validated": True}
elif operation == "plan":
    result = {**config["before"], "planned_mutations": {"replace_repository": True}}
elif operation in {"sync", "rebuild"}:
    if operation == "sync" and config["state"] != "current":
        sys.exit(1)
    executed.write_text(operation)
    result = {"execution": {"snapshot": {"revision": config["after"]["indexed_revision"]}}}
elif operation == "readiness":
    result = {"ready": True}
print(json.dumps({"status": "succeeded", "result": result}))
''')
    cli.chmod(0o755)
    python = bin_dir / "python"
    python.write_text(f"#!{sys.executable}\n" + '''
import os
from pathlib import Path
import sys
args = sys.argv[1:]
if args[:2] == ["scripts/shared_infra.py", "run"]:
    command = args[args.index("--") + 1:]
    assert command[0] == "fetchcore"
    os.execv(str(Path(__file__).with_name("fetchcore")), command)
os.execv(sys.executable, [sys.executable, *args])
''')
    python.chmod(0o755)
    env = {
        **os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "FIXTURE_ENVELOPES": str(config_path), "FIXTURE_LOG": str(log),
        "FIXTURE_EXECUTED": str(tmp_path / "executed"),
        "FC_BACKEND": backend, "FC_INDEX_LIFECYCLE": lifecycle,
        "FC_CACHE": str(tmp_path / "cache"), "FC_EVIDENCE": str(evidence),
        "FC_TARGET_SHA": REVISION, "FC_OLLAMA_URL": "http://fixture.invalid",
        "GITHUB_WORKSPACE": str(tmp_path), "FETCHCORE_DOGFOOD_OUTPUT": str(evidence),
        "FETCHCORE_REPOSITORY_CACHE": str(tmp_path / "cache"),
        "REPOSITORY": "oalangomes/fetchcore", "EXPECTED_REPOSITORY": "oalangomes/fetchcore",
        "EXPECTED_BRANCH": "master", "EXPECTED_REVISION": REVISION,
    }
    if backend == "solr":
        steps = yaml.safe_load((WORKFLOWS / "repository-index.yml").read_text())["jobs"]["index"]["steps"]
        start, end = "Bootstrap FetchCore Solr schema", "Inspect requested Solr retrieval readiness"
    else:
        steps = next(iter(_workflow(backend)["jobs"].values()))["steps"]
        start, end = "Register and inspect snapshot", "Verify persisted snapshot and readiness"
    names = [step["name"] for step in steps]
    result = None
    for step in steps[names.index(start):names.index(end) + 1]:
        if not _expression(step.get("if", True),
                           inputs=SimpleNamespace(index_lifecycle=lifecycle,
                                                  require_existing_snapshot=lifecycle == "sync"),
                           env=SimpleNamespace(FC_INDEX_LIFECYCLE=lifecycle)):
            continue
        result = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path,
                                env=env, capture_output=True, text=True, check=False)
        if result.returncode:
            break
    assert result is not None
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    return result, commands, evidence


@pytest.mark.parametrize("backend", ["solr", "opensearch", "qdrant"])
@pytest.mark.parametrize("state", ["absent", "incompatible", "current"])
@pytest.mark.parametrize("lifecycle", ["plan", "sync", "rebuild"])
def test_workflow_recovery_executes_only_selected_action(tmp_path, backend, state, lifecycle):
    result, commands, evidence = _run_steps(tmp_path, backend, lifecycle, state)
    if lifecycle == "sync" and state != "current":
        assert result.returncode != 0
        assert "sync" not in commands and "rebuild" not in commands
    else:
        assert result.returncode == 0, result.stderr
        assert "plan" in commands
        assert commands.count("rebuild") == (1 if lifecycle == "rebuild" else 0)
        assert commands.count("sync") == (1 if lifecycle == "sync" else 0)
        if lifecycle == "plan":
            assert not (evidence / "execution.json").exists()
            assert "readiness" not in commands
        else:
            assert commands.index("plan") < commands.index(lifecycle)
            assert commands.index(lifecycle) < len(commands) - 1
            assert commands[-1] == "readiness"


@pytest.mark.parametrize("backend", ["solr", "opensearch", "qdrant"])
@pytest.mark.parametrize("state", ["schema", "collision"])
def test_manual_rebuild_never_bypasses_schema_or_collision(tmp_path, backend, state):
    result, commands, _ = _run_steps(tmp_path, backend, "rebuild", state)
    assert result.returncode != 0
    assert not (tmp_path / "executed").exists()
    if state == "collision":
        assert "rebuild" not in commands


@pytest.mark.parametrize("backend", ["solr", "opensearch", "qdrant"])
@pytest.mark.parametrize("invalid", [
    {"indexed_revision": "wrong"}, {"currentness": "unknown"},
    {"snapshot_indexing_fingerprint": "wrong"},
    {"snapshot_projection_fingerprint": None}, {"repository": "other/repository"},
])
def test_rebuild_verification_checks_persisted_identity(tmp_path, backend, invalid):
    result, commands, _ = _run_steps(tmp_path, backend, "rebuild", "incompatible",
                                      after_override=invalid)
    assert "rebuild" in commands
    assert result.returncode != 0
