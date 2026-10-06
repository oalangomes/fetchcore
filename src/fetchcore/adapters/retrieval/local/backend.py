"""Portable local lexical retrieval with typed, deterministic relevance."""

from __future__ import annotations

import fnmatch
import hashlib
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from fetchcore.retrieval.translation import validate_intent_translation, translation_diagnostics

from fetchcore.retrieval import RetrievalBackendCapabilities
from fetchcore.telemetry import (
    RetrievalStageTelemetry,
    RetrievalTelemetry,
    elapsed_ms,
    returned_chars,
)
from fetchcore.retrieval.contracts import (
    KnowledgeSearchResponse,
    SearchIntent,
    SearchCapabilitySource,
    SearchRequirement,
    SearchResult,
    SearchScope,
)
from fetchcore.retrieval.signals import QuerySignal
from fetchcore.retrieval.evidence import EvidenceObservation, QueryMatchEvidence
from fetchcore.repository.safe_scan import SafeSource, scan_repository_text


@dataclass(frozen=True)
class LocalSearchQuery:
    """Provider-local view that preserves the meaning of each query input."""

    exact_terms: tuple[str, ...] = ()
    semantic_terms: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()
    signals: tuple[QuerySignal, ...] = ()

    @property
    def terms(self) -> tuple[str, ...]:
        """Return deduplicated query terms in stable order."""
        return tuple(_unique([*self.exact_terms, *self.semantic_terms]))


@dataclass(frozen=True)
class _RawMatch:
    start: int
    end: int
    matched_line: int
    exact_terms: tuple[str, ...] = ()
    semantic_phrases: tuple[str, ...] = ()
    semantic_words: tuple[str, ...] = ()
    filename_exact_terms: tuple[str, ...] = ()
    filename_semantic_terms: tuple[str, ...] = ()
    symbol_terms: tuple[str, ...] = ()
    hint_terms: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()


@dataclass
class _Region:
    start: int
    end: int
    matched_lines: list[int] = field(default_factory=list)
    exact_terms: list[str] = field(default_factory=list)
    semantic_phrases: list[str] = field(default_factory=list)
    semantic_words: list[str] = field(default_factory=list)
    filename_exact_terms: list[str] = field(default_factory=list)
    filename_semantic_terms: list[str] = field(default_factory=list)
    symbol_terms: list[str] = field(default_factory=list)
    hint_terms: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    line_ranges: list[tuple[int, int]] = field(default_factory=list)

    @classmethod
    def from_match(cls, match: _RawMatch) -> _Region:
        region = cls(start=match.start, end=match.end)
        region.absorb_match(match)
        return region

    def absorb_match(self, match: _RawMatch) -> None:
        self.start = min(self.start, match.start)
        self.end = max(self.end, match.end)
        self.matched_lines = _unique_ints([*self.matched_lines, match.matched_line])
        self.exact_terms = _unique([*self.exact_terms, *match.exact_terms])
        self.semantic_phrases = _unique([*self.semantic_phrases, *match.semantic_phrases])
        self.semantic_words = _unique([*self.semantic_words, *match.semantic_words])
        self.filename_exact_terms = _unique(
            [*self.filename_exact_terms, *match.filename_exact_terms]
        )
        self.filename_semantic_terms = _unique(
            [*self.filename_semantic_terms, *match.filename_semantic_terms]
        )
        self.symbol_terms = _unique([*self.symbol_terms, *match.symbol_terms])
        self.hint_terms = _unique([*self.hint_terms, *match.hint_terms])
        self.symbols = _unique([*self.symbols, *match.symbols])
        self.line_ranges = _unique_ranges([*self.line_ranges, (match.start, match.end)])

    def absorb_region(self, other: _Region, *, extend_range: bool) -> None:
        if extend_range:
            self.start = min(self.start, other.start)
            self.end = max(self.end, other.end)
        self.matched_lines = _unique_ints([*self.matched_lines, *other.matched_lines])
        self.exact_terms = _unique([*self.exact_terms, *other.exact_terms])
        self.semantic_phrases = _unique([*self.semantic_phrases, *other.semantic_phrases])
        self.semantic_words = _unique([*self.semantic_words, *other.semantic_words])
        self.filename_exact_terms = _unique(
            [*self.filename_exact_terms, *other.filename_exact_terms]
        )
        self.filename_semantic_terms = _unique(
            [*self.filename_semantic_terms, *other.filename_semantic_terms]
        )
        self.symbol_terms = _unique([*self.symbol_terms, *other.symbol_terms])
        self.hint_terms = _unique([*self.hint_terms, *other.hint_terms])
        self.symbols = _unique([*self.symbols, *other.symbols])
        self.line_ranges = _unique_ranges([*self.line_ranges, *other.line_ranges])


@dataclass(frozen=True)
class _FileSearchResult:
    candidates: tuple[SearchResult, ...]
    raw_matches: int
    overlap_merges: int
    duplicate_drops: int


class LocalSemanticBackend:
    """Deterministic local backend adapted from SemanticSearchExtractor ideas."""

    backend_id = "local-semantic"
    retrieval_capabilities = RetrievalBackendCapabilities(lexical=True, canonical_query=True)

    def __init__(
        self,
        *,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timer = timer

    def search(
        self,
        intent: SearchIntent,
        scope: SearchScope,
        requirements: tuple[SearchRequirement, ...],
    ) -> KnowledgeSearchResponse:
        """Execute retrieval for the supplied intent, scope, and requirements."""
        mode = scope.retrieval.mode if scope.retrieval else "lexical"
        channels = ("lexical", "dense") if mode == "hybrid" else (mode,)
        for channel in channels:
            validate_intent_translation(
                intent, channel=channel, capabilities=self.retrieval_capabilities,
            )
        started = self.timer()
        settings = scope.settings
        query = _local_query(intent, settings)
        scan = scan_repository_text(
            scope.workspace,
            include_patterns=_strings(
                settings.get(
                    "file_patterns",
                    ["*.py", "*.md", "*.json", "*.yaml", "*.yml", "*.toml"],
                )
            ),
            exclude_patterns=_strings(
                settings.get(
                    "exclude_dirs",
                    [
                        ".git",
                        ".fetchcore",
                        ".agentsorch",
                        ".venv",
                        "venv",
                        "node_modules",
                        "build",
                        "dist",
                        "__pycache__",
                    ],
                )
            ),
            max_files=_positive_int(settings.get("max_files_scanned", 800), "max_files_scanned"),
            max_file_bytes=_positive_int(
                settings.get("max_file_bytes", 1_000_000), "max_file_bytes"
            ),
            max_total_bytes=_positive_int(
                settings.get("max_total_bytes", 32_000_000), "max_total_bytes"
            ),
        )
        configured_limit = _positive_int(
            settings.get("max_matches", scope.max_results), "max_matches"
        )
        output_limit = min(configured_limit, scope.max_results)
        context_lines = _non_negative_int(settings.get("context_lines", 1), "context_lines")
        merge_adjacent_lines = _non_negative_int(
            settings.get("merge_adjacent_lines", 1), "merge_adjacent_lines"
        )
        allowed_types = set(scope.knowledge_types)
        candidates: list[SearchResult] = []
        raw_matches = 0
        overlap_merges = 0
        duplicate_drops = 0
        files_with_matches = 0
        consolidated_candidates = 0
        sources = _repository_sources(scan.sources, scope.repository_paths)
        repository = scope.repository or scope.workspace.name
        for source in sources:
            found = _search_file(
                source=source,
                repository=repository,
                query=query,
                result_namespace=scope.result_namespace,
                revision=scope.revision,
                settings=settings,
                context_lines=context_lines,
                merge_adjacent_lines=merge_adjacent_lines,
            )
            raw_matches += found.raw_matches
            overlap_merges += found.overlap_merges
            duplicate_drops += found.duplicate_drops
            if found.candidates:
                files_with_matches += 1
            consolidated_candidates += len(found.candidates)
            eligible_candidates = tuple(
                item
                for item in found.candidates
                if not allowed_types or item.knowledge_type in allowed_types
            )
            if scope.repository_paths and not eligible_candidates and not intent.has_v2_semantics:
                direct_candidate = _repository_source_candidate(
                    source=source,
                    repository=repository,
                    result_namespace=scope.result_namespace,
                    revision=scope.revision,
                    settings=settings,
                )
                if not allowed_types or direct_candidate.knowledge_type in allowed_types:
                    eligible_candidates = (direct_candidate,)
            candidates.extend(eligible_candidates)

        candidates.sort(key=_local_ordering_key)
        returned = tuple(candidates[:output_limit])
        diagnostics = _retrieval_diagnostics(candidates, returned)
        duration_ms = elapsed_ms(started, self.timer())
        telemetry = RetrievalTelemetry(
            mode="lexical",
            duration_ms=duration_ms,
            candidates_examined=len(candidates),
            candidates_returned=len(returned),
            returned_chars=returned_chars(item.content for item in returned),
            stages=(
                RetrievalStageTelemetry(
                    name="lexical",
                    duration_ms=duration_ms,
                    candidates_out=len(candidates),
                    metadata={
                        "files_scanned": len(scan.sources),
                        "files_with_matches": files_with_matches,
                    },
                ),
            ),
            budget={
                "max_results": output_limit,
                "output_truncated": len(candidates) > output_limit,
            },
        )
        return KnowledgeSearchResponse(
            backend=self.backend_id,
            status="success",
            results=returned,
            metadata={
                **translation_diagnostics(
                    intent,
                    scope,
                    consumed_hints=tuple(
                        hint for hint in intent.hints
                        if hint.strip().casefold() in {value.casefold() for value in query.hints}
                    ),
                    consumed_signals=tuple(
                        signal
                        for signal in intent.signals
                        if signal.value.strip().casefold()
                        in {
                            value.casefold()
                            for value in {
                                "exact": query.exact_terms,
                                "semantic": query.semantic_terms,
                                "hint": query.hints,
                            }.get(signal.type, ())
                        }
                    ),
                    lexical={
                        "exact_terms": list(query.exact_terms),
                        "semantic_terms": list(query.semantic_terms),
                        "hints": list(query.hints),
                    },
                ),
                "query_terms": list(query.terms),
                "exact_terms_used": list(query.exact_terms),
                "semantic_terms_used": list(query.semantic_terms),
                "hints_considered": list(query.hints),
                "query_signals_used": [item.to_mapping() for item in query.signals],
                "files_scanned": len(scan.sources),
                "files_eligible": scan.eligible_files,
                "files_evaluated": len(scan.sources),
                "files_with_matches": files_with_matches,
                "raw_matches": raw_matches,
                "consolidated_candidates": consolidated_candidates,
                "candidates_eligible_by_knowledge_type": len(candidates),
                "candidates_found_before_cutoff": len(candidates),
                "overlap_merges": overlap_merges,
                "duplicate_drops": duplicate_drops,
                "candidates_returned": len(returned),
                "cutoff_score": returned[-1].score if returned else None,
                **diagnostics,
                "scanner_truncated": scan.truncated,
                "output_truncated": len(candidates) > output_limit,
                "output_limit": output_limit,
                "portable_search": "python",
                "safe_scan": scan.audit_mapping(),
                "requirements_received": [item.to_mapping() for item in requirements],
            },
            duration_ms=duration_ms,
            telemetry=telemetry,
        )


def _retrieval_diagnostics(
    candidates: list[SearchResult],
    returned: tuple[SearchResult, ...],
) -> dict[str, object]:
    return {
        "candidate_order_before_cutoff": _candidate_order(candidates, returned),
        "candidate_sources_before_cutoff": _candidate_sources(candidates),
        "candidate_sources_returned": _candidate_sources(returned),
        "score_distribution_before_cutoff": _score_distribution(candidates),
        "score_distribution_returned": _score_distribution(returned),
        "match_channels_before_cutoff": _match_channel_counts(candidates),
        "match_channels_returned": _match_channel_counts(returned),
        "query_signal_matches_before_cutoff": _query_signal_match_counts(candidates),
        "query_signal_matches_returned": _query_signal_match_counts(returned),
    }


def _candidate_order(
    candidates: Iterable[SearchResult],
    returned: Iterable[SearchResult],
) -> list[dict[str, object]]:
    returned_ids = {item.id for item in returned}
    return [
        {
            "id": item.id,
            "source": item.source,
            "source_ref": item.source_identity,
            "pre_cutoff_rank": rank,
            "score": round(item.score, 4),
            "returned": item.id in returned_ids,
        }
        for rank, item in enumerate(candidates, start=1)
    ]


def _candidate_sources(
    candidates: Iterable[SearchResult],
) -> list[str]:
    return sorted({item.source for item in candidates})


def _score_distribution(
    candidates: Iterable[SearchResult],
) -> dict[str, float | int]:
    scores = sorted(item.score for item in candidates)
    if not scores:
        return {"count": 0}

    def percentile(value: float) -> float:
        index = int((len(scores) - 1) * value)
        return round(scores[index], 4)

    return {
        "count": len(scores),
        "min": round(scores[0], 4),
        "p25": percentile(0.25),
        "p50": percentile(0.50),
        "p75": percentile(0.75),
        "p90": percentile(0.90),
        "max": round(scores[-1], 4),
    }


def _match_channel_counts(
    candidates: Iterable[SearchResult],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for candidate in candidates:
        reasons = candidate.reasons
        if any(reason.startswith("filename:") for reason in reasons):
            counts["filename"] += 1
        if any(reason.startswith("symbol:") for reason in reasons):
            counts["symbol"] += 1
        if any(
            reason.startswith(("exact_identifier:", "semantic_phrase:", "semantic_term:"))
            for reason in reasons
        ):
            counts["content"] += 1
    return {channel: counts.get(channel, 0) for channel in ("filename", "content", "symbol")}


def _query_signal_match_counts(
    candidates: Iterable[SearchResult],
) -> list[dict[str, object]]:
    counts: Counter[tuple[str, str, str]] = Counter()
    for candidate in candidates:
        seen: set[tuple[str, str, str]] = set()
        for signal in candidate.matched_signals:
            value = signal.value.strip()
            if not value:
                continue
            seen.add(
                (
                    value,
                    signal.type,
                    signal.source,
                )
            )
        counts.update(seen)
    return [
        {
            "value": value,
            "type": signal_type,
            "source": source,
            "candidate_count": count,
        }
        for (value, signal_type, source), count in sorted(counts.items())
    ]


def _search_file(
    *,
    source: SafeSource,
    repository: str,
    query: LocalSearchQuery,
    result_namespace: str,
    revision: str | None,
    settings: dict[str, object],
    context_lines: int,
    merge_adjacent_lines: int,
) -> _FileSearchResult:
    content = source.content
    relative = source.relative
    lines = content.splitlines() or [""]
    filename_exact_terms = tuple(
        term for term in query.exact_terms if _term_in_path(term, relative)
    )
    filename_semantic_terms = tuple(
        term for term in query.semantic_terms if _term_in_path(term, relative)
    )
    hint_terms = tuple(term for term in query.hints if _hint_matches_path(term, relative))
    raw_matches: list[_RawMatch] = []
    for index, line in enumerate(lines):
        symbols = tuple(_symbols(line))
        symbol_terms = tuple(
            term
            for term in query.exact_terms
            if any(_same_identifier(term, symbol) for symbol in symbols)
        )
        exact_terms = tuple(term for term in query.exact_terms if _contains_term(line, term))
        semantic_phrases = tuple(
            term
            for term in query.semantic_terms
            if _is_phrase(term) and _contains_phrase(line, term)
        )
        semantic_words = tuple(
            term
            for term in query.semantic_terms
            if not _is_phrase(term) and _contains_term(line, term)
        )
        content_match = bool(exact_terms or semantic_phrases or semantic_words or symbol_terms)
        path_match = bool(filename_exact_terms or filename_semantic_terms)
        if not content_match and (not path_match or raw_matches):
            continue
        start = max(1, index + 1 - context_lines)
        end = min(len(lines), index + 1 + context_lines)
        raw_matches.append(
            _RawMatch(
                start=start,
                end=end,
                matched_line=index + 1,
                exact_terms=exact_terms,
                semantic_phrases=semantic_phrases,
                semantic_words=semantic_words,
                filename_exact_terms=filename_exact_terms,
                filename_semantic_terms=filename_semantic_terms,
                symbol_terms=symbol_terms,
                hint_terms=hint_terms,
                symbols=symbols,
            )
        )

    regions, overlap_merges = _consolidate_overlaps(
        raw_matches, merge_adjacent_lines=merge_adjacent_lines
    )
    regions, duplicate_drops = _drop_duplicate_regions(regions, lines)
    candidates = tuple(
        _candidate_from_region(
            region=region,
            lines=lines,
            relative=relative,
            repository=repository,
            document_checksum=source.checksum,
            result_namespace=result_namespace,
            revision=revision,
            settings=settings,
            query=query,
        )
        for region in regions
    )
    return _FileSearchResult(
        candidates=candidates,
        raw_matches=len(raw_matches),
        overlap_merges=overlap_merges,
        duplicate_drops=duplicate_drops,
    )


def _repository_source_candidate(
    *,
    source: SafeSource,
    repository: str,
    result_namespace: str,
    revision: str | None,
    settings: dict[str, object],
) -> SearchResult:
    content = _redact(source.content)
    candidate_id = hashlib.sha256(
        f"{result_namespace}:repository-source:{source.relative}:{source.checksum}".encode()
    ).hexdigest()[:20]
    line_count = max(1, len(content.splitlines()))
    return SearchResult(
        id=f"candidate_{candidate_id}",
        source=source.relative,
        source_ref=_repository_source_ref(repository, source.relative),
        knowledge_type=_knowledge_type(source.relative, settings),
        content=content,
        score=0.0,
        query_alignment_score=0.0,
        line_start=1,
        line_end=line_count,
        revision=revision,
        checksum=source.checksum,
        trust_level="reviewed",
        reasons=("repository_source_exact",),
        metadata={
            "document_checksum": source.checksum,
            "repository_source_exact": True,
        },
    )


def _repository_source_ref(repository: str, relative: str) -> str:
    encoded_repository = quote(repository, safe="/.:@")
    normalized = relative.replace("\\", "/").lstrip("/")
    return f"repository://{encoded_repository}/{quote(normalized, safe='/')}"


def _repository_sources(
    sources: tuple[SafeSource, ...],
    requested_paths: tuple[str, ...],
) -> tuple[SafeSource, ...]:
    if not requested_paths:
        return sources
    selected: dict[str, SafeSource] = {}
    for requested in requested_paths:
        canonical = requested.replace("\\", "/")
        exact = tuple(item for item in sources if item.relative == canonical)
        folded = tuple(
            item
            for item in sources
            if item.relative.casefold() == canonical.casefold()
        )
        eligible = exact or folded
        if not exact and len({item.relative for item in eligible}) > 1:
            continue
        for item in eligible:
            selected[item.relative] = item
    return tuple(selected[key] for key in sorted(selected))


def _consolidate_overlaps(
    matches: list[_RawMatch], *, merge_adjacent_lines: int
) -> tuple[list[_Region], int]:
    regions: list[_Region] = []
    merges = 0
    for match in sorted(matches, key=lambda item: (item.start, item.end, item.matched_line)):
        if regions and match.start <= regions[-1].end + merge_adjacent_lines:
            regions[-1].absorb_match(match)
            merges += 1
        else:
            regions.append(_Region.from_match(match))
    return regions, merges


def _drop_duplicate_regions(regions: list[_Region], lines: list[str]) -> tuple[list[_Region], int]:
    unique: list[_Region] = []
    by_content: dict[str, _Region] = {}
    drops = 0
    for region in regions:
        content = _redact("\n".join(lines[region.start - 1 : region.end]).strip())
        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = by_content.get(checksum)
        if existing is None:
            by_content[checksum] = region
            unique.append(region)
            continue
        existing.absorb_region(region, extend_range=False)
        drops += 1
    return unique, drops


def _candidate_from_region(
    *,
    region: _Region,
    lines: list[str],
    relative: str,
    repository: str,
    document_checksum: str,
    result_namespace: str,
    revision: str | None,
    settings: dict[str, object],
    query: LocalSearchQuery,
) -> SearchResult:
    slice_content = _redact("\n".join(lines[region.start - 1 : region.end]).strip())
    matched_line_content = "\n".join(
        lines[index - 1] for index in region.matched_lines if 0 < index <= len(lines)
    )
    categories = _categorize(
        relative=relative,
        line=matched_line_content,
        slice_content=slice_content,
        settings=settings,
    )
    semantic_types = _unique(
        str(item.get("semantic_type")) for item in categories if item.get("semantic_type")
    )
    matched_terms = tuple(
        _unique(
            [
                *region.exact_terms,
                *region.symbol_terms,
                *region.semantic_phrases,
                *region.semantic_words,
                *region.filename_exact_terms,
                *region.filename_semantic_terms,
            ]
        )
    )
    matched_signal_values = {
        item.casefold() for item in (*matched_terms, *region.hint_terms)
    }
    matched_query_signals = tuple(
        signal for signal in query.signals if signal.value.casefold() in matched_signal_values
    )
    reasons = [f"exact_identifier:{term}" for term in region.exact_terms]
    reasons.extend(f"symbol:{term}" for term in region.symbol_terms)
    reasons.extend(
        f"filename:{term}"
        for term in _unique([*region.filename_exact_terms, *region.filename_semantic_terms])
    )
    reasons.extend(f"semantic_phrase:{term}" for term in region.semantic_phrases)
    reasons.extend(f"semantic_term:{term}" for term in region.semantic_words)
    reasons.extend(f"hint_path:{term}" for term in region.hint_terms)
    reasons.extend(
        str(item["reason"]) for item in categories if str(item.get("reason", "")).strip()
    )
    score = _score(region)
    knowledge_type = _knowledge_type(relative, settings)
    capabilities, capability_sources = _candidate_capabilities(
        knowledge_type=knowledge_type,
        semantic_types=semantic_types,
        region=region,
    )
    checksum = "sha256:" + hashlib.sha256(slice_content.encode("utf-8")).hexdigest()
    slice_ref = f"{relative}#L{region.start}-L{region.end}"
    candidate_id = hashlib.sha256(
        f"{result_namespace}:{relative}:{region.start}:{region.end}:{checksum}".encode()
    ).hexdigest()[:20]
    observations = tuple(
        EvidenceObservation(
            category="identifier",
            subtype="symbol",
            value=symbol,
            source_ref=relative,
            slice_ref=slice_ref,
            revision=revision,
            checksum=document_checksum,
            line_start=region.start,
            line_end=region.end,
            provenance="bounded_source_scan",
        )
        for symbol in region.symbols
    )
    query_matches = tuple(
        _local_query_match_evidence(
            signal,
            region=region,
            source_ref=relative,
        )
        for signal in matched_query_signals
    )
    return SearchResult(
        id=f"candidate_{candidate_id}",
        source=relative,
        source_ref=_repository_source_ref(repository, relative),
        knowledge_type=knowledge_type,
        content=slice_content,
        score=score,
        query_alignment_score=score,
        capabilities=capabilities,
        capability_sources=capability_sources,
        matched_terms=matched_terms,
        matched_signals=matched_query_signals,
        reasons=tuple(_unique(reasons)),
        line_start=region.start,
        line_end=region.end,
        revision=revision,
        checksum=checksum,
        trust_level="reviewed",
        observations=observations,
        query_matches=query_matches,
        metadata={
            "categories": categories,
            "semantic_types": semantic_types,
            "symbols": region.symbols,
            "matched_lines": region.matched_lines,
            "line_ranges": [list(item) for item in region.line_ranges],
            "matched_terms_by_type": {
                "exact": _unique([*region.exact_terms, *region.symbol_terms]),
                "semantic_phrases": region.semantic_phrases,
                "semantic_words": region.semantic_words,
                "filename": _unique(
                    [*region.filename_exact_terms, *region.filename_semantic_terms]
                ),
                "hints": region.hint_terms,
            },
            "matched_query_signals": [
                item.to_mapping() for item in matched_query_signals
            ],
            "slice_ref": slice_ref,
            "slice_checksum": checksum,
            "document_checksum": document_checksum,
        },
    )


def _local_query_match_evidence(
    signal: QuerySignal,
    *,
    region: _Region,
    source_ref: str,
) -> QueryMatchEvidence:
    value = signal.value.casefold()
    symbol_values = {item.casefold(): item for item in region.symbols}
    filename_values = {
        item.casefold(): item
        for item in (*region.filename_exact_terms, *region.filename_semantic_terms)
    }
    signal_strength = max(0.0, min(1.0, signal.weight))
    if value in symbol_values:
        channel = "identifier_symbol"
        matched_value = symbol_values[value]
        strength = signal_strength
        match_type = "exact_structural"
    elif value in filename_values:
        channel = "path_filename"
        matched_value = filename_values[value]
        strength = 0.8 * signal_strength
        match_type = "path_structural"
    elif value in {item.casefold() for item in region.exact_terms}:
        channel = "identifier_content"
        matched_value = signal.value
        strength = 0.9 * signal_strength
        match_type = "exact_structural"
    else:
        channel = "semantic_content"
        matched_value = signal.value
        strength = signal_strength
        match_type = "semantic"
    return QueryMatchEvidence(
        signal=signal,
        match_type=match_type,
        channels=(channel,),
        matched_values=(matched_value,),
        source_ref=source_ref,
        strength=strength,
        normalized_contribution=strength,
        provenance="local_semantic_match",
    )


def _local_query(intent: SearchIntent, settings: dict[str, object]) -> LocalSearchQuery:
    max_terms = _positive_int(
        settings.get("max_expanded_query_terms", 32), "max_expanded_query_terms"
    )
    structured_exact = tuple(item.value for item in intent.signals if item.type == "exact")
    structured_semantic = tuple(item.value for item in intent.signals if item.type == "semantic")
    structured_hints = tuple(item.value for item in intent.signals if item.type == "hint")
    exact_inputs = structured_exact or intent.exact_terms
    semantic_inputs = structured_semantic or intent.semantic_terms
    hint_inputs = structured_hints or intent.hints
    exact = _expand_terms(exact_inputs, settings, limit=max_terms)
    semantic = _expand_terms(
        semantic_inputs,
        settings,
        limit=max(0, max_terms - len(exact)),
    )
    # The legacy term budget must not silently discard the canonical query.
    semantic = tuple(
        _unique(
            (
                *semantic,
                *((intent.canonical_query,) if intent.canonical_query else ()),
                *(item.value for item in intent.expansions),
            )
        )
    )
    exact_keys = {item.casefold() for item in exact}
    semantic = tuple(item for item in semantic if item.casefold() not in exact_keys)
    return LocalSearchQuery(
        exact_terms=exact,
        semantic_terms=semantic,
        hints=tuple(_unique(hint_inputs)),
        signals=intent.signals,
    )


def _expand_terms(
    terms: tuple[str, ...], settings: dict[str, object], *, limit: int
) -> tuple[str, ...]:
    raw_aliases = settings.get("query_aliases", {})
    aliases = raw_aliases if isinstance(raw_aliases, dict) else {}
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        raw = aliases.get(term.casefold(), [])
        if isinstance(raw, str):
            expanded.append(raw)
        elif isinstance(raw, list):
            expanded.extend(str(item) for item in raw)
    return tuple(_unique(expanded)[:limit])


def _categorize(
    *, relative: str, line: str, slice_content: str, settings: dict[str, object]
) -> list[dict[str, object]]:
    categories: list[dict[str, object]] = []
    for raw_rule in _mappings(settings.get("categorization_rules", [])):
        pattern = str(raw_rule.get("pattern", "")).strip()
        if not pattern:
            continue
        file_pattern = str(raw_rule.get("file_pattern", "")).strip()
        if file_pattern and not re.search(file_pattern, relative, re.IGNORECASE):
            continue
        target = (
            relative if raw_rule.get("scope") == "file" else f"{relative}\n{line}\n{slice_content}"
        )
        try:
            matched = re.search(pattern, target, re.IGNORECASE) is not None
        except re.error:
            continue
        if matched:
            categories.append(
                {
                    "name": str(raw_rule.get("name", "rule")),
                    "confidence": _float_value(raw_rule.get("confidence", 0.8)),
                    "semantic_type": str(raw_rule.get("semantic_type", "reference")),
                    "reason": f"rule:{raw_rule.get('name', 'rule')}",
                }
            )
    stem = Path(relative).stem
    for raw_role in _mappings(settings.get("technical_roles", [])):
        token = str(raw_role.get("token", "")).strip()
        if token and token.casefold() in stem.casefold():
            categories.append(
                {
                    "name": f"role_{raw_role.get('role', token)}",
                    "confidence": _float_value(raw_role.get("confidence", 0.8)),
                    "semantic_type": "role",
                    "reason": f"technical_role:{raw_role.get('role', token)}",
                }
            )
    haystack = f"{relative}\n{slice_content}".casefold()
    for raw_segment in _mappings(settings.get("product_segments", [])):
        tokens = _strings(raw_segment.get("token", []))
        if any(token.casefold() in haystack for token in tokens):
            domain = str(raw_segment.get("domain", "domain"))
            categories.append(
                {
                    "name": f"product_{domain}",
                    "confidence": _float_value(raw_segment.get("confidence", 0.8)),
                    "semantic_type": "domain",
                    "reason": f"product_segment:{domain}",
                }
            )
    return categories


def _knowledge_type(relative: str, settings: dict[str, object]) -> str:
    for raw_rule in _mappings(settings.get("knowledge_type_rules", [])):
        pattern = str(raw_rule.get("pattern", "")).strip()
        if pattern and fnmatch.fnmatch(relative, pattern):
            return str(raw_rule.get("knowledge_type", "code"))
    return str(settings.get("default_knowledge_type", "code"))


def _candidate_capabilities(
    *,
    knowledge_type: str,
    semantic_types: list[str],
    region: _Region,
) -> tuple[tuple[str, ...], dict[str, SearchCapabilitySource]]:
    capabilities: list[str] = []
    if knowledge_type == "code" and (
        region.symbol_terms
        or {"behavior", "entity", "role"}.intersection(semantic_types)
    ):
        capabilities.append("implementation")
    normalized = tuple(sorted(set(capabilities)))
    return normalized, {item: "provider_classification" for item in normalized}


def _score(region: _Region) -> float:
    """Return simple local relevance; governance remains in ContextResolver."""

    signal_strengths: list[float] = []
    if region.symbol_terms:
        signal_strengths.append(0.90)
    if region.exact_terms:
        signal_strengths.append(0.72)
    if region.filename_exact_terms:
        signal_strengths.append(0.60)
    if region.filename_semantic_terms:
        signal_strengths.append(0.50)
    if region.semantic_phrases:
        signal_strengths.append(0.45)
    if region.semantic_words:
        signal_strengths.append(0.18)
    if not signal_strengths:
        return 0.0
    semantic_coverage = len(_unique([*region.semantic_phrases, *region.semantic_words]))
    exact_coverage = len(_unique([*region.exact_terms, *region.symbol_terms]))
    coverage_bonus = min(0.18, 0.06 * max(0, semantic_coverage - 1))
    coverage_bonus += min(0.08, 0.04 * max(0, exact_coverage - 1))
    filename_bonus = (
        0.04 if (region.filename_exact_terms or region.filename_semantic_terms) else 0.0
    )
    hint_tiebreak = 0.01 if region.hint_terms else 0.0
    return min(1.0, max(signal_strengths) + coverage_bonus + filename_bonus + hint_tiebreak)


def _symbols(line: str) -> list[str]:
    symbols: list[str] = []
    patterns = (
        r"\b(?:class|def|function|interface|enum|struct|trait|type)\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:lambda|async\s+function|function|\([^)]*\)\s*=>)",
    )
    for pattern in patterns:
        symbols.extend(re.findall(pattern, line))
    return _unique(symbols)


def _contains_term(value: str, term: str) -> bool:
    escaped = re.escape(term.strip())
    if not escaped:
        return False
    return (
        re.search(
            rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])",
            value,
            re.IGNORECASE,
        )
        is not None
    )


def _contains_phrase(value: str, term: str) -> bool:
    phrase = " ".join(_words(term))
    haystack = " ".join(_words(value))
    return bool(phrase) and f" {phrase} " in f" {haystack} "


def _term_in_path(term: str, relative: str) -> bool:
    normalized = _canonical_identifier(term)
    return bool(normalized) and normalized in _canonical_identifier(relative)


def _hint_matches_path(hint: str, relative: str) -> bool:
    normalized = _canonical_identifier(hint)
    return len(normalized) >= 4 and normalized in _canonical_identifier(relative)


def _same_identifier(left: str, right: str) -> bool:
    return _canonical_identifier(left) == _canonical_identifier(right)


def _canonical_identifier(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _words(value: str) -> list[str]:
    return re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", value.casefold())


def _is_phrase(term: str) -> bool:
    return len(_words(term)) > 1


def _local_ordering_key(candidate: SearchResult) -> tuple[object, ...]:
    return (
        -candidate.score,
        candidate.source.casefold(),
        candidate.line_start or 0,
        candidate.line_end or 0,
        candidate.id,
    )


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _mappings(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict):
            result.append({str(key): nested for key, nested in item.items()})
    return result


def _float_value(value: object) -> float:
    return float(str(value))


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(\b(?:ghp|github_pat)_)[A-Za-z0-9_]+\b"),
)


def _redact(value: str) -> str:
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(r"\1[REDACTED]", result)
    return result


def _positive_int(value: object, name: str) -> int:
    parsed = int(str(value))
    if parsed < 1:
        raise ValueError(f"{name} must be positive")
    return parsed


def _non_negative_int(value: object, name: str) -> int:
    parsed = int(str(value))
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(item) for item in values if str(item).strip()))


def _unique_ints(values: Iterable[int]) -> list[int]:
    return list(dict.fromkeys(values))


def _unique_ranges(values: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    return list(dict.fromkeys(values))
