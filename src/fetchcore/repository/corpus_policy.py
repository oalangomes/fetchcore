"""Backend-neutral corpus purpose and source-origin policy."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path


CORPUS_PURPOSE_VALUES: frozenset[str] = frozenset(
    {"source-code", "engineering-docs", "product-docs", "mixed", "unknown"}
)
SOURCE_ORIGIN_VALUES: frozenset[str] = frozenset(
    {"authored", "generated", "runtime", "mirrored", "unknown"}
)


@dataclass(frozen=True)
class CorpusPurposeRule:
    """Declarative path rule assigning one corpus purpose."""

    pattern: str
    purpose: str


@dataclass(frozen=True)
class SourceOriginRule:
    """Declarative path rule assigning one observable/configured source origin."""

    pattern: str
    origin: str


def validate_corpus_policy(
    *,
    default_purpose: str,
    default_origin: str,
    purpose_rules: tuple[CorpusPurposeRule, ...],
    origin_rules: tuple[SourceOriginRule, ...],
) -> None:
    """Fail closed on unsupported canonical corpus values."""

    _validate_value(default_purpose, CORPUS_PURPOSE_VALUES, "default corpus purpose")
    _validate_value(default_origin, SOURCE_ORIGIN_VALUES, "default source origin")
    for purpose_rule in purpose_rules:
        if not purpose_rule.pattern.strip():
            raise ValueError("corpus purpose rule pattern must not be empty")
        _validate_value(
            purpose_rule.purpose,
            CORPUS_PURPOSE_VALUES,
            "corpus purpose",
        )
    for origin_rule in origin_rules:
        if not origin_rule.pattern.strip():
            raise ValueError("source origin rule pattern must not be empty")
        _validate_value(origin_rule.origin, SOURCE_ORIGIN_VALUES, "source origin")


def resolve_corpus_purpose(
    relative: str,
    *,
    default: str,
    rules: tuple[CorpusPurposeRule, ...],
) -> str:
    """Resolve first-match corpus purpose without guessing from repository identity."""

    for rule in rules:
        if _matches(relative, rule.pattern):
            return rule.purpose
    return default


def resolve_source_origin(
    relative: str,
    *,
    default: str,
    rules: tuple[SourceOriginRule, ...],
) -> str:
    """Resolve first-match source origin independently from selection/exclusion."""

    for rule in rules:
        if _matches(relative, rule.pattern):
            return rule.origin
    return default


def _matches(relative: str, pattern: str) -> bool:
    return fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(Path(relative).name, pattern)


def _validate_value(value: str, allowed: frozenset[str], context: str) -> None:
    if value not in allowed:
        rendered = ", ".join(sorted(allowed))
        raise ValueError(f"unsupported {context} {value!r}; expected one of: {rendered}")
