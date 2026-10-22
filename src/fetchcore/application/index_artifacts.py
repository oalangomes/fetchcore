"""Offline, versioned observations of repository-targeted indexing CLI output.

Artifacts report supplied evidence, never authorize work or reconstruct history.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
import re
from typing import Any
from collections.abc import Mapping

from fetchcore.telemetry import IndexStageTelemetry

MAX_ARTIFACT_BYTES = 2_000_000
OPERATIONS = {"status", "plan", "sync", "rebuild"}
MODES = {"initial", "incremental", "noop", "rebuild", "rebuild_required"}


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError(f"{name} must be nonempty text (up to 4096 characters)")
    return value


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _metric(value: object, name: str) -> None:
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        raise ValueError(f"{name} must be a finite nonnegative number or null")


def _diagnostic_integer(value: object, minimum: int = 0, maximum: int = 2**63 - 1) -> int | None:
    return value if type(value) is int and minimum <= value <= maximum else None


@dataclass(frozen=True)
class IndexFailureDiagnostics:
    """Allowlisted failure observations, excluding source text and provider responses."""

    http_status: int | None = None
    attempt: int | None = None
    max_attempts: int | None = None
    input_count: int | None = None
    batch_offset: int | None = None
    total_input_count: int | None = None
    max_input_chars: int | None = None
    total_input_chars: int | None = None
    cause_errno: int | None = None
    retries_exhausted: bool | None = None
    cause_type: str | None = None
    provider_error_type: str | None = None

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, object]) -> IndexFailureDiagnostics:
        cause = metadata.get("cause_type")
        exhausted = metadata.get("retries_exhausted")
        provider_error_type = metadata.get("provider_error_type")
        return cls(
            http_status=_diagnostic_integer(metadata.get("http_status"), 100, 599),
            attempt=_diagnostic_integer(metadata.get("attempt"), 1),
            max_attempts=_diagnostic_integer(metadata.get("max_attempts"), 1),
            input_count=_diagnostic_integer(metadata.get("input_count")),
            batch_offset=_diagnostic_integer(metadata.get("batch_offset")),
            total_input_count=_diagnostic_integer(metadata.get("total_input_count")),
            max_input_chars=_diagnostic_integer(metadata.get("max_input_chars")),
            total_input_chars=_diagnostic_integer(metadata.get("total_input_chars")),
            cause_errno=_diagnostic_integer(metadata.get("cause_errno"), -4095, 4095),
            retries_exhausted=exhausted if isinstance(exhausted, bool) else None,
            cause_type=cause if isinstance(cause, str) and cause in {
                "TimeoutError", "ConnectionError", "ConnectionResetError", "ConnectionRefusedError",
                "ConnectionAbortedError", "BrokenPipeError", "OSError", "gaierror",
            } else None,
            provider_error_type=(
                provider_error_type if isinstance(provider_error_type, str) and provider_error_type in {
                    "context_length_exceeded", "provider_resource_exhausted",
                    "transient_provider_failure",
                } else None
            ),
        )

    @classmethod
    def from_mapping(cls, value: object) -> IndexFailureDiagnostics:
        data = _object(value, "error.diagnostics")
        result = cls.from_metadata(data)
        if not data or data != result.to_mapping():
            raise ValueError("Invalid failure diagnostics")
        return result

    def to_mapping(self) -> dict[str, object]:
        return {name: value for name, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class IndexRunArtifact:
    """v1 envelope. target identifies an operator-declared backend destination.

    It must be stable for a given configuration; it is not an authorization token.
    """

    run_id: str
    observed_at: str
    repository: str
    backend: str
    target: str
    configuration_fingerprint: str
    command: Mapping[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "fetchcore.index-run",
            "run_id": self.run_id,
            "observed_at": self.observed_at,
            "repository": self.repository,
            "backend": self.backend,
            "target": self.target,
            "configuration_fingerprint": self.configuration_fingerprint,
            "command": dict(self.command),
        }

    @classmethod
    def from_mapping(cls, value: object) -> IndexRunArtifact:
        data = _object(value, "artifact")
        fields = {
            "schema_version",
            "kind",
            "run_id",
            "observed_at",
            "repository",
            "backend",
            "target",
            "configuration_fingerprint",
            "command",
        }
        if (
            set(data) != fields
            or type(data["schema_version"]) is not int
            or (data["schema_version"] != 1 or data["kind"] != "fetchcore.index-run")
        ):
            raise ValueError("Unsupported indexing artifact schema or fields")
        values = {k: _text(data[k], k) for k in fields - {"schema_version", "kind", "command"}}
        if not re.fullmatch(r"[0-9a-f]{64}", values["configuration_fingerprint"]):
            raise ValueError("configuration_fingerprint must be a SHA-256 hex digest")
        try:
            timestamp = datetime.fromisoformat(values["observed_at"])
            if timestamp.utcoffset() is None:
                raise ValueError("timestamp must include timezone")
        except ValueError as exc:
            raise ValueError("observed_at must be an ISO timestamp with timezone") from exc
        command = _object(data["command"], "command")
        if not isinstance(command.get("operation"), str) or command["operation"] not in OPERATIONS:
            raise ValueError("Unsupported indexing operation")
        status = command.get("status")
        if not isinstance(status, str):
            raise ValueError("Invalid CLI status")
        expected = {"operation", "status", "result" if status == "succeeded" else "error"}
        if set(command) != expected or status not in {"succeeded", "failed"}:
            raise ValueError("Invalid CLI result envelope")
        if status == "failed":
            error = _object(command["error"], "error")
            if set(error) not in ({"category", "message"}, {"category", "message", "diagnostics"}):
                raise ValueError("Invalid failure evidence")
            for k in ("category", "message"):
                _text(error[k], "error." + k)
            if "diagnostics" in error:
                IndexFailureDiagnostics.from_mapping(error["diagnostics"])
        else:
            _validate_result(command["result"], values["repository"], command["operation"])
        return cls(command=command, **values)


def _validate_result(value: object, repository: str, operation: str) -> None:
    result = _object(value, "result")
    if result.get("repository") != repository or (
        not isinstance(result.get("mode"), str) or result["mode"] not in MODES
    ):
        raise ValueError("Repository identity mismatch or unsupported indexing result")
    for key in ("reason", "indexing_fingerprint", "projection_fingerprint"):
        _text(result.get(key), key)
    for key in ("target_revision", "indexed_revision", "previous_indexed_revision"):
        if result.get(key) is not None:
            _text(result[key], key)
    if "stage_timings_ms" in result:
        _validate_stage_timings(result["stage_timings_ms"])
    executing = operation in {"sync", "rebuild"}
    if executing != ("execution" in result):
        raise ValueError("Execution evidence does not match operation")
    if operation == "rebuild" and result["mode"] != "rebuild":
        raise ValueError("Explicit rebuild must retain its mode")
    if operation == "sync" and result["mode"] not in {"incremental", "noop"}:
        raise ValueError("Invalid successful sync mode")
    if not executing and (
        not isinstance(result.get("currentness"), str)
        or result["currentness"] not in {"current", "stale", "absent", "incompatible", "unknown"}
    ):
        raise ValueError("Invalid currentness")
    if not executing and result["mode"] == "rebuild":
        raise ValueError("Read-only observation cannot claim rebuild execution")
    for key in ("schema_compatible", "projection_compatible", "incremental_possible"):
        if key in result and result[key] is not None and type(result[key]) is not bool:
            raise ValueError(f"{key} must be boolean or null")
    for section in ("planned_mutations", "indexing_telemetry", "execution", "discovery"):
        if section in result:
            _validate_measurements(_object(result[section], section), section)
    if executing:
        execution = result["execution"]
        if "stage_timings_ms" in execution:
            _validate_stage_timings(execution["stage_timings_ms"])
        if execution.get("mode") not in ("incremental", "full_rebuild", "noop"):
            raise ValueError("Unsupported persisted projection mutation mode")
        if execution["mode"] == "noop" and result["mode"] != "noop":
            raise ValueError("Backend noop cannot claim index mutations")
        if result["mode"] == "noop" and execution["mode"] == "full_rebuild":
            raise ValueError("Full rebuild cannot claim noop")
        for key in ("added", "updated", "deleted", "unchanged", "duration_ms"):
            if key not in execution or type(execution[key]) is not int:
                raise ValueError("Missing or invalid execution measurement: " + key)
        if operation == "rebuild" and execution["mode"] != "full_rebuild":
            raise ValueError("Rebuild must evidence full persistent replacement")
        if result["mode"] == "noop" and (
            execution.get("noop") is not True
            or any(execution[k] != 0 for k in ("added", "updated", "deleted"))
        ):
            raise ValueError("Contradictory noop evidence")
        snapshot = _object(execution.get("snapshot"), "snapshot")
        if snapshot.get("repository") != repository:
            raise ValueError("Snapshot repository mismatch")
        for key in ("indexing_fingerprint", "projection_fingerprint"):
            if snapshot.get(key) != result[key]:
                raise ValueError("Snapshot fingerprint mismatch")
        _text(snapshot.get("corpus_checksum"), "corpus_checksum")
        for key in (
            "embedding_provider",
            "embedding_model",
            "serializer_mode",
            "serializer_version",
            "serializer_fingerprint",
        ):
            if snapshot.get(key) is not None:
                _text(snapshot[key], "snapshot." + key)
        dimensions = snapshot.get("vector_dimensions")
        if dimensions is not None and (type(dimensions) is not int or dimensions <= 0):
            raise ValueError("Invalid persisted vector dimensions")
        if result.get("retrieval_mode") in ("dense", "hybrid") and any(
            snapshot.get(k) is None
            for k in (
                "embedding_provider",
                "embedding_model",
                "vector_dimensions",
                "serializer_mode",
                "serializer_version",
                "serializer_fingerprint",
            )
        ):
            raise ValueError("Missing persisted embedding identity for vector execution")
        if type(snapshot.get("document_count")) is not int:
            raise ValueError("Missing or invalid snapshot document_count")
        _metric(snapshot["document_count"], "document_count")
        _text(snapshot.get("revision"), "snapshot.revision")
        # Non-Git workspaces have no target Git revision; the indexer records a
        # content-derived tree revision. Preserve both without inventing equality.
        if (
            result.get("target_revision") is not None
            and snapshot["revision"] != result["target_revision"]
        ):
            raise ValueError("Snapshot revision mismatch")
    if operation != "plan" and "planned_mutations" in result:
        raise ValueError("Planned mutations require plan operation")


def _validate_stage_timings(value: object) -> None:
    IndexStageTelemetry.from_mapping(_object(value, "stage_timings_ms"))


def _validate_adapter_stage_timings(value: object) -> None:
    stages = _object(value, "stage_durations_ms")
    if len(stages) > 32:
        raise ValueError("Too many backend stage measurements")
    for name, duration in stages.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError("Invalid backend stage name")
        if type(duration) is not int or duration < 0:
            raise ValueError("Invalid backend stage duration")


def _validate_measurements(data: dict[str, Any], name: str) -> None:
    counts = {
        "added",
        "updated",
        "deleted",
        "removed",
        "unchanged",
        "upsert_documents",
        "resulting_document_count",
        "previous_document_count",
        "changed_paths",
        "removed_paths",
        "targeted_paths",
        "chunks_total",
        "chunks_changed",
        "embedding_cache_hits",
    }
    for key, value in data.items():
        if key == "stage_timings_ms":
            _validate_stage_timings(value)
            continue
        if key == "stage_durations_ms":
            _validate_adapter_stage_timings(value)
            continue
        if (
            key in counts
            or key.endswith(("_ms", "_count", "_requests", "_vectors"))
            or ("throughput" in key or key.startswith("files_") and type(value) is not bool)
            or key in {"backend_writes", "backend_deletes"}
        ):
            _metric(value, name + "." + key)
        if key in counts and value is not None and type(value) is not int:
            raise ValueError("Count must be an integer: " + name + "." + key)
        if key in {"telemetry", "discovery"}:
            _validate_measurements(_object(value, key), name + "." + key)
        elif key in {"changed_path_values", "removed_path_values"}:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError("Invalid path values")
        elif key in {"noop", "replace_repository"} and type(value) is not bool:
            raise ValueError(f"{key} must be boolean")


def parse_index_json(raw: str) -> object:
    """Bounded strict JSON shared by artifact import and offline CLI capture."""
    if len(raw.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise ValueError("Indexing artifact exceeds 2 MB")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    try:
        data = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
        )
    except (RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid indexing artifact JSON") from exc
    return data


def load_index_artifact(raw: str) -> IndexRunArtifact:
    return IndexRunArtifact.from_mapping(parse_index_json(raw))


def compare_index_artifacts(before: IndexRunArtifact, after: IndexRunArtifact) -> dict[str, Any]:
    """Compare identities, never infer document deltas from aggregate counts."""
    reasons = []
    for key in ("repository", "backend", "target", "configuration_fingerprint"):
        if getattr(before, key) != getattr(after, key):
            reasons.append(key + "_changed")
    results = [a.command.get("result", {}) for a in (before, after)]
    for key in ("indexing_fingerprint", "projection_fingerprint"):
        if not results[0].get(key) or results[0].get(key) != results[1].get(key):
            reasons.append(key + "_changed_or_unknown")
    if not results[0].get("retrieval_mode") or results[0].get("retrieval_mode") != results[1].get(
        "retrieval_mode"
    ):
        reasons.append("retrieval_mode_changed_or_unknown")
    # Embedding identity is only evidenced by persisted snapshots in current CLI output.
    for key in (
        "embedding_provider",
        "embedding_model",
        "vector_dimensions",
        "serializer_mode",
        "serializer_version",
        "serializer_fingerprint",
    ):
        snapshots = [r.get("execution", {}).get("snapshot", {}) for r in results]
        if not all(key in s for s in snapshots):
            reasons.append(key + "_unknown")
        elif snapshots[0][key] != snapshots[1][key]:
            reasons.append(key + "_changed")
    if any(
        r.get("schema_compatible") is False or r.get("projection_compatible") is False
        for r in results
    ):
        reasons.append("incompatible_contract")
    if any(r.get("mode") == "rebuild_required" for r in results):
        reasons.append("rebuild_required")
    if any(a.command["status"] == "failed" for a in (before, after)):
        reasons.append("failed_observation")
    return {
        "comparable": not reasons,
        "reasons": reasons,
        "source_revision_changed": (
            None
            if any("target_revision" not in r or r["target_revision"] is None for r in results)
            else results[0]["target_revision"] != results[1]["target_revision"]
        ),
        "before": before.to_mapping(),
        "after": after.to_mapping(),
    }
