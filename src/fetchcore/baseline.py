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
from itertools import pairwise
from pathlib import Path
from typing import Literal, cast
from urllib.parse import quote, unquote

from fetchcore.retrieval import RetrievalBackendCapabilities
from fetchcore.retrieval.signals import QuerySignal
from fetchcore.retrieval.contracts import (
    KnowledgeSearchBackend,
    KnowledgeSearchResponse,
    SearchIntent,
    SearchRequirement,
    SearchScope,
)


BaselineRetrievalMode = Literal["lexical", "dense", "hybrid"]

BASELINE_PROTOCOL = "fetchcore.baseline-v1"
BASELINE_ARTIFACT_SCHEMA_VERSION = 2
BASELINE_GROUND_TRUTH_SCHEMA_VERSION = 2
BASELINE_METRICS_SCHEMA_VERSION = 1
BASELINE_V1_METRICS_SPEC: dict[str, object] = {
    "schema_version": BASELINE_METRICS_SCHEMA_VERSION,
    "line_coordinates": "1-based inclusive",
    "alternative_coverage": (
        "unique reviewed lines covered / reviewed lines in the alternative"
    ),
    "requirement_coverage": "maximum alternative coverage",
    "source_recall_at_k": "requirements with any source-complete alternative / requirements",
    "evidence_recall_at_k": "requirements with requirement_coverage == 1 / requirements",
    "span_coverage_at_k": "mean requirement_coverage across requirements",
    "evidence_mrr": "reciprocal rank of first result adding new Ground Truth line coverage",
    "evidence_ndcg_at_k": (
        "DCG of incremental mean span-coverage gains by result rank, normalized "
        "against the same gains sorted descending"
    ),
    "useful_context_line_ratio": (
        "unique Ground Truth lines covered from each requirement's best alternative "
        "/ total returned result lines, where duplicate returned spans still consume budget"
    ),
}

BASELINE_CONTEXT_YIELD_PROTOCOL = "fetchcore.baseline-v1-context-yield"
BASELINE_CONTEXT_YIELD_SCHEMA_VERSION = 1
BASELINE_LINE_BUDGETS: tuple[int, ...] = (100, 250, 500, 1000)
BASELINE_CONTEXT_YIELD_METRICS_SPEC: dict[str, object] = {
    "schema_version": BASELINE_CONTEXT_YIELD_SCHEMA_VERSION,
    "budget_unit": "lines",
    "packing": "ranked-prefix whole-result spans; stop before first overflow",
    "budgeted_evidence_recall": (
        "fully covered evidence requirements in the packed prefix / all requirements"
    ),
    "context_yield": (
        "mean best-alternative Ground Truth span coverage in the packed prefix"
    ),
    "consumed_lines": "sum of full returned result spans packed within the line budget",
    "packed_result_count": "number of ranked result spans packed within the line budget",
}


class BaselineConfigurationError(ValueError):
    """Raised when one baseline arm cannot be executed as declared."""


@dataclass(frozen=True)
class BaselineArm:
    """One frozen backend/mode combination in the v1 protocol."""

    id: str
    backend: str
    mode: BaselineRetrievalMode

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> BaselineArm:
        """Parse one explicitly declared benchmark arm."""

        raw_id = value.get("id")
        raw_backend = value.get("backend")
        raw_mode = value.get("mode")
        if not isinstance(raw_id, str) or not isinstance(raw_backend, str):
            raise BaselineConfigurationError(
                "baseline arm requires non-empty id and backend"
            )
        arm_id = raw_id.strip()
        backend = raw_backend.strip()
        if not arm_id or not backend:
            raise BaselineConfigurationError(
                "baseline arm requires non-empty id and backend"
            )
        mode = raw_mode.strip() if isinstance(raw_mode, str) else ""
        if mode not in {"lexical", "dense", "hybrid"}:
            raise BaselineConfigurationError(
                f"baseline arm {arm_id!r} has unsupported mode {mode!r}"
            )
        return cls(arm_id, backend, cast(BaselineRetrievalMode, mode))

    def to_mapping(self) -> dict[str, str]:
        return asdict(self)


BASELINE_V1_ARMS: tuple[BaselineArm, ...] = (
    BaselineArm("local-lexical", "local-semantic", "lexical"),
    BaselineArm("solr-lexical", "solr", "lexical"),
    BaselineArm("solr-dense", "solr", "dense"),
    BaselineArm("solr-hybrid", "solr", "hybrid"),
    BaselineArm("opensearch-lexical", "opensearch", "lexical"),
    BaselineArm("opensearch-dense", "opensearch", "dense"),
    BaselineArm("opensearch-hybrid", "opensearch", "hybrid"),
    BaselineArm("qdrant-dense", "qdrant-vector", "dense"),
    BaselineArm("qdrant-hybrid", "qdrant-vector", "hybrid"),
)


@dataclass(frozen=True)
class BaselineEvidenceRegion:
    """Minimal line region sufficient to contribute one piece of evidence."""

    source_ref: str
    line_start: int
    line_end: int

    def __post_init__(self) -> None:
        if not self.source_ref.strip():
            raise BaselineConfigurationError("baseline evidence source_ref must not be empty")
        if self.line_start < 1 or self.line_end < self.line_start:
            raise BaselineConfigurationError(
                "baseline evidence region requires 1-based line_start <= line_end"
            )

    @property
    def line_count(self) -> int:
        return self.line_end - self.line_start + 1

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BaselineEvidenceAlternative:
    """One sufficient region set for satisfying an evidence requirement."""

    regions: tuple[BaselineEvidenceRegion, ...]

    def __post_init__(self) -> None:
        if not self.regions:
            raise BaselineConfigurationError("baseline evidence alternative must not be empty")
        keys = {
            (region.source_ref, region.line_start, region.line_end)
            for region in self.regions
        }
        if len(keys) != len(self.regions):
            raise BaselineConfigurationError(
                "baseline evidence alternative contains duplicate regions"
            )
        by_source: dict[str, list[tuple[int, int]]] = {}
        for region in self.regions:
            by_source.setdefault(region.source_ref, []).append(
                (region.line_start, region.line_end)
            )
        for ranges in by_source.values():
            ordered = sorted(ranges)
            for previous, current in pairwise(ordered):
                if current[0] <= previous[1]:
                    raise BaselineConfigurationError(
                        "baseline evidence alternative contains overlapping regions"
                    )

    def to_mapping(self) -> dict[str, object]:
        return {"regions": [region.to_mapping() for region in self.regions]}


@dataclass(frozen=True)
class BaselineEvidenceRequirement:
    """One logical evidence need with one or more sufficient alternatives."""

    id: str
    alternatives: tuple[BaselineEvidenceAlternative, ...]

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise BaselineConfigurationError("baseline evidence requirement id must not be empty")
        if not self.alternatives:
            raise BaselineConfigurationError(
                f"baseline evidence requirement {self.id!r} must have alternatives"
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "id": self.id,
            "alternatives": [item.to_mapping() for item in self.alternatives],
        }


@dataclass(frozen=True)
class BaselineGroundTruth:
    """Evidence-region Ground Truth for one retrieval query."""

    query_id: str
    requirements: tuple[BaselineEvidenceRequirement, ...]

    def __post_init__(self) -> None:
        if not self.query_id.strip():
            raise BaselineConfigurationError("baseline query_id must not be empty")
        if not self.requirements:
            raise BaselineConfigurationError(
                f"baseline Ground Truth for {self.query_id!r} must not be empty"
            )
        ids = [item.id for item in self.requirements]
        if len(set(ids)) != len(ids):
            raise BaselineConfigurationError(
                f"baseline Ground Truth for {self.query_id!r} contains duplicate requirement ids"
            )


@dataclass(frozen=True)
class RetrievalResultSpan:
    """Ranked result coordinates persisted so quality can be re-evaluated offline."""

    rank: int
    id: str
    source_ref: str
    line_start: int
    line_end: int

    @property
    def line_count(self) -> int:
        return self.line_end - self.line_start + 1

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BudgetedEvidenceMetric:
    """Evidence recovered by a ranked prefix constrained by a line budget."""

    line_budget: int
    packed_result_count: int
    consumed_lines: int
    evidence_recall: float
    context_yield: float

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalQualityObservation:
    """Evidence quality and output-efficiency metrics for one retrieval run."""

    query_id: str
    iteration: int
    source_recall_at_k: float
    evidence_recall_at_k: float
    evidence_ground_truth_recovered: int
    evidence_ground_truth_total: int
    span_coverage_at_k: float
    evidence_reciprocal_rank: float
    evidence_ndcg_at_k: float
    useful_context_line_ratio: float
    duration_ms: int
    candidates_examined: int
    candidates_returned: int
    returned_chars: int
    returned_line_count: int
    result_spans: tuple[RetrievalResultSpan, ...]
    telemetry: dict[str, object] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, object]:
        payload = asdict(self)
        payload["result_spans"] = [item.to_mapping() for item in self.result_spans]
        return payload


@dataclass(frozen=True)
class BaselineArtifact:
    """External-harness-friendly aggregate artifact for one baseline arm."""

    schema_version: int
    protocol: str
    ground_truth_schema_version: int
    ground_truth_fingerprint: str
    metrics_schema_version: int
    metrics_fingerprint: str
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
            "ground_truth_schema_version": self.ground_truth_schema_version,
            "ground_truth_fingerprint": self.ground_truth_fingerprint,
            "metrics_schema_version": self.metrics_schema_version,
            "metrics_fingerprint": self.metrics_fingerprint,
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
    """One reproducible query plus explicit evidence Ground Truth."""

    query_id: str
    intent: SearchIntent
    requirements: tuple[BaselineEvidenceRequirement, ...]

    @property
    def truth(self) -> BaselineGroundTruth:
        return BaselineGroundTruth(
            query_id=self.query_id,
            requirements=self.requirements,
        )


@dataclass(frozen=True)
class BaselineSuite:
    """Versioned corpus identity plus reproducible retrieval query cases."""

    schema_version: int
    corpus: dict[str, object]
    cases: tuple[BaselineQueryCase, ...]
    fingerprint: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> BaselineSuite:
        schema_version = int(str(value.get("schema_version", 0)))
        if schema_version != BASELINE_GROUND_TRUTH_SCHEMA_VERSION:
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
            raw_requirements = raw_query.get("required_evidence")
            if not isinstance(raw_requirements, list) or not raw_requirements:
                raise BaselineConfigurationError(
                    f"baseline suite query {query_id!r} requires non-empty required_evidence"
                )
            requirements = tuple(
                _evidence_requirement_from_mapping(
                    item,
                    repository=repository,
                    query_id=query_id,
                )
                for item in raw_requirements
            )
            cases.append(
                BaselineQueryCase(
                    query_id=query_id,
                    intent=intent,
                    requirements=requirements,
                )
            )

        canonical = _canonical_mapping(value)
        return cls(
            schema_version=schema_version,
            corpus=corpus,
            cases=tuple(cases),
            fingerprint=mapping_fingerprint(canonical),
        )

    def validate_workspace_regions(self, workspace: Path) -> None:
        """Fail closed when Ground Truth coordinates do not match the frozen checkout."""

        repository = str(self.corpus["repository"])
        source_prefix = f"repository://{quote(repository, safe='/.:@')}/"
        line_counts: dict[str, int] = {}

        for case in self.cases:
            for requirement in case.requirements:
                for alternative in requirement.alternatives:
                    for region in alternative.regions:
                        if not region.source_ref.startswith(source_prefix):
                            raise BaselineConfigurationError(
                                "Ground Truth source_ref escapes the frozen repository: "
                                f"{region.source_ref!r}"
                            )
                        relative = unquote(region.source_ref[len(source_prefix):])
                        normalized = relative.replace("\\", "/").strip("/")
                        if not normalized or ".." in normalized.split("/"):
                            raise BaselineConfigurationError(
                                f"Ground Truth path is invalid: {relative!r}"
                            )
                        if normalized not in line_counts:
                            target = workspace / normalized
                            try:
                                line_counts[normalized] = len(
                                    target.read_text(encoding="utf-8").splitlines()
                                )
                            except (OSError, UnicodeDecodeError) as exc:
                                raise BaselineConfigurationError(
                                    f"cannot validate Ground Truth path {normalized!r}"
                                ) from exc
                        if region.line_end > line_counts[normalized]:
                            raise BaselineConfigurationError(
                                f"Ground Truth region {case.query_id}/{requirement.id} "
                                f"{normalized}:{region.line_start}-{region.line_end} "
                                f"exceeds file length {line_counts[normalized]}"
                            )


def load_baseline_suite(path: Path) -> BaselineSuite:
    """Load a versioned evidence-region baseline suite from deterministic JSON."""

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
        ground_truth_fingerprint: str,
        config_identity: Mapping[str, object] | None = None,
        environment: Mapping[str, object] | None = None,
        requirements: tuple[SearchRequirement, ...] = (),
    ) -> BaselineArtifact:
        """Run warmups + measured repetitions and build one v1 artifact."""

        if not cases:
            raise BaselineConfigurationError("baseline runner requires query cases")
        if not ground_truth_fingerprint.startswith("sha256:"):
            raise BaselineConfigurationError(
                "baseline runner requires a SHA-256 Ground Truth fingerprint"
            )
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
            ground_truth_fingerprint=ground_truth_fingerprint,
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
    """Evaluate ranked result spans against explicit evidence-region Ground Truth."""

    if k < 1:
        raise BaselineConfigurationError("baseline k must be positive")
    if iteration < 1:
        raise BaselineConfigurationError("baseline iteration must be positive")

    results = response.results[:k]
    spans: list[RetrievalResultSpan] = []
    for rank, item in enumerate(results, start=1):
        if item.line_start is None or item.line_end is None:
            raise BaselineConfigurationError(
                "baseline evidence metrics require line_start/line_end for every returned result"
            )
        if item.line_start < 1 or item.line_end < item.line_start:
            raise BaselineConfigurationError(
                "baseline returned result contains invalid line coordinates"
            )
        spans.append(
            RetrievalResultSpan(
                rank=rank,
                id=item.id,
                source_ref=item.source_ref,
                line_start=item.line_start,
                line_end=item.line_end,
            )
        )
    ranked_spans = tuple(spans)

    present_sources = {item.source_ref for item in ranked_spans}
    source_recovered = sum(
        1
        for requirement in truth.requirements
        if _source_requirement_satisfied(requirement, present_sources)
    )
    source_recall = source_recovered / len(truth.requirements)

    coverages = tuple(
        _requirement_coverage(requirement, ranked_spans)
        for requirement in truth.requirements
    )
    evidence_recovered = sum(coverage == 1.0 for coverage in coverages)
    evidence_recall = evidence_recovered / len(truth.requirements)
    span_coverage = sum(coverages) / len(coverages)

    cumulative_coverage: list[float] = []
    for rank in range(1, len(ranked_spans) + 1):
        prefix = ranked_spans[:rank]
        cumulative_coverage.append(
            sum(
                _requirement_coverage(requirement, prefix)
                for requirement in truth.requirements
            )
            / len(truth.requirements)
        )
    gains: list[float] = []
    previous = 0.0
    for current in cumulative_coverage:
        gains.append(max(0.0, current - previous))
        previous = current
    first_gain_rank = next(
        (rank for rank, gain in enumerate(gains, start=1) if gain > 0.0),
        None,
    )
    reciprocal_rank = 0.0 if first_gain_rank is None else 1.0 / first_gain_rank
    evidence_ndcg = _normalized_discounted_gain(gains)

    returned_lines = sum(item.line_count for item in ranked_spans)
    useful_lines = _useful_ground_truth_lines(truth.requirements, ranked_spans)
    useful_context_ratio = (
        0.0 if returned_lines == 0 else useful_lines / returned_lines
    )

    telemetry = {} if response.telemetry is None else response.telemetry.to_mapping()
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
        source_recall_at_k=round(source_recall, 6),
        evidence_recall_at_k=round(evidence_recall, 6),
        evidence_ground_truth_recovered=evidence_recovered,
        evidence_ground_truth_total=len(truth.requirements),
        span_coverage_at_k=round(span_coverage, 6),
        evidence_reciprocal_rank=round(reciprocal_rank, 6),
        evidence_ndcg_at_k=round(evidence_ndcg, 6),
        useful_context_line_ratio=round(useful_context_ratio, 6),
        duration_ms=response.duration_ms,
        candidates_examined=candidates_examined,
        candidates_returned=len(response.results),
        returned_chars=characters,
        returned_line_count=returned_lines,
        result_spans=ranked_spans,
        telemetry=telemetry,
    )


def evaluate_line_budgeted_evidence(
    truth: BaselineGroundTruth,
    spans: Sequence[RetrievalResultSpan],
    *,
    budgets: Sequence[int] = BASELINE_LINE_BUDGETS,
) -> tuple[BudgetedEvidenceMetric, ...]:
    """Measure evidence recovery inside deterministic ranked-prefix line budgets."""

    normalized_budgets = _validated_line_budgets(budgets)
    metrics: list[BudgetedEvidenceMetric] = []
    for budget in normalized_budgets:
        packed: list[RetrievalResultSpan] = []
        consumed_lines = 0
        for span in spans:
            next_total = consumed_lines + span.line_count
            if next_total > budget:
                break
            packed.append(span)
            consumed_lines = next_total

        coverages = tuple(
            _requirement_coverage(requirement, packed)
            for requirement in truth.requirements
        )
        evidence_recall = (
            sum(coverage == 1.0 for coverage in coverages) / len(coverages)
        )
        context_yield = sum(coverages) / len(coverages)
        metrics.append(
            BudgetedEvidenceMetric(
                line_budget=budget,
                packed_result_count=len(packed),
                consumed_lines=consumed_lines,
                evidence_recall=round(evidence_recall, 6),
                context_yield=round(context_yield, 6),
            )
        )
    return tuple(metrics)


def build_context_yield_artifact(
    *,
    suite: BaselineSuite,
    retrieval_artifacts: Sequence[Mapping[str, object]],
    budgets: Sequence[int] = BASELINE_LINE_BUDGETS,
) -> dict[str, object]:
    """Re-evaluate frozen ranked spans under line budgets without invoking retrieval."""

    if not retrieval_artifacts:
        raise BaselineConfigurationError(
            "context-yield re-evaluation requires retrieval artifacts"
        )
    normalized_budgets = _validated_line_budgets(budgets)
    expected_metrics_fingerprint = mapping_fingerprint(BASELINE_V1_METRICS_SPEC)
    metrics_fingerprint = mapping_fingerprint(BASELINE_CONTEXT_YIELD_METRICS_SPEC)
    budget_config = {
        "unit": "lines",
        "budgets": list(normalized_budgets),
        "packing": "ranked-prefix-whole-result-stop-on-overflow",
    }
    truths = {case.query_id: case.truth for case in suite.cases}
    arm_payloads: list[dict[str, object]] = []

    for raw_artifact in retrieval_artifacts:
        if str(raw_artifact.get("protocol", "")) != BASELINE_PROTOCOL:
            raise BaselineConfigurationError(
                "context-yield source artifact protocol mismatch"
            )
        if int(str(raw_artifact.get("schema_version", 0))) != BASELINE_ARTIFACT_SCHEMA_VERSION:
            raise BaselineConfigurationError(
                "context-yield source artifact schema mismatch"
            )
        if str(raw_artifact.get("ground_truth_fingerprint", "")) != suite.fingerprint:
            raise BaselineConfigurationError(
                "context-yield Ground Truth fingerprint mismatch"
            )
        if int(str(raw_artifact.get("metrics_schema_version", 0))) != BASELINE_METRICS_SCHEMA_VERSION:
            raise BaselineConfigurationError(
                "context-yield source metrics schema mismatch"
            )
        if str(raw_artifact.get("metrics_fingerprint", "")) != expected_metrics_fingerprint:
            raise BaselineConfigurationError(
                "context-yield source metrics fingerprint mismatch"
            )

        raw_arm = raw_artifact.get("arm")
        raw_observations = raw_artifact.get("observations")
        if not isinstance(raw_arm, Mapping) or not isinstance(raw_observations, list):
            raise BaselineConfigurationError(
                "context-yield source artifact requires arm and observations"
            )

        aggregate_values: dict[int, dict[str, list[float]]] = {
            budget: {
                "context_yield": [],
                "evidence_recall": [],
                "consumed_lines": [],
                "packed_result_count": [],
            }
            for budget in normalized_budgets
        }
        observations: list[dict[str, object]] = []
        for raw_observation in raw_observations:
            if not isinstance(raw_observation, Mapping):
                raise BaselineConfigurationError(
                    "context-yield source observation must be an object"
                )
            query_id = str(raw_observation.get("query_id", "")).strip()
            truth = truths.get(query_id)
            if truth is None:
                raise BaselineConfigurationError(
                    f"context-yield source observation has unknown query_id {query_id!r}"
                )
            raw_spans = raw_observation.get("result_spans")
            if not isinstance(raw_spans, list):
                raise BaselineConfigurationError(
                    "context-yield source observation requires result_spans"
                )
            spans = tuple(_result_span_from_mapping(item) for item in raw_spans)
            if tuple(span.rank for span in spans) != tuple(range(1, len(spans) + 1)):
                raise BaselineConfigurationError(
                    "context-yield result spans must preserve contiguous ranked order"
                )

            budgeted = evaluate_line_budgeted_evidence(
                truth,
                spans,
                budgets=normalized_budgets,
            )
            for item in budgeted:
                values = aggregate_values[item.line_budget]
                values["context_yield"].append(item.context_yield)
                values["evidence_recall"].append(item.evidence_recall)
                values["consumed_lines"].append(float(item.consumed_lines))
                values["packed_result_count"].append(float(item.packed_result_count))
            observations.append(
                {
                    "query_id": query_id,
                    "iteration": int(str(raw_observation.get("iteration", 0))),
                    "budgets": [item.to_mapping() for item in budgeted],
                }
            )

        aggregate = {
            str(budget): {
                "context_yield_mean": _mean(aggregate_values[budget]["context_yield"]),
                "evidence_recall_mean": _mean(
                    aggregate_values[budget]["evidence_recall"]
                ),
                "consumed_lines_mean": _mean(
                    aggregate_values[budget]["consumed_lines"]
                ),
                "packed_result_count_mean": _mean(
                    aggregate_values[budget]["packed_result_count"]
                ),
            }
            for budget in normalized_budgets
        }
        arm_payloads.append(
            {
                "arm": _canonical_mapping(raw_arm),
                "source_config_fingerprint": str(
                    raw_artifact.get("config_fingerprint", "")
                ),
                "observation_count": len(observations),
                "observations": observations,
                "aggregate": aggregate,
            }
        )

    return {
        "schema_version": BASELINE_CONTEXT_YIELD_SCHEMA_VERSION,
        "protocol": BASELINE_CONTEXT_YIELD_PROTOCOL,
        "source_protocol": BASELINE_PROTOCOL,
        "source_artifact_schema_version": BASELINE_ARTIFACT_SCHEMA_VERSION,
        "source_metrics_schema_version": BASELINE_METRICS_SCHEMA_VERSION,
        "source_metrics_fingerprint": expected_metrics_fingerprint,
        "ground_truth_schema_version": suite.schema_version,
        "ground_truth_fingerprint": suite.fingerprint,
        "metrics_fingerprint": metrics_fingerprint,
        "budget_config": budget_config,
        "budget_config_fingerprint": mapping_fingerprint(budget_config),
        "arms": arm_payloads,
    }


def render_context_yield_report(artifact: Mapping[str, object]) -> str:
    """Render a compact Markdown report from a derived context-yield artifact."""

    raw_budget_config = artifact.get("budget_config")
    raw_arms = artifact.get("arms")
    if not isinstance(raw_budget_config, Mapping) or not isinstance(raw_arms, list):
        raise BaselineConfigurationError(
            "context-yield report requires budget_config and arms"
        )
    raw_budgets = raw_budget_config.get("budgets")
    if not isinstance(raw_budgets, list):
        raise BaselineConfigurationError(
            "context-yield report budget_config requires budgets"
        )
    budgets = _validated_line_budgets(tuple(int(str(item)) for item in raw_budgets))

    lines = [
        "# Baseline context yield",
        "",
        "Derived offline from frozen ranked result spans and evidence-region Ground Truth.",
        "No retrieval backend is invoked by this report.",
        "",
        f"- Protocol: `{artifact.get('protocol', '')}`",
        f"- Source protocol: `{artifact.get('source_protocol', '')}`",
        f"- Metrics fingerprint: `{artifact.get('metrics_fingerprint', '')}`",
        f"- Budget fingerprint: `{artifact.get('budget_config_fingerprint', '')}`",
        "",
        "## ContextYield by line budget",
        "",
        "| Arm | " + " | ".join(f"{budget}L" for budget in budgets) + " |",
        "| --- | " + " | ".join("---:" for _ in budgets) + " |",
    ]

    evidence_rows: list[str] = []
    diagnostic_rows: list[str] = []
    for raw_arm in raw_arms:
        if not isinstance(raw_arm, Mapping):
            raise BaselineConfigurationError(
                "context-yield report arm entry must be an object"
            )
        arm = raw_arm.get("arm")
        aggregate = raw_arm.get("aggregate")
        if not isinstance(arm, Mapping) or not isinstance(aggregate, Mapping):
            raise BaselineConfigurationError(
                "context-yield report arm requires arm and aggregate"
            )
        arm_id = str(arm.get("id", "")).strip()
        if not arm_id:
            raise BaselineConfigurationError(
                "context-yield report arm id must not be empty"
            )
        budget_values = []
        evidence_values = []
        diagnostic_values = []
        for budget in budgets:
            raw_metrics = aggregate.get(str(budget))
            if not isinstance(raw_metrics, Mapping):
                raise BaselineConfigurationError(
                    f"context-yield report missing aggregate for {budget}L"
                )
            budget_values.append(
                f"{float(str(raw_metrics['context_yield_mean'])):.6f}"
            )
            evidence_values.append(
                f"{float(str(raw_metrics['evidence_recall_mean'])):.6f}"
            )
            diagnostic_values.append(
                f"{float(str(raw_metrics['consumed_lines_mean'])):.1f} lines / "
                f"{float(str(raw_metrics['packed_result_count_mean'])):.1f} results"
            )
        lines.append("| " + " | ".join((arm_id, *budget_values)) + " |")
        evidence_rows.append("| " + " | ".join((arm_id, *evidence_values)) + " |")
        diagnostic_rows.append("| " + " | ".join((arm_id, *diagnostic_values)) + " |")

    lines.extend(
        (
            "",
            "## Budgeted evidence recall",
            "",
            "| Arm | " + " | ".join(f"{budget}L" for budget in budgets) + " |",
            "| --- | " + " | ".join("---:" for _ in budgets) + " |",
            *evidence_rows,
            "",
            "## Packed-context diagnostics",
            "",
            "| Arm | " + " | ".join(f"{budget}L" for budget in budgets) + " |",
            "| --- | " + " | ".join("---:" for _ in budgets) + " |",
            *diagnostic_rows,
            "",
            (
                "ContextYield is budgeted span coverage, not consumer satisfaction or "
                "semantic authority."
            ),
            "",
        )
    )
    return "\n".join(lines)


def write_context_yield_artifact(
    path: Path,
    artifact: Mapping[str, object],
) -> Path:
    """Write one deterministic derived context-yield JSON artifact."""

    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            _canonical_mapping(artifact),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def build_baseline_artifact(
    *,
    arm: BaselineArm,
    corpus: Mapping[str, object],
    config: Mapping[str, object],
    observations: Sequence[RetrievalQualityObservation],
    ground_truth_fingerprint: str,
    environment: Mapping[str, object] | None = None,
) -> BaselineArtifact:
    """Aggregate repeated observations using frozen evidence-metric semantics."""

    if not observations:
        raise BaselineConfigurationError("baseline artifact requires observations")
    if not ground_truth_fingerprint.startswith("sha256:"):
        raise BaselineConfigurationError(
            "baseline artifact requires a SHA-256 Ground Truth fingerprint"
        )
    config_mapping = _canonical_mapping(config)
    values = tuple(observations)
    durations = [item.duration_ms for item in values]
    metrics_fingerprint = mapping_fingerprint(BASELINE_V1_METRICS_SPEC)

    aggregate: dict[str, object] = {
        "run_count": len(values),
        "query_count": len({item.query_id for item in values}),
        "source_recall_at_k_mean": _mean(item.source_recall_at_k for item in values),
        "evidence_recall_at_k_mean": _mean(item.evidence_recall_at_k for item in values),
        "span_coverage_at_k_mean": _mean(item.span_coverage_at_k for item in values),
        "evidence_mrr_mean": _mean(
            item.evidence_reciprocal_rank for item in values
        ),
        "evidence_ndcg_at_k_mean": _mean(
            item.evidence_ndcg_at_k for item in values
        ),
        "useful_context_line_ratio_mean": _mean(
            item.useful_context_line_ratio for item in values
        ),
        "evidence_ground_truth_recovered": sum(
            item.evidence_ground_truth_recovered for item in values
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
        "returned_line_count_mean": _mean(
            item.returned_line_count for item in values
        ),
        "fingerprints": _observed_fingerprints(values),
    }

    return BaselineArtifact(
        schema_version=BASELINE_ARTIFACT_SCHEMA_VERSION,
        protocol=BASELINE_PROTOCOL,
        ground_truth_schema_version=BASELINE_GROUND_TRUTH_SCHEMA_VERSION,
        ground_truth_fingerprint=ground_truth_fingerprint,
        metrics_schema_version=BASELINE_METRICS_SCHEMA_VERSION,
        metrics_fingerprint=metrics_fingerprint,
        arm=arm,
        corpus=_canonical_mapping(corpus),
        config=config_mapping,
        config_fingerprint=mapping_fingerprint(config_mapping),
        observations=values,
        aggregate=aggregate,
        environment=_canonical_mapping(environment or {}),
    )


def _evidence_requirement_from_mapping(
    value: object,
    *,
    repository: str,
    query_id: str,
) -> BaselineEvidenceRequirement:
    if not isinstance(value, Mapping):
        raise BaselineConfigurationError(
            f"baseline query {query_id!r} evidence requirement must be an object"
        )
    requirement_id = str(value.get("id", "")).strip()
    raw_alternatives = value.get("alternatives")
    if not requirement_id or not isinstance(raw_alternatives, list) or not raw_alternatives:
        raise BaselineConfigurationError(
            f"baseline query {query_id!r} evidence requirement is incomplete"
        )
    alternatives: list[BaselineEvidenceAlternative] = []
    for raw_alternative in raw_alternatives:
        if not isinstance(raw_alternative, Mapping):
            raise BaselineConfigurationError(
                f"baseline query {query_id!r} evidence alternative must be an object"
            )
        raw_regions = raw_alternative.get("regions")
        if not isinstance(raw_regions, list) or not raw_regions:
            raise BaselineConfigurationError(
                f"baseline query {query_id!r} evidence alternative requires regions"
            )
        regions: list[BaselineEvidenceRegion] = []
        for raw_region in raw_regions:
            if not isinstance(raw_region, Mapping):
                raise BaselineConfigurationError(
                    f"baseline query {query_id!r} evidence region must be an object"
                )
            path = str(raw_region.get("path", "")).strip()
            if not path:
                raise BaselineConfigurationError(
                    f"baseline query {query_id!r} evidence region requires path"
                )
            try:
                line_start = int(str(raw_region["line_start"]))
                line_end = int(str(raw_region["line_end"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise BaselineConfigurationError(
                    f"baseline query {query_id!r} evidence region requires integer line bounds"
                ) from exc
            regions.append(
                BaselineEvidenceRegion(
                    source_ref=_repository_source_ref(repository, path),
                    line_start=line_start,
                    line_end=line_end,
                )
            )
        alternatives.append(BaselineEvidenceAlternative(tuple(regions)))
    return BaselineEvidenceRequirement(
        id=requirement_id,
        alternatives=tuple(alternatives),
    )


def _validated_line_budgets(budgets: Sequence[int]) -> tuple[int, ...]:
    values = tuple(int(item) for item in budgets)
    if not values or any(item < 1 for item in values):
        raise BaselineConfigurationError(
            "context-yield line budgets must contain positive integers"
        )
    if values != tuple(sorted(set(values))):
        raise BaselineConfigurationError(
            "context-yield line budgets must be unique and strictly increasing"
        )
    return values


def _result_span_from_mapping(value: object) -> RetrievalResultSpan:
    if not isinstance(value, Mapping):
        raise BaselineConfigurationError(
            "context-yield result span must be an object"
        )
    try:
        rank = int(str(value["rank"]))
        line_start = int(str(value["line_start"]))
        line_end = int(str(value["line_end"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise BaselineConfigurationError(
            "context-yield result span requires integer rank/line bounds"
        ) from exc
    identifier = str(value.get("id", "")).strip()
    source_ref = str(value.get("source_ref", "")).strip()
    if rank < 1 or not identifier or not source_ref:
        raise BaselineConfigurationError(
            "context-yield result span requires positive rank, id and source_ref"
        )
    if line_start < 1 or line_end < line_start:
        raise BaselineConfigurationError(
            "context-yield result span contains invalid line coordinates"
        )
    return RetrievalResultSpan(
        rank=rank,
        id=identifier,
        source_ref=source_ref,
        line_start=line_start,
        line_end=line_end,
    )


def _source_requirement_satisfied(
    requirement: BaselineEvidenceRequirement,
    present_sources: set[str],
) -> bool:
    return any(
        all(region.source_ref in present_sources for region in alternative.regions)
        for alternative in requirement.alternatives
    )


def _requirement_coverage(
    requirement: BaselineEvidenceRequirement,
    spans: Sequence[RetrievalResultSpan],
) -> float:
    return max(
        _alternative_coverage(alternative, spans)
        for alternative in requirement.alternatives
    )


def _alternative_coverage(
    alternative: BaselineEvidenceAlternative,
    spans: Sequence[RetrievalResultSpan],
) -> float:
    total = sum(region.line_count for region in alternative.regions)
    covered = sum(_covered_lines(region, spans) for region in alternative.regions)
    return 0.0 if total == 0 else covered / total


def _covered_lines(
    region: BaselineEvidenceRegion,
    spans: Sequence[RetrievalResultSpan],
) -> int:
    covered: set[int] = set()
    for span in spans:
        if span.source_ref != region.source_ref:
            continue
        start = max(region.line_start, span.line_start)
        end = min(region.line_end, span.line_end)
        if start <= end:
            covered.update(range(start, end + 1))
    return len(covered)


def _best_alternative(
    requirement: BaselineEvidenceRequirement,
    spans: Sequence[RetrievalResultSpan],
) -> BaselineEvidenceAlternative:
    return max(
        requirement.alternatives,
        key=lambda item: (
            _alternative_coverage(item, spans),
            -sum(region.line_count for region in item.regions),
        ),
    )


def _useful_ground_truth_lines(
    requirements: Sequence[BaselineEvidenceRequirement],
    spans: Sequence[RetrievalResultSpan],
) -> int:
    useful: set[tuple[str, int]] = set()
    for requirement in requirements:
        alternative = _best_alternative(requirement, spans)
        for region in alternative.regions:
            for span in spans:
                if span.source_ref != region.source_ref:
                    continue
                start = max(region.line_start, span.line_start)
                end = min(region.line_end, span.line_end)
                if start <= end:
                    useful.update(
                        (region.source_ref, line)
                        for line in range(start, end + 1)
                    )
    return len(useful)


def _normalized_discounted_gain(gains: Sequence[float]) -> float:
    if not gains or sum(gains) <= 0.0:
        return 0.0
    dcg = sum(
        gain / math.log2(rank + 1)
        for rank, gain in enumerate(gains, start=1)
    )
    ideal = sorted(gains, reverse=True)
    idcg = sum(
        gain / math.log2(rank + 1)
        for rank, gain in enumerate(ideal, start=1)
    )
    return 0.0 if idcg == 0.0 else dcg / idcg


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
