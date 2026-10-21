from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from fetchcore.application.readiness import ReadinessFact, ReadinessReport
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
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(reconcile.subprocess, "run", run)

    reconcile.write_binding(config, output, HEAD)
    reconcile.activate_serving(compose, output)

    generated = yaml.safe_load(output.read_text(encoding="utf-8"))
    original = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert generated["runtime"]["revision"] == HEAD
    assert generated["retrieval"] == original["retrieval"]
    assert generated["backends"] == original["backends"]
    assert original["runtime"]["revision"] == "a" * 40
    command = calls[0][0][0]
    assert command[-5:] == ["-d", "--no-deps", "--force-recreate", "mcp", "api"]
    assert "solr" not in command
    assert calls[0][1]["env"]["FETCHCORE_OPERATIONAL_RUNTIME_CONFIG"] == str(output)


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
