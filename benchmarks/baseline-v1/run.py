#!/usr/bin/env python3
"""Run the frozen FetchCore baseline v1 against an exact local corpus checkout."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.request import Request, urlopen

from fetchcore.application import BackendRegistry
from fetchcore.application.composition import EmbeddingProviderRegistry
from fetchcore.adapters.embedding.ollama.provider import OllamaEmbeddingProvider, OllamaEmbeddingSettings
from fetchcore.adapters.retrieval.opensearch.backend import (
    OpenSearchIndexStore,
    OpenSearchSettings,
    UrllibOpenSearchTransport,
)
from fetchcore.adapters.retrieval.qdrant.backend import (
    QdrantVectorIndexStore,
    QdrantVectorSettings,
    UrllibQdrantTransport,
)
from fetchcore.adapters.retrieval.solr.backend import (
    SolrIndexStore,
    SolrSettings,
    UrllibSolrTransport,
)
from fetchcore.baseline import (
    BASELINE_ARTIFACT_SCHEMA_VERSION,
    BASELINE_GROUND_TRUTH_SCHEMA_VERSION,
    BASELINE_METRICS_SCHEMA_VERSION,
    BASELINE_V1_METRICS_SPEC,
    BaselineArm,
    BaselineConfigurationError,
    BaselineRetrievalMode,
    BaselineRunner,
    BaselineSuite,
    capture_baseline_environment,
    load_baseline_suite,
    mapping_fingerprint,
    write_baseline_artifact,
)
from fetchcore.application.index_operations import prepare_repository_index_runtime
from fetchcore.repository.indexing import RepositoryIndexResult
from fetchcore.retrieval import RetrievalConfig
from fetchcore.retrieval.contracts import SearchScope


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "manifest.json"
GROUND_TRUTH_PATH = ROOT / "ground-truth.json"
ARTIFACTS_DIR = ROOT / "artifacts"
RUN_STATE_FILE = ".run-state.json"


@dataclass(frozen=True)
class BenchmarkBackendContext:
    """Backend-specific benchmark inputs resolved from the suite manifest."""

    endpoints: Mapping[str, object]
    embedding_identity: Mapping[str, object]
    settings: Mapping[str, object]
    diagnostic_candidate_window: int


class BenchmarkBackendAdapter(Protocol):
    """Thin backend integration boundary used only by the benchmark runner."""

    backend_id: str
    indexing_artifact_key: str | None

    def build_settings(self, context: BenchmarkBackendContext) -> dict[str, object]: ...

    def probe_service_versions(
        self,
        context: BenchmarkBackendContext,
    ) -> dict[str, str]: ...

    def prepare_index(
        self,
        canonical: RepositoryIndexResult,
        *,
        modes: frozenset[BaselineRetrievalMode],
        retrieval: RetrievalConfig,
        context: BenchmarkBackendContext,
    ) -> dict[str, object] | None: ...


class LocalBenchmarkAdapter:
    backend_id = "local-semantic"
    indexing_artifact_key: str | None = None

    def build_settings(self, context: BenchmarkBackendContext) -> dict[str, object]:
        return dict(_object(context.settings, self.backend_id))

    def probe_service_versions(
        self,
        context: BenchmarkBackendContext,
    ) -> dict[str, str]:
        return {}

    def prepare_index(
        self,
        canonical: RepositoryIndexResult,
        *,
        modes: frozenset[BaselineRetrievalMode],
        retrieval: RetrievalConfig,
        context: BenchmarkBackendContext,
    ) -> dict[str, object] | None:
        return None


class SolrBenchmarkAdapter:
    backend_id = "solr"
    indexing_artifact_key: str | None = "solr"

    def build_settings(self, context: BenchmarkBackendContext) -> dict[str, object]:
        return {
            **_object(context.settings, self.backend_id),
            "url": context.endpoints["solr"],
            "diagnostic_candidate_window": context.diagnostic_candidate_window,
        }

    def probe_service_versions(
        self,
        context: BenchmarkBackendContext,
    ) -> dict[str, str]:
        payload = _http_json(
            f"{str(context.endpoints['solr']).rstrip('/')}/admin/info/system?wt=json"
        )
        lucene = payload.get("lucene")
        return {
            "solr": (
                str(lucene.get("solr-spec-version", "unknown"))
                if isinstance(lucene, Mapping)
                else "unknown"
            )
        }

    def prepare_index(
        self,
        canonical: RepositoryIndexResult,
        *,
        modes: frozenset[BaselineRetrievalMode],
        retrieval: RetrievalConfig,
        context: BenchmarkBackendContext,
    ) -> dict[str, object]:
        vectors = bool(modes & {"dense", "hybrid"})
        settings = SolrSettings.from_mapping(self.build_settings(context))
        store = SolrIndexStore(
            UrllibSolrTransport(settings),
            settings,
            embedding_provider=(
                _embedding_providers(context).create(
                    retrieval.dense.embedding,
                    backend_id=self.backend_id,
                    backend_name="Solr",
                )
                if vectors else None
            ),
        )
        # Solr intentionally owns schema validation, not schema creation.
        store.validate_schema(require_vectors=vectors)
        return store.full_rebuild(canonical).to_mapping()


class OpenSearchBenchmarkAdapter:
    backend_id = "opensearch"
    indexing_artifact_key: str | None = "opensearch"

    def build_settings(self, context: BenchmarkBackendContext) -> dict[str, object]:
        return {
            **_object(context.settings, self.backend_id),
            "url": context.endpoints["opensearch"],
            "diagnostic_candidate_window": context.diagnostic_candidate_window,
        }

    def probe_service_versions(
        self,
        context: BenchmarkBackendContext,
    ) -> dict[str, str]:
        payload = _http_json(str(context.endpoints["opensearch"]))
        version = payload.get("version")
        return {
            "opensearch": (
                str(version.get("number", "unknown"))
                if isinstance(version, Mapping)
                else "unknown"
            )
        }

    def prepare_index(
        self,
        canonical: RepositoryIndexResult,
        *,
        modes: frozenset[BaselineRetrievalMode],
        retrieval: RetrievalConfig,
        context: BenchmarkBackendContext,
    ) -> dict[str, object]:
        vectors = bool(modes & {"dense", "hybrid"})
        settings = OpenSearchSettings.from_mapping(self.build_settings(context))
        store = OpenSearchIndexStore(
            UrllibOpenSearchTransport(settings),
            settings,
            clock=_utc_now,
            embedding_provider=(
                _embedding_providers(context).create(
                    retrieval.dense.embedding,
                    backend_id=self.backend_id,
                    backend_name="OpenSearch",
                )
                if vectors else None
            ),
        )
        return store.full_rebuild(canonical).to_mapping()


class QdrantBenchmarkAdapter:
    backend_id = "qdrant-vector"
    indexing_artifact_key: str | None = "qdrant"

    def build_settings(self, context: BenchmarkBackendContext) -> dict[str, object]:
        return {
            **_object(context.settings, self.backend_id),
            "url": context.endpoints["qdrant"],
            "embedding_url": context.endpoints["ollama"],
            "embedding_model": context.embedding_identity["model"],
            "diagnostic_candidate_window": context.diagnostic_candidate_window,
        }

    def probe_service_versions(
        self,
        context: BenchmarkBackendContext,
    ) -> dict[str, str]:
        payload = _http_json(str(context.endpoints["qdrant"]))
        return {"qdrant": str(payload.get("version", "unknown"))}

    def prepare_index(
        self,
        canonical: RepositoryIndexResult,
        *,
        modes: frozenset[BaselineRetrievalMode],
        retrieval: RetrievalConfig,
        context: BenchmarkBackendContext,
    ) -> dict[str, object]:
        settings = QdrantVectorSettings.from_mapping(self.build_settings(context))
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
        return store.full_rebuild(canonical).to_mapping()


def benchmark_backend_adapters() -> dict[str, BenchmarkBackendAdapter]:
    """Return the explicit benchmark adapter registry."""

    adapters: tuple[BenchmarkBackendAdapter, ...] = (
        LocalBenchmarkAdapter(),
        SolrBenchmarkAdapter(),
        OpenSearchBenchmarkAdapter(),
        QdrantBenchmarkAdapter(),
    )
    return {adapter.backend_id: adapter for adapter in adapters}


def _embedding_providers(context: BenchmarkBackendContext) -> EmbeddingProviderRegistry:
    provider = str(context.embedding_identity["provider"])
    if provider != "ollama":
        return EmbeddingProviderRegistry()
    ollama_url = context.endpoints.get("ollama")
    if ollama_url is None:
        raise BaselineConfigurationError(
            "ollama embedding provider requires an ollama service endpoint"
        )
    return EmbeddingProviderRegistry(settings={"ollama": {"url": str(ollama_url)}})


def main() -> int:
    args = _parser().parse_args()
    if args.allow_missing_indexing_telemetry and not args.resume:
        raise BaselineConfigurationError(
            "--allow-missing-indexing-telemetry requires --resume"
        )
    if args.assume_no_index_preparation and not args.resume:
        raise BaselineConfigurationError(
            "--assume-no-index-preparation requires --resume"
        )
    if args.assume_no_index_preparation and (
        args.prepare_index or args.allow_missing_indexing_telemetry
    ):
        raise BaselineConfigurationError(
            "--assume-no-index-preparation conflicts with index preparation options"
        )
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
    available_arms = _manifest_arms(manifest)
    adapters = benchmark_backend_adapters()
    _validate_backend_adapters(available_arms, adapters)
    output_root = args.output.resolve()
    resume_dir = _resume_directory(output_root, args.resume) if args.resume else None
    if resume_dir is not None and (resume_dir / "run.json").exists():
        raise BaselineConfigurationError("cannot resume a completed baseline run")
    saved_state = (
        _load_json(resume_dir / RUN_STATE_FILE)
        if resume_dir is not None and (resume_dir / RUN_STATE_FILE).is_file()
        else None
    )
    requested_arms = args.arm or (
        [str(item) for item in saved_state["arms"]]
        if saved_state is not None else []
    )
    selected = _select_arms(requested_arms, available_arms)
    prepare_index = bool(
        args.prepare_index
        or (saved_state is not None and saved_state.get("prepare_index"))
        or args.allow_missing_indexing_telemetry
    )
    if saved_state is not None and args.prepare_index and not saved_state.get("prepare_index"):
        raise BaselineConfigurationError("resume cannot change the index preparation policy")
    backend_context = BenchmarkBackendContext(
        endpoints=endpoints,
        embedding_identity=embedding_identity,
        settings=backend_settings,
        diagnostic_candidate_window=int(
            str(measurement["diagnostic_candidate_window"])
        ),
    )
    backends = BackendRegistry(providers=_embedding_providers(backend_context))
    service_versions = _service_versions(
        selected,
        context=backend_context,
        adapters=adapters,
    )
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

    run_version, output_dir = (
        (resume_dir.name, resume_dir)
        if resume_dir is not None else _allocate_run_output_dir(output_root)
    )
    print(f"[baseline] run_version={run_version} output={output_dir}")

    indexing_telemetry_unavailable = bool(
        args.allow_missing_indexing_telemetry
        or (saved_state is not None and saved_state.get("indexing_telemetry_unavailable"))
    )
    legacy_index_preparation_assumed_absent = bool(
        args.assume_no_index_preparation
        or (saved_state is not None and saved_state.get("legacy_index_preparation_assumed_absent"))
    )
    run_state = {
        "schema_version": 1,
        "protocol": "fetchcore.baseline-v1",
        "arms": [arm.id for arm in selected],
        "prepare_index": prepare_index,
        "workspace": str(workspace),
        "corpus": suite.corpus,
        "environment": environment,
        "config_identity": config_identity,
        "indexing_telemetry_unavailable": indexing_telemetry_unavailable,
        "legacy_index_preparation_assumed_absent": legacy_index_preparation_assumed_absent,
    }
    if saved_state is not None and saved_state != run_state:
        raise BaselineConfigurationError("resume state does not match this execution")
    planned: list[tuple[BaselineArm, SearchScope, dict[str, object], dict[str, object]]] = []
    existing_artifacts: dict[str, dict[str, object]] = {}
    for arm in selected:
        retrieval = _retrieval_for_mode(
            arm.mode,
            provider=str(embedding_identity["provider"]),
            model=str(embedding_identity["model"]),
        )
        settings = _backend_settings(arm, context=backend_context, adapters=adapters)
        scope = SearchScope(
            workspace=workspace,
            revision=expected_revision,
            repository=expected_repository,
            knowledge_types=("code", "tests", "documentation", "configuration"),
            max_results=int(str(measurement["max_results"])),
            settings=settings,
            retrieval=retrieval,
        )
        identity = {**config_identity, "backend_settings": settings}
        planned.append((arm, scope, settings, identity))
        path = output_dir / f"{arm.id}.json"
        if path.exists():
            if resume_dir is None:
                raise BaselineConfigurationError(f"new run already contains {path.name}")
            artifact = _load_json(path)
            _validate_resumed_artifact(
                artifact,
                arm=arm,
                scope=scope,
                identity=identity,
                suite=suite,
                environment=environment,
                measurement=measurement,
            )
            existing_artifacts[arm.id] = artifact

    indexing_path = output_dir / "indexing.json"
    if (
        resume_dir is not None
        and saved_state is None
        and existing_artifacts
        and not indexing_path.exists()
        and not args.allow_missing_indexing_telemetry
        and not args.assume_no_index_preparation
    ):
        raise BaselineConfigurationError(
            "legacy partial run has no index preparation record; specify "
            "--allow-missing-indexing-telemetry or --assume-no-index-preparation"
        )
    indexing_payload: dict[str, object] | None = None
    if indexing_path.exists():
        if not prepare_index:
            raise BaselineConfigurationError("indexing artifact exists but preparation was not selected")
        indexing_payload = _load_json(indexing_path)
        if (
            indexing_payload.get("corpus") != {
                "repository": expected_repository,
                "revision": expected_revision,
            }
            or indexing_payload.get("environment") != environment
            or indexing_payload.get("fetchcore_revision") != fetchcore_revision
        ):
            raise BaselineConfigurationError("indexing artifact does not match this run")
    elif prepare_index and existing_artifacts:
        if not indexing_telemetry_unavailable:
            raise BaselineConfigurationError(
                "indexing telemetry was lost before this runner saved it; "
                "use --allow-missing-indexing-telemetry to resume retrieval only"
            )
    elif prepare_index:
        indexing_payload = _prepare_indexes(
            selected,
            workspace=workspace,
            repository=expected_repository,
            revision=expected_revision,
            context=backend_context,
            adapters=adapters,
            environment=environment,
            fetchcore_revision=fetchcore_revision,
        )
        indexing_path.write_text(
            json.dumps(indexing_payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[baseline] indexing artifact={indexing_path}")

    if saved_state is None:
        (output_dir / RUN_STATE_FILE).write_text(
            json.dumps(run_state, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    retrieval_artifacts: list[dict[str, object]] = []
    for arm, scope, settings, identity in planned:
        existing = existing_artifacts.get(arm.id)
        if existing is not None:
            retrieval_artifacts.append(existing)
            print(f"[baseline] arm={arm.id} reused={output_dir / f'{arm.id}.json'}")
            continue
        backend = backends.create(arm.backend)
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
            config_identity=identity,
            environment=environment,
        )
        destination = write_baseline_artifact(
            output_dir / f"{arm.id}.json",
            artifact,
        )
        retrieval_artifacts.append(artifact.to_mapping())
        print(f"[baseline] arm={arm.id} artifact={destination}")

    run_manifest = {
        "schema_version": 1,
        "run_version": run_version,
        "protocol": "fetchcore.baseline-v1",
        "status": (
            "retrieval_complete" if prepare_index and indexing_payload is None
            else "complete"
        ),
        "completed_at": _utc_now(),
        "fetchcore_revision": fetchcore_revision,
        "corpus": suite.corpus,
        "arms": [arm.to_mapping() for arm in selected],
        "prepare_index": prepare_index,
        "artifacts": [f"{arm.id}.json" for arm in selected],
        "indexing_artifact": ("indexing.json" if indexing_payload is not None else None),
        "metrics_report": "README.md",
        "resume": {
            "reused_artifacts": [f"{arm_id}.json" for arm_id in existing_artifacts],
            "indexing_telemetry_unavailable": prepare_index and indexing_payload is None,
            "legacy_index_preparation_assumed_absent": (
                legacy_index_preparation_assumed_absent
            ),
        },
    }
    report_path = _write_metrics_report(
        output_dir,
        run_manifest=run_manifest,
        retrieval_artifacts=retrieval_artifacts,
        indexing_payload=indexing_payload,
    )
    print(f"[baseline] metrics report={report_path}")

    run_manifest_path = output_dir / "run.json"
    run_manifest_path.write_text(
        json.dumps(run_manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[baseline] run_version={run_version} manifest={run_manifest_path}")

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
        "--resume",
        metavar="V0.N",
        help="Resume an incomplete version in --output after validating its artifacts.",
    )
    parser.add_argument(
        "--allow-missing-indexing-telemetry",
        action="store_true",
        help=(
            "Resume a legacy partial run whose index was prepared but whose "
            "indexing telemetry was lost; final status is retrieval_complete."
        ),
    )
    parser.add_argument(
        "--assume-no-index-preparation",
        action="store_true",
        help="Resume a legacy partial run that was started without --prepare-index.",
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


def _resume_directory(output_root: Path, version: str) -> Path:
    if not version.startswith("V0.") or not version[3:].isdigit():
        raise BaselineConfigurationError("--resume must name a V0.N run version")
    directory = output_root / version
    if not directory.is_dir():
        raise BaselineConfigurationError(f"resume directory does not exist: {directory}")
    return directory


def _validate_resumed_artifact(
    artifact: Mapping[str, object],
    *,
    arm: BaselineArm,
    scope: SearchScope,
    identity: Mapping[str, object],
    suite: BaselineSuite,
    environment: Mapping[str, object],
    measurement: Mapping[str, object],
) -> None:
    expected_config = {
        "scope": scope.to_mapping(),
        "arm": arm.to_mapping(),
        "repetitions": int(str(measurement["repetitions"])),
        "warmups": int(str(measurement["warmups"])),
        "k": int(str(measurement["k"])),
        "identity": dict(identity),
    }
    expected = {
        "schema_version": BASELINE_ARTIFACT_SCHEMA_VERSION,
        "protocol": "fetchcore.baseline-v1",
        "ground_truth_schema_version": BASELINE_GROUND_TRUTH_SCHEMA_VERSION,
        "ground_truth_fingerprint": suite.fingerprint,
        "metrics_schema_version": BASELINE_METRICS_SCHEMA_VERSION,
        "metrics_fingerprint": mapping_fingerprint(BASELINE_V1_METRICS_SPEC),
        "arm": arm.to_mapping(),
        "corpus": suite.corpus,
        "config": expected_config,
        "config_fingerprint": mapping_fingerprint(expected_config),
        "environment": environment,
    }
    mismatched = [key for key, value in expected.items() if artifact.get(key) != value]
    observations = artifact.get("observations")
    aggregate = artifact.get("aggregate")
    expected_count = len(suite.cases) * int(str(measurement["repetitions"]))
    if not isinstance(observations, list) or len(observations) != expected_count:
        mismatched.append("observations")
    if not isinstance(aggregate, Mapping) or aggregate.get("run_count") != expected_count:
        mismatched.append("aggregate.run_count")
    if mismatched:
        raise BaselineConfigurationError(
            f"cannot reuse {arm.id}.json: mismatched {', '.join(mismatched)}"
        )


def _write_metrics_report(
    output_dir: Path,
    *,
    run_manifest: Mapping[str, object],
    retrieval_artifacts: Sequence[Mapping[str, object]],
    indexing_payload: Mapping[str, object] | None,
) -> Path:
    destination = (output_dir / "README.md").resolve()
    destination.write_text(
        _render_metrics_report(
            run_manifest=run_manifest,
            retrieval_artifacts=retrieval_artifacts,
            indexing_payload=indexing_payload,
        ),
        encoding="utf-8",
    )
    return destination


def _render_metrics_report(
    *,
    run_manifest: Mapping[str, object],
    retrieval_artifacts: Sequence[Mapping[str, object]],
    indexing_payload: Mapping[str, object] | None,
) -> str:
    """Render one self-contained Markdown metrics report for a versioned run."""

    run_version = str(run_manifest["run_version"])
    corpus = _object(run_manifest, "corpus")
    repository = str(corpus["repository"])
    revision = str(corpus["revision"])
    lines = [
        f"# Baseline {run_version} metrics",
        "",
        "This report is generated from the aggregate fields in this run's JSON",
        "artifacts. The JSON files remain the source of truth.",
        "",
        "## Run",
        "",
        f"- Protocol: `{run_manifest['protocol']}`",
        f"- Status: `{run_manifest['status']}`",
        f"- Completed at: `{run_manifest['completed_at']}`",
        f"- FetchCore revision: `{run_manifest['fetchcore_revision']}`",
        f"- Corpus: `{repository}@{revision}`",
        "",
        "## Retrieval quality and latency",
        "",
        (
            "| Arm | Source recall@K | Evidence recall@K | Span coverage@K | "
            "Evidence MRR | Evidence nDCG@K | Useful context | p50 | p95 | p99 |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    operational_rows: list[str] = []
    artifact_names: list[str] = []
    for artifact in retrieval_artifacts:
        arm = _object(artifact, "arm")
        aggregate = _object(artifact, "aggregate")
        duration = _object(aggregate, "duration_ms")
        arm_id = str(arm["id"])
        artifact_name = f"{arm_id}.json"
        artifact_names.append(artifact_name)
        lines.append(
            "| "
            + " | ".join(
                (
                    f"[{arm_id}]({artifact_name})",
                    _format_decimal(aggregate["source_recall_at_k_mean"]),
                    _format_decimal(aggregate["evidence_recall_at_k_mean"]),
                    _format_decimal(aggregate["span_coverage_at_k_mean"]),
                    _format_decimal(aggregate["evidence_mrr_mean"]),
                    _format_decimal(aggregate["evidence_ndcg_at_k_mean"]),
                    _format_decimal(aggregate["useful_context_line_ratio_mean"]),
                    f"{duration['p50']} ms",
                    f"{duration['p95']} ms",
                    f"{duration['p99']} ms",
                )
            )
            + " |"
        )
        operational_rows.append(
            "| "
            + " | ".join(
                (
                    arm_id,
                    str(aggregate["run_count"]),
                    str(aggregate["query_count"]),
                    _format_number(aggregate["candidates_examined_mean"]),
                    _format_number(aggregate["candidates_returned_mean"]),
                    _format_number(aggregate["returned_chars_mean"]),
                    _format_number(aggregate["returned_line_count_mean"]),
                    str(aggregate["evidence_ground_truth_recovered"]),
                )
            )
            + " |"
        )

    lines.extend(
        (
            "",
            "## Retrieval volume",
            "",
            (
                "| Arm | Observations | Queries | Candidates examined | Candidates returned | "
                "Returned chars | Returned lines | Ground Truth recovered |"
            ),
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            *operational_rows,
            "",
            "## Indexing",
            "",
        )
    )

    if indexing_payload is None:
        if run_manifest.get("prepare_index"):
            lines.append(
                "Index preparation was performed before interruption, but its "
                "telemetry was not preserved. This run is not complete for baseline promotion."
            )
        else:
            lines.append("Index preparation was not requested for this run.")
    else:
        canonical = _object(indexing_payload, "canonical")
        lines.extend(
            (
                f"- Files discovered: {canonical['files_discovered']}",
                f"- Files indexed: {canonical['files_indexed']}",
                f"- Chunks/documents: {canonical['chunks_total']}",
                f"- Canonical duration: {canonical['duration_ms']} ms",
                (
                    "- Canonical throughput: "
                    f"{_format_number(canonical['indexing_throughput_chunks_per_second'])} "
                    "chunks/s"
                ),
                "",
                (
                    "| Backend | Mode | Duration | Writes | Deletes | Embedding duration | "
                    "Vectors | Vector throughput |"
                ),
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            )
        )
        for backend, raw_result in sorted(_object(indexing_payload, "backends").items()):
            if not isinstance(raw_result, Mapping):
                raise BaselineConfigurationError(
                    f"indexing backend {backend!r} must be a JSON object"
                )
            telemetry = _object(raw_result, "telemetry")
            lines.append(
                "| "
                + " | ".join(
                    (
                        str(backend),
                        str(raw_result["mode"]),
                        f"{raw_result['duration_ms']} ms",
                        str(telemetry["backend_writes"]),
                        str(telemetry["backend_deletes"]),
                        f"{telemetry.get('embedding_duration_ms', 0)} ms",
                        str(telemetry.get("embedding_vectors", 0)),
                        (
                            f"{_format_number(telemetry.get('embedding_throughput_vectors_per_second', 0))} "
                            "vectors/s"
                        ),
                    )
                )
                + " |"
            )

    lines.extend(("", "## Artifacts", "", "- [run.json](run.json)"))
    lines.extend(f"- [{name}]({name})" for name in artifact_names)
    if indexing_payload is not None:
        lines.append("- [indexing.json](indexing.json)")
    lines.append("")
    return "\n".join(lines)


def _format_decimal(value: object) -> str:
    return f"{float(str(value)):.6f}"


def _format_number(value: object) -> str:
    return f"{float(str(value)):.3f}".rstrip("0").rstrip(".")


def _allocate_run_output_dir(output_root: Path) -> tuple[str, Path]:
    """Reserve the next monotonic V0.N directory for one benchmark execution."""

    output_root.mkdir(parents=True, exist_ok=True)
    prefix = "V0."
    observed_versions: list[int] = []
    for entry in output_root.iterdir():
        if not entry.is_dir() or not entry.name.startswith(prefix):
            continue
        suffix = entry.name[len(prefix) :]
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


def _manifest_arms(manifest: Mapping[str, object]) -> tuple[BaselineArm, ...]:
    raw_arms = manifest.get("arms")
    if not isinstance(raw_arms, list) or not raw_arms:
        raise BaselineConfigurationError(
            "baseline manifest requires a non-empty arms list"
        )
    arms: list[BaselineArm] = []
    for raw_arm in raw_arms:
        if not isinstance(raw_arm, Mapping):
            raise BaselineConfigurationError(
                "baseline manifest arms must be JSON objects"
            )
        arms.append(BaselineArm.from_mapping(raw_arm))
    duplicates = sorted(
        arm_id
        for arm_id in {arm.id for arm in arms}
        if sum(arm.id == arm_id for arm in arms) > 1
    )
    if duplicates:
        raise BaselineConfigurationError(
            f"baseline manifest contains duplicate arm ids: {', '.join(duplicates)}"
        )
    return tuple(arms)


def _validate_backend_adapters(
    arms: Sequence[BaselineArm],
    adapters: Mapping[str, BenchmarkBackendAdapter],
) -> None:
    mismatched = sorted(
        key for key, adapter in adapters.items() if key != adapter.backend_id
    )
    if mismatched:
        raise BaselineConfigurationError(
            "benchmark adapter registry keys do not match adapter ids: "
            + ", ".join(mismatched)
        )
    missing = sorted({arm.backend for arm in arms} - adapters.keys())
    if missing:
        raise BaselineConfigurationError(
            f"baseline manifest has no benchmark adapter for: {', '.join(missing)}"
        )


def _select_arms(
    requested: list[str],
    available_arms: Sequence[BaselineArm],
) -> tuple[BaselineArm, ...]:
    available = {arm.id: arm for arm in available_arms}
    if not requested:
        return tuple(available_arms)
    unknown = sorted(set(requested) - available.keys())
    if unknown:
        raise BaselineConfigurationError(f"unknown baseline arms: {', '.join(unknown)}")
    return tuple(available[item] for item in requested)


def _validate_workspace(workspace: Path, expected_revision: str) -> None:
    actual = _git_revision(workspace)
    if actual != expected_revision:
        raise BaselineConfigurationError(
            f"baseline corpus revision mismatch: expected {expected_revision}, got {actual}"
        )
    status = _git(workspace, "status", "--porcelain")
    if status.strip():
        raise BaselineConfigurationError("baseline corpus workspace must have a clean working tree")


def _prepare_indexes(
    arms: tuple[BaselineArm, ...],
    *,
    workspace: Path,
    repository: str,
    revision: str,
    context: BenchmarkBackendContext,
    adapters: Mapping[str, BenchmarkBackendAdapter],
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
    backends.update(
        _prepare_backend_indexes(
            arms,
            canonical=canonical,
            context=context,
            adapters=adapters,
        )
    )

    return payload


def _prepare_backend_indexes(
    arms: Sequence[BaselineArm],
    *,
    canonical: RepositoryIndexResult,
    context: BenchmarkBackendContext,
    adapters: Mapping[str, BenchmarkBackendAdapter],
) -> dict[str, object]:
    retrieval = _retrieval_for_mode(
        "hybrid",
        provider=str(context.embedding_identity["provider"]),
        model=str(context.embedding_identity["model"]),
    )
    assert retrieval is not None

    results: dict[str, object] = {}
    for backend_id, modes in _backend_modes(arms).items():
        adapter = _adapter_for(backend_id, adapters)
        result = adapter.prepare_index(
            canonical,
            modes=modes,
            retrieval=retrieval,
            context=context,
        )
        if result is None:
            continue
        key = adapter.indexing_artifact_key
        if key is None:
            raise BaselineConfigurationError(
                f"benchmark adapter {backend_id!r} returned indexing telemetry "
                "without an artifact key"
            )
        if key in results:
            raise BaselineConfigurationError(f"duplicate indexing artifact key {key!r}")
        results[key] = result
    return results


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
    context: BenchmarkBackendContext,
    adapters: Mapping[str, BenchmarkBackendAdapter],
) -> dict[str, object]:
    return _adapter_for(arm.backend, adapters).build_settings(context)


def _service_versions(
    arms: Sequence[BaselineArm],
    *,
    context: BenchmarkBackendContext,
    adapters: Mapping[str, BenchmarkBackendAdapter],
) -> dict[str, str]:
    versions: dict[str, str] = {}
    for backend_id in _backend_modes(arms):
        versions.update(
            _adapter_for(backend_id, adapters).probe_service_versions(context)
        )
    embedding_provider = str(context.embedding_identity["provider"])
    if (
        any(arm.mode in {"dense", "hybrid"} for arm in arms)
        and embedding_provider == "ollama"
    ):
        payload = _http_json(
            f"{str(context.endpoints['ollama']).rstrip('/')}/api/version"
        )
        versions["ollama"] = str(payload.get("version", "unknown"))
    return versions


def _backend_modes(
    arms: Sequence[BaselineArm],
) -> dict[str, frozenset[BaselineRetrievalMode]]:
    grouped: dict[str, set[BaselineRetrievalMode]] = {}
    for arm in arms:
        grouped.setdefault(arm.backend, set()).add(arm.mode)
    return {backend: frozenset(modes) for backend, modes in grouped.items()}


def _adapter_for(
    backend_id: str,
    adapters: Mapping[str, BenchmarkBackendAdapter],
) -> BenchmarkBackendAdapter:
    adapter = adapters.get(backend_id)
    if adapter is None:
        raise BaselineConfigurationError(
            f"no benchmark adapter registered for backend {backend_id!r}"
        )
    return adapter


def _http_json(url: str) -> dict[str, object]:
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read()
    except OSError as exc:
        raise BaselineConfigurationError(f"baseline service probe failed for {url}: {exc}") from exc
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
        raise BaselineConfigurationError(f"git {' '.join(args)} failed in {workspace}") from exc
    return completed.stdout


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
