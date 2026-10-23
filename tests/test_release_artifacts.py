"""Publication gates must reject partial validation and substituted artifacts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def release_module():
    spec = importlib.util.spec_from_file_location(
        "release_evidence", ROOT / "scripts/ci/release-evidence.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidates(tmp_path):
    module = release_module()
    dist = tmp_path / "dist"
    reports = tmp_path / "reports"
    dist.mkdir()
    reports.mkdir()
    artifacts = []
    for kind, filename in (("wheel", "candidate.whl"), ("sdist", "candidate.tar.gz")):
        (dist / filename).write_bytes(kind.encode())
        artifacts.append({"kind": kind, "filename": filename,
                          "sha256": hashlib.sha256(kind.encode()).hexdigest()})
    for runtime in sorted(module.SUPPORTED_PYTHONS):
        smoke = {"version": "0.0.1", "python": runtime + ".0",
                 "checks": dict.fromkeys(module.CHECKS, True),
                 "dependencies": [{"name": "fetchcore", "version": "0.0.1"}]}
        report = {"schema_version": 1, "kind": "fetchcore.release-artifacts",
                  "source_sha": "a" * 40, "version": "0.0.1",
                  "artifacts": [{**a, "smoke": smoke} for a in artifacts]}
        (reports / f"python-{runtime}.json").write_text(json.dumps(report))
    return module, dist, reports


def test_exact_candidates_are_traceable_and_deterministic(tmp_path):
    module, dist, reports = candidates(tmp_path)
    result = module.verify(dist, reports, "0.0.1", "a" * 40)
    assert result == module.verify(dist, reports, "0.0.1", "a" * 40)
    assert result["tag"] == "v0.0.1"
    assert len(result["artifacts"]) == 2
    assert len(result["python_validation"]) == len(module.SUPPORTED_PYTHONS)


@pytest.mark.parametrize("change", ["source", "version", "entrypoint", "checksum", "matrix"])
def test_publication_rejects_incomplete_or_mismatched_evidence(tmp_path, change):
    module, dist, reports = candidates(tmp_path)
    path = reports / "python-3.11.json"
    report = json.loads(path.read_text())
    if change == "source":
        report["source_sha"] = "b" * 40
    elif change == "version":
        report["artifacts"][0]["smoke"]["version"] = "0.0.2"
    elif change == "entrypoint":
        report["artifacts"][0]["smoke"]["checks"]["mcp_initialize"] = False
    elif change == "checksum":
        (dist / "candidate.whl").write_bytes(b"substituted after CI")
    elif change == "matrix":
        path.unlink()
    if change != "matrix":
        path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        module.verify(dist, reports, "0.0.1", "a" * 40)


def test_ci_support_matrix_agrees_with_metadata_and_release_gate():
    import tomllib
    import yaml
    from packaging.specifiers import SpecifierSet

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    runtimes = set(ci["jobs"]["test"]["strategy"]["matrix"]["python"])
    assert runtimes == set(ci["jobs"]["artifact-smoke"]["strategy"]["matrix"]["python"])
    assert runtimes == release_module().SUPPORTED_PYTHONS
    requires = SpecifierSet(metadata["requires-python"])
    assert all((runtime + ".0") in requires for runtime in runtimes)
    assert "3.10.0" not in requires
    assert "3.16.0" not in requires


def test_container_evidence_hashes_exact_export_and_rejects_root(tmp_path, monkeypatch):
    import sys

    module, dist, reports = candidates(tmp_path)
    smoke = json.loads((reports / "python-3.11.json").read_text())["artifacts"][0]["smoke"]
    smoke["non_root"] = True
    container_smoke = reports / "container.json"
    container_smoke.write_text(json.dumps(smoke))
    archive = dist / "container.tar"
    archive.write_bytes(b"exact exported image")
    output = dist / "release-evidence.json"
    monkeypatch.setattr(sys, "argv", [
        "release-evidence.py", "--dist", str(dist), "--reports", str(reports),
        "--version", "0.0.1", "--source-sha", "a" * 40,
        "--container-smoke", str(container_smoke), "--image", "fetchcore:v0.0.1",
        "--image-id", "sha256:" + "b" * 64, "--container-archive", str(archive),
        "--output", str(output),
    ])
    module.main()
    evidence = json.loads(output.read_text())
    exported = next(a for a in evidence["artifacts"] if a["filename"] == archive.name)
    assert exported["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert "registry_digest" not in evidence["container"]
    assert "container.tar" in (dist / "SHA256SUMS").read_text()
    smoke["non_root"] = False
    container_smoke.write_text(json.dumps(smoke))
    with pytest.raises(ValueError, match="Container smoke incomplete"):
        module.main()
