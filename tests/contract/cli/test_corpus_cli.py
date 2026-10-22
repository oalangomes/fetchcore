from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket

import pytest

from click.testing import CliRunner

from fetchcore.cli import main


def _invoke(workspace: Path, *args: str) -> dict:
    result = CliRunner().invoke(
        main, ["index", "--workspace", str(workspace), "corpus", *args, "--json"]
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["result"]


def test_local_corpus_uses_canonical_scan_without_registration_or_backend(tmp_path: Path) -> None:
    (tmp_path / "runnerctl.sh").write_text("#!/bin/sh\necho ready\n", encoding="utf-8")
    (tmp_path / "site").mkdir()
    (tmp_path / "site/styles.css").write_text("body {}\n", encoding="utf-8")
    before = {
        str(path.relative_to(tmp_path)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.rglob("*") if path.is_file()
    }

    report = _invoke(tmp_path)
    assert report["mode"] == "scan"
    assert report["scan"]["files_read"] == 1
    assert report["coverage"]["skipped_by_reason_and_source_kind"]["not_included"][".css"] == 1
    assert _invoke(tmp_path, "--path", "runnerctl.sh")["path_status"] == "read"
    assert _invoke(tmp_path, "--path", "site/styles.css")["path_status"] == "not_included"
    after = {
        str(path.relative_to(tmp_path)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.rglob("*") if path.is_file()
    }
    assert after == before


def test_external_proposal_is_validated_and_compared_without_applying(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "runnerctl.sh").write_text("echo ready\n", encoding="utf-8")
    (workspace / "site").mkdir()
    (workspace / "site/styles.css").write_text("body {}\n", encoding="utf-8")
    proposal = tmp_path / "proposal.yaml"
    proposal.write_text("version: 1\nselection:\n  include: ['*.css']\n", encoding="utf-8")

    report = _invoke(workspace, "--proposal", str(proposal))
    assert report["impact"]["added_read_paths"] == ["site/styles.css"]
    assert report["impact"]["removed_read_paths"] == []
    assert report["impact"]["applied"] is False
    assert report["impact"]["before_fingerprint"] != report["impact"]["after_fingerprint"]
    assert _invoke(workspace, "--path", "site/styles.css")["path_status"] == "not_included"
    assert _invoke(workspace, "--path", "site/styles.css", "--proposal", str(proposal))[
        "impact"
    ] == {"before": "not_included", "after": "read", "applied": False}
    assert not (workspace / ".fetchcore").exists()


def test_deep_uses_canonical_coverage_and_duplication_diagnostics(tmp_path: Path) -> None:
    for name in ("first.py", "second.py"):
        (tmp_path / name).write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    report = _invoke(tmp_path, "--deep")
    assert report["mode"] == "deep"
    coverage = report["telemetry"]["source_coverage"]
    assert coverage["files_read"] == 3
    assert coverage["files_without_documents"] == 1
    assert coverage["files_without_documents_by_source_kind"] == {".py": 1}
    assert report["corpus_diagnostics"]["duplicate_group_count"] == 1
    assert report["corpus_diagnostics"]["duplicate_groups"][0]["paths"] == [
        "first.py", "second.py",
    ]
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "__init__.py", "first.py", "second.py",
    ]


def test_invalid_proposal_fails_closed_without_showing_contents(tmp_path: Path) -> None:
    proposal = tmp_path / "proposal.yaml"
    proposal.write_text("version: 999\nsecret: DO_NOT_PRINT\n", encoding="utf-8")
    result = CliRunner().invoke(
        main,
        ["index", "--workspace", str(tmp_path), "corpus", "--proposal", str(proposal), "--json"],
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["status"] == "failed"
    assert "DO_NOT_PRINT" not in result.output


@pytest.mark.parametrize("payload", ["false\n", "0\n", "[]\n", "[x]\n", "[\n"])
@pytest.mark.parametrize("policy_name", ["indexing.yaml", "document-indexing.yaml", "proposal"])
def test_invalid_policy_never_falls_back_to_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: str, policy_name: str,
) -> None:
    from fetchcore.application import index_operations

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    args = []
    if policy_name == "proposal":
        policy = tmp_path / "proposal.yaml"
        args = ["--proposal", str(policy)]
    else:
        (workspace / ".fetchcore").mkdir()
        policy = workspace / ".fetchcore" / policy_name
    policy.write_text(payload, encoding="utf-8")

    def forbidden_scan(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid policy reached corpus scan")

    monkeypatch.setattr(index_operations, "_scan_local_corpus", forbidden_scan)
    result = CliRunner().invoke(
        main, ["index", "--workspace", str(workspace), "corpus", *args, "--json"],
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["status"] == "failed"
    assert policy.read_text(encoding="utf-8") == payload


@pytest.mark.parametrize("payload", ["", "null\n", "{}\n"])
@pytest.mark.parametrize("policy_name", ["indexing.yaml", "document-indexing.yaml"])
def test_empty_policy_compatibility(tmp_path: Path, payload: str, policy_name: str) -> None:
    (tmp_path / ".fetchcore").mkdir()
    (tmp_path / ".fetchcore" / policy_name).write_text(payload, encoding="utf-8")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert _invoke(tmp_path)["scan"]["files_read"] == 1


def test_proposal_preserves_broad_exclusion_for_current_and_future_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".fetchcore").mkdir()
    protected = workspace / "benchmarks" / "artifacts"
    protected.mkdir(parents=True)
    (protected / "report.md").write_text("# Technical report\n", encoding="utf-8")
    policy = "version: 1\nselection:\n  exclude: ['benchmarks/artifacts/**']\n"
    (workspace / ".fetchcore/indexing.yaml").write_text(policy, encoding="utf-8")
    proposal = tmp_path / "proposal.yaml"
    proposal.write_text(
        policy + "  include: ['benchmarks/artifacts/report.md']\n", encoding="utf-8",
    )

    impact = _invoke(workspace, "--proposal", str(proposal))["impact"]
    assert impact["added_read_paths"] == impact["removed_read_paths"] == []
    assert _invoke(workspace, "--path", "benchmarks/artifacts/report.md", "--proposal",
                   str(proposal))["impact"] == {
        "before": "excluded", "after": "excluded", "applied": False,
    }
    # A narrower rule looks equivalent today, but silently admits future artifacts.
    narrowed = tmp_path / "narrowed.yaml"
    narrowed.write_text(
        "version: 1\nselection:\n  exclude: ['benchmarks/artifacts/*.json']\n",
        encoding="utf-8",
    )
    (protected / "report.md").unlink()
    (protected / "current.json").write_text('{}\n', encoding="utf-8")
    assert _invoke(workspace, "--proposal", str(narrowed))["impact"]["added_read_paths"] == []
    (protected / "future.md").write_text("# Future artifact\n", encoding="utf-8")
    assert _invoke(workspace, "--proposal", str(narrowed))["impact"]["added_read_paths"] == [
        "benchmarks/artifacts/future.md",
    ]
    assert _invoke(workspace, "--proposal", str(proposal))["impact"]["added_read_paths"] == []


def test_basename_include_is_not_root_only_even_after_excluding_known_copies(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    (workspace / "app").mkdir(parents=True)
    (workspace / ".env.example").write_text("PORT=80\n", encoding="utf-8")
    (workspace / "app/.env.example").write_text("PORT=81\n", encoding="utf-8")
    proposal = tmp_path / "proposal.yaml"
    proposal.write_text("version: 1\nselection:\n  include: ['.env.example']\n", encoding="utf-8")
    assert _invoke(workspace, "--proposal", str(proposal))["impact"]["added_read_paths"] == [
        ".env.example", "app/.env.example",
    ]
    proposal.write_text(
        "version: 1\nselection:\n  include: ['.env.example']\n"
        "  exclude: ['app/.env.example']\n", encoding="utf-8",
    )
    assert _invoke(workspace, "--proposal", str(proposal))["impact"]["added_read_paths"] == [
        ".env.example",
    ]
    (workspace / "new-app").mkdir()
    (workspace / "new-app/.env.example").write_text("PORT=82\n", encoding="utf-8")
    assert _invoke(workspace, "--proposal", str(proposal))["impact"]["added_read_paths"] == [
        ".env.example", "new-app/.env.example",
    ]
    # The existing contract CAN constrain root-only admission with a broad
    # exclusion of every nested occurrence, including copies not yet present.
    proposal.write_text(
        "version: 1\nselection:\n  include: ['.env.example']\n"
        "  exclude: ['**/.env.example']\n", encoding="utf-8",
    )
    assert _invoke(workspace, "--proposal", str(proposal))["impact"]["added_read_paths"] == [
        ".env.example",
    ]
    assert _invoke(workspace, "--path", "future/component/.env.example", "--proposal",
                   str(proposal))["impact"]["after"] == "excluded"


def test_all_corpus_modes_remain_path_safe_quarantined_and_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "repo"
    (workspace / ".fetchcore").mkdir(parents=True)
    (workspace / ".fetchcore/snapshot.json").write_text('{"sentinel": true}\n', encoding="utf-8")
    (workspace / ".fetchcore/indexing.yaml").write_text("version: 1\n", encoding="utf-8")
    (workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    (workspace / "example.css").write_text("body {}\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("DO_NOT_READ\n", encoding="utf-8")
    (workspace / "link.py").symlink_to(outside)
    (workspace / "private.postman_environment.json").write_text(
        '{"values": [{"value": "DO_NOT_PRINT"}]}\n', encoding="utf-8",
    )
    proposal = tmp_path / "proposal.yaml"
    proposal.write_text("version: 1\nselection:\n  include: ['*.css']\n", encoding="utf-8")
    before = {str(p.relative_to(workspace)): p.read_bytes()
              for p in workspace.rglob("*") if p.is_file() and not p.is_symlink()}

    def forbidden_network(*args: object, **kwargs: object) -> None:
        pytest.fail("corpus inspection attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    scan = _invoke(workspace)
    assert scan["scan"]["files_read"] == 1
    assert scan["scan"]["skipped"]["unsafe_path_or_symlink"] == 1
    assert scan["scan"]["skipped"]["api_artifact_blocked_postman_environment"] == 1
    deep = _invoke(workspace, "--deep")
    assert deep["telemetry"]["source_coverage"]["files_read"] == 1
    assert _invoke(workspace, "--proposal", str(proposal))["impact"]["added_read_paths"] == [
        "example.css",
    ]
    for path in ("../outside.py", str(outside)):
        assert _invoke(workspace, "--path", path)["path_status"] == "invalid_path"
    assert _invoke(workspace, "--path", "link.py")["path_status"] == "unsafe_path_or_symlink"
    assert "DO_NOT_PRINT" not in json.dumps([scan, deep])
    after = {str(p.relative_to(workspace)): p.read_bytes()
             for p in workspace.rglob("*") if p.is_file() and not p.is_symlink()}
    assert after == before
    assert (workspace / "link.py").is_symlink()
