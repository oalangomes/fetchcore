#!/usr/bin/env python3
"""Run frozen baseline-v1 arms on explicitly owned BENCH resources.

This wrapper changes only the manifest loaded in memory by the frozen runner.
The historical runner, manifest, Ground Truth, fingerprints and results remain
byte-identical.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("fetchcore_baseline_v1_frozen", HERE / "run.py")
assert spec is not None and spec.loader is not None
frozen = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = frozen
spec.loader.exec_module(frozen)


def load_run_config(path: Path, corpus: dict[str, object]) -> dict[str, object]:
    config = frozen._load_json(path)
    required = {"schema_version", "environment", "repository", "revision", "owner",
                "baseline_id", "provisioning", "cleanup", "performance_comparable",
                "dedicated", "exclusive_host", "infrastructure_project", "endpoints",
                "resources", "embedding"}
    if set(config) != required or config["schema_version"] != 1:
        raise frozen.BaselineConfigurationError("BENCH run config has missing/unknown fields or version")
    if config["environment"] != "bench":
        raise frozen.BaselineConfigurationError("new baseline runs require BENCH environment")
    if config["repository"] != corpus["repository"] or config["revision"] != corpus["revision"]:
        raise frozen.BaselineConfigurationError("BENCH repository/revision must match frozen Ground Truth")
    for field in ("owner", "baseline_id"):
        value = config[field]
        if not isinstance(value, str) or not value.strip():
            raise frozen.BaselineConfigurationError(f"BENCH {field} must be nonempty")
    if config["provisioning"] not in {"reuse_only", "explicit_rebuild"}:
        raise frozen.BaselineConfigurationError("BENCH provisioning must be reuse_only or explicit_rebuild")
    if config["cleanup"] != "retain":
        raise frozen.BaselineConfigurationError("BENCH cleanup must be retain; retire separately")
    for field in ("performance_comparable", "dedicated", "exclusive_host"):
        if not isinstance(config[field], bool):
            raise frozen.BaselineConfigurationError(f"BENCH {field} must be a boolean")
    if config["performance_comparable"] and not (
        config["dedicated"] and config["exclusive_host"]
        and config["infrastructure_project"] == "fetchcore-bench-dedicated"
    ):
        raise frozen.BaselineConfigurationError("comparable performance requires dedicated BENCH and exclusive host")
    if config["infrastructure_project"] not in {"fetchcore-shared", "fetchcore-bench-dedicated"}:
        raise frozen.BaselineConfigurationError("BENCH infrastructure project is invalid")
    if bool(config["dedicated"]) != (config["infrastructure_project"] == "fetchcore-bench-dedicated"):
        raise frozen.BaselineConfigurationError("BENCH dedicated flag and project must agree")
    endpoints = frozen._object(config, "endpoints")
    resources = frozen._object(config, "resources")
    embedding = frozen._object(config, "embedding")
    if set(endpoints) != {"solr", "opensearch", "qdrant", "ollama"}:
        raise frozen.BaselineConfigurationError("BENCH endpoints must identify all services")
    if set(resources) != {"solr", "opensearch", "qdrant-vector"}:
        raise frozen.BaselineConfigurationError("BENCH resources must identify all remote backends")
    for backend, endpoint in endpoints.items():
        if not isinstance(endpoint, str):
            raise frozen.BaselineConfigurationError(f"BENCH {backend} endpoint must be a URL")
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise frozen.BaselineConfigurationError(f"BENCH {backend} endpoint must be HTTP(S) without credentials")
        if backend == "solr" and parsed.path.rstrip("/") != "/solr":
            raise frozen.BaselineConfigurationError("BENCH Solr endpoint must end in /solr")
        if parsed.port in {38983, 39200, 39600, 36333, 36334}:
            raise frozen.BaselineConfigurationError("BENCH endpoint cannot use isolated OPERATIONAL ports")
        if config["performance_comparable"] and parsed.port in {18983, 19200, 16333, 38983, 39200, 36333}:
            raise frozen.BaselineConfigurationError("comparable performance cannot use shared or operational ports")
    repository = config["repository"]
    assert isinstance(repository, str)
    resource_name = repository.split("/")[-1].lower()[:24]
    resource_digest = hashlib.sha256(repository.encode()).hexdigest()[:16]
    expected_resource = f"fc-bench-{resource_name}-{resource_digest}"
    for backend, resource in resources.items():
        if resource != expected_resource:
            raise frozen.BaselineConfigurationError(
                f"BENCH {backend} resource must match repository routing: {expected_resource}"
            )
    if (set(embedding) != {"provider", "model", "dimensions"}
            or not isinstance(embedding["provider"], str)
            or not isinstance(embedding["model"], str)
            or not isinstance(embedding["dimensions"], int)
            or embedding["dimensions"] < 1):
        raise frozen.BaselineConfigurationError("BENCH embedding identity requires provider, model, dimensions")
    return config


def configured_manifest(manifest: dict[str, object], config: dict[str, object]) -> dict[str, object]:
    effective = copy.deepcopy(manifest)
    environment = frozen._object(effective, "environment")
    environment["profile_id"] = config["baseline_id"]
    environment["service_endpoints"] = frozen._object(config, "endpoints")
    environment["embedding"] = frozen._object(config, "embedding")
    effective["environment"] = environment
    settings = frozen._object(effective, "backend_settings")
    resources = frozen._object(config, "resources")
    for backend, field in (("solr", "collection"), ("opensearch", "index"),
                           ("qdrant-vector", "collection")):
        selected = frozen._object(settings, backend)
        selected[field] = resources[backend]
        settings[backend] = selected
    effective["backend_settings"] = settings
    return effective


def observed_fingerprints(run_dir: Path) -> dict[str, object]:
    """Summarize observed identities; keep the original artifacts unchanged."""
    observed: dict[str, object] = {"indexing": None, "retrieval": {}}
    indexing_path = run_dir / "indexing.json"
    if indexing_path.is_file():
        indexing = frozen._load_json(indexing_path)
        observed["indexing"] = indexing.get("indexing_fingerprint")
    retrieval: dict[str, dict[str, list[str]]] = {}
    run = frozen._load_json(run_dir / "run.json")
    for arm in run.get("arms", []):
        if not isinstance(arm, dict) or not isinstance(arm.get("id"), str):
            continue
        arm_id = arm["id"]
        path = run_dir / f"{arm_id}.json"
        if not path.is_file():
            continue
        artifact = frozen._load_json(path)
        identities: dict[str, set[str]] = {}
        for row in artifact.get("observations", []):
            if not isinstance(row, dict):
                continue
            telemetry = row.get("telemetry")
            fingerprints = telemetry.get("fingerprints") if isinstance(telemetry, dict) else None
            if isinstance(fingerprints, dict):
                for name, value in fingerprints.items():
                    if isinstance(name, str) and isinstance(value, str):
                        identities.setdefault(name, set()).add(value)
        retrieval[arm_id] = {key: sorted(values) for key, values in sorted(identities.items())}
    observed["retrieval"] = retrieval
    return observed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arm", action="append", default=[])
    parser.add_argument("--prepare-index", action="store_true")
    parser.add_argument("--resume", help="Resume an incomplete V0.N run with the same BENCH configuration")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    frozen_artifacts = frozen.ARTIFACTS_DIR.resolve()
    if output == frozen_artifacts or frozen_artifacts in output.parents:
        parser.error("new BENCH runs need output outside frozen artifacts")
    suite = frozen.load_baseline_suite(frozen.GROUND_TRUTH_PATH)
    config = load_run_config(args.run_config, suite.corpus)
    if args.prepare_index and config["provisioning"] != "explicit_rebuild":
        parser.error("--prepare-index requires explicit_rebuild in BENCH run config")
    effective = configured_manifest(frozen._load_json(frozen.MANIFEST_PATH), config)
    before = set(output.glob("V0.*")) if output.exists() else set()
    with TemporaryDirectory(prefix="fetchcore-bench-manifest-") as temporary:
        manifest_path = Path(temporary) / "manifest.json"
        manifest_path.write_text(json.dumps(effective, sort_keys=True) + "\n", encoding="utf-8")
        frozen.MANIFEST_PATH = manifest_path
        original_argv = sys.argv
        sys.argv = [str(HERE / "run.py"), "--workspace", str(args.workspace),
                    "--output", str(output)]
        for arm in args.arm:
            sys.argv.extend(("--arm", arm))
        if args.prepare_index:
            sys.argv.append("--prepare-index")
        if args.resume:
            sys.argv.extend(("--resume", args.resume))
        # Some backend telemetry is inapplicable to lexical-only runs. The
        # frozen report formatter raises on None; represent it as unmeasured
        # for this new-run wrapper without mutating source measurements.
        original_format = frozen._format_number
        frozen._format_number = lambda value: (
            "not measured" if value is None else original_format(value)
        )
        try:
            result = frozen.main()
        finally:
            sys.argv = original_argv
            frozen._format_number = original_format
    if args.resume:
        run_dir = output / args.resume
    else:
        created = set(output.glob("V0.*")) - before
        if len(created) != 1:
            raise frozen.BaselineConfigurationError("BENCH run output identity is ambiguous")
        run_dir = created.pop()
    destination = run_dir / "bench-run.json"
    destination.write_text(json.dumps({
        "schema_version": 1,
        "config": config,
        "config_sha256": hashlib.sha256(args.run_config.read_bytes()).hexdigest(),
        "effective_manifest_fingerprint": frozen.mapping_fingerprint(effective),
        "ground_truth_fingerprint": suite.fingerprint,
        "runner_sha256": hashlib.sha256((HERE / "run.py").read_bytes()).hexdigest(),
        "observed_fingerprints": observed_fingerprints(run_dir),
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"[bench] identity={destination}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
