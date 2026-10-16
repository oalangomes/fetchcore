"""Guard the frozen V0.2 measurements from removal or accidental edits.

The run is RETRIEVAL_ONLY_OBSERVATION, not an accepted S0 indexing checkpoint.
Historical source commit: c4b49f79fe3b4835a7794b6cb9a3d417a773371a.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / "benchmarks" / "baseline-v1" / "artifacts"
V02_BLOBS = {
    "README.md": "4027a8ca7d282a5b302a800d9f1d0a2a1aeeefad",
    "context-yield.json": "19fcb8e51b3e526a5c0c51473b631de1617d21be",
    "context-yield.md": "419a5717b839f5622b0b16846f57241a1d520c4a",
    "local-lexical.json": "a6de0f11fb7385f3ee90504348dddc63925d77d8",
    "opensearch-dense.json": "369a99c76367429a906f3a821a1ddc027d0c3b24",
    "opensearch-hybrid.json": "dcc8c7662584f15031b6e0eb2aa05ecec0e211b9",
    "opensearch-lexical.json": "659d1a72de2e802675c514b7e9c53e0e92da76b2",
    "qdrant-dense.json": "198cc6f08a5680a5e4c0dd72d0ccc06f6f33203b",
    "qdrant-hybrid.json": "4b2ffb020a1c7616490c101eea95ef8190ba6927",
    "run.json": "03ec55157c748b3dfd26dd0670c8f87de54603e2",
    "solr-dense.json": "bc7d3a5298f8a8d2dddca0f9a588d3bcc6317472",
    "solr-hybrid.json": "d1510216892fad54e69c74fb412ade6891e639a7",
    "solr-lexical.json": "7e7506dbe0f626a78822211bfa233ecf6500ecb6",
}


def test_frozen_v02_artifacts_are_present_and_byte_identical() -> None:
    directory = ARTIFACTS / "V0.2"
    assert (ARTIFACTS / "V0.1" / "run.json").is_file()
    assert sorted(path.name for path in directory.iterdir() if path.is_file()) == sorted(V02_BLOBS)
    for name, expected in V02_BLOBS.items():
        contents = (directory / name).read_bytes()
        header = f"blob {len(contents)}".encode() + bytes([0])
        actual = hashlib.sha1(header + contents).hexdigest()
        assert actual == expected, f"Historical artifact drifted: {name}"
    run = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert run["status"] == "complete"
    assert run["run_version"] == "V0.2"
    assert run["indexing_artifact"] is None
    assert len(run["artifacts"]) == 9


def test_frozen_v01_v02_offline_comparison_from_clean_checkout(tmp_path: Path) -> None:
    output = tmp_path / "baseline-v1-v02-comparison.json"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = (
        str(ROOT / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "benchmarks" / "baseline-v1" / "compare_runs.py"),
            "--baseline",
            str(ARTIFACTS / "V0.1"),
            "--candidate",
            str(ARTIFACTS / "V0.2"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    comparison = json.loads(output.read_text(encoding="utf-8"))
    assert len(comparison["arms"]) == 9
