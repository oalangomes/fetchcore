"""Verify CI artifact identities before publication and compose release provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

SUPPORTED_PYTHONS = {"3.11", "3.12", "3.13", "3.14", "3.15"}
CHECKS = {"import", "cli", "runtime_inspection", "mcp_initialize", "mcp_tools", "configset"}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(dist: Path, reports: Path, version: str, source_sha: str) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("Source SHA must be immutable")
    artifacts = sorted([*dist.glob("*.whl"), *dist.glob("*.tar.gz")])
    if len(artifacts) != 2 or len(list(dist.glob("*.whl"))) != 1:
        raise ValueError("Expected exactly one wheel and one sdist")
    expected = {path.name: sha256(path) for path in artifacts}
    observed = set()
    evidence = []
    for path in sorted(reports.glob("python-*.json")):
        report = json.loads(path.read_text())
        if (report.get("schema_version") != 1
                or report.get("kind") != "fetchcore.release-artifacts"
                or report.get("source_sha") != source_sha or report.get("version") != version):
            raise ValueError("CI evidence source/version mismatch")
        candidates = report["artifacts"]
        if (len(candidates) != 2
                or {a["filename"]: a["sha256"] for a in candidates} != expected
                or {a["kind"] for a in candidates} != {"wheel", "sdist"}):
            raise ValueError("Candidate checksums differ from validated CI artifacts")
        runtimes = set()
        for candidate in candidates:
            smoke = candidate["smoke"]
            if (smoke["version"] != version or set(smoke["checks"]) != CHECKS
                    or any(value is not True for value in smoke["checks"].values())
                    or not smoke.get("dependencies")):
                raise ValueError("Incomplete installed-artifact smoke")
            runtimes.add(".".join(smoke["python"].split(".")[:2]))
        if len(runtimes) != 1 or observed & runtimes:
            raise ValueError("Inconsistent or duplicate Python smoke evidence")
        observed.update(runtimes)
        evidence.append(report)
    if observed != SUPPORTED_PYTHONS:
        raise ValueError("Incomplete supported Python artifact matrix")
    return {"schema_version": 1, "kind": "fetchcore.release", "source_sha": source_sha,
            "version": version, "tag": "v" + version,
            "artifacts": [{"filename": name, "sha256": value}
                          for name, value in sorted(expected.items())],
            "python_validation": evidence}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--container-smoke", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--image-id")
    parser.add_argument("--container-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = verify(args.dist, args.reports, args.version, args.source_sha)
    if args.container_smoke:
        smoke = json.loads(args.container_smoke.read_text())
        if (smoke["version"] != args.version or smoke.get("non_root") is not True
                or set(smoke["checks"]) != CHECKS
                or any(v is not True for v in smoke["checks"].values())
                or not smoke.get("dependencies")):
            raise ValueError("Container smoke incomplete or version mismatch")
        if not args.image or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_id or ""):
            raise ValueError("Container identity missing")
        if not args.container_archive or not args.container_archive.is_file():
            raise ValueError("Exported candidate container missing")
        evidence["container"] = {"image": args.image, "image_id": args.image_id,
                                 "smoke": smoke}
        evidence["artifacts"].append({
            "filename": args.container_archive.name,
            "sha256": sha256(args.container_archive), "kind": "docker-image-archive",
        })
    args.output.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")
    checksums = args.output.with_name("SHA256SUMS")
    candidates = [*args.dist.glob("*.whl"), *args.dist.glob("*.tar.gz"), args.output]
    if args.container_archive:
        candidates.append(args.container_archive)
    checksums.write_text("".join(
        f"{sha256(path)}  {path.name}\n" for path in sorted(candidates)
    ))


if __name__ == "__main__":
    main()
