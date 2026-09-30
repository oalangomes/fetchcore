"""Deterministic FetchCore baseline metrics and artifact contracts.

This module evaluates retrieval outputs. It does not execute retrieval, decide
consumer satisfaction, or replace an external regression gate.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fetchcore.retrieval import RetrievalBackendCapabilities
from fetchcore.signals import QuerySignal
from fetchcore.search import (
    KnowledgeSearchBackend,
    KnowledgeSearchResponse,
    SearchIntent,
    SearchRequirement,
    SearchScope,
)


BaselineIdentityField = Literal["id", "source_ref"]
BaselineRetrievalMode = Literal["lexical", "dense", "hybrid"]


class BaselineConfigurationError(ValueError):
    """Raised when one baseline arm cannot be executed as declared."""


@dataclass(frozen=True)
class BaselineArm:
    """One frozen backend/mode combination in the v1 protocol."""

    id: str
    backend: str
    mode: BaselineRetrievalMode

    def to_mapping(self) -> dict[str, str]:
        return asdict(self)


BASELINE_V1_ARMS: tuple[BaselineArm, ...] = (
    BaselineArm("local-lexical", "local-semantic", "lexical"),
    BaselineArm("solr-lexical", "solr", "lexical"),
    BaselineArm("solr-hybrid", "solr", "hybrid"),
    BaselineArm("opensearch-lexical", "opensearch", "lexical"),
    BaselineArm("opensearch-hybrid", "opensearch", "hybrid"),
    BaselineArm("qdrant-dense", "qdrant-vector", "dense"),
    BaselineArm("qdrant-hybrid", "qdrant-vector", "hybrid"),
)


@dataclass(frozen=True)
class BaselineGroundTruth:
    """Ground Truth identity set for one retrieval query."""

    query_id: str
    relevant: tuple[str, ...]
    identity_field: BaselineIdentityField = "id"

    def __post_init__(self) -> None:
        if not self.query_id.strip():
            raise BaselineConfigurationError("baseline query_id must not be empty")
        if not self.relevant:
            raise BaselineConfigurationError(
                f"baseline Ground Truth for {self.query_id!r} must not be empty"
            )
        if len(set(self.relevant)) != len(self.relevant):
            raise BaselineConfigurationError(
                f"baseline Ground Truth for {self.query_id!r} contains duplicates"
            )


@dataclass(frozen=True)
class RetrievalQualityObservation:
    """Quality and output-efficiency metrics for one retrieval run."""

    query_id: str
    iteration: int
    recall_at_k: float
    ground_truth_recovered: int
    ground_truth_total: int
    reciprocal_rank: float
    ndcg_at_k: float
    useful_evidence_density: float
    duration_ms: int
    candidates_examined: int
    candidates_returned: int
    returned_chars: int
    result_identities: tuple[str, ...]
    telemetry: dict[str, object] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, object]:
        payload = asdict(self)
        payload["result_identities"] = list(self.result_identities)
        return payload


@dataclass(frozen=True)
class BaselineArtifact:
    """External-harness-friendly aggregate artifact for one baseline arm."""

    schema_version: int
    protocol: str
    arm: BaselineArm
    corpus: dict[str, object]
    config: dict[str, object]
    config_fingerprint: str
    observations: tuple[RetrievalQualityObservation, ...]
    aggregate: dict[str, object]
    environment: dict[str, object] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "protocol": self.protocol,
            "arm": self.arm.to_mapping(),
            "corpus": dict(self.corpus),
            "config": dict(self.config),
            "config_fingerprint": self.config_fingerprint,
            "environment": dict(self.environment),
            "observations": [item.to_mapping() for item in self.observations],
            "aggregate": dict(self.aggregate),
        }


@dataclass(frozen=True)
class BaselineEnvironment:
    """Execution environment identity recorded with a baseline artifact."""

    profile_id: str
    python_version: str
    platform_system: str
    platform_release: str
    machine: str
    cpu_count: int | None
    services: dict[str, str] = field(default_factory=dict)
    embedding: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise BaselineConfigurationError(
                "baseline environment profile_id must not be empty"
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "python_version": self.python_version,
            "platform_system": self.platform_system,
            "platform_release": self.platform_release,
            "machine": self.machine,
            "cpu_count": self.cpu_count,
            "services": dict(sorted(self.services.items())),
            "embedding": _canonical_mapping(self.embedding),
        }


def capture_baseline_environment(
    profile_id: str,
    *,
    services: Mapping[str, str] | None = None,
    embedding: Mapping[str, object] | None = None,
) -> BaselineEnvironment:
    """Capture stable host/runtime metadata without probing private machine identity."""

    return BaselineEnvironment(
        profile_id=profile_id,
        python_version=platform.python_version(),
        platform_system=platform.system(),
        platform_release=platform.release(),
        machine=platform.machine(),
        cpu_count=os.cpu_count(),
        services=dict(services or {}),
        embedding=_canonical_mapping(embedding or {}),
    )


@dataclass(frozen=True)
class BaselineQueryCase:
    """One reproducible query plus explicit Ground Truth."""

    query_id: str
    intent: SearchIntent
    relevant: tuple[str, ...]
    identity_field: BaselineIdentityField = "id"

    @property
    def truth(self) -> BaselineGroundTruth:
        return BaselineGroundTruth(
            query_id=self.query_id,
            relevant=self.relevant,
            identity_field=self.identity_field,
        )


@dataclass(frozen=True)
class BaselineSuite:
    """Versioned corpus identity plus reproducible retrieval query cases."""

    schema_version: int
    corpus: dict[str, object]
    cases: tuple[BaselineQueryCase, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "BaselineSuite":
        schema_version = int(str(value.get("schema_version", 0)))
        if schema_version != 1:
            raise BaselineConfigurationError(
                f"unsupported baseline suite schema_version {schema_version}"
            )
        raw_corpus = value.get("corpus")
        if not isinstance(raw_corpus, Mapping):
            raise BaselineConfigurationError("baseline suite corpus must be an object")
        corpus = _canonical_mapping(raw_corpus)
        repository = str(corpus.get("repository", "")).strip()
        revision = str(corpus.get("revision", "")).strip()
        if not repository or not revision:
            raise BaselineConfigurationError(
                "baseline suite corpus requires repository and revision"
            )
        raw_queries = value.get("queries")
        if not isinstance(raw_queries, list) or not raw_queries:
            raise BaselineConfigurationError(
                "baseline suite queries must be a non-empty list"
            )

        cases: list[BaselineQueryCase] = []
        for raw_query in raw_queries:
            if not isinstance(raw_query, Mapping):
                raise BaselineConfigurationError(
                    "baseline suite query entries must be objects"
                )
            query_id = str(raw_query.get("id", "")).strip()
            if not query_id:
                raise BaselineConfigurationError(
                    "baseline suite query id must not be empty"
                )
            raw_intent = raw_query.get("intent")
            if not isinstance(raw_intent, Mapping):
                raise BaselineConfigurationError(
                    f"baseline suite query {query_id!r} intent must be an object"
                )
            intent = SearchIntent(
                exact_terms=_string_tuple(raw_intent.get("exact_terms")),
                semantic_terms=_string_tuple(raw_intent.get("semantic_terms")),
                hints=_string_tuple(raw_intent.get("hints")),
                signals=tuple(
                    QuerySignal.from_mapping(item)
                    for item in _object_list(raw_intent.get("signals"))
                ),
            )
            identity_field = str(
                raw_query.get("identity_field", "source_ref")
            ).strip()
            if identity_field not in {"id", "source_ref"}:
                raise BaselineConfigurationError(
                    f"baseline suite query {query_id!r} has invalid identity_field"
                )
            raw_relevant = raw_query.get("relevant")
            raw_paths = raw_query.get("relevant_paths")
            if raw_relevant is not None and raw_paths is not None:
                raise BaselineConfigurationError(
                    f"baseline suite query {query_id!r} must use relevant or relevant_paths"
                )
            if raw_paths is not None:
                if identity_field != "source_ref":
                    raise BaselineConfigurationError(
                        f"baseline suite query {query_id!r} relevant_paths require source_ref identity"
                    )
                paths = _string_tuple(raw_paths)
                relevant = tuple(
                    _repository_source_ref(repository, path) for path in paths
                )
            else:
                relevant = _string_tuple(raw_relevant)
            cases.append(
                BaselineQueryCase(
                    query_id=query_id,
                    intent=intent,
                    relevant=relevant,
                    identity_field=identity_field,  # type: ignore[arg-type]
                )
            )
        return cls(
            schema_version=schema_version,
            corpus=corpus,
            cases=tuple(cases),
        )


def load_baseline_suite(path: Path) -> BaselineSuite:
    """Load a versioned baseline query suite from deterministic JSON."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineConfigurationError(
            f"failed to load baseline suite from {path}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise BaselineConfigurationError("baseline suite root must be an object")
    return BaselineSuite.from_mapping(raw)


class BaselineRunner:
    """Execute one frozen arm against a configured FetchCore backend."""

    def __init__(
        self,
        *,
        backend: KnowledgeSearchBackend,
        arm: BaselineArm,
        scope: SearchScope,
        repetitions: int = 20,
        warmups: int = 1,
        k: int | None = None,
    ) -> None:
        if repetitions < 1:
            raise BaselineConfigurationError("baseline repetitions must be positive")
        if warmups < 0:
            raise BaselineConfigurationError("baseline warmups must be non-negative")
        if k is not None and k < 1:
            raise BaselineConfigurationError("baseline k must be positive")
        if backend.backend_id != arm.backend:
            raise BaselineConfigurationError(
                f"baseline arm {arm.id!r} declares backend {arm.backend!r} "
                f"but runner received {backend.backend_id!r}"
            )
        validate_baseline_arm(arm, backend.retrieval_capabilities)
        observed_mode = scope.retrieval.mode if scope.retrieval is not None else "lexical"
        if observed_mode != arm.mode:
            raise BaselineConfigurationError(
                f"baseline arm {arm.id!r} requires mode {arm.mode!r} "
                f"but SearchScope config resolves to {observed_mode!r}"
            )
        self.backend = backend
        self.arm = arm
        self.scope = scope
        self.repetitions = repetitions
        self.warmups = warmups
        self.k = k or scope.max_results

    def run(
        self,
        cases: Sequence[BaselineQueryCase],
        *,
        corpus: Mapping[str, object],
        config_identity: Mapping[str, object] | None = None,
        environment: Mapping[str, object] | None = None,
        requirements: tuple[SearchRequirement, ...] = (),
    ) -> BaselineArtifact:
        """Run warmups + measured repetitions and build one v1 artifact."""

        if not cases:
            raise BaselineConfigurationError("baseline runner requires query cases")
        for case in cases:
            for _ in range(self.warmups):
                self.backend.search(case.intent, self.scope, requirements)

        observations: list[RetrievalQualityObservation] = []
        for iteration in range(1, self.repetitions + 1):
            for case in cases:
                response = self.backend.search(case.intent, self.scope, requirements)
                observations.append(
                    evaluate_retrieval(
                        response,
                        case.truth,
                        iteration=iteration,
                        k=self.k,
                    )
                )

        config: dict[str, object] = {
            "scope": self.scope.to_mapping(),
            "arm": self.arm.to_mapping(),
            "repetitions": self.repetitions,
            "warmups": self.warmups,
            "k": self.k,
        }
        if config_identity:
            config["identity"] = _canonical_mapping(config_identity)
        return build_baseline_artifact(
            arm=self.arm,
            corpus=corpus,
            config=config,
            observations=observations,
            environment=environment,
        )


def validate_baseline_arm(
    arm: BaselineArm,
    capabilities: RetrievalBackendCapabilities,
) -> None:
    """Fail closed when a frozen arm asks for an undeclared capability."""

    supported = {
        "lexical": capabilities.lexical,
        "dense": capabilities.dense,
        "hybrid": capabilities.hybrid,
    }
    if not supported[arm.mode]:
        raise BaselineConfigurationError(
            f"baseline arm {arm.id!r} requires unsupported {arm.mode!r} capability "
            f"from backend {arm.backend!r}"
        )


def evaluate_retrieval(
    response: KnowledgeSearchResponse,
    truth: BaselineGroundTruth,
    *,
    iteration: int,
    k: int,
) -> RetrievalQualityObservation:
    """Evaluate one backend response against explicit Ground Truth."""

    if k < 1:
        raise BaselineConfigurationError("baseline k must be positive")
    if iteration < 1:
        raise BaselineConfigurationError("baseline iteration must be positive")

    results = response.results[:k]
    identities = tuple(
        item.id if truth.identity_field == "id" else item.source_ref
        for item in results
    )
    relevant = set(truth.relevant)
    seen_relevant: set[str] = set()
    relevant_ranks: list[int] = []
    for rank, identity in enumerate(identities, start=1):
        if identity not in relevant or identity in seen_relevant:
            continue
        seen_relevant.add(identity)
        relevant_ranks.append(rank)

    recovered = len(seen_relevant)
    recall = recovered / len(relevant)

    first_rank = relevant_ranks[0] if relevant_ranks else None
    reciprocal_rank = 0.0 if first_rank is None else 1.0 / first_rank

    dcg = sum(1.0 / math.log2(rank + 1) for rank in relevant_ranks)
    ideal_hits = min(len(relevant), k)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    ndcg = 0.0 if ideal_dcg == 0.0 else dcg / ideal_dcg
    density = 0.0 if not identities else recovered / len(identities)

    telemetry = (
        {}
        if response.telemetry is None
        else response.telemetry.to_mapping()
    )
    candidates_examined = (
        len(response.results)
        if response.telemetry is None
        else response.telemetry.candidates_examined
    )
    characters = (
        sum(len(item.content) for item in response.results)
        if response.telemetry is None
        else response.telemetry.returned_chars
    )

    return RetrievalQualityObservation(
        query_id=truth.query_id,
        iteration=iteration,
        recall_at_k=round(recall, 6),
        ground_truth_recovered=recovered,
        ground_truth_total=len(relevant),
        reciprocal_rank=round(reciprocal_rank, 6),
        ndcg_at_k=round(ndcg, 6),
        useful_evidence_density=round(density, 6),
        duration_ms=response.duration_ms,
        candidates_examined=candidates_examined,
        candidates_returned=len(response.results),
        returned_chars=characters,
        result_identities=identities,
        telemetry=telemetry,
    )


def build_baseline_artifact(
    *,
    arm: BaselineArm,
    corpus: Mapping[str, object],
    config: Mapping[str, object],
    observations: Sequence[RetrievalQualityObservation],
    environment: Mapping[str, object] | None = None,
) -> BaselineArtifact:
    """Aggregate repeated observations into the frozen v1 artifact shape."""

    if not observations:
        raise BaselineConfigurationError("baseline artifact requires observations")
    config_mapping = _canonical_mapping(config)
    values = tuple(observations)
    durations = [item.duration_ms for item in values]

    aggregate: dict[str, object] = {
        "run_count": len(values),
        "query_count": len({item.query_id for item in values}),
        "recall_at_k_mean": _mean(item.recall_at_k for item in values),
        "mrr_mean": _mean(item.reciprocal_rank for item in values),
        "ndcg_at_k_mean": _mean(item.ndcg_at_k for item in values),
        "useful_evidence_density_mean": _mean(
            item.useful_evidence_density for item in values
        ),
        "ground_truth_recovered": sum(
            item.ground_truth_recovered for item in values
        ),
        "duration_ms": {
            "p50": percentile(durations, 0.50),
            "p95": percentile(durations, 0.95),
            "p99": percentile(durations, 0.99),
        },
        "candidates_examined_mean": _mean(
            item.candidates_examined for item in values
        ),
        "candidates_returned_mean": _mean(
            item.candidates_returned for item in values
        ),
        "returned_chars_mean": _mean(item.returned_chars for item in values),
        "fingerprints": _observed_fingerprints(values),
    }

    return BaselineArtifact(
        schema_version=1,
        protocol="fetchcore.baseline-v1",
        arm=arm,
        corpus=_canonical_mapping(corpus),
        config=config_mapping,
        config_fingerprint=mapping_fingerprint(config_mapping),
        observations=values,
        aggregate=aggregate,
        environment=_canonical_mapping(environment or {}),
    )


def _observed_fingerprints(
    observations: Sequence[RetrievalQualityObservation],
) -> dict[str, list[str]]:
    observed: dict[str, set[str]] = {}
    for observation in observations:
        raw = observation.telemetry.get("fingerprints")
        if not isinstance(raw, Mapping):
            continue
        for key, value in raw.items():
            if value is None:
                continue
            observed.setdefault(str(key), set()).add(str(value))
    return {
        key: sorted(values)
        for key, values in sorted(observed.items())
    }


def write_baseline_artifact(path: Path, artifact: BaselineArtifact) -> Path:
    """Write one deterministic JSON baseline artifact for external regression tooling."""

    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(
        artifact.to_mapping(),
        sort_keys=True,
        indent=2,
        ensure_ascii=True,
    )
    destination.write_text(rendered + "\n", encoding="utf-8")
    return destination


def percentile(values: Sequence[int], quantile: float) -> int:
    """Return deterministic nearest-rank percentile for repeated measurements."""

    if not values:
        raise BaselineConfigurationError("percentile requires at least one value")
    if not 0.0 < quantile <= 1.0:
        raise BaselineConfigurationError("percentile quantile must be in (0, 1]")
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def mapping_fingerprint(value: Mapping[str, object]) -> str:
    """Return stable identity for benchmark-relevant configuration."""

    rendered = json.dumps(
        _canonical_mapping(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "sha256:" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _repository_source_ref(repository: str, path: str) -> str:
    normalized = path.replace("\\", "/").strip("/")
    if not normalized or ".." in normalized.split("/"):
        raise BaselineConfigurationError(
            f"baseline relevant path is invalid: {path!r}"
        )
    encoded_repository = quote(repository, safe="/.:@")
    return f"repository://{encoded_repository}/{quote(normalized, safe='/')}"


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BaselineConfigurationError("baseline string collection must be a list")
    result = tuple(str(item).strip() for item in value)
    if any(not item for item in result):
        raise BaselineConfigurationError(
            "baseline string collection must not contain empty values"
        )
    return result


def _object_list(value: object) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BaselineConfigurationError("baseline object collection must be a list")
    return list(value)


def _canonical_mapping(value: Mapping[str, object]) -> dict[str, object]:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    loaded = json.loads(rendered)
    if not isinstance(loaded, dict):
        raise BaselineConfigurationError("baseline mapping must serialize to an object")
    return {str(key): item for key, item in loaded.items()}


def _mean(values: Iterable[float | int]) -> float:
    evaluated = tuple(float(item) for item in values)
    if not evaluated:
        return 0.0
    return round(sum(evaluated) / len(evaluated), 6)
