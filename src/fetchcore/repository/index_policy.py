"""Declarative repository-local indexing policy.

The policy lives at ``.agentsorch/indexing.yaml`` inside the materialized
repository workspace. It configures canonical repository indexing without
binding the indexer to repository names, transports, or persistence backends.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from fetchcore.repository.corpus_policy import (
    CORPUS_PURPOSE_VALUES,
    SOURCE_ORIGIN_VALUES,
    CorpusPurposeRule,
    SourceOriginRule,
)
from fetchcore.repository.indexing import (
    ClassificationRule,
    RepositoryIndexConfig,
    SymbolExtractor,
    TechnicalRoleRule,
)


INDEXING_POLICY_VERSION = 1
INDEXING_POLICY_PATH = Path(".agentsorch/indexing.yaml")
_ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {
        "version",
        "selection",
        "limits",
        "chunking",
        "classification",
        "technical_roles",
        "extractors",
        "corpus",
    }
)
_ALLOWED_SELECTION_KEYS = frozenset(
    {"include", "exclude", "replace_default_includes"}
)
_ALLOWED_LIMIT_KEYS = frozenset({"max_files", "max_file_bytes", "max_total_bytes"})
_ALLOWED_CHUNKING_KEYS = frozenset({"max_lines", "overlap_lines"})
_ALLOWED_RULE_SET_KEYS = frozenset({"replace_defaults", "rules"})
_ALLOWED_EXTRACTOR_KEYS = frozenset({"enabled"})
_ALLOWED_CORPUS_KEYS = frozenset(
    {"default_purpose", "default_origin", "purpose", "origin"}
)
_CORPUS_RULE_KEYS = frozenset({"pattern", "value"})
_CLASSIFICATION_RULE_KEYS = frozenset({"pattern", "knowledge_type"})
_TECHNICAL_ROLE_RULE_KEYS = frozenset({"token", "role"})


class RepositoryIndexPolicyError(ValueError):
    """Raised when repository indexing policy is malformed or unsupported."""


@dataclass(frozen=True)
class RepositoryIndexPolicy:
    """Resolved repository-local policy before repository identity is attached."""

    config: RepositoryIndexConfig
    enabled_extractors: tuple[str, ...] | None = None
    source: Path | None = None
    version: int = INDEXING_POLICY_VERSION

    def config_for(
        self,
        *,
        repository: str,
        branch: str | None,
        revision: str | None,
    ) -> RepositoryIndexConfig:
        """Attach observed repository identity without changing corpus policy."""

        return replace(
            self.config,
            repository=repository,
            branch=branch,
            revision=revision,
        )

    def select_extractors(
        self,
        registry: Mapping[str, SymbolExtractor],
    ) -> dict[str, SymbolExtractor]:
        """Return the structural extractor registry allowed by this policy."""

        if self.enabled_extractors is None:
            return dict(registry)
        unknown = sorted(set(self.enabled_extractors) - set(registry))
        if unknown:
            raise RepositoryIndexPolicyError(
                "unknown structural extractor language(s): " + ", ".join(unknown)
            )
        return {language: registry[language] for language in self.enabled_extractors}

    def to_mapping(self) -> dict[str, object]:
        """Expose resolved policy facts for operational inspection in Part 7."""

        return {
            "version": self.version,
            "source": None if self.source is None else str(self.source),
            "include_patterns": list(self.config.include_patterns),
            "exclude_patterns": list(self.config.exclude_patterns),
            "classification_rules": [
                {"pattern": item.pattern, "knowledge_type": item.knowledge_type}
                for item in self.config.classification_rules
            ],
            "technical_role_rules": [
                {"token": item.token, "role": item.role}
                for item in self.config.technical_role_rules
            ],
            "limits": {
                "max_files": self.config.max_files,
                "max_file_bytes": self.config.max_file_bytes,
                "max_total_bytes": self.config.max_total_bytes,
            },
            "chunking": {
                "max_lines": self.config.max_chunk_lines,
                "overlap_lines": self.config.chunk_overlap_lines,
            },
            "enabled_extractors": (
                None if self.enabled_extractors is None else list(self.enabled_extractors)
            ),
            "corpus": {
                "default_purpose": self.config.default_corpus_purpose,
                "default_origin": self.config.default_source_origin,
                "purpose": [
                    {"pattern": item.pattern, "value": item.purpose}
                    for item in self.config.corpus_purpose_rules
                ],
                "origin": [
                    {"pattern": item.pattern, "value": item.origin}
                    for item in self.config.source_origin_rules
                ],
            },
        }


def load_repository_index_policy(
    workspace: Path,
    *,
    base_config: RepositoryIndexConfig | None = None,
) -> RepositoryIndexPolicy:
    """Load ``.agentsorch/indexing.yaml`` or return the canonical defaults."""

    root = workspace.resolve(strict=True)
    if not root.is_dir():
        raise RepositoryIndexPolicyError(
            f"repository workspace is not a directory: {workspace}"
        )
    config = base_config or RepositoryIndexConfig()
    policy_path = root / INDEXING_POLICY_PATH
    if not policy_path.is_file():
        return RepositoryIndexPolicy(config=config)

    payload = _read_policy(policy_path)
    _reject_unknown_keys(payload, _ALLOWED_TOP_LEVEL_KEYS, context="indexing policy")
    version = _integer(payload.get("version", INDEXING_POLICY_VERSION), "version")
    if version != INDEXING_POLICY_VERSION:
        raise RepositoryIndexPolicyError(
            f"unsupported indexing policy version {version}; "
            f"expected {INDEXING_POLICY_VERSION}"
        )

    selection = _mapping(payload.get("selection"), "selection")
    limits = _mapping(payload.get("limits"), "limits")
    chunking = _mapping(payload.get("chunking"), "chunking")
    classification = _mapping(payload.get("classification"), "classification")
    technical_roles = _mapping(payload.get("technical_roles"), "technical_roles")
    extractors = _mapping(payload.get("extractors"), "extractors")
    corpus = _mapping(payload.get("corpus"), "corpus")

    _reject_unknown_keys(selection, _ALLOWED_SELECTION_KEYS, context="selection")
    _reject_unknown_keys(limits, _ALLOWED_LIMIT_KEYS, context="limits")
    _reject_unknown_keys(chunking, _ALLOWED_CHUNKING_KEYS, context="chunking")
    _reject_unknown_keys(
        classification,
        _ALLOWED_RULE_SET_KEYS,
        context="classification",
    )
    _reject_unknown_keys(
        technical_roles,
        _ALLOWED_RULE_SET_KEYS,
        context="technical_roles",
    )
    _reject_unknown_keys(extractors, _ALLOWED_EXTRACTOR_KEYS, context="extractors")
    _reject_unknown_keys(corpus, _ALLOWED_CORPUS_KEYS, context="corpus")

    replace_default_includes = _boolean(
        selection.get("replace_default_includes", False),
        "selection.replace_default_includes",
    )
    configured_includes = _string_tuple(selection.get("include"), "selection.include")
    if replace_default_includes:
        if "include" not in selection or not configured_includes:
            raise RepositoryIndexPolicyError(
                "selection.include must be non-empty when "
                "replace_default_includes is true"
            )
        include_patterns = configured_includes
    else:
        include_patterns = _dedupe((*config.include_patterns, *configured_includes))

    configured_excludes = _string_tuple(selection.get("exclude"), "selection.exclude")
    # Safety exclusions are intentionally permanent. Repository policy can only
    # add exclusions, never silently re-enable .git, .agentsorch, build output,
    # dependencies, or other canonical default exclusions.
    exclude_patterns = _dedupe((*config.exclude_patterns, *configured_excludes))

    replace_classification_defaults = _boolean(
        classification.get("replace_defaults", False),
        "classification.replace_defaults",
    )
    classification_rules = _classification_rules(
        classification.get("rules"),
        "classification.rules",
    )
    resolved_classification_rules = (
        classification_rules
        if replace_classification_defaults
        else (*classification_rules, *config.classification_rules)
    )

    default_corpus_purpose = _enum_string(
        corpus.get("default_purpose"),
        "corpus.default_purpose",
        CORPUS_PURPOSE_VALUES,
        config.default_corpus_purpose,
    )
    default_source_origin = _enum_string(
        corpus.get("default_origin"),
        "corpus.default_origin",
        SOURCE_ORIGIN_VALUES,
        config.default_source_origin,
    )
    configured_purpose_rules = _corpus_purpose_rules(
        corpus.get("purpose"),
        "corpus.purpose",
    )
    configured_origin_rules = _source_origin_rules(
        corpus.get("origin"),
        "corpus.origin",
    )

    replace_technical_role_defaults = _boolean(
        technical_roles.get("replace_defaults", False),
        "technical_roles.replace_defaults",
    )
    technical_role_rules = _technical_role_rules(
        technical_roles.get("rules"),
        "technical_roles.rules",
    )
    resolved_technical_role_rules = (
        technical_role_rules
        if replace_technical_role_defaults
        else (*technical_role_rules, *config.technical_role_rules)
    )

    try:
        resolved = replace(
            config,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            classification_rules=resolved_classification_rules,
            technical_role_rules=resolved_technical_role_rules,
            default_corpus_purpose=default_corpus_purpose,
            default_source_origin=default_source_origin,
            corpus_purpose_rules=(
                *configured_purpose_rules,
                *config.corpus_purpose_rules,
            ),
            source_origin_rules=(
                *configured_origin_rules,
                *config.source_origin_rules,
            ),
            max_files=_positive_integer(
                limits.get("max_files"),
                "limits.max_files",
                config.max_files,
            ),
            max_file_bytes=_positive_integer(
                limits.get("max_file_bytes"),
                "limits.max_file_bytes",
                config.max_file_bytes,
            ),
            max_total_bytes=_positive_integer(
                limits.get("max_total_bytes"),
                "limits.max_total_bytes",
                config.max_total_bytes,
            ),
            max_chunk_lines=_positive_integer(
                chunking.get("max_lines"),
                "chunking.max_lines",
                config.max_chunk_lines,
            ),
            chunk_overlap_lines=_non_negative_integer(
                chunking.get("overlap_lines"),
                "chunking.overlap_lines",
                config.chunk_overlap_lines,
            ),
        )
    except ValueError as exc:
        raise RepositoryIndexPolicyError(str(exc)) from exc

    enabled_extractors = _optional_string_tuple(
        extractors.get("enabled"),
        "extractors.enabled",
    )
    if enabled_extractors is not None and len(enabled_extractors) != len(
        set(enabled_extractors)
    ):
        raise RepositoryIndexPolicyError("extractors.enabled must not contain duplicates")

    return RepositoryIndexPolicy(
        config=resolved,
        enabled_extractors=enabled_extractors,
        source=policy_path,
        version=version,
    )


def _read_policy(path: Path) -> dict[str, object]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RepositoryIndexPolicyError(
            f"unable to read repository indexing policy: {path}"
        ) from exc
    if not isinstance(raw, dict):
        raise RepositoryIndexPolicyError("repository indexing policy must be a mapping")
    return {str(key): value for key, value in raw.items()}


def _mapping(value: object, context: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RepositoryIndexPolicyError(f"{context} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _reject_unknown_keys(
    value: Mapping[str, object],
    allowed: frozenset[str],
    *,
    context: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RepositoryIndexPolicyError(
            f"unsupported {context} key(s): " + ", ".join(unknown)
        )


def _optional_string_tuple(value: object, context: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _string_tuple(value, context)


def _string_tuple(value: object, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RepositoryIndexPolicyError(f"{context} must be a list of strings")
    rendered = tuple(item.strip() for item in value if item.strip())
    if len(rendered) != len(value):
        raise RepositoryIndexPolicyError(f"{context} must not contain empty values")
    return rendered


def _classification_rules(value: object, context: str) -> tuple[ClassificationRule, ...]:
    rules = _rule_mappings(
        value,
        context,
        allowed_keys=_CLASSIFICATION_RULE_KEYS,
    )
    return tuple(
        ClassificationRule(
            pattern=_required_string(item, "pattern", context),
            knowledge_type=_required_string(item, "knowledge_type", context),
        )
        for item in rules
    )


def _corpus_purpose_rules(value: object, context: str) -> tuple[CorpusPurposeRule, ...]:
    rules = _rule_mappings(value, context, allowed_keys=_CORPUS_RULE_KEYS)
    return tuple(
        CorpusPurposeRule(
            pattern=_required_string(item, "pattern", context),
            purpose=_required_enum(
                item,
                "value",
                context,
                CORPUS_PURPOSE_VALUES,
            ),
        )
        for item in rules
    )


def _source_origin_rules(value: object, context: str) -> tuple[SourceOriginRule, ...]:
    rules = _rule_mappings(value, context, allowed_keys=_CORPUS_RULE_KEYS)
    return tuple(
        SourceOriginRule(
            pattern=_required_string(item, "pattern", context),
            origin=_required_enum(
                item,
                "value",
                context,
                SOURCE_ORIGIN_VALUES,
            ),
        )
        for item in rules
    )


def _technical_role_rules(value: object, context: str) -> tuple[TechnicalRoleRule, ...]:
    rules = _rule_mappings(
        value,
        context,
        allowed_keys=_TECHNICAL_ROLE_RULE_KEYS,
    )
    return tuple(
        TechnicalRoleRule(
            token=_required_string(item, "token", context),
            role=_required_string(item, "role", context),
        )
        for item in rules
    )


def _rule_mappings(
    value: object,
    context: str,
    *,
    allowed_keys: frozenset[str],
) -> tuple[dict[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RepositoryIndexPolicyError(f"{context} must be a list")
    result: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise RepositoryIndexPolicyError(f"{context}[{index}] must be a mapping")
        mapped = {str(key): raw for key, raw in item.items()}
        _reject_unknown_keys(
            mapped,
            allowed_keys,
            context=f"{context}[{index}]",
        )
        result.append(mapped)
    return tuple(result)


def _required_string(value: Mapping[str, object], key: str, context: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise RepositoryIndexPolicyError(
            f"{context} entry requires non-empty {key!r}"
        )
    return raw.strip()


def _required_enum(
    value: Mapping[str, object],
    key: str,
    context: str,
    allowed: frozenset[str],
) -> str:
    rendered = _required_string(value, key, context)
    if rendered not in allowed:
        expected = ", ".join(sorted(allowed))
        raise RepositoryIndexPolicyError(
            f"{context} entry has unsupported {key!r} {rendered!r}; expected one of: {expected}"
        )
    return rendered


def _enum_string(
    value: object,
    context: str,
    allowed: frozenset[str],
    default: str,
) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise RepositoryIndexPolicyError(f"{context} must be a non-empty string")
    rendered = value.strip()
    if rendered not in allowed:
        expected = ", ".join(sorted(allowed))
        raise RepositoryIndexPolicyError(
            f"{context} has unsupported value {rendered!r}; expected one of: {expected}"
        )
    return rendered


def _positive_integer(value: object, context: str, default: int) -> int:
    if value is None:
        return default
    rendered = _integer(value, context)
    if rendered < 1:
        raise RepositoryIndexPolicyError(f"{context} must be positive")
    return rendered


def _non_negative_integer(value: object, context: str, default: int) -> int:
    if value is None:
        return default
    rendered = _integer(value, context)
    if rendered < 0:
        raise RepositoryIndexPolicyError(f"{context} must be non-negative")
    return rendered


def _integer(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepositoryIndexPolicyError(f"{context} must be an integer")
    return value


def _boolean(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise RepositoryIndexPolicyError(f"{context} must be a boolean")
    return value


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
