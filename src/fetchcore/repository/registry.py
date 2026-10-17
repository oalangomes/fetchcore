"""Versioned repository registrations, independent of indexing and retrieval."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .sources import RepositorySourceRequest

REGISTRY_VERSION = 1


class RepositoryRegistryError(ValueError):
    """Stable, safe-to-present repository operation failure."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def validate_reference(value: str) -> str:
    if (
        not value
        or value != value.strip()
        or value.startswith(("/", "-"))
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or not re.fullmatch(r"[\w.-]+(?:/[\w.-]+)*", value)
    ):
        raise RepositoryRegistryError(
            "invalid_repository_reference", "Invalid repository reference"
        )
    return value


def _safe_text(value: str) -> None:
    if not value or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise RepositoryRegistryError("unsafe_source", "Source contains unsafe characters")
    if "\\" in value or any(part == ".." for part in unquote(value).split("/")):
        raise RepositoryRegistryError("unsafe_source", "Source traversal is not allowed")


def local_source_path(value: str, allowed_roots: tuple[Path, ...]) -> Path:
    _safe_text(value)
    try:
        path = Path(value).expanduser().absolute()
        # Python 3.13 may leave a self-referential symlink unresolved with the
        # non-strict default; force resolution only for symlink sources.
        if path.is_symlink():
            path.resolve(strict=True)
        resolved = path.resolve()
        allowed = any(resolved.is_relative_to(root.resolve()) for root in allowed_roots)
    except (OSError, RuntimeError) as exc:
        raise RepositoryRegistryError(
            "unsafe_source", "Local source cannot be safely resolved"
        ) from exc
    if not allowed:
        raise RepositoryRegistryError("unsafe_source", "Local source is outside allowed roots")
    return resolved


def normalize_source(
    source: str,
    provider: str | None,
    allowed_roots: tuple[Path, ...],
) -> tuple[str, str]:
    """Validate before normalization; only expose the two existing source providers."""
    _safe_text(source)
    remote = "://" in source or bool(re.match(r"[^/]+@[^/]+:", source))
    provider = provider or ("git" if remote else "local-workspace")
    if provider not in {"git", "local-workspace"}:
        raise RepositoryRegistryError(
            "unsupported_source", "Unsupported repository source provider"
        )
    if provider == "local-workspace" or not remote:
        if remote or ":" in source or source.startswith("-"):
            raise RepositoryRegistryError("unsupported_source", "Unsupported local source")
        return provider, str(local_source_path(source, allowed_roots))
    if "://" not in source:
        if not re.fullmatch(r"git@[A-Za-z0-9.-]+:[\w./-]+", source):
            raise RepositoryRegistryError("unsafe_source", "Invalid SSH source")
        path = source.split(":", 1)[1]
        validate_reference(path.removesuffix("/").removesuffix(".git"))
        return provider, source.removesuffix("/")
    try:
        url = urlsplit(source)
        port = url.port
    except ValueError as exc:
        raise RepositoryRegistryError("unsafe_source", "Invalid source URL") from exc
    if url.scheme not in {"https", "http", "ssh", "git", "file"}:
        raise RepositoryRegistryError("unsupported_source", "Unsupported source URL scheme")
    if (
        url.password is not None
        or url.query
        or url.fragment
        or (url.username is not None and not (url.scheme == "ssh" and url.username == "git"))
        or any(c.isspace() for c in source)
        or (port is not None and port < 1)
    ):
        raise RepositoryRegistryError(
            "unsafe_source", "Credentials or unsafe URL components rejected"
        )
    if url.scheme == "file":
        if url.netloc or not url.path.startswith("/"):
            raise RepositoryRegistryError(
                "unsafe_source", "File sources must be local absolute paths"
            )
        return provider, local_source_path(unquote(url.path), allowed_roots).as_uri()
    if not url.hostname or not re.fullmatch(r"[A-Za-z0-9.-]+", url.hostname):
        raise RepositoryRegistryError("unsafe_source", "Invalid source host")
    validate_reference(url.path.lstrip("/").removesuffix("/").removesuffix(".git"))
    return provider, source.removesuffix("/")


@dataclass(frozen=True)
class RegisteredRepository:
    repository: str
    provider: str
    source: str
    source_ref: str
    branch: str | None
    revision: str | None
    workspace: str | None
    cache_path: str | None

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)

    def source_request(self) -> RepositorySourceRequest:
        """Pin Git snapshots; let a mutable local workspace re-observe its current HEAD."""
        return RepositorySourceRequest(
            source=self.source,
            repository=self.repository,
            branch=self.branch if self.provider == "git" else None,
            revision=self.revision if self.provider == "git" else None,
        )

    @classmethod
    def from_mapping(cls, raw: object) -> RegisteredRepository:
        if not isinstance(raw, Mapping) or set(raw) != set(cls.__dataclass_fields__):
            raise ValueError("Invalid registry record fields")
        values: dict[str, str | None] = {}
        for key in cls.__dataclass_fields__:
            value = raw[key]
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError("Invalid registry field type")
            values[key] = value
        for key in ("repository", "provider", "source", "source_ref"):
            if values[key] is None:
                raise ValueError("Missing registry field")
        record = cls(**values)  # type: ignore[arg-type]
        validate_reference(record.repository)
        if record.provider not in {"local-workspace", "git"}:
            raise ValueError("Unsupported persisted provider")
        if record.provider == "local-workspace":
            if record.workspace != record.source or record.cache_path is not None:
                raise ValueError("Invalid local registration")
            if not Path(record.source).is_absolute():
                raise ValueError("Local registration must be absolute")
        elif record.workspace is not None or record.cache_path is None or record.revision is None:
            raise ValueError("Invalid Git registration")
        return record


class RepositoryRegistry:
    """Small atomic JSON catalog. Writers fail explicitly if another writer holds the lock."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _check_path(self) -> None:
        if any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise RepositoryRegistryError(
                "unsafe_source", "Registry path must not contain symlinks"
            )

    def read(self) -> tuple[RegisteredRepository, ...]:
        self._check_path()
        try:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return ()
            if (
                not isinstance(raw, dict)
                or set(raw) != {"version", "repositories"}
                or type(raw["version"]) is not int
                or raw["version"] != REGISTRY_VERSION
                or not isinstance(raw["repositories"], list)
            ):
                raise ValueError("Invalid registry schema/version")
            records = tuple(RegisteredRepository.from_mapping(item) for item in raw["repositories"])
            if len({r.repository for r in records}) != len(records):
                raise ValueError("Duplicate canonical repository identity")
            if len({(r.provider, r.source) for r in records}) != len(records):
                raise ValueError("Duplicate repository source")
            return tuple(sorted(records, key=lambda r: r.repository))
        except (OSError, ValueError, UnicodeError) as exc:
            raise RepositoryRegistryError(
                "registry_persistence_failure",
                "Unable to read a compatible repository registry",
            ) from exc

    @contextmanager
    def writer(self) -> Iterator[None]:
        self._check_path()
        lock = self.path.with_suffix(".lock")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as exc:
            raise RepositoryRegistryError(
                "registry_persistence_failure",
                "Registry is unwritable or another writer holds its lock",
            ) from exc
        try:
            os.close(fd)
            yield
        finally:
            try:
                lock.unlink()
            except OSError as exc:
                raise RepositoryRegistryError(
                    "registry_persistence_failure",
                    "Unable to release registry writer lock",
                ) from exc

    def write(self, records: tuple[RegisteredRepository, ...]) -> None:
        self._check_path()
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                delete=False,
            ) as stream:
                temporary = stream.name
                json.dump(
                    {
                        "version": REGISTRY_VERSION,
                        "repositories": [
                            r.to_mapping() for r in sorted(records, key=lambda r: r.repository)
                        ],
                    },
                    stream,
                    sort_keys=True,
                    indent=2,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise RepositoryRegistryError(
                "registry_persistence_failure",
                "Unable to persist repository registry",
            ) from exc
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError as exc:
                    raise RepositoryRegistryError(
                        "registry_persistence_failure",
                        "Unable to remove temporary registry file",
                    ) from exc
