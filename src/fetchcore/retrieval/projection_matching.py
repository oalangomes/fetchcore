"""Backend-neutral retrieval matching and diagnostics over Projection v3 records."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from fetchcore.retrieval.signals import QuerySignal
from fetchcore.retrieval.evidence import EvidenceObservation, QueryMatchEvidence
from fetchcore.projection import PROJECTION_VERSION, _identifiers_from_values
from fetchcore.retrieval.contracts import (
    KnowledgeSearchError,
    SearchIntent,
    SearchRelationship,
)


class ProjectionV3SearchControls(Protocol):
    """Controls consumed by neutral Projection v3 query semantics."""

    @property
    def field_boosts(self) -> dict[str, float]: ...

    @property
    def signal_type_boosts(self) -> dict[str, float]: ...

    @property
    def diagnostic_candidate_window(self) -> int | None: ...


@dataclass(frozen=True)
class ValidatedProjectionV3Controls:
    projection_version: str
    field_boosts: dict[str, float]
    signal_type_boosts: dict[str, float]
    diagnostic_candidate_window: int | None

_SEMANTIC_TOKEN_PATTERN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+")


_EXECUTABLE_SYMBOL_TYPES = frozenset(
    {"class", "constructor", "function", "method", "procedure"}
)


_EXECUTABLE_DECLARATION_PATTERNS = (
    (
        "function_declaration",
        re.compile(r"\b(?:async\s+)?function\s+[A-Za-z_$][A-Za-z0-9_$]*\s*\("),
    ),
    (
        "class_declaration",
        re.compile(r"\bclass\s+[A-Za-z_$][A-Za-z0-9_$]*\b"),
    ),
    (
        "arrow_function_declaration",
        re.compile(
            r"\b(?:const|let|var)\s+[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*"
            r"(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>"
        ),
    ),
    (
        "python_function_declaration",
        re.compile(r"(?m)^\s*(?:async\s+)?def\s+[A-Za-z_][A-Za-z0-9_]*\s*\("),
    ),
    (
        "go_or_rust_function_declaration",
        re.compile(r"(?m)^\s*(?:func|fn)\s+[A-Za-z_][A-Za-z0-9_]*\s*\("),
    ),
)


_IMPLEMENTATION_TECHNICAL_ROLES = frozenset(
    {
        "adapter",
        "client",
        "command",
        "controller",
        "converter",
        "dao",
        "facade",
        "factory",
        "gateway",
        "handler",
        "interceptor",
        "job",
        "listener",
        "mapper",
        "process",
        "provider",
        "repository",
        "service",
        "strategy",
        "validator",
        "workflow",
    }
)


QUERY_CHANNELS_BY_SIGNAL_TYPE = {
    "exact": frozenset(
        {
            "identifier_exact_content",
            "identifier_exact_filename",
            "identifier_exact_path",
            "symbol",
            "filename",
            "content",
        }
    ),
    "reference": frozenset(
        {
            "identifier_exact_content",
            "identifier_exact_filename",
            "identifier_exact_path",
            "symbol",
            "filename",
            "path",
            "content",
            "technical_role",
            "path_segment",
            "route_method",
            "route_path",
            "route_mechanism",
            "import_target",
            "dependency_target",
            "relation_target",
        }
    ),
    "semantic": frozenset({"content", "documentation", "path", "technical_role"}),
    "hint": frozenset(
        {"content", "path", "technical_role", "route_method", "route_mechanism"}
    ),
}


_STRUCTURAL_CHANNEL_STRENGTH = {
    "identifier_exact_content": 1.0,
    "symbol": 1.0,
    "content": 0.8,
    "identifier_exact_filename": 0.3,
    "filename": 0.3,
    "identifier_exact_path": 0.2,
    "path": 0.2,
    "technical_role": 0.2,
    "path_segment": 0.3,
    "route_method": 0.7,
    "route_path": 0.9,
    "route_mechanism": 0.7,
    "import_target": 0.9,
    "dependency_target": 0.9,
    "relation_target": 1.0,
}


_SEMANTIC_CHANNEL_STRENGTH = {
    "content": 1.0,
    "documentation": 0.75,
    "path": 0.25,
    "technical_role": 0.2,
    "route_method": 0.4,
    "route_mechanism": 0.4,
}


_SEMANTIC_REUSE_MAX = 0.8


_HINT_ALIGNMENT_MAX = 0.2


_MAX_DIAGNOSTIC_SIGNAL_MATCHES = 32


_MAX_DIAGNOSTIC_VALUES_PER_CHANNEL = 8


_MAX_DIAGNOSTIC_RELATION_EDGES = 16


_MAX_DIAGNOSTIC_VALUE_CHARS = 256


_ALLOWED_BOOST_FIELDS = frozenset(
    {
        "identifiers_exact",
        "content_identifiers_exact",
        "symbols",
        "filename_exact",
        "path_text",
        "technical_role",
        "content",
        "path_segments",
        "route_methods",
        "route_paths",
        "route_mechanisms",
        "import_targets",
        "dependency_targets",
        "relation_targets",
        "documentation_text",
    }
)


DEFAULT_FIELD_BOOSTS: dict[str, float] = {
    "identifiers_exact": 10.0,
    "content_identifiers_exact": 16.0,
    "symbols": 16.0,
    "filename_exact": 6.0,
    "path_text": 3.0,
    "technical_role": 2.0,
    "content": 1.0,
    "path_segments": 3.0,
    "route_methods": 4.0,
    "route_paths": 8.0,
    "route_mechanisms": 4.0,
    "import_targets": 8.0,
    "dependency_targets": 8.0,
    "relation_targets": 12.0,
    "documentation_text": 0.75,
}


DEFAULT_SIGNAL_TYPE_BOOSTS: dict[str, float] = {
    "exact": 1.0,
    "reference": 1.0,
    "semantic": 1.0,
    "hint": 1.0,
}


SIGNAL_FIELD_FACTORS: dict[str, dict[str, float]] = {
    "exact": {
        "content_identifiers_exact": 1.0,
        "symbols": 1.0,
        "identifiers_exact": 0.75,
        "filename_exact": 0.5,
        "content": 0.5,
    },
    "reference": {
        "filename_exact": 1.0,
        "path_text": 1.0,
        "symbols": 0.75,
        "identifiers_exact": 0.5,
        "technical_role": 0.4,
        "content": 0.25,
    },
    "semantic": {
        "content": 1.0,
        "path_text": 0.2,
        "technical_role": 0.25,
    },
    "hint": {
        "content": 0.25,
        "path_text": 0.1,
        "technical_role": 0.15,
    },
}


QUERY_FIELDS_BY_SIGNAL_TYPE: dict[str, tuple[str, ...]] = {
    "exact": (),
    "reference": (
        "path_segments",
        "route_methods",
        "route_paths",
        "route_mechanisms",
        "import_targets",
        "dependency_targets",
        "relation_targets",
    ),
    "semantic": ("documentation_text",),
    "hint": (
        "route_methods",
        "route_mechanisms",
    ),
}


SIGNAL_FIELD_FACTORS_V3: dict[str, dict[str, float]] = {
    "exact": {},
    "reference": {
        "path_segments": 0.5,
        "route_methods": 0.75,
        "route_paths": 1.0,
        "route_mechanisms": 0.75,
        "import_targets": 1.0,
        "dependency_targets": 1.0,
        "relation_targets": 1.0,
    },
    "semantic": {"documentation_text": 1.0},
    "hint": {
        "route_methods": 0.25,
        "route_mechanisms": 0.25,
    },
}


_CHANNEL_QUERY_FIELDS = {
    "identifier_exact_content": ("content_identifiers_exact", "identifiers_exact"),
    "symbol": ("symbols",),
    "identifier_exact_filename": ("filename_exact",),
    "identifier_exact_path": ("identifiers_exact",),
    "filename": ("filename_exact",),
    "path": ("path_text",),
    "content": ("content",),
    "technical_role": ("technical_role",),
    "path_segment": ("path_segments",),
    "route_method": ("route_methods",),
    "route_path": ("route_paths",),
    "route_mechanism": ("route_mechanisms",),
    "import_target": ("import_targets",),
    "dependency_target": ("dependency_targets",),
    "relation_target": ("relation_targets",),
    "documentation": ("documentation_text",),
}


_KEYWORD_MATCH_CHANNELS = frozenset(
    {
        "identifier_exact_content",
        "symbol",
        "identifier_exact_filename",
        "identifier_exact_path",
        "filename",
        "technical_role",
        "path_segment",
        "route_method",
        "route_path",
        "route_mechanism",
        "import_target",
        "dependency_target",
        "relation_target",
    }
)


@dataclass(frozen=True)
class ProjectionV3MatchResult:
    channels: tuple[str, ...]
    channel_values: dict[str, tuple[str, ...]]
    channel_boosts: dict[str, float]
    matched_terms: tuple[str, ...]
    matched_signals: tuple[QuerySignal, ...]
    signal_matches: tuple[dict[str, object], ...]
    relation_edges: tuple[str, ...]
    diagnostics_truncated: bool
    alignment: float
    structural_alignment: float
    auxiliary_alignment: float
    semantic_alignment: float
    hint_alignment: float
    protected_semantic_alignment: float
    protected_identity_alignment: float
    semantic_reuse_alignment: float
    structural_candidate_confidence: float
    implementation_evidence: tuple[str, ...]
    channels_by_signal_type: dict[str, tuple[str, ...]]


def repository_source_documents(
    documents: list[dict[str, object]],
    requested_paths: tuple[str, ...],
) -> list[dict[str, object]]:
    selected_indexes: set[int] = set()
    normalized = tuple(
        str(item.get("path", "")).replace("\\", "/") for item in documents
    )
    for requested in requested_paths:
        canonical = requested.replace("\\", "/")
        exact = tuple(index for index, path in enumerate(normalized) if path == canonical)
        folded = tuple(
            index
            for index, path in enumerate(normalized)
            if path.casefold() == canonical.casefold()
        )
        eligible = exact or folded
        if not exact and len({normalized[index] for index in eligible}) > 1:
            continue
        selected_indexes.update(eligible)
    return [item for index, item in enumerate(documents) if index in selected_indexes]


def typed_query_entries(intent: SearchIntent) -> tuple[tuple[str, str, float], ...]:
    entries: dict[tuple[str, str], tuple[str, str, float]] = {}
    for signal in intent.signals:
        key = (signal.type, signal.value.casefold())
        multiplier = query_signal_multiplier(signal)
        entries.setdefault(key, (signal.type, signal.value, multiplier))
    for signal_type, values in (
        ("exact", intent.exact_terms),
        ("semantic", intent.semantic_terms),
        ("hint", intent.hints),
    ):
        for value in values:
            stripped = value.strip()
            if stripped:
                entries.setdefault(
                    (signal_type, stripped.casefold()),
                    (signal_type, stripped, 1.0),
                )
    return tuple(entries.values())


def query_signal_multiplier(signal: QuerySignal) -> float:
    multiplier = max(0.1, signal.weight)
    multiplier *= 1.0 + (max(0, signal.priority) / 100.0)
    if signal.protected:
        multiplier *= 1.25
    if signal.required:
        multiplier *= 1.15
    return multiplier


def query_field_boost(
    signal_type: str,
    field_name: str,
    multiplier: float,
    settings: ProjectionV3SearchControls,
) -> float:
    field_factor = SIGNAL_FIELD_FACTORS[signal_type].get(
        field_name,
        SIGNAL_FIELD_FACTORS_V3[signal_type].get(field_name, 0.0),
    )
    return (
        settings.field_boosts[field_name]
        * field_factor
        * settings.signal_type_boosts[signal_type]
        * multiplier
    )


def typed_query_mapping_telemetry(
    intent: SearchIntent,
    settings: ProjectionV3SearchControls,
) -> dict[str, object]:
    entries = typed_query_entries(intent)
    return {
        "signal_type_counts": distribution(signal_type for signal_type, _, _ in entries),
        "fields_by_signal_type": {
            signal_type: list(
                dict.fromkeys(
                    (*field_factors, *QUERY_FIELDS_BY_SIGNAL_TYPE[signal_type])
                )
            )
            for signal_type, field_factors in SIGNAL_FIELD_FACTORS.items()
        },
        "signal_type_boosts": dict(settings.signal_type_boosts),
        "field_boosts": {
            field_name: settings.field_boosts[field_name]
            for field_name in sorted(
                {
                    *(
                        field_name
                        for fields in SIGNAL_FIELD_FACTORS.values()
                        for field_name in fields
                    ),
                    *(
                        field_name
                        for fields in SIGNAL_FIELD_FACTORS_V3.values()
                        for field_name in fields
                    ),
                }
            )
        },
        "field_factors_by_signal_type": {
            signal_type: {
                **SIGNAL_FIELD_FACTORS[signal_type],
                **SIGNAL_FIELD_FACTORS_V3[signal_type],
            }
            for signal_type in SIGNAL_FIELD_FACTORS
        },
    }


_NEUTRAL_MATCH_CHANNELS = {
    "identifier_exact_content": "identifier_content",
    "symbol": "identifier_symbol",
    "identifier_exact_filename": "identifier_filename",
    "identifier_exact_path": "path_file",
    "filename": "path_filename",
    "path": "path_file",
    "content": "semantic_content",
    "technical_role": "technical_role",
    "path_segment": "path_segment",
    "route_method": "route_method",
    "route_path": "route_path",
    "route_mechanism": "route_mechanism",
    "import_target": "reference_import",
    "dependency_target": "dependency_target",
    "relation_target": "reference_relation_target",
    "documentation": "semantic_documentation",
}


def structural_observations(
    document: Mapping[str, object],
    *,
    source_ref: str,
    revision: str,
) -> tuple[EvidenceObservation, ...]:
    """Translate Projection v3 fields derived from canonical knowledge."""

    slice_ref = _optional_str(document.get("chunk_ref"))
    checksum = _optional_str(document.get("source_checksum"))
    line_start = _optional_int(document.get("line_start"))
    line_end = _optional_int(document.get("line_end"))
    observations: dict[tuple[str, str, str], EvidenceObservation] = {}

    def add(
        category: str,
        subtype: str,
        values: tuple[str, ...],
        *,
        attributes: dict[str, object] | None = None,
    ) -> None:
        for value in values:
            if not value.strip():
                continue
            key = (category, subtype, value.casefold())
            observations.setdefault(
                key,
                EvidenceObservation(
                    category=category,  # type: ignore[arg-type]
                    subtype=subtype,  # type: ignore[arg-type]
                    value=value,
                    source_ref=source_ref,
                    slice_ref=slice_ref,
                    revision=revision,
                    checksum=checksum,
                    line_start=line_start,
                    line_end=line_end,
                    provenance="canonical_repository_projection_v3",
                    attributes=dict(attributes or {}),
                ),
            )

    symbols = _string_values(document.get("symbols"))
    symbol_types = _string_values(document.get("symbol_types"))
    for index, symbol in enumerate(symbols):
        add(
            "identifier",
            "symbol",
            (symbol,),
            attributes=(
                {"symbol_type": symbol_types[index]}
                if index < len(symbol_types)
                else None
            ),
        )
    add(
        "identifier",
        "content_identifier",
        _string_values(document.get("content_identifiers_exact")),
    )
    add("path", "filename", _string_values(document.get("filename_exact")))
    add("path", "file", _string_values(document.get("path_text")))
    add("path", "segment", _string_values(document.get("path_segments")))
    add(
        "technical_role",
        "technical_role",
        _string_values(document.get("technical_role")),
    )
    add("route", "method", _string_values(document.get("route_methods")))
    add("route", "path", _string_values(document.get("route_paths")))
    add("route", "mechanism", _string_values(document.get("route_mechanisms")))
    add("reference", "import", _string_values(document.get("import_targets")))
    add(
        "dependency",
        "dependency_target",
        _string_values(document.get("dependency_targets")),
    )
    add(
        "reference",
        "relation_target",
        _string_values(document.get("relation_targets")),
    )
    return tuple(observations.values())


def relationships(
    document: Mapping[str, object],
) -> tuple[SearchRelationship, ...]:
    relationships: dict[tuple[str, str], SearchRelationship] = {}
    for token in _string_values(document.get("relation_tokens")):
        relationship_type, separator, target = token.partition(":")
        if not separator or not relationship_type.strip() or not target.strip():
            continue
        key = (relationship_type.casefold(), target.casefold())
        relationships.setdefault(
            key,
            SearchRelationship(
                type=relationship_type,
                target=target,
                discovered_from=_optional_str(document.get("source_ref")),
            ),
        )
    return tuple(relationships.values())


def query_match_contribution(
    signal: QuerySignal,
    raw_channels: Mapping[str, object],
) -> float:
    """Normalize Projection v3 channel strength without flattening all structure to 1.0."""

    field_factors = {
        **SIGNAL_FIELD_FACTORS.get(signal.type, {}),
        **SIGNAL_FIELD_FACTORS_V3.get(signal.type, {}),
    }
    factors: list[float] = []
    for raw_channel in raw_channels:
        query_fields = _CHANNEL_QUERY_FIELDS.get(str(raw_channel), ())
        factor = max(
            (field_factors.get(field_name, 0.0) for field_name in query_fields),
            default=0.0,
        )
        if factor > 0.0:
            factors.append(factor)
    channel_strength = max(factors, default=0.0)
    signal_strength = max(0.0, min(1.0, signal.weight))
    return max(0.0, min(1.0, channel_strength * signal_strength))


def query_match_evidence(
    match: ProjectionV3MatchResult,
    *,
    source_ref: str,
    provenance: str,
) -> tuple[QueryMatchEvidence, ...]:
    raw_by_signal: dict[tuple[str, str], Mapping[str, object]] = {}
    for raw in match.signal_matches:
        raw_signal = raw.get("query_signal")
        if not isinstance(raw_signal, Mapping):
            continue
        raw_by_signal[
            (
                str(raw_signal.get("type", "")),
                str(raw_signal.get("value", "")).casefold(),
            )
        ] = raw
    evidence: list[QueryMatchEvidence] = []
    for signal in match.matched_signals:
        match_data: Mapping[str, object] = raw_by_signal.get(
            (signal.type, signal.value.casefold()), {}
        )
        raw_channels = match_data.get("channels", {})
        if not isinstance(raw_channels, Mapping):
            continue
        channels: list[str] = []
        matched_values: list[str] = []
        for raw_channel, raw_values in raw_channels.items():
            neutral = _NEUTRAL_MATCH_CHANNELS.get(str(raw_channel))
            if neutral is None:
                continue
            channels.append(neutral)
            matched_values.extend(_string_values(raw_values))
        if not channels or not matched_values:
            continue
        structural = any(
            item.startswith(("identifier_", "path_", "route_", "reference_", "dependency_"))
            or item == "technical_role"
            for item in channels
        )
        strength = query_match_contribution(signal, raw_channels)
        evidence.append(
            QueryMatchEvidence(
                signal=signal,
                match_type=(
                    "exact_structural"
                    if structural and signal.type in {"exact", "reference"}
                    else "structural"
                    if structural
                    else "semantic"
                ),
                channels=tuple(dict.fromkeys(channels)),
                matched_values=tuple(dict.fromkeys(matched_values)),
                source_ref=source_ref,
                strength=strength,
                normalized_contribution=strength,
                provenance=provenance,
                derivation=("canonical_repository_projection_v3",),
            )
        )
    return tuple(evidence)


def match_projection_record(
    intent: SearchIntent,
    document: Mapping[str, object],
    settings: ProjectionV3SearchControls,
) -> ProjectionV3MatchResult:
    filename_values = _string_values(document.get("filename"))
    path_values = _string_values(document.get("path"))
    fields = {
        "identifier_exact_content": _string_values(
            document.get("content_identifiers_exact")
        ),
        "symbol": _string_values(document.get("symbols")),
        "identifier_exact_filename": _identifiers_from_values(filename_values),
        "identifier_exact_path": _identifiers_from_values(path_values),
        "filename": filename_values,
        "path": path_values,
        "content": _string_values(document.get("content")),
        "technical_role": _string_values(document.get("technical_role")),
        "path_segment": _string_values(document.get("path_segments")),
        "route_method": _string_values(document.get("route_methods")),
        "route_path": _string_values(document.get("route_paths")),
        "route_mechanism": _string_values(document.get("route_mechanisms")),
        "import_target": _string_values(document.get("import_targets")),
        "dependency_target": _string_values(document.get("dependency_targets")),
        "relation_target": _string_values(document.get("relation_targets")),
        "documentation": _string_values(document.get("documentation_text")),
    }
    relation_tokens = _string_values(document.get("relation_tokens"))
    channels: set[str] = set()
    channel_values: dict[str, tuple[str, ...]] = {}
    channel_boosts: dict[str, float] = {}
    channels_by_signal_type: dict[str, set[str]] = {}
    signal_matches: list[dict[str, object]] = []
    relation_edges: list[str] = []
    diagnostics_truncated = False
    structural_candidate_confidence, implementation_evidence = (
        structural_candidate_evidence(document)
    )
    matched_terms: list[str] = []

    def matched(
        term: str,
        signal_type: str,
        multiplier: float,
        weighted_signal: QuerySignal | None,
    ) -> set[str]:
        nonlocal diagnostics_truncated
        needle = term.casefold()
        term_channels: set[str] = set()
        term_channel_values: dict[str, tuple[str, ...]] = {}
        term_channel_boosts: dict[str, float] = {}
        allowed_channels = QUERY_CHANNELS_BY_SIGNAL_TYPE[signal_type]
        for channel, values in fields.items():
            if channel not in allowed_channels:
                continue
            matching_values = tuple(
                value
                for value in values
                if (
                    needle == value
                    if channel in _KEYWORD_MATCH_CHANNELS
                    else needle in value
                )
            )
            if matching_values:
                channels.add(channel)
                term_channels.add(channel)
                bounded_values = tuple(
                    _bounded_diagnostic_value(value)
                    for value in matching_values[:_MAX_DIAGNOSTIC_VALUES_PER_CHANNEL]
                )
                if len(matching_values) > len(bounded_values):
                    diagnostics_truncated = True
                if bounded_values != matching_values[: len(bounded_values)]:
                    diagnostics_truncated = True
                term_channel_values[channel] = bounded_values
                combined_values = tuple(
                    dict.fromkeys((*channel_values.get(channel, ()), *bounded_values))
                )
                if len(combined_values) > _MAX_DIAGNOSTIC_VALUES_PER_CHANNEL:
                    diagnostics_truncated = True
                channel_values[channel] = combined_values[
                    :_MAX_DIAGNOSTIC_VALUES_PER_CHANNEL
                ]
                query_fields = {
                    *SIGNAL_FIELD_FACTORS[signal_type],
                    *SIGNAL_FIELD_FACTORS_V3[signal_type],
                }
                field_name = next(
                    item
                    for item in _CHANNEL_QUERY_FIELDS[channel]
                    if item in query_fields
                )
                boost = query_field_boost(
                    signal_type,
                    field_name,
                    multiplier,
                    settings,
                )
                term_channel_boosts[channel] = boost
                channel_boosts[channel] = max(channel_boosts.get(channel, 0.0), boost)
        if term_channels:
            matched_terms.append(term)
            channels_by_signal_type.setdefault(signal_type, set()).update(term_channels)
            matched_edges: tuple[str, ...] = ()
            if "relation_target" in term_channels:
                raw_matched_edges = tuple(
                    token
                    for token in relation_tokens
                    if (parts := token.partition(":"))[1] and parts[2] == needle
                )
                if len(raw_matched_edges) > _MAX_DIAGNOSTIC_RELATION_EDGES:
                    diagnostics_truncated = True
                matched_edges = tuple(
                    _bounded_diagnostic_value(value)
                    for value in raw_matched_edges[:_MAX_DIAGNOSTIC_RELATION_EDGES]
                )
                if matched_edges != raw_matched_edges[: len(matched_edges)]:
                    diagnostics_truncated = True
                combined_edges = tuple(dict.fromkeys((*relation_edges, *matched_edges)))
                if len(combined_edges) > _MAX_DIAGNOSTIC_RELATION_EDGES:
                    diagnostics_truncated = True
                relation_edges[:] = combined_edges[:_MAX_DIAGNOSTIC_RELATION_EDGES]
            if len(signal_matches) < _MAX_DIAGNOSTIC_SIGNAL_MATCHES:
                if len(term) > _MAX_DIAGNOSTIC_VALUE_CHARS or (
                    weighted_signal is not None
                    and len(weighted_signal.classification) > _MAX_DIAGNOSTIC_VALUE_CHARS
                ):
                    diagnostics_truncated = True
                signal_matches.append(
                    {
                        "query_signal": {
                            "type": signal_type,
                            "value": _bounded_diagnostic_value(term),
                            **(
                                {
                                    "classification": _bounded_diagnostic_value(
                                        weighted_signal.classification
                                    ),
                                    "protected": weighted_signal.protected,
                                    "required": weighted_signal.required,
                                }
                                if weighted_signal is not None
                                else {}
                            ),
                        },
                        "channels": {
                            key: list(value)
                            for key, value in sorted(term_channel_values.items())
                        },
                        "channel_boosts": dict(sorted(term_channel_boosts.items())),
                        "relation_edges": list(matched_edges),
                    }
                )
            else:
                diagnostics_truncated = True
        return term_channels

    weighted: list[tuple[str, str, float, float, QuerySignal | None]] = []
    seen: set[tuple[str, str]] = set()
    for signal in intent.signals:
        key = (signal.type, signal.value.casefold())
        if key in seen:
            continue
        seen.add(key)
        weight = max(0.1, signal.weight) * (1.25 if signal.protected else 1.0)
        if signal.required:
            weight *= 1.15
        weighted.append(
            (
                signal.type,
                signal.value,
                weight,
                query_signal_multiplier(signal),
                signal,
            )
        )
    for signal_type, terms, weight in (
        ("exact", intent.exact_terms, 3.0),
        ("semantic", intent.semantic_terms, 1.0),
        ("hint", intent.hints, 0.5),
    ):
        for term in terms:
            term_key = (signal_type, term.casefold())
            if term_key not in seen:
                seen.add(term_key)
                weighted.append((signal_type, term, weight, 1.0, None))
    structural_total = 0.0
    structural_matched = 0.0
    semantic_total = 0.0
    semantic_matched = 0.0
    protected_semantic_total = 0.0
    protected_semantic_matched = 0.0
    protected_identity_matched = 0.0
    hint_total = 0.0
    hint_matched = 0.0
    matched_semantic_concepts: set[str] = set()
    matched_signals: list[QuerySignal] = []
    for signal_type, term, weight, multiplier, weighted_signal in weighted:
        is_structural = signal_type in {"exact", "reference"}
        if is_structural:
            structural_total += weight
        elif signal_type == "semantic":
            semantic_total += weight
            if weighted_signal is not None and weighted_signal.protected:
                protected_semantic_total += weight
        else:
            hint_total += weight
        term_channels = matched(term, signal_type, multiplier, weighted_signal)
        if term_channels:
            if is_structural:
                structural_matched += weight * _channel_strength(
                    term_channels, _STRUCTURAL_CHANNEL_STRENGTH
                )
            elif signal_type == "semantic":
                match_strength = _channel_strength(
                    term_channels, _SEMANTIC_CHANNEL_STRENGTH
                )
                semantic_matched += weight * match_strength
                if weighted_signal is not None and weighted_signal.protected:
                    protected_semantic_matched += weight * match_strength
                    if "path" in term_channels or _matches_structural_identity(
                        term, fields
                    ):
                        protected_identity_matched += weight
                matched_semantic_concepts.update(_semantic_concepts(term))
            else:
                hint_matched += weight * _channel_strength(
                    term_channels, _SEMANTIC_CHANNEL_STRENGTH
                )
            if weighted_signal is not None:
                matched_signals.append(weighted_signal)
    structural_alignment = (
        _clamp(structural_matched / structural_total) if structural_total else 0.0
    )
    semantic_alignment = _clamp(semantic_matched / semantic_total) if semantic_total else 0.0
    protected_semantic_alignment = 0.0
    protected_identity_alignment = 0.0
    if protected_semantic_total:
        protected_semantic_alignment = _clamp(
            protected_semantic_matched / protected_semantic_total
        )
        protected_identity_alignment = _clamp(
            protected_identity_matched / protected_semantic_total
        )
        semantic_alignment = min(
            protected_semantic_alignment,
            protected_identity_alignment,
        )
    if len(matched_semantic_concepts) < 2:
        semantic_alignment = min(semantic_alignment, 0.5)
    hint_alignment = _clamp(hint_matched / hint_total) if hint_total else 0.0
    auxiliary_alignment = max(semantic_alignment, hint_alignment)
    semantic_reuse_alignment = _clamp(
        semantic_alignment
        * structural_candidate_confidence
        * _SEMANTIC_REUSE_MAX
    )
    hint_support_alignment = _clamp(hint_alignment * _HINT_ALIGNMENT_MAX)
    if structural_total or semantic_total or hint_total:
        alignment = max(
            structural_alignment,
            semantic_reuse_alignment,
            hint_support_alignment,
        )
    else:
        alignment = 1.0
    return ProjectionV3MatchResult(
        channels=tuple(sorted(channels)),
        channel_values={key: value for key, value in sorted(channel_values.items())},
        channel_boosts=dict(sorted(channel_boosts.items())),
        matched_terms=_dedupe(matched_terms)[:_MAX_DIAGNOSTIC_SIGNAL_MATCHES],
        matched_signals=tuple(matched_signals[:_MAX_DIAGNOSTIC_SIGNAL_MATCHES]),
        signal_matches=tuple(signal_matches),
        relation_edges=tuple(relation_edges),
        diagnostics_truncated=(
            diagnostics_truncated
            or len(_dedupe(matched_terms)) > _MAX_DIAGNOSTIC_SIGNAL_MATCHES
            or len(matched_signals) > _MAX_DIAGNOSTIC_SIGNAL_MATCHES
        ),
        alignment=alignment,
        structural_alignment=structural_alignment,
        auxiliary_alignment=auxiliary_alignment,
        semantic_alignment=semantic_alignment,
        hint_alignment=hint_alignment,
        protected_semantic_alignment=protected_semantic_alignment,
        protected_identity_alignment=protected_identity_alignment,
        semantic_reuse_alignment=semantic_reuse_alignment,
        structural_candidate_confidence=structural_candidate_confidence,
        implementation_evidence=implementation_evidence,
        channels_by_signal_type={
            key: tuple(sorted(value)) for key, value in sorted(channels_by_signal_type.items())
        },
    )


def classify_capabilities(knowledge_type: str, match: ProjectionV3MatchResult) -> tuple[str, ...]:
    if not match.channels:
        return ()
    if knowledge_type == "code" and match.implementation_evidence:
        return ("implementation",)
    if knowledge_type == "tests":
        return ("tests",)
    return ()


def structural_candidate_evidence(
    document: Mapping[str, object],
) -> tuple[float, tuple[str, ...]]:
    knowledge_type = str(document.get("knowledge_type", "unknown"))
    if knowledge_type == "tests":
        return 1.0, ("test_classification",)
    if knowledge_type != "code":
        return 0.0, ()

    symbols = _string_values(document.get("symbols"))
    symbol_types = _string_values(document.get("symbol_types"))
    evidence: set[str] = set()
    if symbols and any(item in _EXECUTABLE_SYMBOL_TYPES for item in symbol_types):
        evidence.add("executable_symbol")

    content = "\n".join(_string_values(document.get("content")))
    for evidence_name, pattern in _EXECUTABLE_DECLARATION_PATTERNS:
        if pattern.search(content):
            evidence.add(evidence_name)

    if not evidence:
        return 0.0, ()
    confidence = 0.9
    technical_role = technical_role_value(document.get("technical_role"))
    if "executable_symbol" in evidence or technical_role in _IMPLEMENTATION_TECHNICAL_ROLES:
        confidence = 1.0
    return confidence, tuple(sorted(evidence))


def _channel_strength(channels: set[str], strengths: Mapping[str, float]) -> float:
    return max((strengths.get(channel, 0.0) for channel in channels), default=0.0)


def _semantic_concepts(term: str) -> set[str]:
    return {
        token.casefold()
        for token in _SEMANTIC_TOKEN_PATTERN.findall(term)
        if token.strip()
    }


def _matches_structural_identity(
    term: str,
    fields: Mapping[str, tuple[str, ...]],
) -> bool:
    needle = term.casefold()
    return any(
        needle in value
        for channel in ("symbol", "filename", "path")
        for value in fields[channel]
    )


def normalize_provider_scores(
    docs: list[dict[str, object]],
    matches: tuple[ProjectionV3MatchResult, ...],
    max_raw_score: float,
) -> tuple[float, ...]:
    scores: list[float] = []
    previous_score = 1.0
    for document, match in zip(docs, matches, strict=True):
        raw_score = _float_value(document.get("score"))
        relative = raw_score / max_raw_score if max_raw_score > 0 else 0.0
        proposed = _clamp((0.7 * relative) + (0.3 * match.alignment))
        normalized = min(previous_score, proposed)
        scores.append(normalized)
        previous_score = normalized
    return tuple(scores)


def bounded_diagnostic_metadata(
    *,
    docs: list[dict[str, object]],
    matches: tuple[ProjectionV3MatchResult, ...],
    provider_scores: tuple[float, ...],
    output_limit: int,
    max_raw_score: float,
    settings: ProjectionV3SearchControls,
    num_found: int,
) -> dict[str, object]:
    window = settings.diagnostic_candidate_window
    if window is None:
        return {"diagnostic_candidate_window": "disabled"}
    order = [
        {
            "id": str(document.get("id", "")),
            "source": str(document.get("path", document.get("source_ref", ""))),
            "source_ref": str(document.get("source_ref", "")),
            "pre_cutoff_rank": rank,
            "raw_score": _float_value(document.get("score")),
            "normalized_diagnostic_score": (
                _float_value(document.get("score")) / max_raw_score
                if max_raw_score > 0
                else 0.0
            ),
            "provider_relevance_score": provider_score,
            "query_alignment_score": match.alignment,
            "knowledge_type": str(document.get("knowledge_type", "unknown")),
            "technical_role": technical_role_value(document.get("technical_role")),
            "matched_channels": list(match.channels),
            "matched_channel_values": {
                key: list(value) for key, value in match.channel_values.items()
            },
            "matched_channel_boosts": dict(match.channel_boosts),
            "matched_query_signal_channels": list(match.signal_matches),
            "matched_relation_edges": list(match.relation_edges),
            "diagnostics_truncated": match.diagnostics_truncated,
            "returned": rank <= output_limit,
        }
        for rank, (document, match, provider_score) in enumerate(
            zip(docs, matches, provider_scores, strict=True),
            start=1,
        )
    ]
    diagnostics_truncated = num_found > len(order) or any(
        match.diagnostics_truncated for match in matches
    )
    return {
        "diagnostic_candidate_window": window,
        "diagnostic_signal_match_limit": _MAX_DIAGNOSTIC_SIGNAL_MATCHES,
        "diagnostic_values_per_channel_limit": _MAX_DIAGNOSTIC_VALUES_PER_CHANNEL,
        "diagnostic_relation_edge_limit": _MAX_DIAGNOSTIC_RELATION_EDGES,
        "diagnostic_value_character_limit": _MAX_DIAGNOSTIC_VALUE_CHARS,
        "diagnostics_truncated": diagnostics_truncated,
        "diagnostic_truncation_reasons": [
            reason
            for condition, reason in (
                (num_found > len(order), "candidate_window"),
                (
                    any(match.diagnostics_truncated for match in matches),
                    "candidate_match_detail",
                ),
            )
            if condition
        ],
        "candidates_found_before_cutoff": num_found,
        "candidates_observed_before_cutoff": len(order),
        "candidate_sources_before_cutoff": [item["source"] for item in order],
        "candidate_order_before_cutoff": order,
    }


def query_type_counts(intent: SearchIntent) -> dict[str, int]:
    return {
        "exact": len(intent.exact_terms),
        "semantic": len(intent.semantic_terms),
        "hint": len(intent.hints),
        "signals": len(intent.signals),
    }


def score_distribution(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "min": 0.0, "max": 0.0, "mean": 0.0}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


def distribution(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def technical_role_value(value: object) -> str:
    if value is None:
        return "unknown"
    rendered = str(value).strip()
    return rendered or "unknown"


def _string_values(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(item).casefold() for item in value)
    return (str(value).casefold(),)


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    result: dict[str, str] = {}
    for value in values:
        stripped = value.strip()
        if stripped:
            result.setdefault(stripped.casefold(), stripped)
    return tuple(result.values())


def _bounded_diagnostic_value(value: str) -> str:
    if len(value) <= _MAX_DIAGNOSTIC_VALUE_CHARS:
        return value
    return value[: _MAX_DIAGNOSTIC_VALUE_CHARS - 3] + "..."


def _float_value(value: object) -> float:
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return 0.0


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(cast(int | str, value))
    except (TypeError, ValueError):
        return None


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


DEFAULT_PROJECTION_V3_FIELD_BOOSTS = DEFAULT_FIELD_BOOSTS
DEFAULT_PROJECTION_V3_SIGNAL_TYPE_BOOSTS = DEFAULT_SIGNAL_TYPE_BOOSTS
PROJECTION_V3_QUERY_FIELDS_BY_SIGNAL_TYPE = QUERY_FIELDS_BY_SIGNAL_TYPE
PROJECTION_V3_SIGNAL_FIELD_FACTORS = SIGNAL_FIELD_FACTORS


def _finite_control_number(value: object, name: str, backend_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KnowledgeSearchError(
            f"{backend_name} {name} must be a finite number",
            category="schema_mismatch",
        )
    try:
        number = float(value)
    except OverflowError as exc:
        raise KnowledgeSearchError(
            f"{backend_name} {name} must be a finite number",
            category="schema_mismatch",
        ) from exc
    if not math.isfinite(number):
        raise KnowledgeSearchError(
            f"{backend_name} {name} must be a finite number",
            category="schema_mismatch",
        )
    return number


def validate_projection_v3_controls(
    value: Mapping[str, object],
    *,
    backend_name: str,
) -> ValidatedProjectionV3Controls:
    """Validate only the shared Projection v3 controls for a backend."""

    raw_projection_version = value.get("projection_version", PROJECTION_VERSION)
    if not isinstance(raw_projection_version, str):
        raise KnowledgeSearchError(
            f"{backend_name} projection_version must be a string",
            category="schema_mismatch",
        )
    projection_version = raw_projection_version.strip()
    if not projection_version:
        raise KnowledgeSearchError(
            f"{backend_name} projection_version must not be empty",
            category="schema_mismatch",
        )

    raw_boosts = value.get("field_boosts", DEFAULT_FIELD_BOOSTS)
    if not isinstance(raw_boosts, Mapping):
        raise KnowledgeSearchError(
            f"{backend_name} field_boosts must be an object",
            category="schema_mismatch",
        )
    boosts = dict(DEFAULT_FIELD_BOOSTS)
    for raw_name, raw_boost in raw_boosts.items():
        if not isinstance(raw_name, str):
            raise KnowledgeSearchError(
                f"{backend_name} boost field name must be a string",
                category="schema_mismatch",
            )
        name = raw_name
        if name not in _ALLOWED_BOOST_FIELDS:
            raise KnowledgeSearchError(
                f"unsupported {backend_name} boost field {name!r}",
                category="schema_mismatch",
            )
        boost = _finite_control_number(raw_boost, f"boost for {name!r}", backend_name)
        if not 0 < boost <= 100:
            raise KnowledgeSearchError(
                f"{backend_name} boost for {name!r} must be between 0 and 100",
                category="schema_mismatch",
            )
        boosts[name] = boost

    raw_signal_boosts = value.get("signal_type_boosts", DEFAULT_SIGNAL_TYPE_BOOSTS)
    if not isinstance(raw_signal_boosts, Mapping):
        raise KnowledgeSearchError(
            f"{backend_name} signal_type_boosts must be an object",
            category="schema_mismatch",
        )
    signal_boosts = dict(DEFAULT_SIGNAL_TYPE_BOOSTS)
    for raw_name, raw_boost in raw_signal_boosts.items():
        if not isinstance(raw_name, str):
            raise KnowledgeSearchError(
                f"{backend_name} query signal type must be a string",
                category="schema_mismatch",
            )
        name = raw_name
        if name not in DEFAULT_SIGNAL_TYPE_BOOSTS:
            raise KnowledgeSearchError(
                f"unsupported {backend_name} query signal type {name!r}",
                category="schema_mismatch",
            )
        boost = _finite_control_number(raw_boost, f"signal boost for {name!r}", backend_name)
        if not 0 < boost <= 10:
            raise KnowledgeSearchError(
                f"{backend_name} signal boost for {name!r} must be between 0 and 10",
                category="schema_mismatch",
            )
        signal_boosts[name] = boost

    raw_window = value.get("diagnostic_candidate_window")
    diagnostic_candidate_window: int | None = None
    if raw_window is not None:
        if isinstance(raw_window, bool) or not isinstance(raw_window, int):
            raise KnowledgeSearchError(
                f"{backend_name} diagnostic_candidate_window must be an integer",
                category="schema_mismatch",
            )
        diagnostic_candidate_window = raw_window
        if not 1 <= diagnostic_candidate_window <= 5_000:
            raise KnowledgeSearchError(
                f"{backend_name} diagnostic_candidate_window must be between 1 and 5000",
                category="schema_mismatch",
            )

    return ValidatedProjectionV3Controls(
        projection_version=projection_version,
        field_boosts=boosts,
        signal_type_boosts=signal_boosts,
        diagnostic_candidate_window=diagnostic_candidate_window,
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


float_value = _float_value
optional_int = _optional_int
optional_str = _optional_str
dedupe = _dedupe
semantic_token_pattern = _SEMANTIC_TOKEN_PATTERN
executable_symbol_types = _EXECUTABLE_SYMBOL_TYPES
executable_declaration_patterns = _EXECUTABLE_DECLARATION_PATTERNS
implementation_technical_roles = _IMPLEMENTATION_TECHNICAL_ROLES
structural_channel_strength = _STRUCTURAL_CHANNEL_STRENGTH
semantic_channel_strength = _SEMANTIC_CHANNEL_STRENGTH
semantic_reuse_max = _SEMANTIC_REUSE_MAX
hint_alignment_max = _HINT_ALIGNMENT_MAX
max_diagnostic_signal_matches = _MAX_DIAGNOSTIC_SIGNAL_MATCHES
max_diagnostic_values_per_channel = _MAX_DIAGNOSTIC_VALUES_PER_CHANNEL
max_diagnostic_relation_edges = _MAX_DIAGNOSTIC_RELATION_EDGES
max_diagnostic_value_chars = _MAX_DIAGNOSTIC_VALUE_CHARS
allowed_boost_fields = _ALLOWED_BOOST_FIELDS
channel_query_fields = _CHANNEL_QUERY_FIELDS
keyword_match_channels = _KEYWORD_MATCH_CHANNELS
neutral_match_channels = _NEUTRAL_MATCH_CHANNELS
channel_strength = _channel_strength
semantic_concepts = _semantic_concepts
matches_structural_identity = _matches_structural_identity
string_values = _string_values
bounded_diagnostic_value = _bounded_diagnostic_value
