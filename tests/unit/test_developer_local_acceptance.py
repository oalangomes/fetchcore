"""Safety checks for the opt-in local acceptance harness."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "developer_local_acceptance.py"


def _invoke(tmp_path: Path, *extra: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    work = tmp_path / "work"
    output = tmp_path / "report.json"
    cli = tmp_path / "fetchcore"
    mcp = tmp_path / "fetchcore-mcp"
    cli.write_text("", encoding="utf-8")
    mcp.write_text("", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--cli", str(cli), "--mcp", str(mcp),
         "--work-dir", str(work), "--output", str(output),
         "--solr-url", "http://127.0.0.1:18984/solr",
         "--collection", "fetchcore-p01-codex-unit", *extra],
        capture_output=True, text=True, check=False,
    )
    return result, output


def test_harness_requires_explicit_owned_resource_acknowledgement(tmp_path: Path) -> None:
    result, output = _invoke(tmp_path)

    assert result.returncode == 1
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "blocked"
    assert not (tmp_path / "work").exists()


def test_harness_rejects_shared_collection_before_writing(tmp_path: Path) -> None:
    result, output = _invoke(tmp_path, "--ack-owned-resource", "--collection", "fetchcore")

    assert result.returncode == 1
    assert "isolated" in json.loads(output.read_text(encoding="utf-8"))["reason"]
    assert not (tmp_path / "work").exists()
