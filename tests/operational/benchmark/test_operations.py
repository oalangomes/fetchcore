"""Operational contract tests: real invariants, no Docker or retrieval dependency."""
from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import sys

import pytest

BENCHMARKS = Path(__file__).resolve().parents[3] / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))
import benchmark_ops as ops
import benchmark_infra as infra


def sample_run(tmp_path: Path, *, kind: str = "full") -> Path:
    run_dir = tmp_path / "runs" / "targeted-exact" / "b4e4d2c6a0e8"
    (run_dir / "native").mkdir(parents=True)
    for representation in ("canonical", "legacy"):
        ops.save(run_dir / "native" / f"{representation}.json", {
            "status": "complete", "representation": representation,
            "repetitions": 20, "warmups": 1,
            "protocol": "fetchcore.targeted-exact-v2",
        })
    ops.save(run_dir / "native" / "run.json", {
        "status": "complete", "repetitions": 20, "warmups": 1,
        "representations": ["canonical", "legacy"],
        "protocol": "fetchcore.targeted-exact-v2",
    })
    ops.save(run_dir / "bundle.json", {
        "schema_version": 1, "suite": "targeted-exact",
        "protocol": "fetchcore.targeted-exact-v2",
        "run_id": run_dir.name, "kind": kind, "status": "completed",
        "review": {"status": "unreviewed", "note": None},
        "artifacts": ops.capture(run_dir),
    })
    return run_dir


def test_detects_mutated_and_unlisted_measurements(tmp_path: Path) -> None:
    run = sample_run(tmp_path)
    assert ops.validate(run)["status"] == "completed"
    (run / "native" / "canonical.json").write_text("{}")
    with pytest.raises(ops.BundleError, match="checksum"):
        ops.validate(run)
    run = sample_run(tmp_path / "another")
    (run / "native" / "unlisted.json").write_text("{}")
    with pytest.raises(ops.BundleError, match="inventory"):
        ops.validate(run)


def test_refuses_symlink_and_path_escape(tmp_path: Path) -> None:
    run = sample_run(tmp_path)
    (run / "native" / "link.json").symlink_to(run / "native" / "canonical.json")
    with pytest.raises(ops.BundleError, match="symlink"):
        ops.validate(run)
    (run / "native" / "link.json").unlink()
    bundle = ops.load(run / "bundle.json")
    bundle["artifacts"][0]["path"] = "native/../bundle.json"
    ops.save(run / "bundle.json", bundle)
    with pytest.raises(ops.BundleError, match="unsafe"):
        ops.validate(run)


def test_publish_is_reviewed_full_only_and_immutable(tmp_path: Path) -> None:
    run = sample_run(tmp_path)
    catalog = tmp_path / "catalog"
    args = Namespace(run=run, catalog=catalog, write=True)
    with pytest.raises(ops.BundleError, match="reviewed full"):
        ops.publish(args)
    ops.review(Namespace(run=run, decision="accepted", note="Validated native result"))
    assert ops.publish(args) == 0
    copied = catalog / "targeted-exact" / run.name
    assert copied.is_dir()
    assert ops.validate(copied, publish=True)["review"]["status"] == "accepted"
    with pytest.raises(ops.BundleError, match="already exists"):
        ops.publish(args)
    smoke = sample_run(tmp_path / "smoke", kind="smoke")
    with pytest.raises(ops.BundleError, match="completed full"):
        ops.review(Namespace(run=smoke, decision="accepted", note="Not full"))


def test_blocked_native_preflight_is_not_measurement() -> None:
    assert ops._native_status("typed-knowledge-retrieval", {"status": "BLOCKED"}, "preflight") == "blocked"
    assert ops._native_status("search-intent-v2", {
        "status": "complete", "full_protocol": False,
    }, "full") == "failed"
    assert ops._native_status("search-intent-v2", {
        "status": "complete", "full_protocol": False,
    }, "smoke") == "completed"
    assert ops._native_status("targeted-exact", {
        "status": "complete", "repetitions": 1, "warmups": 1,
    }, "full") == "failed"


def test_backend_writes_and_output_override_require_explicit_policy() -> None:
    with pytest.raises(ops.BundleError, match="authorize-writes"):
        ops._safe_arguments(["--prepare-index"], False)
    with pytest.raises(ops.BundleError, match="authorize-writes"):
        ops._safe_arguments(["--execute-authorized"], False)
    with pytest.raises(ops.BundleError, match="owned"):
        ops._safe_arguments(["--output=/tmp/elsewhere"], True)


def test_infra_plans_isolate_resource_identity_and_refuse_reserved_ports() -> None:
    plan = infra.make_plan("typed-knowledge-retrieval", "functional", None, infra.FUNCTIONAL_PORTS)
    assert plan["resources"]["solr"] == "fc-bench-typed-knowledge"
    assert plan["compose_project"] == "fetchcore-bench-dedicated"
    assert plan["performance_comparable"] is False
    ephemeral = infra.make_plan("typed-knowledge-retrieval", "performance", "abcdef123456",
                                {"solr": 49183, "opensearch": 49200,
                                 "opensearch_metrics": 49600, "qdrant": 46333,
                                 "qdrant_grpc": 46334})
    assert ephemeral["compose_project"] == "fetchcore-bench-abcdef123456"
    assert ephemeral["resources"]["solr"] != plan["resources"]["solr"]
    assert ephemeral["performance_comparable"] is False
    with pytest.raises(ops.BundleError, match="reserved"):
        infra.make_plan("typed-knowledge-retrieval", "performance", "abcdef123456",
                        {**ephemeral["ports"], "solr": 28983})
    with pytest.raises(ops.BundleError, match="explicit run-id"):
        infra.make_plan("typed-knowledge-retrieval", "performance", None, ephemeral["ports"])


def test_infra_rejects_foreign_stopped_container(monkeypatch: pytest.MonkeyPatch) -> None:
    def docker(*args: str, env: object = None) -> str:
        if args[0] == "ps":
            return "other-container"
        if args[0] == "inspect":
            return json.dumps([{"Config": {"Labels": {
                "com.docker.compose.project": "fetchcore-bench-abcdef123456",
                "io.fetchcore.owner": "another-owner",
            }}}])
        raise AssertionError(args)

    monkeypatch.setattr(infra, "_docker", docker)
    with pytest.raises(ops.BundleError, match="foreign container"):
        infra.verify_owned("fetchcore-bench-abcdef123456")
