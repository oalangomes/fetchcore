"""Observational diagnostics for repository-corpus inflation and duplication.

The diagnostics in this module never mutate selection policy, canonical documents,
or ranking. They describe what the current canonical corpus contains so repository
owners can make explicit policy decisions with evidence instead of path-name guesses.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


_MAX_REPORTED_DUPLICATE_GROUPS = 25
_MAX_PATH_SAMPLES_PER_SIGNAL = 20
_PATH_SIGNAL_TOKENS: dict[str, frozenset[str]] = {
    "generated_like": frozenset({"generated"}),
    "cache_like": frozenset({"cache", "caches"}),
    "output_like": frozenset(
        {"output", "outputs", "out", "build", "dist", "target", "coverage"}
    ),
    "extracted_like": frozenset({"extract", "extracted", "extraction"}),
    "mirror_like": frozenset({"mirror", "mirrored", "vendor"}),
    "runtime_like": frozenset(
        {
            "runtime",
            "validation",
            "artifact",
            "artifacts",
            "report",
            "reports",
            "tmp",
            "temp",
        }
    ),
}
_PATH_TOKEN_SPLIT = re.compile(r"[._-]+")


class _ProvenanceLike(Protocol):
    @property
    def source_checksum(self) -> str: ...


class CorpusDiagnosticDocument(Protocol):
    """Minimal read-only KnowledgeDocument surface needed by corpus diagnostics."""

    @property
    def path(self) -> str: ...

    @property
    def corpus_purpose(self) -> str: ...

    @property
    def source_origin(self) -> str: ...

    @property
    def provenance(self) -> _ProvenanceLike: ...

    @property
    def metadata(self) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class DuplicateSourceGroup:
    """One exact whole-source duplicate group."""

    source_checksum: str
    size_bytes: int
    paths: tuple[str, ...]
    source_origins: tuple[str, ...]
    corpus_purposes: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "source_checksum": self.source_checksum,
            "size_bytes": self.size_bytes,
            "paths": list(self.paths),
            "source_origins": list(self.source_origins),
            "corpus_purposes": list(self.corpus_purposes),
        }


@dataclass(frozen=True)
class CorpusDuplicationDiagnostics:
    """Bounded, deterministic corpus-level diagnostics."""

    sources_observed: int
    sources_by_origin: dict[str, int]
    sources_by_purpose: dict[str, int]
    indexed_generated_sources: int
    indexed_runtime_sources: int
    indexed_mirrored_sources: int
    duplicate_group_count: int
    duplicate_source_files: int
    redundant_source_files: int
    redundant_source_bytes: int
    cross_origin_duplicate_group_count: int
    cross_purpose_duplicate_group_count: int
    duplicate_groups: tuple[DuplicateSourceGroup, ...]
    duplicate_groups_truncated: bool
    path_signal_counts: dict[str, int]
    path_signal_samples: dict[str, tuple[str, ...]]
    inconsistent_source_metadata_paths: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "sources_observed": self.sources_observed,
            "sources_by_origin": dict(sorted(self.sources_by_origin.items())),
            "sources_by_purpose": dict(sorted(self.sources_by_purpose.items())),
            "indexed_generated_sources": self.indexed_generated_sources,
            "indexed_runtime_sources": self.indexed_runtime_sources,
            "indexed_mirrored_sources": self.indexed_mirrored_sources,
            "duplicate_group_count": self.duplicate_group_count,
            "duplicate_source_files": self.duplicate_source_files,
            "redundant_source_files": self.redundant_source_files,
            "redundant_source_bytes": self.redundant_source_bytes,
            "cross_origin_duplicate_group_count": self.cross_origin_duplicate_group_count,
            "cross_purpose_duplicate_group_count": self.cross_purpose_duplicate_group_count,
            "duplicate_groups": [item.to_mapping() for item in self.duplicate_groups],
            "duplicate_groups_truncated": self.duplicate_groups_truncated,
            "path_signal_counts": dict(sorted(self.path_signal_counts.items())),
            "path_signal_samples": {
                signal: list(paths)
                for signal, paths in sorted(self.path_signal_samples.items())
            },
            "inconsistent_source_metadata_paths": list(
                self.inconsistent_source_metadata_paths
            ),
        }


@dataclass(frozen=True)
class _SourceObservation:
    path: str
    source_checksum: str
    size_bytes: int
    source_origin: str
    corpus_purpose: str


def analyze_corpus_duplication(
    documents: Iterable[CorpusDiagnosticDocument],
) -> CorpusDuplicationDiagnostics:
    """Analyze unique indexed sources without treating diagnostics as policy.

    Multiple chunks from the same source are collapsed before duplicate analysis.
    Exact duplicate groups are based on whole-source checksums, not chunk checksums.
    Path signals are intentionally descriptive heuristics and never cause exclusion.
    """

    by_path: dict[str, _SourceObservation] = {}
    inconsistent: set[str] = set()
    for document in documents:
        observation = _SourceObservation(
            path=document.path,
            source_checksum=document.provenance.source_checksum,
            size_bytes=_source_size(document.metadata),
            source_origin=document.source_origin,
            corpus_purpose=document.corpus_purpose,
        )
        prior = by_path.get(document.path)
        if prior is None:
            by_path[document.path] = observation
        elif prior != observation:
            inconsistent.add(document.path)

    sources = tuple(by_path[path] for path in sorted(by_path))
    origin_counts = Counter(item.source_origin for item in sources)
    purpose_counts = Counter(item.corpus_purpose for item in sources)

    by_checksum: dict[str, list[_SourceObservation]] = defaultdict(list)
    for source in sources:
        if source.source_checksum:
            by_checksum[source.source_checksum].append(source)

    all_groups: list[DuplicateSourceGroup] = []
    duplicate_source_files = 0
    redundant_source_files = 0
    redundant_source_bytes = 0
    cross_origin = 0
    cross_purpose = 0
    for checksum, grouped in sorted(by_checksum.items()):
        if len(grouped) < 2:
            continue
        ordered = tuple(sorted(grouped, key=lambda item: item.path))
        paths = tuple(item.path for item in ordered)
        origins = tuple(sorted({item.source_origin for item in ordered}))
        purposes = tuple(sorted({item.corpus_purpose for item in ordered}))
        size_bytes = max(item.size_bytes for item in ordered)
        duplicate_source_files += len(ordered)
        redundant_source_files += len(ordered) - 1
        redundant_source_bytes += size_bytes * (len(ordered) - 1)
        if len(origins) > 1:
            cross_origin += 1
        if len(purposes) > 1:
            cross_purpose += 1
        all_groups.append(
            DuplicateSourceGroup(
                source_checksum=checksum,
                size_bytes=size_bytes,
                paths=paths,
                source_origins=origins,
                corpus_purposes=purposes,
            )
        )

    all_groups.sort(key=lambda item: (-len(item.paths), item.paths, item.source_checksum))
    reported_groups = tuple(all_groups[:_MAX_REPORTED_DUPLICATE_GROUPS])

    path_signals: dict[str, list[str]] = defaultdict(list)
    for source in sources:
        tokens = _path_tokens(source.path)
        for signal, candidates in _PATH_SIGNAL_TOKENS.items():
            if tokens & candidates:
                path_signals[signal].append(source.path)

    path_signal_counts = {
        signal: len(paths) for signal, paths in sorted(path_signals.items()) if paths
    }
    path_signal_samples = {
        signal: tuple(sorted(paths)[:_MAX_PATH_SAMPLES_PER_SIGNAL])
        for signal, paths in sorted(path_signals.items())
        if paths
    }

    return CorpusDuplicationDiagnostics(
        sources_observed=len(sources),
        sources_by_origin=dict(origin_counts),
        sources_by_purpose=dict(purpose_counts),
        indexed_generated_sources=origin_counts.get("generated", 0),
        indexed_runtime_sources=origin_counts.get("runtime", 0),
        indexed_mirrored_sources=origin_counts.get("mirrored", 0),
        duplicate_group_count=len(all_groups),
        duplicate_source_files=duplicate_source_files,
        redundant_source_files=redundant_source_files,
        redundant_source_bytes=redundant_source_bytes,
        cross_origin_duplicate_group_count=cross_origin,
        cross_purpose_duplicate_group_count=cross_purpose,
        duplicate_groups=reported_groups,
        duplicate_groups_truncated=len(all_groups) > len(reported_groups),
        path_signal_counts=path_signal_counts,
        path_signal_samples=path_signal_samples,
        inconsistent_source_metadata_paths=tuple(sorted(inconsistent)),
    )


def _source_size(metadata: Mapping[str, object]) -> int:
    raw = metadata.get("source_size_bytes", 0)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return max(0, raw)


def _path_tokens(path: str) -> set[str]:
    tokens: set[str] = set()
    for part in Path(path).parts[:-1]:
        normalized = part.casefold().strip()
        if not normalized:
            continue
        tokens.add(normalized)
        tokens.update(token for token in _PATH_TOKEN_SPLIT.split(normalized) if token)
    return tokens
