"""Repository-local policy for binary/document knowledge sources.

Document-source policy is intentionally separate from text-source selection in
``.agentsorch/indexing.yaml``. This prevents binary acquisition limits and
format enablement from turning ``RepositoryIndexConfig`` into an unrelated
configuration god object.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from fetchcore.repository.index_policy import RepositoryIndexPolicyError


DOCUMENT_INDEX_POLICY_VERSION = 1
DOCUMENT_INDEX_POLICY_PATH = Path(".agentsorch/document-indexing.yaml")
DEFAULT_DOCUMENT_INCLUDE_PATTERNS = ("*.pdf", "*.xlsx")
DEFAULT_DOCUMENT_FORMATS = ("pdf", "xlsx")
DEFAULT_MAX_DOCUMENT_FILES = 200
DEFAULT_MAX_DOCUMENT_FILE_BYTES = 20_000_000
DEFAULT_MAX_DOCUMENT_TOTAL_BYTES = 64_000_000
_ALLOWED_KEYS = frozenset(
    {
        "version",
        "enabled",
        "include",
        "replace_default_includes",
        "formats",
        "limits",
    }
)
_ALLOWED_LIMIT_KEYS = frozenset({"max_files", "max_file_bytes", "max_total_bytes"})
_KNOWN_FORMATS = frozenset(DEFAULT_DOCUMENT_FORMATS)


class RepositoryDocumentPolicyError(RepositoryIndexPolicyError):
    """Raised when repository document indexing policy is malformed."""


@dataclass(frozen=True)
class RepositoryDocumentIndexPolicy:
    """Resolved policy for binary/document knowledge sources."""

    enabled: bool = False
    include_patterns: tuple[str, ...] = DEFAULT_DOCUMENT_INCLUDE_PATTERNS
    formats: tuple[str, ...] = DEFAULT_DOCUMENT_FORMATS
    max_files: int = DEFAULT_MAX_DOCUMENT_FILES
    max_file_bytes: int = DEFAULT_MAX_DOCUMENT_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_DOCUMENT_TOTAL_BYTES
    source: Path | None = None
    version: int = DOCUMENT_INDEX_POLICY_VERSION

    def __post_init__(self) -> None:
        if any(
            value < 1
            for value in (self.max_files, self.max_file_bytes, self.max_total_bytes)
        ):
            raise ValueError("repository document indexing limits must be positive")
        if self.enabled and not self.include_patterns:
            raise ValueError(
                "repository document include patterns must not be empty when enabled"
            )
        unknown = sorted(set(self.formats) - _KNOWN_FORMATS)
        if unknown:
            raise ValueError(
                "unknown repository document format(s): " + ", ".join(unknown)
            )

    def to_mapping(self) -> dict[str, object]:
        payload = asdict(self)
        payload["source"] = None if self.source is None else str(self.source)
        payload["include_patterns"] = list(self.include_patterns)
        payload["formats"] = list(self.formats)
        return payload


def load_repository_document_index_policy(workspace: Path) -> RepositoryDocumentIndexPolicy:
    """Load optional ``.agentsorch/document-indexing.yaml`` or canonical defaults."""

    root = workspace.resolve(strict=True)
    if not root.is_dir():
        raise RepositoryDocumentPolicyError(
            f"repository workspace is not a directory: {workspace}"
        )
    policy_path = root / DOCUMENT_INDEX_POLICY_PATH
    if not policy_path.is_file():
        return RepositoryDocumentIndexPolicy()

    try:
        raw = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RepositoryDocumentPolicyError(
            f"unable to read repository document indexing policy: {policy_path}"
        ) from exc
    if not isinstance(raw, dict):
        raise RepositoryDocumentPolicyError(
            "repository document indexing policy must be a mapping"
        )
    payload = {str(key): value for key, value in raw.items()}
    _reject_unknown(payload, _ALLOWED_KEYS, "document indexing policy")

    version = _integer(payload.get("version", DOCUMENT_INDEX_POLICY_VERSION), "version")
    if version != DOCUMENT_INDEX_POLICY_VERSION:
        raise RepositoryDocumentPolicyError(
            f"unsupported document indexing policy version {version}; "
            f"expected {DOCUMENT_INDEX_POLICY_VERSION}"
        )
    enabled = _boolean(payload.get("enabled", False), "enabled")
    replace_defaults = _boolean(
        payload.get("replace_default_includes", False),
        "replace_default_includes",
    )
    configured_includes = _strings(payload.get("include"), "include")
    if replace_defaults:
        if enabled and not configured_includes:
            raise RepositoryDocumentPolicyError(
                "include must be non-empty when enabled and replace_default_includes is true"
            )
        include_patterns = configured_includes
    else:
        include_patterns = _dedupe(
            (*DEFAULT_DOCUMENT_INCLUDE_PATTERNS, *configured_includes)
        )

    raw_formats = payload.get("formats")
    formats = (
        DEFAULT_DOCUMENT_FORMATS
        if raw_formats is None
        else _strings(raw_formats, "formats")
    )
    unknown = sorted(set(formats) - _KNOWN_FORMATS)
    if unknown:
        raise RepositoryDocumentPolicyError(
            "unknown repository document format(s): " + ", ".join(unknown)
        )
    if enabled and not formats:
        raise RepositoryDocumentPolicyError("formats must not be empty when enabled")

    limits = payload.get("limits")
    if limits is None:
        limits_mapping: dict[str, object] = {}
    elif isinstance(limits, dict):
        limits_mapping = {str(key): value for key, value in limits.items()}
    else:
        raise RepositoryDocumentPolicyError("limits must be a mapping")
    _reject_unknown(limits_mapping, _ALLOWED_LIMIT_KEYS, "document limits")

    try:
        return RepositoryDocumentIndexPolicy(
            enabled=enabled,
            include_patterns=include_patterns,
            formats=formats,
            max_files=_positive_integer(
                limits_mapping.get("max_files"),
                "limits.max_files",
                DEFAULT_MAX_DOCUMENT_FILES,
            ),
            max_file_bytes=_positive_integer(
                limits_mapping.get("max_file_bytes"),
                "limits.max_file_bytes",
                DEFAULT_MAX_DOCUMENT_FILE_BYTES,
            ),
            max_total_bytes=_positive_integer(
                limits_mapping.get("max_total_bytes"),
                "limits.max_total_bytes",
                DEFAULT_MAX_DOCUMENT_TOTAL_BYTES,
            ),
            source=policy_path,
            version=version,
        )
    except ValueError as exc:
        raise RepositoryDocumentPolicyError(str(exc)) from exc


def _reject_unknown(
    value: dict[str, object], allowed: frozenset[str], context: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RepositoryDocumentPolicyError(
            f"unsupported {context} key(s): " + ", ".join(unknown)
        )


def _strings(value: object, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RepositoryDocumentPolicyError(f"{context} must be a list of strings")
    result = tuple(item.strip() for item in value if item.strip())
    if len(result) != len(value):
        raise RepositoryDocumentPolicyError(
            f"{context} must not contain empty values"
        )
    if len(result) != len(set(result)):
        raise RepositoryDocumentPolicyError(f"{context} must not contain duplicates")
    return result


def _positive_integer(value: object, context: str, default: int) -> int:
    if value is None:
        return default
    rendered = _integer(value, context)
    if rendered < 1:
        raise RepositoryDocumentPolicyError(f"{context} must be positive")
    return rendered


def _integer(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepositoryDocumentPolicyError(f"{context} must be an integer")
    return value


def _boolean(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise RepositoryDocumentPolicyError(f"{context} must be a boolean")
    return value


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
