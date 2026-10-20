#!/usr/bin/env python3
"""Explicit, owner-checked BENCH infrastructure planning and lifecycle.

Functional benchmarks reuse the existing BENCH fleet. Performance runs use
isolated Compose projects and volumes; host exclusivity is not inferred.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from benchmark_ops import BundleError, RUN_ID, SUITES, load, output_root, save

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "infra/compose/shared.yml"
OWNER = "fetchcore-shared-infra-v1"
PROJECT_RE = re.compile(r"fetchcore-bench-[a-z0-9]{12}\Z")
PORT_FIELDS = ("solr", "opensearch", "opensearch_metrics", "qdrant", "qdrant_grpc")
FUNCTIONAL_PORTS = {
    "solr": 28983, "opensearch": 29200, "opensearch_metrics": 29600,
    "qdrant": 26333, "qdrant_grpc": 26334,
}
FORBIDDEN_PORTS = {18983, 19200, 19600, 16333, 16334, 28983, 29200, 29600,
                   26333, 26334, 38983, 39200, 39600, 36333, 36334}
SUITE_RESOURCE = {
    "baseline-v1": "fc-bench-baseline-v1",
    "search-intent-v2": "fc-bench-search-intent-v2",
    "targeted-exact": "fc-bench-targeted-exact",
    "typed-knowledge-retrieval": "fc-bench-typed-knowledge",
}
# Targeted-exact currently measures local-semantic; remote exact lookup uses
# integration tests, not an undeclared benchmark arm.
ACTIVE_BACKENDS = {
    "baseline-v1": ("solr", "opensearch", "qdrant"),
    "search-intent-v2": ("solr", "opensearch", "qdrant"),
    "targeted-exact": (),
    "typed-knowledge-retrieval": ("solr", "opensearch", "qdrant"),
}


def ports(value: dict[str, object], *, functional: bool) -> dict[str, int]:
    if set(value) != set(PORT_FIELDS):
        raise BundleError("all five published backend ports must be named")
    if any(type(value[key]) is not int or not 1024 <= value[key] <= 65535
           for key in PORT_FIELDS):
        raise BundleError("ports must be unprivileged TCP integers")
    parsed = {key: int(value[key]) for key in PORT_FIELDS}
    if len(set(parsed.values())) != len(PORT_FIELDS):
        raise BundleError("backend ports must be distinct")
    if functional and parsed != FUNCTIONAL_PORTS:
        raise BundleError("functional BENCH uses the existing dedicated fleet")
    if not functional and set(parsed.values()) & FORBIDDEN_PORTS:
        raise BundleError("ephemeral ports collide with a reserved environment")
    return parsed


def make_plan(suite: str, mode: str, run_id: str | None,
              selected_ports: dict[str, object]) -> dict[str, object]:
    if suite not in SUITES:
        raise BundleError("unknown benchmark suite")
    if mode not in {"functional", "performance"}:
        raise BundleError("unknown infrastructure mode")
    if mode == "performance":
        if run_id is None or not RUN_ID.fullmatch(run_id):
            raise BundleError("performance infrastructure requires explicit run-id")
        project = f"fetchcore-bench-{run_id[:12]}"
        if not PROJECT_RE.fullmatch(project):
            raise BundleError("run-id must start with 12 lowercase letters/digits")
    else:
        project = "fetchcore-bench-dedicated"
    p = ports(selected_ports, functional=mode == "functional")
    base = (SUITE_RESOURCE[suite] if mode == "functional"
            else f"{SUITE_RESOURCE[suite]}-{run_id[:12]}")
    endpoints = {
        "solr": f"http://127.0.0.1:{p['solr']}/solr",
        "opensearch": f"http://127.0.0.1:{p['opensearch']}",
        "qdrant": f"http://127.0.0.1:{p['qdrant']}",
    }
    return {
        "schema_version": 1, "suite": suite, "mode": mode, "run_id": run_id,
        "compose_project": project, "compose_file": "infra/compose/shared.yml",
        "owner": OWNER, "ports": p, "endpoints": endpoints,
        "resources": {backend: base for backend in ACTIVE_BACKENDS[suite]},
        "active_backends": list(ACTIVE_BACKENDS[suite]),
        "backend_provisioning": "explicit", "cleanup_policy": "retain_until_review",
        "performance_comparable": False,
        "notes": "Docker isolation alone does not attest exclusive host or provider load",
    }


def environment(plan: dict[str, object]) -> dict[str, str]:
    p = plan["ports"]
    assert isinstance(p, dict)
    return {
        **os.environ,
        "FETCHCORE_INFRA_PROJECT": str(plan["compose_project"]),
        "FETCHCORE_INFRA_OWNER": OWNER,
        "FETCHCORE_SOLR_PORT": str(p["solr"]),
        "OPENSEARCH_PORT": str(p["opensearch"]),
        "OPENSEARCH_METRICS_PORT": str(p["opensearch_metrics"]),
        "QDRANT_HTTP_PORT": str(p["qdrant"]),
        "QDRANT_GRPC_PORT": str(p["qdrant_grpc"]),
    }


def _docker(*args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(["docker", *args], text=True, env=env).strip()


def verify_owned(project: str) -> None:
    """Refuse foreign containers and volumes, including stopped objects."""
    ids = _docker("ps", "-aq", "--filter", f"label=com.docker.compose.project={project}").split()
    for identifier in ids:
        details = json.loads(_docker("inspect", identifier))[0]
        labels = details["Config"].get("Labels") or {}
        if (labels.get("com.docker.compose.project") != project
                or labels.get("io.fetchcore.owner") != OWNER):
            raise BundleError("refusing foreign container in BENCH project")
    for name in (f"{project}_{part}-data" for part in ("solr", "opensearch", "qdrant")):
        if name not in _docker("volume", "ls", "--format", "{{.Name}}").split():
            continue
        volume = json.loads(_docker("volume", "inspect", name))[0]
        labels = volume.get("Labels") or {}
        if (labels.get("com.docker.compose.project") != project
                or labels.get("io.fetchcore.owner") != OWNER):
            raise BundleError("refusing foreign volume in BENCH project")


def verified_plan(path: Path) -> dict[str, object]:
    raw = load(path)
    suite, mode, run_id = raw.get("suite"), raw.get("mode"), raw.get("run_id")
    if not isinstance(suite, str) or not isinstance(mode, str):
        raise BundleError("invalid BENCH plan")
    if run_id is not None and not isinstance(run_id, str):
        raise BundleError("invalid run id")
    p = raw.get("ports")
    if not isinstance(p, dict):
        raise BundleError("missing ports")
    rebuilt = make_plan(suite, mode, run_id, p)
    if rebuilt != raw:
        raise BundleError("BENCH plan identity or configuration was modified")
    return raw


def compose(plan: dict[str, object], *args: str) -> None:
    subprocess.run(["docker", "compose", "-p", str(plan["compose_project"]),
                    "-f", str(COMPOSE), *args],
                   cwd=ROOT, env=environment(plan), check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--suite", required=True, choices=sorted(SUITES))
    p.add_argument("--mode", required=True, choices=("functional", "performance"))
    p.add_argument("--run-id")
    p.add_argument("--ports", help="JSON object with five backend host ports")
    p.add_argument("--output", type=Path)
    p = sub.add_parser("up")
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--authorize", action="store_true")
    p = sub.add_parser("down")
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--authorize", action="store_true")
    p.add_argument("--remove-volumes", action="store_true")
    args = parser.parse_args()
    try:
        if args.operation == "plan":
            selected = (json.loads(args.ports) if args.ports
                        else FUNCTIONAL_PORTS if args.mode == "functional" else None)
            if not isinstance(selected, dict):
                raise BundleError("performance BENCH requires explicit host ports")
            plan = make_plan(args.suite, args.mode, args.run_id, selected)
            if args.output:
                target = args.output.expanduser().resolve()
                output_root(target.parent)
                target.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                save(target, plan)
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        plan = verified_plan(args.plan)
        if plan["mode"] != "performance":
            raise BundleError("functional BENCH lifecycle is owned by scripts/shared_infra.py")
        if not args.authorize:
            raise BundleError("explicit --authorize is required for Docker lifecycle")
        project = str(plan["compose_project"])
        if not PROJECT_RE.fullmatch(project):
            raise BundleError("unsafe Compose project identity")
        verify_owned(project)
        if args.operation == "up":
            ids = _docker("ps", "-aq", "--filter",
                          f"label=com.docker.compose.project={project}").split()
            volumes = set(_docker("volume", "ls", "--format", "{{.Name}}").split())
            if ids or any(f"{project}_{part}-data" in volumes
                          for part in ("solr", "opensearch", "qdrant")):
                raise BundleError("ephemeral BENCH project or volumes already exist")
            compose(plan, "up", "-d", "--no-build", "solr", "opensearch", "qdrant")
            verify_owned(project)
        else:
            compose(plan, "down", *(["--volumes"] if args.remove_volumes else []))
        print(json.dumps({"project": project, "operation": args.operation,
                          "volumes_removed": bool(args.operation == "down" and args.remove_volumes)}))
        return 0
    except (BundleError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
