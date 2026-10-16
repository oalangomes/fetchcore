"""Explicit local infrastructure routing; never called by retrieval or CI implicitly."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OWNER = "fetchcore-shared-infra-v1"
BACKENDS = {"solr": "solr", "opensearch": "opensearch", "qdrant": "qdrant-vector"}


def index_settings(config: dict[str, str]) -> str:
    """Keep indexing pointed at the selected backend, including unsupported ones."""
    backend = config["FETCHCORE_BACKEND"]
    prefix = {"solr": "SOLR", "opensearch": "OPENSEARCH", "qdrant-vector": "QDRANT"}[backend]
    field = "index" if backend == "opensearch" else "collection"
    return json.dumps({
        "url": config[f"FETCHCORE_{prefix}_URL"],
        field: config[f"FETCHCORE_{prefix}_{field.upper()}"],
        "projection_version": "3",
    })


def route(
    environment: str,
    repository: str,
    backend: str = "solr",
    dedicated: bool = False,
    ollama_url: str = "http://host.docker.internal:11434",
) -> dict[str, str]:
    if environment not in {"dev", "bench", "operational"}:
        raise ValueError("unknown environment")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*", repository):
        raise ValueError("repository must be canonical lowercase owner/name")
    if backend not in BACKENDS:
        raise ValueError("unknown retrieval backend")
    if dedicated and environment != "bench":
        raise ValueError("dedicated mode is only available for bench")
    if not isinstance(ollama_url, str):
        raise ValueError("Ollama endpoint must be an HTTP(S) URL")
    parsed_ollama = urlsplit(ollama_url)
    if (parsed_ollama.scheme not in {"http", "https"} or not parsed_ollama.hostname
            or parsed_ollama.username or parsed_ollama.password or parsed_ollama.query
            or parsed_ollama.fragment):
        raise ValueError("Ollama endpoint must be an HTTP(S) URL without credentials/query/fragment")
    instance = "bench-dedicated" if dedicated else (
        "operational" if environment == "operational" else "shared"
    )
    offset = {"shared": 10000, "bench-dedicated": 20000, "operational": 30000}[instance]
    digest = hashlib.sha256(repository.encode()).hexdigest()[:16]
    resource = f"fc-{environment}-{repository.split('/')[-1][:24]}-{digest}"
    config = {
        "FETCHCORE_INFRA_PROJECT": f"fetchcore-{instance}",
        "FETCHCORE_INFRA_OWNER": OWNER,
        "FETCHCORE_ENVIRONMENT": environment,
        "FETCHCORE_REPOSITORY": repository,
        "FETCHCORE_SOLR_PORT": str(8983 + offset),
        "OPENSEARCH_PORT": str(9200 + offset),
        "OPENSEARCH_METRICS_PORT": str(9600 + offset),
        "QDRANT_HTTP_PORT": str(6333 + offset),
        "QDRANT_GRPC_PORT": str(6334 + offset),
        "FETCHCORE_SOLR_URL": f"http://127.0.0.1:{8983 + offset}/solr",
        "FETCHCORE_OPENSEARCH_URL": f"http://127.0.0.1:{9200 + offset}",
        "FETCHCORE_QDRANT_URL": f"http://127.0.0.1:{6333 + offset}",
        "FETCHCORE_OLLAMA_URL": ollama_url.rstrip("/"),
        "FETCHCORE_BACKEND": BACKENDS[backend],
        "FETCHCORE_SOLR_COLLECTION": resource,
        "FETCHCORE_OPENSEARCH_INDEX": resource,
        "FETCHCORE_QDRANT_COLLECTION": resource,
        "FETCHCORE_INDEX_BACKEND": BACKENDS[backend],
    }
    config["FETCHCORE_INDEX_BACKEND_SETTINGS"] = index_settings(config)
    return config


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


def shared_endpoints(config: dict[str, str], path: Path | None) -> set[str]:
    """Route shared consumers to operator-managed services without adopting them."""
    if path is None:
        return set()
    endpoints = json.loads(path.read_text())
    if not isinstance(endpoints, dict) or set(endpoints) - {
        "solr", "opensearch", "qdrant", "ollama"
    }:
        raise ValueError("shared endpoints must map known backend names to URLs")
    for backend, url in endpoints.items():
        if not isinstance(url, str):
            raise ValueError("endpoint must be an HTTP(S) URL")
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("endpoint must be an HTTP(S) URL without credentials/query/fragment")
        # Reserved dedicated/operational ports cannot become shared endpoints,
        # including through host aliases. External services remain operator-owned.
        if parsed.port in {28983, 29200, 29600, 26333, 26334,
                           38983, 39200, 39600, 36333, 36334}:
            raise ValueError("shared endpoint uses a reserved isolated-service port")
        if backend == "solr" and parsed.path.rstrip("/") != "/solr":
            raise ValueError("Solr endpoint must include /solr")
    if config["FETCHCORE_INFRA_PROJECT"] != "fetchcore-shared":
        return set()
    for backend, url in endpoints.items():
        config[f"FETCHCORE_{backend.upper()}_URL"] = url.rstrip("/")
    config["FETCHCORE_INDEX_BACKEND_SETTINGS"] = index_settings(config)
    return set(endpoints)


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


def wait_ready(backend: str, config: dict[str, str], timeout: float = 180.0) -> dict[str, str]:
    """Wait for an HTTP-level readiness endpoint without provisioning resources."""
    endpoint = {
        "solr": f"{config['FETCHCORE_SOLR_URL']}/admin/info/system?wt=json",
        "opensearch": f"{config['FETCHCORE_OPENSEARCH_URL']}/_cluster/health",
        "qdrant": f"{config['FETCHCORE_QDRANT_URL']}/readyz",
    }[backend]
    deadline = time.monotonic() + timeout
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            request = Request(endpoint, headers={"Accept": "application/json"})
            with urlopen(request, timeout=3) as response:
                body = response.read()
            if backend == "opensearch":
                health = json.loads(body.decode("utf-8"))
                if health.get("status") not in {"green", "yellow"}:
                    raise RuntimeError(f"OpenSearch cluster status is {health.get('status')!r}")
            return {"backend": backend, "endpoint": endpoint, "status": "ready"}
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = str(exc)
            time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
    raise RuntimeError(f"{backend} readiness timed out at {endpoint}: {last_error}")


def _opensearch_resource(config: dict[str, str], repository: str, create: bool) -> dict[str, str]:
    from fetchcore.adapters.retrieval.opensearch.backend import (
        OpenSearchSettings,
        UrllibOpenSearchTransport,
        OPENSEARCH_VECTOR_DIMENSIONS,
        _index_mapping,
        _repository_alias,
    )
    from fetchcore.adapters.retrieval.opensearch.query import OPENSEARCH_VECTOR_FIELD
    from fetchcore.retrieval.contracts import KnowledgeSearchError

    settings = OpenSearchSettings.from_mapping({
        "url": config["FETCHCORE_OPENSEARCH_URL"],
        "index": config["FETCHCORE_OPENSEARCH_INDEX"],
    })
    transport = UrllibOpenSearchTransport(settings)
    health = transport.request("GET", "_cluster/health")
    if health.get("status") not in {"green", "yellow"}:
        raise RuntimeError(f"OpenSearch is not ready: {health.get('status')!r}")

    alias = _repository_alias(settings, repository)
    expected = _index_mapping(include_vectors=False)

    def contains_mapping(expected_value: object, actual_value: object) -> bool:
        if isinstance(expected_value, dict):
            return isinstance(actual_value, dict) and all(
                key in actual_value and contains_mapping(value, actual_value[key])
                for key, value in expected_value.items()
            )
        return expected_value == actual_value

    def validate_index(index: str) -> list[str]:
        mappings = transport.request("GET", f"{quote(index, safe='')}/_mapping")
        index_data = mappings.get(index)
        raw_mappings = index_data.get("mappings") if isinstance(index_data, dict) else None
        expected_mappings = expected.get("mappings")
        if (not isinstance(raw_mappings, dict) or not isinstance(expected_mappings, dict)
                or not contains_mapping(expected_mappings, raw_mappings)):
            raise ValueError(f"OpenSearch index {index} has no compatible Projection v3 mapping")
        properties = raw_mappings.get("properties")
        vector = properties.get(OPENSEARCH_VECTOR_FIELD) if isinstance(properties, dict) else None
        if vector is not None and (
            not isinstance(vector, dict)
            or vector.get("type") != "knn_vector"
            or vector.get("dimension") != OPENSEARCH_VECTOR_DIMENSIONS
        ):
            raise ValueError(f"OpenSearch index {index} has an incompatible vector mapping")
        capabilities = ["lexical"]
        if vector is not None:
            capabilities.extend(("dense", "hybrid"))
        return capabilities

    try:
        aliases = transport.request("GET", f"_alias/{quote(alias, safe='')}")
    except KnowledgeSearchError as exc:
        if exc.category != "index_missing":
            raise
        aliases = {}
    if aliases:
        indices = sorted(aliases)
        if len(indices) != 1:
            raise ValueError(f"OpenSearch alias {alias} must resolve to exactly one index")
        capabilities = validate_index(indices[0])
        return {"backend": "opensearch", "resource": alias, "state": "reused",
                "physical_resource": indices[0], "capabilities": ",".join(capabilities)}
    if not create:
        raise ValueError(f"OpenSearch resource {alias} is absent; use --create-if-missing")

    physical = f"{alias}-infra-v3"
    try:
        transport.request("GET", quote(physical, safe=""))
    except KnowledgeSearchError as exc:
        if exc.category != "index_missing":
            raise
    else:
        raise ValueError(f"refusing to adopt unaliased OpenSearch index {physical}")
    transport.request("PUT", quote(physical, safe=""), {
        **expected,
        "aliases": {alias: {"is_write_index": True}},
    })
    aliases = transport.request("GET", f"_alias/{quote(alias, safe='')}")
    if sorted(aliases) != [physical]:
        raise RuntimeError(f"OpenSearch failed to activate expected alias {alias}")
    capabilities = validate_index(physical)
    return {"backend": "opensearch", "resource": alias, "state": "created",
            "physical_resource": physical, "capabilities": ",".join(capabilities)}


def _qdrant_resource(
    config: dict[str, str],
    repository: str,
    create: bool,
    vector_dimensions: int | None,
) -> dict[str, str]:
    from fetchcore.adapters.retrieval.qdrant.backend import (
        QdrantVectorSettings,
        UrllibQdrantTransport,
        _repository_alias,
    )
    from fetchcore.adapters.retrieval.qdrant.sparse import SPARSE_MODIFIER, SPARSE_VECTOR_NAME

    if vector_dimensions is None or vector_dimensions < 1:
        raise ValueError("Qdrant prepare requires positive --vector-dimensions")
    settings = QdrantVectorSettings.from_mapping({
        "url": config["FETCHCORE_QDRANT_URL"],
        "collection": config["FETCHCORE_QDRANT_COLLECTION"],
    })
    transport = UrllibQdrantTransport(settings)
    transport.request("GET", "collections")
    alias = _repository_alias(settings, repository)
    concrete = f"{alias}-infra-v3"

    def aliases_for_resource() -> list[dict[str, object]]:
        response = transport.request("GET", "aliases")
        result = response.get("result")
        aliases = result.get("aliases") if isinstance(result, dict) else None
        if not isinstance(aliases, list):
            raise ValueError("Qdrant returned an invalid alias inventory")
        return [item for item in aliases if isinstance(item, dict)
                and item.get("alias_name") == alias]

    def validate_collection(collection: str) -> None:
        response = transport.request("GET", f"collections/{quote(collection, safe='')}")
        result = response.get("result")
        config_data = result.get("config") if isinstance(result, dict) else None
        params = config_data.get("params") if isinstance(config_data, dict) else None
        vectors = params.get("vectors") if isinstance(params, dict) else None
        sparse_vectors = params.get("sparse_vectors") if isinstance(params, dict) else None
        dense = (
            vectors.get("")
            if isinstance(vectors, dict) and "" in vectors
            else vectors if isinstance(vectors, dict) and "size" in vectors else None
        )
        if not isinstance(dense, dict) or dense.get("size") != vector_dimensions:
            raise ValueError(
                f"Qdrant collection {collection} vector dimensions do not match "
                f"{vector_dimensions}"
            )
        if dense.get("distance") != "Cosine":
            raise ValueError(f"Qdrant collection {collection} has incompatible vector distance")
        if not isinstance(sparse_vectors, dict) or SPARSE_VECTOR_NAME not in sparse_vectors:
            raise ValueError(f"Qdrant collection {collection} lacks FetchCore sparse-vector schema")
        sparse = sparse_vectors[SPARSE_VECTOR_NAME]
        if not isinstance(sparse, dict) or sparse.get("modifier") != SPARSE_MODIFIER:
            raise ValueError(f"Qdrant collection {collection} has incompatible sparse modifier")

    bindings = aliases_for_resource()
    if bindings:
        if len(bindings) != 1:
            raise ValueError(f"Qdrant alias {alias} must resolve to exactly one collection")
        collection = str(bindings[0].get("collection_name", ""))
        if not collection:
            raise ValueError(f"Qdrant alias {alias} has no collection binding")
        validate_collection(collection)
        return {"backend": "qdrant", "resource": alias, "state": "reused",
                "physical_resource": collection,
                "vector_dimensions": str(vector_dimensions or "validated")}
    if not create:
        raise ValueError(f"Qdrant resource {alias} is absent; use --create-if-missing")
    if vector_dimensions is None:
        raise ValueError("Qdrant creation requires --vector-dimensions")

    collections = transport.request("GET", "collections").get("result")
    collection_items = collections.get("collections") if isinstance(collections, dict) else None
    if not isinstance(collection_items, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str)
        for item in collection_items
    ):
        raise ValueError("Qdrant returned an invalid collection inventory")
    existing_names = {
        str(item.get("name")) for item in collection_items if isinstance(item, dict)
    }
    if concrete in existing_names:
        raise ValueError(f"refusing to adopt unaliased Qdrant collection {concrete}")
    transport.request("PUT", f"collections/{quote(concrete, safe='')}", {
        "vectors": {"size": vector_dimensions, "distance": "Cosine"},
        "sparse_vectors": {
            SPARSE_VECTOR_NAME: {"modifier": SPARSE_MODIFIER},
        },
    })
    transport.request("POST", "collections/aliases", {
        "actions": [{"create_alias": {"collection_name": concrete, "alias_name": alias}}],
    })
    bindings = aliases_for_resource()
    if len(bindings) != 1 or bindings[0].get("collection_name") != concrete:
        raise RuntimeError(f"Qdrant failed to activate expected alias {alias}")
    validate_collection(concrete)
    return {"backend": "qdrant", "resource": alias, "state": "created",
            "physical_resource": concrete, "vector_dimensions": str(vector_dimensions)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["env", "ready", "up", "prepare", "run", "cleanup"])
    parser.add_argument("--environment", choices=["dev", "bench", "operational"], required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--dedicated", action="store_true")
    parser.add_argument("--performance", action="store_true")
    parser.add_argument("--exclusive-host", action="store_true",
                        help="operator attests this host has no competing workload")
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="solr")
    parser.add_argument("--confirm-project")
    parser.add_argument("--shared-endpoints", type=Path,
                        help="Explicit JSON of existing shared backend URLs; never adopted by up")
    parser.add_argument("--ollama-url", default="http://host.docker.internal:11434",
                        help="Existing Ollama endpoint; this tool never starts Ollama")
    parser.add_argument("--create-if-missing", action="store_true",
                        help="Explicitly create an absent OpenSearch/Qdrant resource")
    parser.add_argument("--vector-dimensions", type=int,
                        help="Required when explicitly creating a Qdrant collection")
    args, command = parser.parse_known_args()
    if command[:1] == ["--"]:
        command = command[1:]
    if command and args.action != "run":
        parser.error("extra command is only valid for run")
    if args.action == "prepare" and args.backend == "solr":
        parser.error("Solr resources use the existing Solr runtime; --backend must be opensearch or qdrant")
    if args.create_if_missing and args.action != "prepare":
        parser.error("--create-if-missing is only valid for prepare")
    if args.action == "prepare" and args.backend == "qdrant" and args.vector_dimensions is None:
        parser.error("Qdrant prepare requires explicit --vector-dimensions")
    if args.vector_dimensions is not None and (
        args.action != "prepare" or args.backend != "qdrant"
    ):
        parser.error("--vector-dimensions is only valid for Qdrant prepare")
    if args.vector_dimensions is not None and args.vector_dimensions < 1:
        parser.error("--vector-dimensions must be positive")
    config = route(args.environment, args.repository, args.backend, args.dedicated, args.ollama_url)
    external = shared_endpoints(config, args.shared_endpoints)
    if args.performance and not (
        args.environment == "bench" and args.dedicated and args.exclusive_host
    ):
        parser.error("performance requires bench --dedicated --exclusive-host")
    env = {**os.environ, **config}
    if args.action == "env":
        print(json.dumps(config, indent=2, sort_keys=True))
    elif args.action == "ready":
        print(json.dumps(wait_ready(args.backend, config), indent=2, sort_keys=True))
    elif args.action == "run":
        if not command:
            parser.error("run requires -- command [arguments]")
        # Pass only routing. Never start Docker, create resources, pull models or bootstrap.
        subprocess.run(command, env=env, check=True)
    elif args.action == "up":
        if args.backend in external:
            parser.error("selected backend is externally managed; use run for read-only readiness")
        owned_containers(config)  # refuse adoption of an unrelated reserved project
        validate_volumes(config)
        compose_files = ["-f", str(ROOT / "infra/compose/shared.yml")]
        if args.backend == "ollama":
            compose_files += ["-f", str(ROOT / "infra/compose/ollama.yml")]
        subprocess.run([
            "docker", "compose", "--env-file", "/dev/null", "-p",
            config["FETCHCORE_INFRA_PROJECT"], *compose_files,
            "up", "-d", "--no-recreate", args.backend,
        ], env=env, check=True)
        print(json.dumps(wait_ready(args.backend, config), indent=2, sort_keys=True))
    elif args.action == "prepare":
        if args.backend == "opensearch":
            result = _opensearch_resource(config, args.repository, args.create_if_missing)
        else:
            result = _qdrant_resource(
                config, args.repository, args.create_if_missing, args.vector_dimensions,
            )
        print(json.dumps(result, indent=2, sort_keys=True))
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
