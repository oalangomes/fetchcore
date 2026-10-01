#!/usr/bin/env python3
"""Run the frozen FetchCore baseline v1 against an exact local corpus checkout."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping
from urllib.request import Request, urlopen

from fetchcore.application import BackendRegistry
from fetchcore.backends.opensearch import (
    OpenSearchIndexStore,
    OpenSearchSettings,
    UrllibOpenSearchTransport,
    opensearch_embedding_provider,
)
from fetchcore.backends.qdrant_vector import (
    QdrantVectorIndexStore,
    QdrantVectorSettings,
    UrllibQdrantTransport,
)
from fetchcore.backends.solr import (
    SolrIndexStore,
    SolrSettings,
    UrllibSolrTransport,
    solr_embedding_provider,
)
from fetchcore.baseline import (
    BASELINE_V1_ARMS,
    BaselineArm,
    BaselineConfigurationError,
    BaselineRunner,
    capture_baseline_environment,
    load_baseline_suite,
    mapping_fingerprint,
    write_baseline_artifact,
)
from fetchcore.embedding import OllamaEmbeddingProvider, OllamaEmbeddingSettings
from fetchcore.repository.index_operations import prepare_repository_index_runtime
from fetchcore.retrieval import RetrievalConfig
from fetchcore.search import SearchScope


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "manifest.json"
GROUND_TRUTH_PATH = ROOT / "ground-truth.json"
ARTIFACTS_DIR = ROOT / "artifacts"


def main() -> int:
    args = _parser().parse_args()
    manifest = _load_json(MANIFEST_PATH)
    suite = load_baseline_suite(GROUND_TRUTH_PATH)
    workspace = args.workspace.resolve(strict=True)

    expected_repository = str(suite.corpus["repository"])
    expected_revision = str(suite.corpus["revision"])
    _validate_workspace(workspace, expected_revision)
    suite.validate_workspace_regions(workspace)

    measurement = _object(manifest, "measurement")
    environment_config = _object(manifest, "environment")
    endpoints = _object(environment_config, "service_endpoints")
    embedding_identity = _object(environment_config, "embedding")
    backend_settings = _object(manifest, "backend_settings")
    os.environ["OLLAMA_URL"] = str(endpoints["ollama"])

    selected = _select_arms(args.arm)
    service_versions = _service_versions(selected, endpoints)
    environment = capture_baseline_environment(
        str(environment_config["profile_id"]),
        services=service_versions,
        embedding=embedding_identity,
    ).to_mapping()

    fetchcore_revision = _git_revision(ROOT.parents[1])
    config_identity = {
        "fetchcore_revision": fetchcore_revision,
        "manifest_fingerprint": mapping_fingerprint(manifest),
        "corpus_revision": expected_revision,
    }

    output_root = args.output.resolve()
    run_version, output_dir = _allocate_run_output_dir(output_root)
    print(f"[baseline] run_version={run_version} output={output_dir}")

    indexing_payload: dict[str, object] | None = None
    if args.prepare_index:
        indexing_payload = _prepare_indexes(
            selected,
            workspace=workspace,
            repository=expected_repository,
            revision=expected_revision,
            endpoints=endpoints,
            embedding_identity=embedding_identity,
            backend_settings=backend_settings,
            environment=environment,
            fetchcore_revision=fetchcore_revision,
        )

    for arm in selected:
        backend = BackendRegistry().create(arm.backend)
        retrieval = _retrieval_for_mode(
            arm.mode,
            provider=str(embedding_identity["provider"]),
            model=str(embedding_identity["model"]),
        )
        settings = _backend_settings(
            arm,
            endpoints=endpoints,
            diagnostic_candidate_window=int(
                str(measurement["diagnostic_candidate_window"])
            ),
            embedding_identity=embedding_identity,
            backend_settings=backend_settings,
        )
        scope = SearchScope(
            workspace=workspace,
            revision=expected_revision,
            repository=expected_repository,
            knowledge_types=(
                "code",
                "tests",
                "documentation",
                "configuration",
            ),
            max_results=int(str(measurement["max_results"])),
            settings=settings,
            retrieval=retrieval,
        )
        artifact = BaselineRunner(
            backend=backend,
            arm=arm,
            scope=scope,
            repetitions=int(str(measurement["repetitions"])),
            warmups=int(str(measurement["warmups"])),
            k=int(str(measurement["k"])),
        ).run(
            suite.cases,
            corpus=suite.corpus,
            ground_truth_fingerprint=suite.fingerprint,
            config_identity={
                **config_identity,
                "backend_settings": settings,
            },
            environment=environment,
        )
        destination = write_baseline_artifact(
            output_dir / f"{arm.id}.json",
            artifact,
        )
        print(f"[baseline] arm={arm.id} artifact={destination}")

    if indexing_payload is not None:
        destination = output_dir / "indexing.json"
        destination.write_text(
            json.dumps(indexing_payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[baseline] indexing artifact={destination}")

    run_manifest = {
        "schema_version": 1,
        "run_version": run_version,
        "protocol": "fetchcore.baseline-v1",
        "status": "complete",
        "completed_at": _utc_now(),
        "fetchcore_revision": fetchcore_revision,
        "corpus": suite.corpus,
        "arms": [arm.to_mapping() for arm in selected],
        "prepare_index": bool(args.prepare_index),
        "artifacts": [f"{arm.id}.json" for arm in selected],
        "indexing_artifact": (
            "indexing.json" if indexing_payload is not None else None
        ),
    }
    run_manifest_path = output_dir / "run.json"
    run_manifest_path.write_text(
        json.dumps(run_manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"[baseline] run_version={run_version} "
        f"manifest={run_manifest_path}"
    )

    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="Local checkout of the frozen NeuroTrack_MS corpus.",
    )
    parser.add_argument(
        "--arm",
        action="append",
        default=[],
        help="Baseline arm id. Repeat to run multiple arms; omitted means all.",
    )
    parser.add_argument(
        "--prepare-index",
        action="store_true",
        help="Rebuild remote backend indexes before measuring retrieval.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ARTIFACTS_DIR,
        help=(
            "Artifact output root. Each execution creates the next "
            "V0.N child directory automatically."
        ),
    )
    return parser


def _allocate_run_output_dir(output_root: Path) -> tuple[str, Path]:
    """Reserve the next monotonic V0.N directory for one benchmark execution."""

    output_root.mkdir(parents=True, exist_ok=True)
    prefix = "V0."
    observed_versions: list[int] = []
    for entry in output_root.iterdir():
        if not entry.is_dir() or not entry.name.startswith(prefix):
            continue
        suffix = entry.name[len(prefix):]
        if suffix.isdigit():
            observed_versions.append(int(suffix))

    version_number = max(observed_versions, default=0) + 1
    while True:
        run_version = f"{prefix}{version_number}"
        output_dir = output_root / run_version
        try:
            output_dir.mkdir()
        except FileExistsError:
            version_number += 1
            continue
        return run_version, output_dir


def _select_arms(requested: list[str]) -> tuple[BaselineArm, ...]:
    available = {arm.id: arm for arm in BASELINE_V1_ARMS}
    if not requested:
        return BASELINE_V1_ARMS
    unknown = sorted(set(requested) - available.keys())
    if unknown:
        raise BaselineConfigurationError(
            f"unknown baseline arms: {', '.join(unknown)}"
        )
    return tuple(available[item] for item in requested)


def _validate_workspace(workspace: Path, expected_revision: str) -> None:
    actual = _git_revision(workspace)
    if actual != expected_revision:
        raise BaselineConfigurationError(
            "baseline corpus revision mismatch: "
            f"expected {expected_revision}, got {actual}"
        )
    status = _git(workspace, "status", "--porcelain")
    if status.strip():
        raise BaselineConfigurationError(
            "baseline corpus workspace must have a clean working tree"
        )


def _prepare_indexes(
    arms: tuple[BaselineArm, ...],
    *,
    workspace: Path,
    repository: str,
    revision: str,
    endpoints: Mapping[str, object],
    embedding_identity: Mapping[str, object],
    backend_settings: Mapping[str, object],
    environment: Mapping[str, object],
    fetchcore_revision: str,
) -> dict[str, object]:
    runtime = prepare_repository_index_runtime(
        workspace,
        repository=repository,
        branch=None,
        revision=revision,
    )
    canonical = runtime.indexer.index(workspace)
    payload: dict[str, object] = {
        "schema_version": 1,
        "protocol": "fetchcore.baseline-v1",
        "fetchcore_revision": fetchcore_revision,
        "corpus": {
            "repository": repository,
            "revision": revision,
        },
        "environment": dict(environment),
        "indexing_fingerprint": runtime.fingerprint,
        "canonical": canonical.telemetry.to_mapping(),
        "backends": {},
    }
    backends = payload["backends"]
    assert isinstance(backends, dict)

    ids = {arm.id for arm in arms}
    dense_retrieval = _retrieval_for_mode(
        "hybrid",
        provider=str(embedding_identity["provider"]),
        model=str(embedding_identity["model"]),
    )

    if ids & {"solr-lexical", "solr-dense", "solr-hybrid"}:
        solr_vectors = bool(ids & {"solr-dense", "solr-hybrid"})
        settings = SolrSettings.from_mapping(
            {
                **_object(backend_settings, "solr"),
                "url": endpoints["solr"],
                "diagnostic_candidate_window": 80,
            }
        )
        store = SolrIndexStore(
            UrllibSolrTransport(settings),
            settings,
            embedding_provider=(
                solr_embedding_provider(dense_retrieval)
                if solr_vectors
                else None
            ),
        )
        # Solr intentionally owns schema validation, not schema creation.
        store.validate_schema(require_vectors=solr_vectors)
        result = store.full_rebuild(canonical)
        backends["solr"] = result.to_mapping()

    if ids & {"opensearch-lexical", "opensearch-dense", "opensearch-hybrid"}:
        opensearch_vectors = bool(ids & {"opensearch-dense", "opensearch-hybrid"})
        settings = OpenSearchSettings.from_mapping(
            {
                **_object(backend_settings, "opensearch"),
                "url": endpoints["opensearch"],
                "diagnostic_candidate_window": 80,
            }
        )
        store = OpenSearchIndexStore(
            UrllibOpenSearchTransport(settings),
            settings,
            clock=_utc_now,
            embedding_provider=(
                opensearch_embedding_provider(dense_retrieval)
                if opensearch_vectors
                else None
            ),
        )
        result = store.full_rebuild(canonical)
        backends["opensearch"] = result.to_mapping()

    if ids & {"qdrant-dense", "qdrant-hybrid"}:
        settings = QdrantVectorSettings.from_mapping(
            {
                **_object(backend_settings, "qdrant-vector"),
                "url": endpoints["qdrant"],
                "embedding_url": endpoints["ollama"],
                "embedding_model": embedding_identity["model"],
                "diagnostic_candidate_window": 80,
            }
        )
        embedder = OllamaEmbeddingProvider(
            OllamaEmbeddingSettings(
                url=settings.embedding_url,
                model=settings.embedding_model,
                timeout_seconds=settings.timeout_seconds,
                request_batch_size=settings.batch_size,
            )
        )
        store = QdrantVectorIndexStore(
            UrllibQdrantTransport(settings),
            settings,
            embedder,
            clock=_utc_now,
        )
        result = store.full_rebuild(canonical)
        backends["qdrant"] = result.to_mapping()

    return payload


def _retrieval_for_mode(
    mode: str,
    *,
    provider: str,
    model: str,
) -> RetrievalConfig | None:
    if mode == "lexical":
        return None
    if mode == "dense":
        return RetrievalConfig.from_mapping(
            {
                "dense": {
                    "enabled": True,
                    "embedding": {"provider": provider, "model": model},
                }
            }
        )
    if mode == "hybrid":
        return RetrievalConfig.from_mapping(
            {
                "dense": {
                    "enabled": True,
                    "embedding": {"provider": provider, "model": model},
                },
                "hybrid": {"enabled": True, "strategy": "rrf"},
            }
        )
    raise BaselineConfigurationError(f"unsupported baseline mode {mode!r}")


def _backend_settings(
    arm: BaselineArm,
    *,
    endpoints: Mapping[str, object],
    diagnostic_candidate_window: int,
    embedding_identity: Mapping[str, object],
    backend_settings: Mapping[str, object],
) -> dict[str, object]:
    if arm.backend == "local-semantic":
        return dict(_object(backend_settings, "local-semantic"))
    if arm.backend == "solr":
        return {
            **_object(backend_settings, "solr"),
            "url": endpoints["solr"],
            "diagnostic_candidate_window": diagnostic_candidate_window,
        }
    if arm.backend == "opensearch":
        return {
            **_object(backend_settings, "opensearch"),
            "url": endpoints["opensearch"],
            "diagnostic_candidate_window": diagnostic_candidate_window,
        }
    if arm.backend == "qdrant-vector":
        return {
            **_object(backend_settings, "qdrant-vector"),
            "url": endpoints["qdrant"],
            "embedding_url": endpoints["ollama"],
            "embedding_model": embedding_identity["model"],
            "diagnostic_candidate_window": diagnostic_candidate_window,
        }
    raise BaselineConfigurationError(
        f"no benchmark settings for backend {arm.backend!r}"
    )


def _service_versions(
    arms: tuple[BaselineArm, ...],
    endpoints: Mapping[str, object],
) -> dict[str, str]:
    ids = {arm.id for arm in arms}
    versions: dict[str, str] = {}
    if ids & {"solr-lexical", "solr-dense", "solr-hybrid"}:
        payload = _http_json(f"{str(endpoints['solr']).rstrip('/')}/admin/info/system?wt=json")
        lucene = payload.get("lucene")
        versions["solr"] = (
            str(lucene.get("solr-spec-version", "unknown"))
            if isinstance(lucene, Mapping)
            else "unknown"
        )
    if ids & {"opensearch-lexical", "opensearch-dense", "opensearch-hybrid"}:
        payload = _http_json(str(endpoints["opensearch"]))
        version = payload.get("version")
        versions["opensearch"] = (
            str(version.get("number", "unknown"))
            if isinstance(version, Mapping)
            else "unknown"
        )
    if ids & {"qdrant-dense", "qdrant-hybrid"}:
        payload = _http_json(str(endpoints["qdrant"]))
        versions["qdrant"] = str(payload.get("version", "unknown"))
    if any(arm.mode in {"dense", "hybrid"} for arm in arms):
        payload = _http_json(f"{str(endpoints['ollama']).rstrip('/')}/api/version")
        versions["ollama"] = str(payload.get("version", "unknown"))
    return versions


def _http_json(url: str) -> dict[str, object]:
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read()
    except OSError as exc:
        raise BaselineConfigurationError(
            f"baseline service probe failed for {url}: {exc}"
        ) from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaselineConfigurationError(
            f"baseline service probe returned invalid JSON for {url}"
        ) from exc
    if not isinstance(value, dict):
        raise BaselineConfigurationError(
            f"baseline service probe returned non-object JSON for {url}"
        )
    return {str(key): item for key, item in value.items()}


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BaselineConfigurationError(f"{path} must contain a JSON object")
    return {str(key): item for key, item in value.items()}


def _object(value: Mapping[str, object], key: str) -> dict[str, object]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise BaselineConfigurationError(f"{key} must be a JSON object")
    return {str(name): entry for name, entry in item.items()}


def _git_revision(workspace: Path) -> str:
    return _git(workspace, "rev-parse", "HEAD").strip()


def _git(workspace: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ("git", *args),
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BaselineConfigurationError(
            f"git {' '.join(args)} failed in {workspace}"
        ) from exc
    return completed.stdout


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
