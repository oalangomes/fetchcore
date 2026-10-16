"""Explicit local infrastructure routing; never called by retrieval or CI implicitly."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OWNER = "fetchcore-shared-infra-v1"


def route(environment: str, repository: str, dedicated: bool = False) -> dict[str, str]:
    if environment not in {"dev", "bench", "operational"}:
        raise ValueError("unknown environment")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*", repository):
        raise ValueError("repository must be canonical lowercase owner/name")
    if dedicated and environment != "bench":
        raise ValueError("dedicated mode is only available for bench")
    instance = "bench-dedicated" if dedicated else (
        "operational" if environment == "operational" else "shared"
    )
    offset = {"shared": 10000, "bench-dedicated": 20000, "operational": 30000}[instance]
    digest = hashlib.sha256(repository.encode()).hexdigest()[:16]
    resource = f"fc-{environment}-{repository.split('/')[-1][:24]}-{digest}"
    return {
        "FETCHCORE_INFRA_PROJECT": f"fetchcore-{instance}",
        "FETCHCORE_INFRA_OWNER": OWNER,
        "FETCHCORE_ENVIRONMENT": environment,
        "FETCHCORE_REPOSITORY": repository,
        "FETCHCORE_SOLR_PORT": str(8983 + offset),
        "OPENSEARCH_PORT": str(9200 + offset),
        "OPENSEARCH_METRICS_PORT": str(9600 + offset),
        "QDRANT_HTTP_PORT": str(6333 + offset),
        "QDRANT_GRPC_PORT": str(6334 + offset),
        "FETCHCORE_OLLAMA_PORT": str(11434 + offset),
        "FETCHCORE_SOLR_URL": f"http://127.0.0.1:{8983 + offset}/solr",
        "FETCHCORE_OPENSEARCH_URL": f"http://127.0.0.1:{9200 + offset}",
        "FETCHCORE_QDRANT_URL": f"http://127.0.0.1:{6333 + offset}",
        "FETCHCORE_OLLAMA_URL": f"http://127.0.0.1:{11434 + offset}",
        "FETCHCORE_SOLR_COLLECTION": resource,
        "FETCHCORE_OPENSEARCH_INDEX": resource,
        "FETCHCORE_QDRANT_COLLECTION": resource,
        "FETCHCORE_INDEX_BACKEND": "solr",
        "FETCHCORE_INDEX_BACKEND_SETTINGS": json.dumps({
            "url": f"http://127.0.0.1:{8983 + offset}/solr",
            "collection": resource, "projection_version": "3",
        }),
    }


def owned_containers(config: dict[str, str]) -> list[str]:
    project = config["FETCHCORE_INFRA_PROJECT"]
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
        check=True, capture_output=True, text=True,
    )
    ids = result.stdout.split()
    for container in ids:
        detail = json.loads(subprocess.run(
            ["docker", "inspect", container], check=True, capture_output=True, text=True,
        ).stdout)[0]
        labels = detail["Config"].get("Labels") or {}
        if labels.get("io.fetchcore.owner") != OWNER or labels.get(
            "com.docker.compose.project"
        ) != project:
            raise ValueError("refusing unowned container in reserved project")
    return ids


def validate_volumes(config: dict[str, str]) -> None:
    project = config["FETCHCORE_INFRA_PROJECT"]
    # Inspect exact reserved names even when no containers currently exist.
    existing = subprocess.run(
        ["docker", "volume", "ls", "--format", "{{.Name}}"],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    for name in (f"{project}_{kind}-data" for kind in ("solr", "opensearch", "qdrant", "ollama")):
        if name not in existing:
            continue
        detail = json.loads(subprocess.run(
            ["docker", "volume", "inspect", name], check=True, capture_output=True, text=True,
        ).stdout)[0]
        labels = detail.get("Labels") or {}
        if labels.get("io.fetchcore.owner") != OWNER or labels.get(
            "com.docker.compose.project"
        ) != project:
            raise ValueError("refusing unowned volume in reserved project")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["env", "up", "run", "cleanup"])
    parser.add_argument("--environment", choices=["dev", "bench", "operational"], required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--dedicated", action="store_true")
    parser.add_argument("--performance", action="store_true")
    parser.add_argument("--exclusive-host", action="store_true",
                        help="operator attests this host has no competing workload")
    parser.add_argument("--backend", choices=["solr", "opensearch", "qdrant", "ollama"],
                        default="solr")
    parser.add_argument("--confirm-project")
    args, command = parser.parse_known_args()
    if command[:1] == ["--"]:
        command = command[1:]
    if command and args.action != "run":
        parser.error("extra command is only valid for run")
    if args.backend != "solr" and args.action != "up":
        parser.error("--backend selects a service for up only; env emits all endpoints")
    config = route(args.environment, args.repository, args.dedicated)
    if args.performance and not (
        args.environment == "bench" and args.dedicated and args.exclusive_host
    ):
        parser.error("performance requires bench --dedicated --exclusive-host")
    env = {**os.environ, **config}
    if args.action == "env":
        print(json.dumps(config, indent=2, sort_keys=True))
    elif args.action == "run":
        if not command:
            parser.error("run requires -- command [arguments]")
        # Pass only routing. Never start Docker, create resources, pull models or bootstrap.
        subprocess.run(command, env=env, check=True)
    elif args.action == "up":
        owned_containers(config)  # refuse adoption of an unrelated reserved project
        validate_volumes(config)
        compose_files = ["-f", str(ROOT / "infra/compose/shared.yml")]
        if args.backend == "ollama":
            compose_files += ["-f", str(ROOT / "infra/compose/ollama.yml")]
        subprocess.run([
            "docker", "compose", "--env-file", "/dev/null", "-p",
            config["FETCHCORE_INFRA_PROJECT"], *compose_files,
            "up", "-d", args.backend,
        ], env=env, check=True)
    else:
        project = config["FETCHCORE_INFRA_PROJECT"]
        if not args.dedicated or args.environment != "bench" or args.confirm_project != project:
            parser.error("cleanup requires dedicated bench and exact --confirm-project")
        ids = owned_containers(config)
        # No compose down, volume deletion, logical-resource deletion or global prune.
        for container in ids:
            subprocess.run(["docker", "rm", "-f", container], check=True)


if __name__ == "__main__":
    main()
