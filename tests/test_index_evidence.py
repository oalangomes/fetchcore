from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def _module() -> ModuleType:
    path = ROOT / "scripts" / "ci" / "build-index-evidence.py"
    spec = importlib.util.spec_from_file_location("build_index_evidence", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_bounded_evidence_preserves_observed_lifecycle_and_runtime(tmp_path: Path) -> None:
    module = _module()
    evidence = module.build_evidence(
        repository="oalangomes/fetchcore",
        revision="abc123",
        lifecycle="sync",
        evaluation_modes=["lexical"],
        preflight_path=_write(tmp_path / "preflight.json", {
            "endpoint": "http://127.0.0.1:8983/solr",
            "deployment_mode": "standalone",
            "instance": "reused",
            "resource_kind": "core",
            "resource": "fetchcore-self-dogfood",
            "resource_state": "existing",
            "repository_binding": "matching",
        }),
        bootstrap_path=_write(tmp_path / "bootstrap.json", {
            "status": "succeeded", "result": {"validated": True},
        }),
        plan_path=_write(tmp_path / "plan.json", {
            "status": "succeeded", "result": {"mode": "noop"},
        }),
        status_path=_write(tmp_path / "status.json", {
            "status": "succeeded", "result": {
                "indexed_revision": "abc123", "currentness": "current", "reason": "up_to_date",
            },
        }),
        execution_path=_write(tmp_path / "sync.json", {
            "status": "succeeded", "result": {
                "mode": "noop",
                "execution": {
                    "telemetry": {
                        "embedding_requests": 0,
                        "embedding_vectors": 0,
                        "backend_writes": 0,
                    },
                    "snapshot": {
                        "embedding_provider": None,
                        "projection_fingerprint": "sha256:projection",
                    },
                },
            },
        }),
    )
    assert evidence["solr"] == {
        "endpoint": "http://127.0.0.1:8983/solr",
        "deployment_mode": "standalone",
        "instance": "reused",
        "resource_kind": "core",
        "resource": "fetchcore-self-dogfood",
        "resource_state": "existing",
        "repository_binding": "matching",
        "schema_compatibility": "compatible",
    }
    assert evidence["index_lifecycle"] == {
        "requested": "sync",
        "planned": "noop",
        "executed": "noop",
        "telemetry": {
            "embedding_requests": 0,
            "embedding_vectors": 0,
            "backend_writes": 0,
        },
    }
    assert evidence["snapshot"]["currentness"] == "current"
    assert evidence["snapshot"]["projection_fingerprint"] == "sha256:projection"
    assert evidence["evaluation"] == {"modes": ["lexical"]}


def test_plan_only_is_reported_without_inventing_execution(tmp_path: Path) -> None:
    module = _module()
    successful = {"status": "succeeded", "result": {}}
    evidence = module.build_evidence(
        repository="acme/widgets",
        revision="def456",
        lifecycle="plan",
        evaluation_modes=[],
        preflight_path=_write(tmp_path / "preflight.json", {}),
        bootstrap_path=_write(tmp_path / "bootstrap.json", successful),
        plan_path=_write(tmp_path / "plan.json", {
            "status": "succeeded", "result": {"mode": "initial"},
        }),
        status_path=_write(tmp_path / "status.json", successful),
        execution_path=None,
    )
    assert evidence["index_lifecycle"] == {
        "requested": "plan", "planned": "initial", "executed": "plan", "telemetry": {},
    }
