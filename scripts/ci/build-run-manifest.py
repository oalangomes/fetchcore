"""Compose existing CLI JSON into a bounded operational run manifest, offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from fetchcore.application.index_artifacts import (
    MAX_ARTIFACT_BYTES, IndexRunArtifact, parse_index_json,
)
from fetchcore.application.run_manifest import OperationalRunIdentity, compose_run_manifest


def read_json(path: Path) -> object:
    with path.open("rb") as stream:
        raw = stream.read(MAX_ARTIFACT_BYTES + 1)
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("Run observation exceeds 2 MB")
    return parse_index_json(raw.decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repository", "revision", "backend", "target", "run-id", "observed-at",
                 "lifecycle", "outcome"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = read_json(args.configuration)
    if not isinstance(config, dict) or not config:
        raise ValueError("Expected effective configuration object (hashed, never copied)")
    fingerprint = hashlib.sha256(json.dumps(
        config, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    observations = []
    for name in ("status-before.json", "plan.json", "execution.json", "status-after.json"):
        path = args.directory / name
        if not path.exists():
            continue
        # A pipe can leave an empty file when the CLI fails before emitting JSON.
        # Absence is unknown; malformed nonempty evidence must fail validation.
        if not path.stat().st_size:
            continue
        observations.append(IndexRunArtifact.from_mapping({
            "schema_version": 1, "kind": "fetchcore.index-run", "run_id": args.run_id,
            "observed_at": args.observed_at, "repository": args.repository,
            "backend": args.backend, "target": args.target,
            "configuration_fingerprint": fingerprint,
            "command": read_json(path),
        }))
    identity = OperationalRunIdentity(args.run_id, args.repository, args.backend, args.target,
                                      fingerprint)
    manifest = compose_run_manifest(observations, identity=identity, requested=args.lifecycle,
                                    revision=args.revision, outcome=args.outcome)
    args.output.write_text(json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
