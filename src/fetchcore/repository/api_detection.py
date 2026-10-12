"""Conservative deterministic preflight for API artifact format/version routing.

This is NOT a document parser or a permission to remove the S0 quarantine.
Only bounded version/signature facts are returned; source values are never output.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from fetchcore.repository.api_artifact_policy import blocked_api_artifact_kind

ApiDetectionStatus = Literal[
    "unrecognized", "supported", "unsupported", "invalid", "quarantined",
]
_SUPPORTED_OPENAPI = re.compile(r"^3\.(?:0|1)\.\d+$")
_SUPPORTED_SWAGGER = "2.0"
_SUPPORTED_POSTMAN = "2.1.0"
_POSTMAN_VERSION = re.compile(r"/v(\d+\.\d+\.\d+)/", re.IGNORECASE)
_YAML_VERSION = re.compile(
    r"""(?m)^(openapi|swagger):\s*['"]?([A-Za-z0-9._-]+)"""
)
_MAX_PREVIEW_CHARS = 256_000


@dataclass(frozen=True)
class ApiArtifactClassification:
    kind: str | None
    version: str | None
    status: ApiDetectionStatus
    reason: str
    source: Literal["none", "filename", "content"] = "none"

    def __post_init__(self) -> None:
        if self.status == "unrecognized" and self.kind is not None:
            raise ValueError("unrecognized artifact cannot declare an API kind")
        if self.kind is None and self.status != "unrecognized":
            raise ValueError("recognized artifact requires a kind")
        # Reasons/versions are fixed vocabulary, never source-derived strings.
        if self.reason not in {
            "no_signature", "supported_version", "unsupported_version",
            "invalid_shape", "invalid_syntax", "conflicting_signatures",
            "missing_version", "size_limit", "quarantined_by_policy",
        }:
            raise ValueError("unknown API classification reason")


def _closed_result(
    kind: str | None, version: str | None, status: ApiDetectionStatus,
    reason: str, source: Literal["none", "filename", "content"],
) -> ApiArtifactClassification:
    return ApiArtifactClassification(kind, version, status, reason, source)


def _duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    obj: dict[str, object] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate key")
        obj[key] = value
    return obj


def detect_api_artifact(relative: str, content: str) -> ApiArtifactClassification:
    """Report only closed format/version observations for quarantined sources.

    A filename hint alone never upgrades an artifact to supported; explicit
    content and version checks are required. Invalid/unsupported remains blocked.
    """

    kind = blocked_api_artifact_kind(relative, content)
    if kind is None:
        return _closed_result(None, None, "unrecognized", "no_signature", "none")
    by_name = blocked_api_artifact_kind(relative)
    source: Literal["none", "filename", "content"] = (
        "filename" if by_name is not None else "content"
    )
    if kind in {"postman_environment", "postman_v3"}:
        return _closed_result(kind, None, "quarantined", "quarantined_by_policy", source)
    if len(content) > _MAX_PREVIEW_CHARS:
        return _closed_result(kind, None, "invalid", "size_limit", source)

    suffix = relative.casefold().rsplit(".", 1)[-1]
    if suffix == "json":
        try:
            root = json.loads(content, object_pairs_hook=_duplicate_keys)
        except (ValueError, RecursionError):
            return _closed_result(kind, None, "invalid", "invalid_syntax", source)
        if not isinstance(root, dict):
            return _closed_result(kind, None, "invalid", "invalid_shape", source)

        if kind in {"openapi", "swagger"}:
            if "openapi" in root and "swagger" in root:
                return _closed_result(kind, None, "invalid", "conflicting_signatures", source)
            marker = root.get(kind)
            if marker is None:
                return _closed_result(kind, None, "invalid", "missing_version", source)
            if not isinstance(marker, str):
                return _closed_result(kind, None, "invalid", "invalid_shape", source)
            version = marker
        else:
            info = root.get("info")
            if not isinstance(info, dict) or not isinstance(info.get("schema"), str):
                return _closed_result(kind, None, "invalid", "invalid_shape", source)
            schema = info["schema"]
            match = _POSTMAN_VERSION.search(schema)
            if "schema.getpostman.com/json/collection/" not in schema.casefold() or match is None:
                return _closed_result(kind, None, "invalid", "missing_version", source)
            version = match.group(1)
    elif suffix in {"yml", "yaml"} and kind in {"openapi", "swagger"}:
        # A constrained, top-level version probe only. Full safe YAML parsing
        # and schema validation belong to S2; no aliases or refs are resolved.
        markers = _YAML_VERSION.findall(content)
        present = {key for key, _ in markers}
        if len(present) > 1 or len(markers) > 1:
            return _closed_result(kind, None, "invalid", "conflicting_signatures", source)
        if not markers or markers[0][0] != kind:
            return _closed_result(kind, None, "invalid", "missing_version", source)
        version = markers[0][1]
    else:
        return _closed_result(kind, None, "invalid", "invalid_shape", source)

    if kind == "openapi":
        supported = bool(_SUPPORTED_OPENAPI.fullmatch(version))
    elif kind == "swagger":
        supported = version == _SUPPORTED_SWAGGER
    else:
        supported = version == _SUPPORTED_POSTMAN
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version):
        return _closed_result(kind, None, "invalid", "invalid_shape", source)
    return _closed_result(
        kind, version,
        "supported" if supported else "unsupported",
        "supported_version" if supported else "unsupported_version", source,
    )


class ApiInterpreterRegistration(Protocol):
    """S2/S3-owned routing declaration; S1 never invokes interpreters."""

    indexing_contract_id: str
    indexing_contract_version: str
    api_kind: str
    api_versions: tuple[str, ...]


def select_api_interpreter(
    classification: ApiArtifactClassification,
    registrations: Sequence[ApiInterpreterRegistration],
) -> ApiInterpreterRegistration | None:
    """Select exactly one compatible interpreter; no implicit generic fallback.

    This is a preflight selection, not an admission bypass. The S0 quarantine
    remains mandatory until an actual sanitized extractor owns the source.
    """

    if classification.status == "unrecognized":
        return None
    if classification.status != "supported":
        raise ValueError("unsafe or unsupported API artifact cannot be routed")
    eligible = [
        item for item in registrations
        if item.api_kind == classification.kind
        and classification.version in item.api_versions
    ]
    if len(eligible) > 1:
        raise ValueError("ambiguous API interpreter registration")
    if not eligible:
        return None
    selected = eligible[0]
    if not selected.indexing_contract_id or not selected.indexing_contract_version:
        raise ValueError("API interpreter registration requires a fingerprint identity")
    return selected
