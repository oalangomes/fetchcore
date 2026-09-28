"""Domain-neutral semantic evidence contracts for Repository Knowledge."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypeVar, cast

_SEMANTIC_NAME = re.compile(r"^[a-z][a-z0-9._-]*$")
_SEMANTIC_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_RELATION_KIND = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class SemanticEvidenceEnvelope:
    """Canonical versioned envelope for one pack-owned typed evidence payload."""

    namespace: str
    kind: str
    version: str
    payload_json: str

    def __post_init__(self) -> None:
        """Validate schema identity and canonicalize deterministic JSON bytes."""
        namespace = self.namespace.strip().casefold()
        kind = self.kind.strip().casefold()
        version = self.version.strip()
        if _SEMANTIC_NAME.fullmatch(namespace) is None:
            raise ValueError("semantic evidence namespace is invalid")
        if _SEMANTIC_NAME.fullmatch(kind) is None:
            raise ValueError("semantic evidence kind is invalid")
        if _SEMANTIC_VERSION.fullmatch(version) is None:
            raise ValueError("semantic evidence version is invalid")
        try:
            payload = json.loads(self.payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError("semantic evidence payload must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("semantic evidence payload must be a JSON object")
        object.__setattr__(self, "namespace", namespace)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "version", version)
        object.__setattr__(
            self,
            "payload_json",
            _canonical_json(cast(Mapping[str, object], payload)),
        )

    @classmethod
    def from_payload(
        cls,
        *,
        namespace: str,
        kind: str,
        version: str,
        payload: Mapping[str, object],
    ) -> SemanticEvidenceEnvelope:
        """Build an envelope from a pack-owned typed payload mapping."""
        return cls(namespace, kind, version, _canonical_json(payload))

    @property
    def schema_id(self) -> str:
        """Return the stable pack-owned schema identity."""
        return f"{self.namespace}/{self.kind}@{self.version}"

    def payload(self) -> dict[str, object]:
        """Return the validated payload as a fresh mapping."""
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise ValueError("semantic evidence payload must be an object")
        return cast(dict[str, object], value)

    def to_mapping(self) -> dict[str, object]:
        """Serialize the canonical envelope."""
        return {
            "namespace": self.namespace,
            "kind": self.kind,
            "version": self.version,
            "payload": self.payload(),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SemanticEvidenceEnvelope:
        """Hydrate one envelope and reject malformed canonical evidence."""
        payload = value.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("semantic evidence mapping requires an object payload")
        return cls.from_payload(
            namespace=_required_string(value, "namespace", "semantic evidence"),
            kind=_required_string(value, "kind", "semantic evidence"),
            version=_required_string(value, "version", "semantic evidence"),
            payload=cast(Mapping[str, object], payload),
        )


SemanticValue = TypeVar("SemanticValue")


class SemanticEvidenceCodec(Protocol[SemanticValue]):
    """Pack-owned typed codec for one semantic evidence schema."""

    namespace: str
    kind: str
    version: str

    def to_payload(self, value: SemanticValue) -> Mapping[str, object]:
        """Serialize one typed value into the codec-owned payload schema."""
        ...

    def from_payload(self, value: Mapping[str, object]) -> SemanticValue:
        """Hydrate one typed value from the codec-owned payload schema."""
        ...


def encode_semantic_evidence(
    codec: SemanticEvidenceCodec[SemanticValue],
    value: SemanticValue,
) -> SemanticEvidenceEnvelope:
    """Encode a typed domain value into deterministic canonical evidence."""
    return SemanticEvidenceEnvelope.from_payload(
        namespace=codec.namespace,
        kind=codec.kind,
        version=codec.version,
        payload=codec.to_payload(value),
    )


def decode_semantic_evidence(
    codec: SemanticEvidenceCodec[SemanticValue],
    envelope: SemanticEvidenceEnvelope,
) -> SemanticValue:
    """Decode only evidence owned by the supplied codec schema."""
    expected = (
        codec.namespace.strip().casefold(),
        codec.kind.strip().casefold(),
        codec.version.strip(),
    )
    actual = (envelope.namespace, envelope.kind, envelope.version)
    if actual != expected:
        raise ValueError(
            f"semantic evidence schema mismatch: expected {expected!r}, got {actual!r}"
        )
    return codec.from_payload(envelope.payload())


@dataclass(frozen=True)
class SemanticRelationEvidence:
    """Generic relation observed or exactly resolved from canonical evidence."""

    kind: str
    target: str
    resolution: str = "observed"
    evidence_basis: str = "source"
    target_document_id: str | None = None
    target_source_ref: str | None = None
    target_path: str | None = None

    def __post_init__(self) -> None:
        """Normalize generic relation identity and optional target provenance."""
        kind = self.kind.strip().casefold()
        target = self.target.strip()
        resolution = self.resolution.strip().casefold()
        basis = self.evidence_basis.strip().casefold()
        if _RELATION_KIND.fullmatch(kind) is None:
            raise ValueError("semantic relation kind is invalid")
        if not target:
            raise ValueError("semantic relation target must not be empty")
        if _SEMANTIC_NAME.fullmatch(resolution) is None:
            raise ValueError("semantic relation resolution is invalid")
        if _SEMANTIC_NAME.fullmatch(basis) is None:
            raise ValueError("semantic relation evidence_basis is invalid")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "resolution", resolution)
        object.__setattr__(self, "evidence_basis", basis)
        object.__setattr__(self, "target_document_id", _clean_optional(self.target_document_id))
        object.__setattr__(self, "target_source_ref", _clean_optional(self.target_source_ref))
        object.__setattr__(self, "target_path", _clean_optional(self.target_path))

    def to_mapping(self) -> dict[str, object]:
        """Serialize the relation without backend or graph concepts."""
        return {
            "kind": self.kind,
            "target": self.target,
            "resolution": self.resolution,
            "evidence_basis": self.evidence_basis,
            "target_document_id": self.target_document_id,
            "target_source_ref": self.target_source_ref,
            "target_path": self.target_path,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SemanticRelationEvidence:
        """Hydrate one relation and reject malformed canonical evidence."""
        return cls(
            kind=_required_string(value, "kind", "semantic relation"),
            target=_required_string(value, "target", "semantic relation"),
            resolution=_string_with_default(value, "resolution", "observed"),
            evidence_basis=_string_with_default(value, "evidence_basis", "source"),
            target_document_id=_optional_mapping_string(value, "target_document_id"),
            target_source_ref=_optional_mapping_string(value, "target_source_ref"),
            target_path=_optional_mapping_string(value, "target_path"),
        )


def semantic_evidence_from_mapping(
    value: object,
) -> tuple[SemanticEvidenceEnvelope, ...]:
    """Hydrate envelopes strictly so malformed evidence cannot disappear."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("semantic evidence must be a sequence")
    result: list[SemanticEvidenceEnvelope] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"semantic evidence item {index} must be an object")
        result.append(SemanticEvidenceEnvelope.from_mapping(item))
    return tuple(result)


def semantic_relations_from_mapping(
    value: object,
) -> tuple[SemanticRelationEvidence, ...]:
    """Hydrate relations strictly so malformed evidence cannot disappear."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("semantic relations must be a sequence")
    result: list[SemanticRelationEvidence] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"semantic relation item {index} must be an object")
        result.append(SemanticRelationEvidence.from_mapping(item))
    return tuple(result)


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("semantic evidence payload must be JSON-serializable") from exc


def _required_string(value: Mapping[str, object], key: str, subject: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{subject} {key} must be a non-empty string")
    return raw


def _string_with_default(value: Mapping[str, object], key: str, default: str) -> str:
    if key not in value:
        return default
    raw = value[key]
    if not isinstance(raw, str):
        raise ValueError(f"semantic relation {key} must be a string")
    return raw


def _optional_mapping_string(value: Mapping[str, object], key: str) -> str | None:
    if key not in value or value[key] is None:
        return None
    raw = value[key]
    if not isinstance(raw, str):
        raise ValueError(f"semantic relation {key} must be a string or null")
    return raw


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
