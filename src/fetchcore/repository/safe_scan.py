"""Bounded, symlink-safe repository text scanning."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
import subprocess
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SafeSource:
    """A regular file read within the configured workspace boundary."""

    path: Path
    relative: str
    content: str
    size_bytes: int
    checksum: str


@dataclass(frozen=True)
class SafeScanCoverage:
    """Deterministic source-kind coverage for one safe repository traversal."""

    discovered_by_source_kind: dict[str, int]
    eligible_by_source_kind: dict[str, int]
    read_by_source_kind: dict[str, int]
    unvisited_by_source_kind: dict[str, int]
    skipped_by_reason_and_source_kind: dict[str, dict[str, int]]

    def to_mapping(self) -> dict[str, object]:
        return {
            "discovered_by_source_kind": dict(sorted(self.discovered_by_source_kind.items())),
            "eligible_by_source_kind": dict(sorted(self.eligible_by_source_kind.items())),
            "read_by_source_kind": dict(sorted(self.read_by_source_kind.items())),
            "unvisited_by_source_kind": dict(sorted(self.unvisited_by_source_kind.items())),
            "skipped_by_reason_and_source_kind": {
                reason: dict(sorted(values.items()))
                for reason, values in sorted(self.skipped_by_reason_and_source_kind.items())
            },
        }


@dataclass(frozen=True)
class SafeScanResult:
    """Safe sources plus deterministic coverage and truncation telemetry."""

    sources: tuple[SafeSource, ...]
    discovered_files: int
    evaluated_files: int
    eligible_files: int
    bytes_read: int
    skipped: dict[str, int]
    coverage: SafeScanCoverage
    truncated: bool
    truncation_reasons: tuple[str, ...]

    @property
    def unvisited_files(self) -> int:
        """Discovered paths not evaluated because traversal stopped early."""

        return max(0, self.discovered_files - self.evaluated_files)

    def audit_mapping(self) -> dict[str, object]:
        """Return the stable operational scan-audit payload.

        Source-kind coverage is intentionally available through ``coverage`` and
        repository indexing telemetry rather than being injected into this legacy
        audit mapping. That keeps existing context-provider audit consumers stable
        while allowing coverage diagnostics to evolve independently.
        """

        return {
            "discovered_files": self.discovered_files,
            "eligible_files": self.eligible_files,
            "files_read": len(self.sources),
            "bytes_read": self.bytes_read,
            "skipped": dict(sorted(self.skipped.items())),
            "truncated": self.truncated,
            "truncation_reasons": list(self.truncation_reasons),
        }


def scan_repository_text(
    workspace: Path,
    *,
    include_patterns: Iterable[str],
    exclude_patterns: Iterable[str],
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
) -> SafeScanResult:
    """Read bounded regular files without following links outside the workspace."""

    if max_files < 1 or max_file_bytes < 1 or max_total_bytes < 1:
        raise ValueError("safe repository scan limits must be positive")
    root = workspace.resolve(strict=True)
    includes = tuple(include_patterns)
    excludes = tuple(exclude_patterns)
    sources: list[SafeSource] = []
    skipped: Counter[str] = Counter()
    skipped_by_reason_and_source_kind: dict[str, Counter[str]] = {}
    truncation_reasons: list[str] = []
    eligible_by_source_kind: Counter[str] = Counter()
    read_by_source_kind: Counter[str] = Counter()
    evaluated_by_source_kind: Counter[str] = Counter()

    def record_skip(reason: str, relative: str) -> None:
        skipped[reason] += 1
        bucket = skipped_by_reason_and_source_kind.setdefault(reason, Counter())
        bucket[source_kind(relative)] += 1

    eligible = 0
    evaluated = 0
    bytes_read = 0

    repository_paths = _repository_paths(root)
    discovered_by_source_kind = Counter(source_kind(item) for item in repository_paths)
    for raw_path in repository_paths:
        evaluated += 1
        evaluated_by_source_kind[source_kind(raw_path)] += 1
        relative = _safe_relative_path(raw_path)
        if relative is None:
            record_skip("invalid_path", raw_path)
            continue
        if _excluded(relative, excludes):
            record_skip("excluded", relative)
            continue
        if not _matches_file(relative, includes):
            record_skip("not_included", relative)
            continue
        eligible += 1
        eligible_by_source_kind[source_kind(relative)] += 1
        if len(sources) >= max_files:
            record_skip("max_files", relative)
            _append_unique(truncation_reasons, "max_files")
            break

        candidate = root / relative
        safe_path = _validated_regular_path(root, candidate)
        if safe_path is None:
            record_skip("unsafe_path_or_symlink", relative)
            continue
        try:
            file_size = safe_path.stat(follow_symlinks=False).st_size
        except OSError:
            record_skip("io_error", relative)
            continue
        if file_size > max_file_bytes:
            record_skip("max_file_bytes", relative)
            _append_unique(truncation_reasons, "max_file_bytes")
            continue
        remaining = max_total_bytes - bytes_read
        if file_size > remaining:
            record_skip("max_total_bytes", relative)
            _append_unique(truncation_reasons, "max_total_bytes")
            break

        try:
            payload = _read_regular_file(safe_path, limit=min(max_file_bytes, remaining))
        except (OSError, ValueError):
            record_skip("io_error", relative)
            continue
        if payload is None:
            record_skip("changed_or_oversized", relative)
            _append_unique(truncation_reasons, "changed_or_oversized")
            continue
        bytes_read += len(payload)
        read_by_source_kind[source_kind(relative)] += 1
        sources.append(
            SafeSource(
                path=safe_path,
                relative=relative,
                content=payload.decode("utf-8", errors="replace"),
                size_bytes=len(payload),
                checksum="sha256:" + hashlib.sha256(payload).hexdigest(),
            )
        )

    unvisited_by_source_kind = {
        kind: count - evaluated_by_source_kind.get(kind, 0)
        for kind, count in discovered_by_source_kind.items()
        if count - evaluated_by_source_kind.get(kind, 0) > 0
    }
    coverage = SafeScanCoverage(
        discovered_by_source_kind=dict(discovered_by_source_kind),
        eligible_by_source_kind=dict(eligible_by_source_kind),
        read_by_source_kind=dict(read_by_source_kind),
        unvisited_by_source_kind=unvisited_by_source_kind,
        skipped_by_reason_and_source_kind={
            reason: dict(values)
            for reason, values in skipped_by_reason_and_source_kind.items()
        },
    )

    return SafeScanResult(
        sources=tuple(sources),
        discovered_files=len(repository_paths),
        evaluated_files=evaluated,
        eligible_files=eligible,
        bytes_read=bytes_read,
        skipped=dict(skipped),
        coverage=coverage,
        truncated=bool(truncation_reasons),
        truncation_reasons=tuple(truncation_reasons),
    )


def _repository_paths(workspace: Path) -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=workspace,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        completed = None
    if completed is not None and completed.returncode == 0:
        return tuple(sorted(os.fsdecode(item) for item in completed.stdout.split(b"\0") if item))
    return tuple(
        path.relative_to(workspace).as_posix()
        for path in sorted(workspace.rglob("*"))
        if not path.is_dir()
    )


def _safe_relative_path(raw_path: str) -> str | None:
    path = Path(raw_path)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        return None
    return path.as_posix()


def _validated_regular_path(root: Path, candidate: Path) -> Path | None:
    try:
        relative = candidate.relative_to(root)
        current = root
        for part in relative.parts:
            current /= part
            if stat.S_ISLNK(current.lstat().st_mode):
                return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        if not stat.S_ISREG(resolved.stat(follow_symlinks=False).st_mode):
            return None
    except (OSError, ValueError):
        return None
    return resolved


def _read_regular_file(path: Path, *, limit: int) -> bytes | None:
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > limit
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            return None
        chunks: list[bytes] = []
        consumed = 0
        while consumed < opened.st_size:
            chunk = os.read(descriptor, min(64 * 1024, opened.st_size - consumed))
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
        final = os.fstat(descriptor)
        after = path.stat(follow_symlinks=False)
        if (
            final.st_dev != opened.st_dev
            or final.st_ino != opened.st_ino
            or after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
        ):
            return None
        if final.st_size != consumed or consumed > limit:
            return None
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _excluded(relative: str, excludes: tuple[str, ...]) -> bool:
    parts = relative.split("/")
    return any(
        exclude in parts
        or fnmatch.fnmatch(relative, exclude)
        or fnmatch.fnmatch(relative, f"**/{exclude}/**")
        for exclude in excludes
    )


def _matches_file(relative: str, patterns: tuple[str, ...]) -> bool:
    name = Path(relative).name
    return any(
        fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(relative, pattern) for pattern in patterns
    )


def source_kind(relative: str) -> str:
    """Return a deterministic extension/basename bucket for coverage diagnostics."""

    path = Path(relative)
    suffix = path.suffix.lower()
    if suffix:
        return suffix
    name = path.name
    if name.startswith(".") or name in {"Dockerfile", "Makefile"}:
        return name
    return "<no-extension>"


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
