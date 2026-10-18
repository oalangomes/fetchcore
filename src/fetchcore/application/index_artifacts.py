"""Offline, versioned observations of repository-targeted indexing CLI output.

Artifacts report supplied evidence, never authorize work or reconstruct history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
import re
from typing import Any
from collections.abc import Mapping

MAX_ARTIFACT_BYTES = 2_000_000
OPERATIONS = {"status", "plan", "sync", "rebuild"}
MODES = {"initial", "incremental", "noop", "rebuild", "rebuild_required"}
STAGE_TIMING_FIELDS = frozenset({
    "snapshot_load_ms", "previous_documents_load_ms",
    "canonical_delta_index_ms", "persistence_call_ms",
})


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
            if set(error) != {"category", "message"}:
                raise ValueError("Invalid failure evidence")
            for k in error:
                _text(error[k], "error." + k)
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
        if execution.get("mode") not in ("incremental", "full_rebuild"):
            raise ValueError("Unsupported persisted projection mutation mode")
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
    stages = _object(value, "stage_timings_ms")
    if not set(stages).issubset(STAGE_TIMING_FIELDS):
        raise ValueError("Unknown indexing stage measurement")
    for key, value in stages.items():
        if type(value) is not int or value < 0:
            raise ValueError("Invalid indexing stage duration: " + key)


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
