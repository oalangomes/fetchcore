from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from fetchcore.application.readiness import ReadinessFact, ReadinessReport
from fetchcore.application.runtime_inspection import (
    DoctorReport, RuntimeCheck, RuntimeInspectionService,
)
from fetchcore.operational import reconcile


HEAD = "b" * 40


def test_compose_rebinds_only_serving_file_mounts() -> None:
    compose = yaml.safe_load(Path("infra/compose/operational-mcp.yml").read_text())
    mount = (
        "${FETCHCORE_OPERATIONAL_RUNTIME_CONFIG:-./operational-mcp.runtime.yml}"
        ":/etc/fetchcore/operational-mcp.yml:ro"
    )
    assert mount in compose["services"]["mcp"]["volumes"]
    assert mount in compose["services"]["api"]["volumes"]
    assert "solr" not in compose["services"]


def test_materialized_head_requires_git_root(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Test",
                    "-c", "user.email=test@example.test", "commit", "-qm", "initial",
                    "--allow-empty"], check=True)
    head = reconcile.materialized_head(tmp_path)
    assert len(head) == 40
    child = tmp_path / "child"
    child.mkdir()
    with pytest.raises(ValueError, match="workspace must be the Git root"):
        reconcile.materialized_head(child)


def _config(path: Path) -> None:
    path.write_text(yaml.safe_dump({
        "version": 1,
        "runtime": {
            "backend": "solr", "workspace": "/workspace",
            "repository": "demo/repo", "revision": "a" * 40,
        },
        "retrieval": {"mode": "hybrid", "dense": {"enabled": True},
                      "hybrid": {"enabled": True}},
        "backends": {"solr": {"url": "http://solr:8983/solr", "collection": "owned"}},
    }), encoding="utf-8")


def _report(*, revision: str = HEAD, failed: str | None = None) -> ReadinessReport:
    checks = {name: ReadinessFact("ready") for name in (
        "service", "schema", "snapshot", "currentness", "indexing_fingerprint",
        "embedding", "retrieval",
    )}
    if failed:
        checks[failed] = ReadinessFact("incompatible", {
            "schema": "schema_mismatch",
            "snapshot": "repository_binding_mismatch",
            "indexing_fingerprint": "indexing_contract_changed",
            "embedding": "embedding_identity_changed",
            "currentness": "changes_pending",
        }[failed])
    return ReadinessReport(
        "solr", "demo/repo", HEAD, revision, "hybrid", checks,
        {"indexing": "fp", "expected_indexing": "fp"},
    )


def test_candidate_uses_materialized_head_and_host_endpoints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    config = tmp_path / "runtime.yml"
    _config(config)
    monkeypatch.setattr(reconcile, "materialized_head", lambda workspace: HEAD)
    seen = []
    def inspect(inspector: object) -> ReadinessReport:
        seen.append(inspector.config)  # type: ignore[attr-defined]
        return _report()
    monkeypatch.setattr(reconcile, "inspect_readiness", inspect)

    revision, report = reconcile.validate_candidate(
        config, workspace=tmp_path, solr_url="http://127.0.0.1:38983/solr",
        ollama_url="http://127.0.0.1:11434",
    )

    assert revision == report.indexed_revision == HEAD
    assert seen[0].revision == HEAD
    assert seen[0].workspace == tmp_path
    assert seen[0].backend_settings["url"] == "http://127.0.0.1:38983/solr"
    assert seen[0].ollama.url == "http://127.0.0.1:11434"


@pytest.mark.parametrize("report", [
    _report(revision="a" * 40),
    _report(failed="indexing_fingerprint"),
    _report(failed="schema"),
    _report(failed="snapshot"),
    _report(failed="embedding"),
    _report(failed="currentness"),
])
def test_candidate_rejects_incompatible_snapshot_without_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, report: ReadinessReport,
) -> None:
    config = tmp_path / "runtime.yml"
    _config(config)
    monkeypatch.setattr(reconcile, "materialized_head", lambda workspace: HEAD)
    monkeypatch.setattr(reconcile, "inspect_readiness", lambda inspector: report)

    with pytest.raises(ValueError, match="serving_reconciliation_rejected") as rejected:
        reconcile.validate_candidate(
            config, workspace=tmp_path, solr_url="http://127.0.0.1:38983/solr",
        )
    expected = ("snapshot_revision_mismatch" if report.indexed_revision != HEAD else
                next(fact.category for fact in report.checks.values()
                     if fact.state != "ready"))
    assert expected in str(rejected.value)
    assert yaml.safe_load(config.read_text())["runtime"]["revision"] == "a" * 40


@pytest.mark.parametrize("name, category", [
    ("service", "connection_failure"),
    ("schema", "schema_mismatch"),
])
def test_candidate_preserves_probe_failure_before_snapshot_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, category: str,
) -> None:
    config = tmp_path / "runtime.yml"
    _config(config)
    monkeypatch.setattr(reconcile, "materialized_head", lambda workspace: HEAD)
    # Use real readiness: failed probes leave indexed_revision unknown.
    monkeypatch.setattr(RuntimeInspectionService, "doctor", lambda self: DoctorReport((
        RuntimeCheck("backend_connectivity", "failed" if name == "service" else "ok",
                     category if name == "service" else None),
        RuntimeCheck("schema_compatibility", "failed" if name == "schema" else "ok",
                     category if name == "schema" else None),
        RuntimeCheck("embedding_provider", "ok"),
        RuntimeCheck("retrieval_compatibility", "ok"),
    )))

    with pytest.raises(ValueError, match=f"serving_reconciliation_rejected/{category}:"):
        reconcile.validate_candidate(
            config, workspace=tmp_path, solr_url="http://127.0.0.1:38983/solr",
        )


@pytest.mark.parametrize("check, category", [
    ("snapshot", "snapshot_missing"),
    ("snapshot", "repository_binding_mismatch"),
    ("indexing_fingerprint", "indexing_contract_changed"),
    ("embedding", "embedding_identity_changed"),
])
def test_unknown_or_blocked_currentness_does_not_mask_observed_failure(
    check: str, category: str,
) -> None:
    report = _report()
    report.checks["currentness"] = ReadinessFact("unknown", "not_checked")
    report.checks[check] = ReadinessFact("incompatible", category)
    report = replace(report, indexed_revision=None)

    assert reconcile._rejection_category(report, HEAD, "demo/repo") == category


def test_unknown_indexed_revision_is_not_a_revision_mismatch() -> None:
    report = replace(_report(), indexed_revision=None)
    report.checks["currentness"] = ReadinessFact("unknown", "not_checked")
    assert reconcile._rejection_category(report, HEAD, "demo/repo") == "currentness_unproven"


def _compose_model(workspace: Path, output: Path) -> dict:
    return {"services": {name: {
        "environment": {"FETCHCORE_CONFIG": "/etc/fetchcore/operational-mcp.yml"},
        "volumes": [
            {"type": "bind", "source": str(workspace), "target": "/workspace",
             "read_only": True},
            {"type": "bind", "source": str(output),
             "target": "/etc/fetchcore/operational-mcp.yml", "read_only": True},
        ],
    } for name in ("mcp", "api")}}


def test_activation_updates_only_owned_config_and_serving_containers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    config = tmp_path / "runtime.yml"
    output = tmp_path / "operator-runtime.yml"
    compose = tmp_path / "operational-mcp.yml"
    _config(config)
    compose.write_text("services: {}\n", encoding="utf-8")
    calls = []
    def run(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(_compose_model(tmp_path, output)))
    monkeypatch.setattr(reconcile.subprocess, "run", run)

    reconcile.activate_serving(
        compose, output, config_path=config, workspace=tmp_path, revision=HEAD,
    )

    generated = yaml.safe_load(output.read_text(encoding="utf-8"))
    original = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert generated["runtime"]["revision"] == HEAD
    assert generated["retrieval"] == original["retrieval"]
    assert generated["backends"] == original["backends"]
    assert original["runtime"]["revision"] == "a" * 40
    assert calls[0][0][0][-3:] == ["config", "--format", "json"]
    command = calls[1][0][0]
    assert command[-5:] == ["-d", "--no-deps", "--force-recreate", "mcp", "api"]
    assert "solr" not in command
    assert calls[0][1]["env"]["FETCHCORE_OPERATIONAL_RUNTIME_CONFIG"] == str(output)
    assert calls[0][1]["env"] == calls[1][1]["env"]


@pytest.mark.parametrize("service", ["mcp", "api"])
@pytest.mark.parametrize("fault", [
    "workspace", "runtime_file", "writable", "missing", "named_volume", "overlap",
    "environment", "missing_service", "tmpfs", "volumes_from",
    "configs_workspace", "configs_runtime", "secrets_workspace", "secrets_runtime",
])
def test_activation_rejects_mismatched_deployment_before_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str], service: str, fault: str,
) -> None:
    workspace = tmp_path / "validated"
    workspace.mkdir()
    mounted = tmp_path / "other-checkout"
    mounted.mkdir()
    (mounted / "a.py").write_text("dirty content\n", encoding="utf-8")
    config = tmp_path / "runtime.yml"
    _config(config)
    original = config.read_bytes()
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    output = tmp_path / "operator-runtime.yml"
    output.write_bytes(b"previous serving binding\n")
    model = _compose_model(workspace, output)
    target = model["services"][service]
    if fault == "workspace":
        target["volumes"][0]["source"] = str(mounted)
    elif fault == "runtime_file":
        target["volumes"][1]["source"] = str(config)
    elif fault == "writable":
        target["volumes"][0]["read_only"] = False
    elif fault == "missing":
        target["volumes"].pop(0)
    elif fault == "named_volume":
        target["volumes"][0]["type"] = "volume"
    elif fault == "overlap":
        target["volumes"].append({
            "type": "bind", "source": str(mounted), "target": "/workspace/src",
        })
    elif fault == "environment":
        target["environment"]["FETCHCORE_WORKSPACE"] = "/other-workspace"
    elif fault == "missing_service":
        del model["services"][service]
    elif fault == "tmpfs":
        target["tmpfs"] = ["/workspace/src:size=1m"]
    elif fault == "volumes_from":
        target["volumes_from"] = ["other"]
    elif fault.startswith(("configs_", "secrets_")):
        kind, destination = fault.split("_", 1)
        target[kind] = [{
            "source": "override",
            "target": ("/workspace/a.py" if destination == "workspace"
                       else "/etc/fetchcore/operational-mcp.yml"),
        }]
    commands = []
    def run(command: list[str], **kwargs: object) -> object:
        commands.append(command)
        assert command[-3:] == ["config", "--format", "json"]
        return SimpleNamespace(stdout=json.dumps(model))
    monkeypatch.setattr(reconcile.subprocess, "run", run)
    monkeypatch.setattr(reconcile, "validate_candidate", lambda *a, **kw: (HEAD, _report()))

    result = reconcile.main([
        "--config", str(config), "--workspace", str(workspace),
        "--solr-url", "http://127.0.0.1:38983/solr", "--output", str(output),
        "--compose-file", str(compose), "--activate",
    ])

    assert result == 1
    assert "serving_binding_mismatch" in json.loads(capsys.readouterr().err)["reason"]
    assert len(commands) == 1
    assert output.read_bytes() == b"previous serving binding\n"
    assert config.read_bytes() == original


@pytest.mark.parametrize("kind", ["configs", "secrets"])
@pytest.mark.parametrize("target", [
    "/", "/workspace", "/etc/fetchcore",
    "/etc/fetchcore/operational-mcp.yml/child",
    "/elsewhere/../workspace/a.py", "//workspace/a.py",
])
def test_file_mounts_reject_overlapping_and_normalized_targets(
    tmp_path: Path, kind: str, target: str,
) -> None:
    model = _compose_model(tmp_path, tmp_path / "runtime.yml")
    model["services"]["mcp"][kind] = [{"source": "override", "target": target}]

    with pytest.raises(ValueError, match="serving_binding_mismatch"):
        reconcile._validate_serving_mounts(
            model, workspace=tmp_path, container_workspace=Path("/workspace"),
            output=tmp_path / "runtime.yml",
        )


@pytest.mark.parametrize("kind", ["configs", "secrets"])
@pytest.mark.parametrize("explicit_empty_target", [False, True])
def test_file_mount_default_destination_cannot_shadow_workspace(
    tmp_path: Path, kind: str, explicit_empty_target: bool,
) -> None:
    container_workspace = Path("/workspace" if kind == "configs" else "/run/secrets/workspace")
    model = _compose_model(tmp_path, tmp_path / "runtime.yml")
    model["services"]["mcp"]["volumes"][0]["target"] = str(container_workspace)
    mount = {"source": "workspace"}
    if explicit_empty_target:
        mount["target"] = ""
    model["services"]["mcp"][kind] = [mount]

    with pytest.raises(ValueError, match="serving_binding_mismatch"):
        reconcile._validate_serving_mounts(
            model, workspace=tmp_path, container_workspace=container_workspace,
            output=tmp_path / "runtime.yml",
        )


@pytest.mark.parametrize("kind, target", [
    ("configs", "workspace/a.py"),
    ("secrets", "../../workspace/a.py"),
])
def test_file_mount_relative_targets_cannot_bypass_validation(
    tmp_path: Path, kind: str, target: str,
) -> None:
    model = _compose_model(tmp_path, tmp_path / "runtime.yml")
    model["services"]["mcp"][kind] = [{"source": "override", "target": target}]

    with pytest.raises(ValueError, match="serving_binding_mismatch"):
        reconcile._validate_serving_mounts(
            model, workspace=tmp_path, container_workspace=Path("/workspace"),
            output=tmp_path / "runtime.yml",
        )


@pytest.mark.parametrize("kind", ["configs", "secrets"])
@pytest.mark.parametrize("target", [None, "", "/etc/unrelated/settings", "/workspace-other/a.py"])
def test_file_mounts_allow_unrelated_destinations(
    tmp_path: Path, kind: str, target: str | None,
) -> None:
    model = _compose_model(tmp_path, tmp_path / "runtime.yml")
    mount = {"source": "settings"}
    if target is not None:
        mount["target"] = target
    model["services"]["mcp"][kind] = [mount]

    reconcile._validate_serving_mounts(
        model, workspace=tmp_path, container_workspace=Path("/workspace"),
        output=tmp_path / "runtime.yml",
    )


def test_relative_secret_destination_is_under_run_secrets(tmp_path: Path) -> None:
    model = _compose_model(tmp_path, tmp_path / "runtime.yml")
    model["services"]["mcp"]["secrets"] = [{"source": "settings", "target": "workspace"}]

    reconcile._validate_serving_mounts(
        model, workspace=tmp_path, container_workspace=Path("/workspace"),
        output=tmp_path / "runtime.yml",
    )


@pytest.mark.parametrize("kind", ["configs", "secrets"])
@pytest.mark.parametrize("mounts", [
    None, {}, ["override"], [{}], [{"source": 1}],
    [{"source": "override", "target": None}],
    [{"source": "override", "target": 1}],
    [{"source": "override", "target": "/invalid\0path"}],
])
def test_file_mounts_reject_unproven_declarations(
    tmp_path: Path, kind: str, mounts: object,
) -> None:
    model = _compose_model(tmp_path, tmp_path / "runtime.yml")
    model["services"]["mcp"][kind] = mounts

    with pytest.raises(ValueError, match="serving_binding_mismatch"):
        reconcile._validate_serving_mounts(
            model, workspace=tmp_path, container_workspace=Path("/workspace"),
            output=tmp_path / "runtime.yml",
        )


def test_main_is_read_only_without_activate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "runtime.yml"
    _config(config)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "operator-runtime.yml"
    monkeypatch.setattr(reconcile, "validate_candidate", lambda *args, **kwargs: (HEAD, _report()))

    result = reconcile.main([
        "--config", str(config), "--workspace", str(workspace),
        "--solr-url", "http://127.0.0.1:38983/solr", "--output", str(output),
    ])

    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "compatible"
    assert not output.exists()
