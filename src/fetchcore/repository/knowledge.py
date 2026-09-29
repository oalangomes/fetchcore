"""Repository-owned knowledge manifest, freshness, and refresh planning contracts."""

from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import yaml


FreshnessStatus = Literal["fresh", "stale", "unknown", "missing", "invalid"]
ValidationStatus = Literal["valid", "invalid", "missing", "skipped", "unavailable"]
ArtifactValidationStatus = Literal["valid", "invalid", "missing", "unknown"]


@dataclass(frozen=True)
class KnowledgeVocabulary:
    domains: tuple[str, ...] = ()
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    path_topology: dict[str, tuple[str, ...]] = field(default_factory=dict)
    preferred_search_scopes: tuple[str, ...] = ()
    category_mappings: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: object) -> KnowledgeVocabulary:
        raw = _mapping(data)
        aliases = _mapping(raw.get("aliases"))
        topology = _mapping(raw.get("path_topology"))
        return cls(
            domains=_strings(raw.get("domains")),
            aliases={str(key): _strings(value) for key, value in aliases.items()},
            path_topology={str(key): _strings(value) for key, value in topology.items()},
            preferred_search_scopes=_strings(raw.get("preferred_search_scopes")),
            category_mappings={
                str(key): str(value)
                for key, value in _mapping(raw.get("category_mappings")).items()
            },
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "domains": list(self.domains),
            "aliases": {key: list(value) for key, value in self.aliases.items()},
            "path_topology": {
                key: list(value) for key, value in self.path_topology.items()
            },
            "preferred_search_scopes": list(self.preferred_search_scopes),
            "category_mappings": self.category_mappings,
        }


@dataclass(frozen=True)
class KnowledgeSource:
    id: str
    type: str
    paths: tuple[str, ...]
    knowledge_types: tuple[str, ...] = ()
    classification: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: object) -> KnowledgeSource:
        raw = _mapping(data)
        source_id = _required_string(raw, "id")
        paths = _strings(raw.get("paths"))
        path = raw.get("path")
        if path is not None:
            paths = _unique((*paths, str(path)))
        if not paths:
            raise ValueError(f"repository knowledge source {source_id!r} requires path(s)")
        return cls(
            id=source_id,
            type=_required_string(raw, "type"),
            paths=paths,
            knowledge_types=_strings(raw.get("knowledge_types")),
            classification=_optional_string(raw.get("classification")),
            metadata=_mapping(raw.get("metadata")),
        )

    def to_mapping(self) -> dict[str, object]:
        return _compact(
            {
                **asdict(self),
                "paths": list(self.paths),
                "knowledge_types": list(self.knowledge_types),
            }
        )


@dataclass(frozen=True)
class RefreshContract:
    command: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    when: str | None = None
    capability_checks: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: object) -> RefreshContract | None:
        if data is None:
            return None
        raw = _mapping(data)
        command_value = raw.get("command", [])
        command = (
            (command_value,)
            if isinstance(command_value, str) and command_value.strip()
            else _strings(command_value)
        )
        return cls(
            command=command,
            triggers=_strings(raw.get("triggers")),
            inputs=_strings(raw.get("inputs")),
            outputs=_strings(raw.get("outputs")),
            when=_optional_string(raw.get("when")),
            capability_checks=_strings(raw.get("capability_checks")),
        )

    def to_mapping(self) -> dict[str, object]:
        return _compact(
            {
                **asdict(self),
                "command": list(self.command),
                "triggers": list(self.triggers),
                "inputs": list(self.inputs),
                "outputs": list(self.outputs),
                "capability_checks": list(self.capability_checks),
            }
        )


@dataclass(frozen=True)
class ValidatorContract:
    id: str
    type: str
    command: tuple[str, ...] = ()
    schema: str | None = None
    required: bool = True
    metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: object, *, ordinal: int) -> ValidatorContract:
        raw = _mapping(data)
        command_value = raw.get("command", [])
        command = (
            (command_value,)
            if isinstance(command_value, str) and command_value.strip()
            else _strings(command_value)
        )
        validator_type = str(raw.get("type", "command" if command else "required"))
        return cls(
            id=str(raw.get("id", f"validator-{ordinal}")),
            type=validator_type,
            command=command,
            schema=_optional_string(raw.get("schema")),
            required=bool(raw.get("required", True)),
            metadata=_mapping(raw.get("metadata")),
        )

    def to_mapping(self) -> dict[str, object]:
        return _compact({**asdict(self), "command": list(self.command)})


@dataclass(frozen=True)
class KnowledgeArtifact:
    id: str
    type: str
    path: str
    format: str
    knowledge_types: tuple[str, ...]
    derived_from: tuple[str, ...] = ()
    classification: str | None = None
    refresh: RefreshContract | None = None
    validators: tuple[ValidatorContract, ...] = ()
    freshness: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: object) -> KnowledgeArtifact:
        raw = _mapping(data)
        artifact_id = _required_string(raw, "id")
        path = _required_string(raw, "path")
        raw_validators = raw.get("validators", [])
        if not isinstance(raw_validators, list):
            raise ValueError(f"artifact {artifact_id!r} validators must be a list")
        knowledge_types = _strings(raw.get("knowledge_types"))
        if not knowledge_types:
            singular = raw.get("knowledge_type")
            knowledge_types = (str(singular),) if singular else (_default_knowledge_type(raw),)
        return cls(
            id=artifact_id,
            type=_required_string(raw, "type"),
            path=path,
            format=str(raw.get("format", Path(path).suffix.lstrip(".") or "text")),
            knowledge_types=knowledge_types,
            derived_from=_strings(raw.get("derived_from")),
            classification=_optional_string(raw.get("classification")),
            refresh=RefreshContract.from_mapping(raw.get("refresh")),
            validators=tuple(
                ValidatorContract.from_mapping(item, ordinal=index)
                for index, item in enumerate(raw_validators, start=1)
            ),
            freshness=_mapping(raw.get("freshness")),
            metadata=_mapping(raw.get("metadata")),
        )

    def to_mapping(self) -> dict[str, object]:
        result = _compact(
            {
                **asdict(self),
                "knowledge_types": list(self.knowledge_types),
                "derived_from": list(self.derived_from),
            }
        )
        if self.refresh is not None:
            result["refresh"] = self.refresh.to_mapping()
        result["validators"] = [item.to_mapping() for item in self.validators]
        return result


@dataclass(frozen=True)
class RepositoryKnowledgeManifest:
    schema_version: int = 1
    identity_terms: tuple[str, ...] = ()
    vocabulary: KnowledgeVocabulary = field(default_factory=KnowledgeVocabulary)
    sources: tuple[KnowledgeSource, ...] = ()
    artifacts: tuple[KnowledgeArtifact, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)
    loaded_from: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls, data: dict[str, object], *, loaded_from: tuple[str, ...] = ()
    ) -> RepositoryKnowledgeManifest:
        raw = _mapping(data.get("repository_knowledge", data))
        identity = _mapping(raw.get("identity"))
        sources_value = raw.get("sources", [])
        artifacts_value = raw.get("artifacts", [])
        if not isinstance(sources_value, list) or not isinstance(artifacts_value, list):
            raise ValueError("repository knowledge sources and artifacts must be lists")
        sources = tuple(KnowledgeSource.from_mapping(item) for item in sources_value)
        artifacts = tuple(KnowledgeArtifact.from_mapping(item) for item in artifacts_value)
        source_ids = {item.id for item in sources}
        artifact_ids = {item.id for item in artifacts}
        if len(source_ids) != len(sources) or len(artifact_ids) != len(artifacts):
            raise ValueError("repository knowledge ids must be unique within their section")
        unknown = sorted(
            {
                source_id
                for artifact in artifacts
                for source_id in artifact.derived_from
                if source_id not in source_ids and source_id not in artifact_ids
            }
        )
        if unknown:
            raise ValueError("unknown derived_from knowledge id(s): " + ", ".join(unknown))
        return cls(
            schema_version=int(str(raw.get("schema_version", 1))),
            identity_terms=_strings(identity.get("terms", raw.get("identity_terms", []))),
            vocabulary=KnowledgeVocabulary.from_mapping(raw.get("vocabulary")),
            sources=sources,
            artifacts=artifacts,
            metadata=_mapping(raw.get("metadata")),
            loaded_from=loaded_from,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "identity": {"terms": list(self.identity_terms)},
            "vocabulary": self.vocabulary.to_mapping(),
            "sources": [item.to_mapping() for item in self.sources],
            "artifacts": [item.to_mapping() for item in self.artifacts],
            "metadata": self.metadata,
            "loaded_from": list(self.loaded_from),
        }

    @property
    def empty(self) -> bool:
        return not (self.identity_terms or self.sources or self.artifacts)


@dataclass(frozen=True)
class ArtifactFreshness:
    artifact_id: str
    status: FreshnessStatus
    path: str
    reasons: tuple[str, ...] = ()
    observed_checksum: str | None = None
    source_checksum: str | None = None

    def to_mapping(self) -> dict[str, object]:
        return _compact({**asdict(self), "reasons": list(self.reasons)})


@dataclass(frozen=True)
class ValidationOutcome:
    artifact_id: str
    validator_id: str
    status: ValidationStatus
    reason: str
    command_executed: bool = False
    required: bool = True

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RefreshPlanItem:
    artifact_id: str
    status: FreshnessStatus
    command: tuple[str, ...]
    triggers: tuple[str, ...]
    action: str = "proposed"
    execution_allowed: bool = False

    def to_mapping(self) -> dict[str, object]:
        return {
            **asdict(self),
            "command": list(self.command),
            "triggers": list(self.triggers),
        }


def load_repository_knowledge(
    workspace: Path,
    *,
    global_config_dir: Path | None = None,
    include_global: bool = True,
) -> RepositoryKnowledgeManifest:
    """Merge builtin-empty, optional global, then repository-owned manifest."""

    workspace = workspace.resolve()
    merged: dict[str, object] = {"schema_version": 1}
    loaded: list[str] = []
    directories: list[Path] = []
    if include_global:
        if global_config_dir is not None:
            directories.append(global_config_dir.resolve())
        else:
            directories.append(
                _select_knowledge_directory(
                    Path.home() / ".config" / "fetchcore",
                    Path.home() / ".config" / "agentsorch",
                    scope="global",
                )
            )
    directories.append(
        _select_knowledge_directory(
            workspace / ".fetchcore",
            workspace / ".agentsorch",
            scope="repository",
        )
    )
    for directory in directories:
        for filename in ("repository-knowledge.json", "repository-knowledge.yaml"):
            path = directory / filename
            if not path.is_file():
                continue
            merged = _deep_merge(merged, _read_mapping(path))
            loaded.append(str(path))
    return RepositoryKnowledgeManifest.from_mapping(merged, loaded_from=tuple(loaded))


def _select_knowledge_directory(
    primary: Path,
    legacy: Path,
    *,
    scope: str,
) -> Path:
    """Prefer FetchCore config while rejecting ambiguous dual definitions."""

    filenames = ("repository-knowledge.json", "repository-knowledge.yaml")
    primary_has_manifest = any((primary / name).is_file() for name in filenames)
    legacy_has_manifest = any((legacy / name).is_file() for name in filenames)
    if primary_has_manifest and legacy_has_manifest:
        raise ValueError(
            f"both FetchCore and legacy {scope} repository knowledge manifests are "
            "present; remove one to avoid ambiguous configuration"
        )
    if primary_has_manifest or not legacy_has_manifest:
        return primary.resolve()
    return legacy.resolve()


def evaluate_artifact_freshness(
    manifest: RepositoryKnowledgeManifest,
    artifact: KnowledgeArtifact,
    workspace: Path,
) -> ArtifactFreshness:
    path = safe_repository_path(workspace, artifact.path)
    if not path.is_file():
        return ArtifactFreshness(artifact.id, "missing", artifact.path, ("artifact_missing",))
    configured = artifact.freshness
    if bool(configured.get("stale", False)):
        return ArtifactFreshness(artifact.id, "stale", artifact.path, ("explicit_stale",))
    try:
        observed = _file_checksum(path)
    except OSError:
        return ArtifactFreshness(artifact.id, "invalid", artifact.path, ("artifact_unreadable",))
    expected = _optional_string(configured.get("checksum"))
    if expected and not _checksum_matches(expected, observed):
        return ArtifactFreshness(
            artifact.id,
            "invalid",
            artifact.path,
            ("artifact_checksum_mismatch",),
            observed_checksum=observed,
        )
    source_revision = _optional_string(configured.get("source_revision"))
    generated_revision = _optional_string(configured.get("generated_revision"))
    if source_revision and generated_revision and source_revision != generated_revision:
        return ArtifactFreshness(
            artifact.id,
            "stale",
            artifact.path,
            ("source_revision_changed",),
            observed_checksum=observed,
        )
    observed_source_checksum = _source_checksum(manifest, artifact, workspace)
    expected_source_checksum = _optional_string(configured.get("source_checksum"))
    if expected_source_checksum and observed_source_checksum is None:
        return ArtifactFreshness(
            artifact.id,
            "unknown",
            artifact.path,
            ("source_checksum_unavailable",),
            observed_checksum=observed,
        )
    if (
        expected_source_checksum
        and observed_source_checksum
        and not _checksum_matches(expected_source_checksum, observed_source_checksum)
    ):
        return ArtifactFreshness(
            artifact.id,
            "stale",
            artifact.path,
            ("source_checksum_changed",),
            observed_checksum=observed,
            source_checksum=observed_source_checksum,
        )
    has_deterministic_signal = bool(
        expected or (source_revision and generated_revision) or expected_source_checksum
    )
    return ArtifactFreshness(
        artifact.id,
        "fresh" if has_deterministic_signal else "unknown",
        artifact.path,
        ("deterministic_freshness_match",) if has_deterministic_signal else ("no_freshness_signal",),
        observed_checksum=observed,
        source_checksum=observed_source_checksum,
    )


def validate_artifact_contracts(
    artifact: KnowledgeArtifact,
    workspace: Path,
) -> tuple[ValidationOutcome, ...]:
    """Evaluate safe validators; command validators are intentionally not executed."""

    path = safe_repository_path(workspace, artifact.path)
    outcomes: list[ValidationOutcome] = []
    for validator in artifact.validators:
        if validator.type in {"required", "exists"}:
            status: ValidationStatus = "valid" if path.is_file() else "missing"
            outcomes.append(
                ValidationOutcome(
                    artifact.id,
                    validator.id,
                    status,
                    "artifact_exists" if path.is_file() else "artifact_missing",
                    required=validator.required,
                )
            )
        elif validator.type == "json":
            if not path.is_file():
                outcomes.append(
                    ValidationOutcome(
                        artifact.id,
                        validator.id,
                        "missing",
                        "artifact_missing",
                        required=validator.required,
                    )
                )
                continue
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                outcomes.append(
                    ValidationOutcome(
                        artifact.id,
                        validator.id,
                        "invalid",
                        "invalid_json",
                        required=validator.required,
                    )
                )
            else:
                outcomes.append(
                    ValidationOutcome(
                        artifact.id,
                        validator.id,
                        "valid",
                        "valid_json",
                        required=validator.required,
                    )
                )
        elif validator.type == "schema":
            outcomes.append(
                ValidationOutcome(
                    artifact.id,
                    validator.id,
                    "unavailable" if path.is_file() else "missing",
                    (
                        "schema_validation_not_implemented"
                        if path.is_file()
                        else "artifact_missing"
                    ),
                    required=validator.required,
                )
            )
        elif validator.command:
            outcomes.append(
                ValidationOutcome(
                    artifact.id,
                    validator.id,
                    "skipped",
                    "command_execution_requires_explicit_policy",
                    required=validator.required,
                )
            )
        else:
            outcomes.append(
                ValidationOutcome(
                    artifact.id,
                    validator.id,
                    "unavailable",
                    "validator_not_supported",
                    required=validator.required,
                )
            )
    return tuple(outcomes)


def aggregate_artifact_validation(
    outcomes: Iterable[ValidationOutcome],
) -> ArtifactValidationStatus:
    """Aggregate validator outcomes without upgrading incomplete validation."""

    evaluated = tuple(outcomes)
    if not evaluated:
        return "unknown"
    if any(item.status == "invalid" for item in evaluated):
        return "invalid"
    if any(item.status == "missing" and item.required for item in evaluated):
        return "missing"
    if any(item.status != "valid" for item in evaluated):
        return "unknown"
    return "valid"


def plan_refresh(
    manifest: RepositoryKnowledgeManifest,
    workspace: Path,
    *,
    changed_paths: tuple[str, ...] = (),
) -> tuple[RefreshPlanItem, ...]:
    result: list[RefreshPlanItem] = []
    for artifact in manifest.artifacts:
        freshness = evaluate_artifact_freshness(manifest, artifact, workspace)
        refresh = artifact.refresh
        invalidated = bool(
            refresh
            and changed_paths
            and any(
                fnmatch.fnmatch(path, pattern)
                for path in changed_paths
                for pattern in refresh.triggers
            )
        )
        if refresh is None or not refresh.command:
            continue
        if freshness.status not in {"stale", "missing", "invalid"} and not invalidated:
            continue
        result.append(
            RefreshPlanItem(
                artifact_id=artifact.id,
                status="stale" if invalidated and freshness.status == "fresh" else freshness.status,
                command=refresh.command,
                triggers=refresh.triggers,
            )
        )
    return tuple(result)


def safe_repository_path(workspace: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise ValueError("repository knowledge paths must be relative")
    root = workspace.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"repository knowledge path escapes workspace: {relative}") from exc
    return resolved


def _source_checksum(
    manifest: RepositoryKnowledgeManifest,
    artifact: KnowledgeArtifact,
    workspace: Path,
) -> str | None:
    sources_by_id = {item.id: item for item in manifest.sources}
    if not artifact.derived_from:
        return None
    paths: list[Path] = []
    for source_id in artifact.derived_from:
        source = sources_by_id.get(source_id)
        if source is None:
            return None
        source_paths: list[Path] = []
        for pattern in source.paths:
            try:
                source_paths.extend(
                    path
                    for path in workspace.glob(pattern)
                    if path.is_file() and _is_within(workspace, path)
                )
            except OSError:
                return None
        if not source_paths:
            return None
        paths.extend(source_paths)
    digest = hashlib.sha256()
    try:
        for path in sorted(set(paths)):
            digest.update(path.relative_to(workspace).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    except OSError:
        return None
    return "sha256:" + digest.hexdigest()


def _is_within(workspace: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return False
    return True


def _file_checksum(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _checksum_matches(expected: str, observed: str) -> bool:
    return expected == observed or f"sha256:{expected}" == observed


def _default_knowledge_type(raw: dict[str, object]) -> str:
    artifact_type = str(raw.get("type", "documentation"))
    return {
        "generated_index": "code",
        "generated_documentation": "documentation",
        "contract": "contract",
        "test": "tests",
        "decision": "decisions",
        "business_rule": "business_rules",
        "architecture": "architecture",
        "configuration": "configuration",
    }.get(artifact_type, "documentation")


def _read_mapping(path: Path) -> dict[str, object]:
    if path.suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"repository knowledge manifest must be a mapping: {path}")
    return {str(key): value for key, value in raw.items()}


def _deep_merge(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(base)
    for raw_key, value in override.items():
        key = str(raw_key)
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _deep_merge(_mapping(current), _mapping(value))
        else:
            result[key] = copy.deepcopy(value)
    return result


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return _unique(str(item) for item in value if str(item).strip())


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item) for item in values))


def _required_string(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"repository knowledge key {key!r} must be a non-empty string")
    return value.strip()


def _optional_string(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _compact(data: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in data.items() if value not in (None, (), {}, [])}
