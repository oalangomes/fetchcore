"""Bounded composition of validated indexing observations, outside retrieval execution."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
import re
from typing import Any

from .index_artifacts import IndexRunArtifact


@dataclass(frozen=True)
class OperationalRunIdentity:
    """Operator-supplied destination and run correlation, without endpoint secrets."""

    run_id: str
    repository: str
    backend: str
    target: str
    configuration_fingerprint: str


def compose_run_manifest(
    observations: Sequence[IndexRunArtifact], *, identity: OperationalRunIdentity,
    requested: str, revision: str, outcome: str,
) -> dict[str, Any]:
    """Project measured facts only; raw paths, endpoints and error messages stay out.

    The workflow outcome is separate from individual command outcomes: a run can
    fail after a successful sync. Discovery scans never imply persistent rebuilds.
    """
    if len(observations) > 16:
        raise ValueError("A run supports at most 16 observations")
    if requested not in {"plan", "sync", "rebuild"} or outcome not in {
        "success", "failure", "cancelled",
    } or not revision.strip():
        raise ValueError("Invalid run lifecycle, outcome or revision")
    if not observations and outcome == "success":
        raise ValueError("A successful run requires command evidence")
    context = asdict(identity)
    if any(not value.strip() or len(value) > 4096 for value in context.values()):
        raise ValueError("Invalid run identity")
    if not re.fullmatch(r"[0-9a-f]{64}", identity.configuration_fingerprint):
        raise ValueError("Invalid configuration fingerprint")
    commands = []
    for observation in observations:
        # Revalidate at the public composition boundary, including callers that
        # constructed the dataclass directly rather than loading its JSON.
        observation = IndexRunArtifact.from_mapping(observation.to_mapping())
        if any(value != getattr(observation, key) for key, value in context.items()):
            raise ValueError("Cannot compose observations from different runs or destinations")
        command = observation.command
        item: dict[str, Any] = {
            "operation": command["operation"], "status": command["status"],
            "observed_at": observation.observed_at,
        }
        if command["status"] == "failed":
            if outcome == "success":
                raise ValueError("A successful run cannot contain a failed command")
            item["error"] = {"category": command["error"]["category"]}
            diagnostics = command["error"].get("diagnostics")
            if diagnostics:
                item["error"]["diagnostics"] = diagnostics
        else:
            result = command["result"]
            if result.get("target_revision") not in {None, revision}:
                raise ValueError("Run target revision mismatch")
            # These are canonical observations validated by IndexRunArtifact.
            for key in ("mode", "reason", "target_revision", "indexed_revision", "currentness",
                        "previous_indexed_revision", "previous_currentness", "retrieval_mode",
                        "indexing_fingerprint", "projection_fingerprint", "stage_timings_ms"):
                if key in result:
                    item[key] = result[key]
            if "planned_mutations" in result:
                item["planned_mutations"] = {key: result["planned_mutations"][key] for key in (
                    "replace_repository", "added", "updated", "deleted", "unchanged",
                    "upsert_documents", "resulting_document_count", "previous_document_count",
                ) if key in result["planned_mutations"]}
            if "indexing_telemetry" in result:
                item["indexing_telemetry"] = {key: result["indexing_telemetry"][key] for key in (
                    "files_discovered", "files_scanned", "files_read", "files_indexed",
                    "files_changed", "files_skipped", "chunks_total", "chunks_changed",
                    "added", "updated", "removed", "unchanged", "duration_ms",
                ) if key in result["indexing_telemetry"]}
            discovery = result.get("discovery")
            execution = result.get("execution")
            if execution is not None:
                item["execution"] = {
                    key: execution[key] for key in (
                        "mode", "noop", "added", "updated", "deleted", "unchanged",
                        "duration_ms", "stage_timings_ms",
                    ) if key in execution
                }
                if "telemetry" in execution:
                    item["execution"]["telemetry"] = {key: execution["telemetry"][key]
                        for key in ("duration_ms", "backend_writes", "backend_deletes",
                                    "embedding_requests", "embedding_vectors", "embedding_duration_ms",
                                    "embedding_throughput_vectors_per_second", "embedding_cache_hits",
                                    "stage_durations_ms") if key in execution["telemetry"]}
                item["execution"]["snapshot"] = {key: execution["snapshot"][key] for key in (
                    "repository", "revision", "document_count", "corpus_checksum", "indexed_at",
                    "projection_version", "indexing_fingerprint", "projection_fingerprint",
                    "embedding_provider", "embedding_model", "vector_dimensions",
                    "serializer_mode", "serializer_version", "serializer_fingerprint",
                ) if key in execution["snapshot"]}
                discovery = execution.get("discovery", discovery)
            if discovery is not None:
                item["discovery"] = {key: discovery[key] for key in (
                    "mode", "reason", "base_revision", "current_revision", "changed_paths",
                    "removed_paths", "targeted_paths", "discovery_duration_ms", "git_diff_duration_ms",
                ) if key in discovery}
        commands.append(item)
    return {
        "schema_version": 1, "kind": "fetchcore.operational-run",
        **context,
        "revision": revision, "requested_lifecycle": requested, "outcome": outcome,
        "observations": commands,
    }
