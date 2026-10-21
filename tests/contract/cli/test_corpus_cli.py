from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
