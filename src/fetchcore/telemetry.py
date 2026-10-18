"""Backend-neutral operational telemetry for FetchCore retrieval.

Telemetry records observable acquisition cost and flow. It is deliberately
separate from diagnostics (why a candidate matched) and benchmark quality
metrics (whether Ground Truth was recovered).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class RetrievalStageTelemetry:
    """One bounded retrieval stage with optional candidate counts."""

    name: str
    duration_ms: int
    candidates_in: int | None = None
    candidates_out: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("retrieval telemetry stage name must not be empty")
        if self.duration_ms < 0:
            raise ValueError("retrieval telemetry duration_ms must be non-negative")
        for name, value in (
            ("candidates_in", self.candidates_in),
            ("candidates_out", self.candidates_out),
        ):
            if value is not None and value < 0:
                raise ValueError(f"retrieval telemetry {name} must be non-negative")

    def to_mapping(self) -> dict[str, object]:
        payload = asdict(self)
        return {
            key: value
            for key, value in payload.items()
            if value is not None and value not in ({}, ())
        }


@dataclass(frozen=True)
class RetrievalTelemetry:
    """Operational telemetry for one backend-neutral retrieval response."""

    mode: str
    duration_ms: int
    candidates_examined: int
    candidates_returned: int
    returned_chars: int
    stages: tuple[RetrievalStageTelemetry, ...] = ()
    time_to_first_candidate_ms: int | None = None
    fingerprints: dict[str, str] = field(default_factory=dict)
    cache: dict[str, object] = field(default_factory=dict)
    budget: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.mode.strip():
            raise ValueError("retrieval telemetry mode must not be empty")
        for name, value in (
            ("duration_ms", self.duration_ms),
            ("candidates_examined", self.candidates_examined),
            ("candidates_returned", self.candidates_returned),
            ("returned_chars", self.returned_chars),
        ):
            if value < 0:
                raise ValueError(f"retrieval telemetry {name} must be non-negative")
        if (
            self.time_to_first_candidate_ms is not None
            and self.time_to_first_candidate_ms < 0
        ):
            raise ValueError(
                "retrieval telemetry time_to_first_candidate_ms must be non-negative"
            )
        if self.candidates_returned > self.candidates_examined:
            raise ValueError(
                "retrieval telemetry candidates_returned cannot exceed candidates_examined"
            )

    def to_mapping(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "mode": self.mode,
            "duration_ms": self.duration_ms,
            "candidates_examined": self.candidates_examined,
            "candidates_returned": self.candidates_returned,
            "returned_chars": self.returned_chars,
            "stages": [item.to_mapping() for item in self.stages],
            "time_to_first_candidate_ms": self.time_to_first_candidate_ms,
            "fingerprints": dict(self.fingerprints),
            "cache": dict(self.cache),
            "budget": dict(self.budget),
            "metadata": dict(self.metadata),
        }
        return {
            key: value
            for key, value in payload.items()
            if value is not None and value not in ({}, [], ())
        }




@dataclass(frozen=True)
class IndexStageTelemetry:
    """Optional observed indexing phases, independent from a storage backend.

    Omitted means not executed or unmeasured; even a real 0 ms is an observation.
    Durations do not cover the full command, startup, or external readiness checks.
    """

    snapshot_load_ms: int | None = None
    previous_documents_load_ms: int | None = None
    canonical_delta_index_ms: int | None = None
    canonical_full_index_ms: int | None = None
    persistence_call_ms: int | None = None

    def __post_init__(self) -> None:
        for name, duration in asdict(self).items():
            if duration is not None and (type(duration) is not int or duration < 0):
                raise ValueError(f"Invalid indexing stage duration: {name}")

    def to_mapping(self) -> dict[str, int]:
        """Preserve only measured phases and their stable field names."""
        return {
            name: duration
            for name, duration in asdict(self).items()
            if duration is not None
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> IndexStageTelemetry:
        """Reject unknown or invalid observations; never synthesize zeros."""
        names = cls.__dataclass_fields__
        if not set(value).issubset(names):
            raise ValueError("Unknown indexing stage measurement")

        def measured(name: str) -> int | None:
            if name not in value:
                return None
            duration = value[name]
            if type(duration) is not int or duration < 0:
                raise ValueError(f"Invalid indexing stage duration: {name}")
            return duration

        return cls(
            snapshot_load_ms=measured("snapshot_load_ms"),
            previous_documents_load_ms=measured("previous_documents_load_ms"),
            canonical_delta_index_ms=measured("canonical_delta_index_ms"),
            canonical_full_index_ms=measured("canonical_full_index_ms"),
            persistence_call_ms=measured("persistence_call_ms"),
        )


@dataclass(frozen=True)
class IndexSyncTelemetry:
    """Operational storage/embedding telemetry for one index synchronization."""

    duration_ms: int
    backend_writes: int
    backend_deletes: int
    embedding_requests: int = 0
    embedding_vectors: int = 0
    embedding_duration_ms: int = 0

    def __post_init__(self) -> None:
        for name, value in (
            ("duration_ms", self.duration_ms),
            ("backend_writes", self.backend_writes),
            ("backend_deletes", self.backend_deletes),
            ("embedding_requests", self.embedding_requests),
            ("embedding_vectors", self.embedding_vectors),
            ("embedding_duration_ms", self.embedding_duration_ms),
        ):
            if value < 0:
                raise ValueError(f"index sync telemetry {name} must be non-negative")

    def to_mapping(self) -> dict[str, object]:
        return {
            "duration_ms": self.duration_ms,
            "backend_writes": self.backend_writes,
            "backend_deletes": self.backend_deletes,
            "embedding_requests": self.embedding_requests,
            "embedding_vectors": self.embedding_vectors,
            "embedding_duration_ms": self.embedding_duration_ms,
            "embedding_throughput_vectors_per_second": (
                None
                if self.embedding_duration_ms <= 0
                else round(
                    self.embedding_vectors / (self.embedding_duration_ms / 1_000),
                    3,
                )
            ),
        }


def elapsed_ms(started: float, ended: float) -> int:
    """Convert a monotonic interval to a bounded integer millisecond value."""

    return max(0, round((ended - started) * 1_000))


def returned_chars(contents: Iterable[object]) -> int:
    """Return deterministic character cost for an iterable of result contents."""

    return sum(len(str(value)) for value in contents)
