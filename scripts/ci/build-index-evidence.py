"""Compose one bounded dogfood artifact from existing measured command outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return cast(dict[str, Any], value)


def _result(path: Path) -> dict[str, Any]:
    payload = _object(path)
    result = payload.get("result")
    if payload.get("status") != "succeeded" or not isinstance(result, dict):
        raise ValueError(f"{path.name} does not contain a successful command result")
    return cast(dict[str, Any], result)


def build_evidence(
    *,
    repository: str,
    revision: str,
    lifecycle: str,
    evaluation_modes: list[str],
    preflight_path: Path,
    bootstrap_path: Path,
    plan_path: Path,
    status_path: Path,
    execution_path: Path | None,
) -> dict[str, object]:
    preflight = _object(preflight_path)
    bootstrap = _result(bootstrap_path)
    plan = _result(plan_path)
    status = _result(status_path)
    execution = _result(execution_path) if execution_path is not None else None
    executed = "plan"
    if execution is not None:
        mode = execution.get("mode")
        executed = str(mode) if mode in {"rebuild", "incremental", "noop"} else lifecycle
    return {
        "version": 1,
        "repository": repository,
        "revision": revision,
        "solr": {
            "endpoint": preflight.get("endpoint"),
            "deployment_mode": preflight.get("deployment_mode"),
            "instance": preflight.get("instance"),
            "resource_kind": preflight.get("resource_kind"),
            "resource": preflight.get("resource"),
            "resource_state": preflight.get("resource_state"),
            "repository_binding": preflight.get("repository_binding"),
            "schema_compatibility": (
                "compatible" if bootstrap.get("validated") is True else "unknown"
            ),
        },
        "snapshot": {
            "indexed_revision": status.get("indexed_revision"),
            "currentness": status.get("currentness"),
            "reason": status.get("reason"),
        },
        "index_lifecycle": {
            "requested": lifecycle,
            "planned": plan.get("mode"),
            "executed": executed,
        },
        "evaluation": {
            "modes": evaluation_modes,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--lifecycle", choices=("plan", "sync", "rebuild"), required=True)
    parser.add_argument("--evaluation-modes-json", required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--bootstrap", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--execution", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    modes = json.loads(args.evaluation_modes_json)
    if not isinstance(modes, list) or not all(isinstance(item, str) for item in modes):
        raise SystemExit("evaluation modes must be a JSON string array")
    evidence = build_evidence(
        repository=args.repository,
        revision=args.revision,
        lifecycle=args.lifecycle,
        evaluation_modes=modes,
        preflight_path=args.preflight,
        bootstrap_path=args.bootstrap,
        plan_path=args.plan,
        status_path=args.status,
        execution_path=args.execution,
    )
    args.output.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
