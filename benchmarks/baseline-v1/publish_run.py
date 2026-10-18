"""Validate and stage a complete baseline-v1 BENCH run for immutable publication.

Offline only: never invokes a retrieval backend, prepares an index, or changes
the measured JSON artifacts. Published versions cannot be replaced in place.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PUBLISHED = ROOT / "artifacts"
VERSION_RE = re.compile(r"V0\.[1-9][0-9]*\Z")
PROTOCOL = "fetchcore.baseline-v1"


class PublicationError(ValueError):
    """An incomplete or incompatible run must not be published."""


def _object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublicationError(f"cannot load JSON artifact: {path.name}") from exc
    if not isinstance(value, dict):
        raise PublicationError(f"{path.name} must contain a JSON object")
    return value


def _artifact(source: Path, name: str) -> Path:
    if Path(name).name != name or not name or name.startswith("."):
        raise PublicationError(f"unsafe artifact name: {name!r}")
    path = source / name
    if path.is_symlink() or not path.is_file():
        raise PublicationError(f"missing or unsafe artifact: {name}")
    return path


def validate(source: Path) -> tuple[str, tuple[str, ...], dict[str, str]]:
    """Return version, allowlisted filenames and their SHA256 digests."""
    if source.is_symlink() or not source.is_dir() or not VERSION_RE.fullmatch(source.name):
        raise PublicationError("source must be a real V0.N run directory")
    run = _object(_artifact(source, "run.json"))
    manifest = _object(ROOT / "manifest.json")
    ground_truth = _object(ROOT / "ground-truth.json")
    if (run.get("protocol") != PROTOCOL or run.get("status") != "complete"
            or run.get("run_version") != source.name or run.get("schema_version") != 1):
        raise PublicationError("run manifest is incomplete or has incompatible identity")
    if run.get("corpus") != ground_truth.get("corpus"):
        raise PublicationError("run corpus differs from frozen Ground Truth")
    if (run.get("fetchcore_revision") is None or
            not re.fullmatch(r"[0-9a-f]{40}", str(run["fetchcore_revision"]))):
        raise PublicationError("missing FetchCore revision")

    arms = run.get("arms")
    expected_arms = manifest.get("arms")
    if arms != expected_arms or not isinstance(arms, list):
        raise PublicationError("run must contain all frozen arms in manifest order")
    expected_names = [f"{arm['id']}.json" for arm in arms]
    if run.get("artifacts") != expected_names:
        raise PublicationError("run artifact names differ from frozen arms")
    count = len(ground_truth["queries"]) * int(manifest["measurement"]["repetitions"])
    names = ["run.json", "bench-run.json", "README.md", "context-yield.json",
             "context-yield.md", *expected_names]

    if run.get("prepare_index") is True:
        if run.get("indexing_artifact") != "indexing.json":
            raise PublicationError("prepared run is missing indexing reference")
        names.append("indexing.json")
    elif run.get("prepare_index") is not False or run.get("indexing_artifact") is not None:
        raise PublicationError("inconsistent index preparation state")
    if run.get("metrics_report") != "README.md":
        raise PublicationError("missing metrics report reference")

    ground_truth_fingerprints: set[str] = set()
    for arm, name in zip(arms, expected_names):
        item = _object(_artifact(source, name))
        if (item.get("protocol") != PROTOCOL or item.get("arm") != arm
                or item.get("corpus") != run["corpus"]):
            raise PublicationError(f"incompatible retrieval identity: {name}")
        observations = item.get("observations")
        aggregate = item.get("aggregate")
        if (not isinstance(observations, list) or len(observations) != count
                or not isinstance(aggregate, dict) or aggregate.get("run_count") != count):
            raise PublicationError(f"incomplete measurements: {name}")
        identity = item.get("config", {}).get("identity")
        if (not isinstance(identity, dict)
                or identity.get("fetchcore_revision") != run["fetchcore_revision"]):
            raise PublicationError(f"incompatible FetchCore revision: {name}")
        fingerprint = item.get("ground_truth_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise PublicationError(f"missing Ground Truth fingerprint: {name}")
        ground_truth_fingerprints.add(fingerprint)
    if len(ground_truth_fingerprints) != 1:
        raise PublicationError("retrieval artifacts disagree on Ground Truth fingerprint")

    bench = _object(_artifact(source, "bench-run.json"))
    config = bench.get("config")
    if (bench.get("schema_version") != 1 or not isinstance(config, dict)
            or config.get("environment") != "bench"
            or config.get("repository") != run["corpus"]["repository"]
            or config.get("revision") != run["corpus"]["revision"]
            or bench.get("ground_truth_fingerprint") not in ground_truth_fingerprints
            or not isinstance(bench.get("observed_fingerprints"), dict)):
        raise PublicationError("BENCH provenance is missing or incompatible")
    if config.get("performance_comparable") is True and not (
        config.get("dedicated") is True and config.get("exclusive_host") is True
        and config.get("infrastructure_project") == "fetchcore-bench-dedicated"
    ):
        raise PublicationError("invalid performance comparability attestation")

    if "indexing.json" in names:
        indexing = _object(_artifact(source, "indexing.json"))
        if (indexing.get("corpus") != run["corpus"]
                or indexing.get("fetchcore_revision") != run["fetchcore_revision"]
                or not isinstance(indexing.get("backends"), dict)
                or set(indexing["backends"]) != {"solr", "opensearch", "qdrant"}):
            raise PublicationError("incomplete or incompatible indexing telemetry")

    context_yield = _object(_artifact(source, "context-yield.json"))
    if context_yield.get("protocol") != "fetchcore.baseline-v1-context-yield":
        raise PublicationError("invalid derived context-yield protocol")

    for name in names:
        _artifact(source, name)
    return source.name, tuple(names), {
        name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in names
    }


def publish(source: Path, published: Path, *, write: bool = False) -> Path:
    version, names, hashes = validate(source)
    if source.resolve() == published.resolve() / version:
        raise PublicationError("source is already inside the published artifact directory")
    destination = published / version
    if destination.exists() or destination.is_symlink():
        raise PublicationError(f"published version already exists: {destination}")
    if not write:
        print(f"[publication] validated {version}: {len(names)} files; dry run only")
        return destination

    published.mkdir(parents=True, exist_ok=True)
    lock_path = published / ".publication.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PublicationError("publication is locked; investigate before removing the lock") from exc
    try:
        os.close(descriptor)
        if destination.exists() or destination.is_symlink():
            raise PublicationError(f"published version already exists: {destination}")
        with tempfile.TemporaryDirectory(prefix=".publication-", dir=published) as staging:
            staged = Path(staging) / version
            staged.mkdir()
            for name in names:
                shutil.copyfile(_artifact(source, name), staged / name)
                if hashlib.sha256((staged / name).read_bytes()).hexdigest() != hashes[name]:
                    raise PublicationError(f"artifact changed during publication: {name}")
            # Rename is atomic on one filesystem; no partial visible run.
            if destination.exists() or destination.is_symlink():
                raise PublicationError(f"published version already exists: {destination}")
            staged.rename(destination)
    finally:
        lock_path.unlink(missing_ok=True)
    print(f"[publication] staged {version}: {len(names)} files at {destination}")
    print("[publication] review BENCH metadata and commit these files separately")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--publish", action="store_true",
                        help="Copy validated artifacts into the immutable published catalog.")
    args = parser.parse_args()
    try:
        publish(args.source, PUBLISHED, write=args.publish)
    except PublicationError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
