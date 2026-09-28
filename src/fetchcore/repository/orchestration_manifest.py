"""Declarative manifest contract for multi-repository indexing orchestration."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml


REPOSITORY_ORCHESTRATION_VERSION = 1
DEFAULT_MAX_CONCURRENCY = 4
MAX_CONCURRENCY = 32

_ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {"version", "max_concurrency", "purposes", "repositories"}
)
_ALLOWED_PURPOSE_KEYS = frozenset({"projection"})
_ALLOWED_PROJECTION_KEYS = frozenset({"backend", "collection"})
_ALLOWED_REPOSITORY_KEYS = frozenset(
    {"name", "source", "purpose", "repository", "branch", "revision", "enabled"}
)
_ALLOWED_SOURCE_KEYS = frozenset({"provider", "path", "url"})


class RepositoryOrchestrationError(ValueError):
    """Raised when a repository orchestration manifest is invalid."""


@dataclass(frozen=True)
class RepositoryPurpose:
    name: str
    backend: str
    collection: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "collection": self.collection,
        }


@dataclass(frozen=True)
class RepositoryTarget:
    name: str
    provider: str
    source: str | Path
    purpose: str
    repository: str | None = None
    branch: str | None = None
    revision: str | None = None
    enabled: bool = True

    def to_mapping(self) -> dict[str, object]:
        return {
            "name": self.name,
            "provider": self.provider,
            "source": str(self.source),
            "purpose": self.purpose,
            "repository": self.repository,
            "branch": self.branch,
            "revision": self.revision,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class RepositoryOrchestrationManifest:
    path: Path
    max_concurrency: int
    purposes: dict[str, RepositoryPurpose]
    repositories: tuple[RepositoryTarget, ...]
    version: int = REPOSITORY_ORCHESTRATION_VERSION

    def to_mapping(self) -> dict[str, object]:
        return {
            "version": self.version,
            "path": str(self.path),
            "max_concurrency": self.max_concurrency,
            "purposes": {
                name: purpose.to_mapping()
                for name, purpose in sorted(self.purposes.items())
            },
            "repositories": [item.to_mapping() for item in self.repositories],
        }


def load_repository_orchestration_manifest(path: Path) -> RepositoryOrchestrationManifest:
    """Load and validate a multi-repository orchestration manifest."""

    try:
        manifest_path = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise RepositoryOrchestrationError(
            f"repository orchestration manifest does not exist: {path}"
        ) from exc
    if not manifest_path.is_file():
        raise RepositoryOrchestrationError(
            f"repository orchestration manifest is not a file: {path}"
        )
    payload = _read_manifest(manifest_path)
    _reject_unknown_keys(payload, _ALLOWED_TOP_LEVEL_KEYS, context="manifest")

    version = _integer(
        payload.get("version", REPOSITORY_ORCHESTRATION_VERSION),
        "version",
    )
    if version != REPOSITORY_ORCHESTRATION_VERSION:
        raise RepositoryOrchestrationError(
            f"unsupported repository orchestration version {version}; "
            f"expected {REPOSITORY_ORCHESTRATION_VERSION}"
        )

    max_concurrency = _integer(
        payload.get("max_concurrency", DEFAULT_MAX_CONCURRENCY),
        "max_concurrency",
    )
    if not 1 <= max_concurrency <= MAX_CONCURRENCY:
        raise RepositoryOrchestrationError(
            f"max_concurrency must be between 1 and {MAX_CONCURRENCY}"
        )

    purposes = _purposes(payload.get("purposes"))
    repositories = _repositories(
        payload.get("repositories"),
        manifest_path=manifest_path,
        purposes=purposes,
    )
    _validate_manifest_uniqueness(purposes, repositories)

    return RepositoryOrchestrationManifest(
        path=manifest_path,
        max_concurrency=max_concurrency,
        purposes=purposes,
        repositories=repositories,
        version=version,
    )


def _purposes(value: object) -> dict[str, RepositoryPurpose]:
    if not isinstance(value, dict) or not value:
        raise RepositoryOrchestrationError("purposes must be a non-empty mapping")
    result: dict[str, RepositoryPurpose] = {}
    for raw_name, raw_value in value.items():
        name = str(raw_name).strip()
        if not name:
            raise RepositoryOrchestrationError("purpose names must not be empty")
        purpose = _mapping(raw_value, f"purposes.{name}")
        _reject_unknown_keys(purpose, _ALLOWED_PURPOSE_KEYS, context=f"purposes.{name}")
        projection = _mapping(
            purpose.get("projection"),
            f"purposes.{name}.projection",
        )
        _reject_unknown_keys(
            projection,
            _ALLOWED_PROJECTION_KEYS,
            context=f"purposes.{name}.projection",
        )
        backend = _required_string(
            projection,
            "backend",
            f"purposes.{name}.projection",
        )
        if backend != "solr":
            raise RepositoryOrchestrationError(
                f"unsupported projection backend {backend!r} for purpose {name!r}"
            )
        collection = _required_string(
            projection,
            "collection",
            f"purposes.{name}.projection",
        )
        result[name] = RepositoryPurpose(
            name=name,
            backend=backend,
            collection=collection,
        )
    return result


def _repositories(
    value: object,
    *,
    manifest_path: Path,
    purposes: Mapping[str, RepositoryPurpose],
) -> tuple[RepositoryTarget, ...]:
    if not isinstance(value, list) or not value:
        raise RepositoryOrchestrationError("repositories must be a non-empty list")
    result: list[RepositoryTarget] = []
    for index, raw in enumerate(value):
        context = f"repositories[{index}]"
        item = _mapping(raw, context)
        _reject_unknown_keys(item, _ALLOWED_REPOSITORY_KEYS, context=context)
        name = _required_string(item, "name", context)
        purpose = _required_string(item, "purpose", context)
        if purpose not in purposes:
            raise RepositoryOrchestrationError(
                f"{context}.purpose references unknown purpose {purpose!r}"
            )
        source = _mapping(item.get("source"), f"{context}.source")
        _reject_unknown_keys(source, _ALLOWED_SOURCE_KEYS, context=f"{context}.source")
        provider = _required_string(source, "provider", f"{context}.source")
        source_value = _source_value(
            source,
            provider=provider,
            manifest_path=manifest_path,
            context=f"{context}.source",
        )
        enabled = _boolean(item.get("enabled", True), f"{context}.enabled")
        result.append(
            RepositoryTarget(
                name=name,
                provider=provider,
                source=source_value,
                purpose=purpose,
                repository=_optional_string(item.get("repository"), f"{context}.repository"),
                branch=_optional_string(item.get("branch"), f"{context}.branch"),
                revision=_optional_string(item.get("revision"), f"{context}.revision"),
                enabled=enabled,
            )
        )
    return tuple(result)


def _source_value(
    source: Mapping[str, object],
    *,
    provider: str,
    manifest_path: Path,
    context: str,
) -> str | Path:
    raw_path = source.get("path")
    raw_url = source.get("url")
    has_path = raw_path is not None
    has_url = raw_url is not None

    if provider == "local-workspace":
        if has_url:
            raise RepositoryOrchestrationError(
                f"{context} local-workspace source uses 'path', not 'url'"
            )
        path_value = _required_string(source, "path", context)
        path = Path(path_value).expanduser()
        if not path.is_absolute():
            path = (manifest_path.parent / path).resolve()
        return path

    if provider == "git":
        if has_path:
            raise RepositoryOrchestrationError(
                f"{context} git source uses 'url', not 'path'"
            )
        url = _required_string(source, "url", context)
        _validate_git_url(url, context=context)
        return url

    if has_path == has_url:
        raise RepositoryOrchestrationError(
            f"{context} requires exactly one of 'path' or 'url'"
        )
    if has_path:
        path_value = _required_string(source, "path", context)
        path = Path(path_value).expanduser()
        if not path.is_absolute():
            path = (manifest_path.parent / path).resolve()
        return path
    return _required_string(source, "url", context)


def _validate_git_url(value: str, *, context: str) -> None:
    if "://" not in value:
        return
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.username is not None:
        raise RepositoryOrchestrationError(
            f"{context}.url must not embed HTTP credentials; use Git credential helpers "
            "or the runtime credential environment"
        )


def _validate_manifest_uniqueness(
    purposes: Mapping[str, RepositoryPurpose],
    repositories: tuple[RepositoryTarget, ...],
) -> None:
    duplicate_names = sorted(
        name for name, count in Counter(item.name for item in repositories).items() if count > 1
    )
    if duplicate_names:
        raise RepositoryOrchestrationError(
            "repository target names must be unique: " + ", ".join(duplicate_names)
        )
    duplicate_collections = sorted(
        collection
        for collection, count in Counter(
            item.collection for item in purposes.values()
        ).items()
        if count > 1
    )
    if duplicate_collections:
        raise RepositoryOrchestrationError(
            "each purpose must own a distinct collection: "
            + ", ".join(duplicate_collections)
        )


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RepositoryOrchestrationError(
            f"unable to read repository orchestration manifest: {path}"
        ) from exc
    if not isinstance(raw, dict):
        raise RepositoryOrchestrationError(
            "repository orchestration manifest must be a mapping"
        )
    return {str(key): value for key, value in raw.items()}


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RepositoryOrchestrationError(f"{context} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _reject_unknown_keys(
    value: Mapping[str, object],
    allowed: frozenset[str],
    *,
    context: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RepositoryOrchestrationError(
            f"unsupported {context} key(s): " + ", ".join(unknown)
        )


def _required_string(value: Mapping[str, object], key: str, context: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise RepositoryOrchestrationError(
            f"{context} requires non-empty {key!r}"
        )
    return raw.strip()


def _optional_string(value: object, context: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RepositoryOrchestrationError(f"{context} must be a non-empty string")
    return value.strip()


def _integer(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepositoryOrchestrationError(f"{context} must be an integer")
    return value


def _boolean(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise RepositoryOrchestrationError(f"{context} must be a boolean")
    return value
