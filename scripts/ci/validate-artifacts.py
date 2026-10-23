"""Install the exact wheel and sdist in separate clean environments and emit evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import venv


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate(dist: Path, version: str, source_sha: str) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("Source revision must be a full Git SHA")
    candidates = [("wheel", list(dist.glob("*.whl"))), ("sdist", list(dist.glob("*.tar.gz")))]
    if any(len(paths) != 1 for _, paths in candidates):
        raise ValueError("Expected exactly one wheel and one sdist")
    smoke = Path(__file__).with_name("artifact-smoke.py").resolve()
    reports = []
    for kind, paths in candidates:
        artifact = paths[0].resolve()
        checksum = digest(artifact)
        with tempfile.TemporaryDirectory(prefix="fetchcore-artifact-") as directory:
            root = Path(directory)
            venv.EnvBuilder(with_pip=True).create(root / "venv")
            python = root / "venv" / "bin" / "python"
            # pip builds the sdist in its isolated build environment; the wheel
            # is installed directly. Neither environment installs editable source.
            subprocess.run([
                str(python), "-I", "-m", "pip", "install", "--disable-pip-version-check",
                "--no-cache-dir",
                f"fetchcore[mcp] @ {artifact.as_uri()}",
            ], cwd=root, check=True, stdout=sys.stderr, timeout=300)
            result = subprocess.run([
                str(python), "-I", str(smoke), "--version", version,
            ], cwd=root, check=True, capture_output=True, text=True, timeout=90)
            report = json.loads(result.stdout)
        if checksum != digest(artifact):
            raise ValueError("Artifact changed during validation")
        reports.append({"kind": kind, "filename": artifact.name, "sha256": checksum,
                        "smoke": report})
    return {"schema_version": 1, "kind": "fetchcore.release-artifacts",
            "source_sha": source_sha, "version": version, "artifacts": reports}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = validate(args.dist, args.version, args.source_sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"Validated wheel and sdist for {args.version} at {args.source_sha}")


if __name__ == "__main__":
    main()
