"""Bounded, symlink-safe scanning for repository document sources.

Binary/document sources intentionally use a separate acquisition path from
``scan_repository_text``.  The scanner only discovers and reads bounded regular
files; format parsing belongs to document-source extractors.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from fetchcore.repository.safe_scan import (
    SafeScanCoverage,
    _append_unique,
    _excluded,
    _matches_file,
    _read_regular_file,
    _repository_paths,
    _safe_relative_path,
    _validated_regular_path,
    source_kind,
)


@dataclass(frozen=True)
class SafeDocumentSource:
    """One bounded regular binary/document file observed inside a repository."""

    path: Path
    relative: str
    payload: bytes
    size_bytes: int
    checksum: str


@dataclass(frozen=True)
class SafeDocumentScanResult:
    """Document sources plus deterministic discovery/truncation telemetry."""

    sources: tuple[SafeDocumentSource, ...]
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
        return max(0, self.discovered_files - self.evaluated_files)

    def audit_mapping(self) -> dict[str, object]:
        return {
            "discovered_files": self.discovered_files,
            "eligible_files": self.eligible_files,
            "files_read": len(self.sources),
            "bytes_read": self.bytes_read,
            "skipped": dict(sorted(self.skipped.items())),
            "coverage": self.coverage.to_mapping(),
            "truncated": self.truncated,
            "truncation_reasons": list(self.truncation_reasons),
        }


def scan_repository_documents(
    workspace: Path,
    *,
    include_patterns: Iterable[str],
    exclude_patterns: Iterable[str],
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
) -> SafeDocumentScanResult:
    """Read bounded document bytes without decoding or interpreting the format."""

    if max_files < 1 or max_file_bytes < 1 or max_total_bytes < 1:
        raise ValueError("safe repository document scan limits must be positive")

    root = workspace.resolve(strict=True)
    includes = tuple(include_patterns)
    excludes = tuple(exclude_patterns)
    sources: list[SafeDocumentSource] = []
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
            SafeDocumentSource(
                path=safe_path,
                relative=relative,
                payload=payload,
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
    return SafeDocumentScanResult(
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
