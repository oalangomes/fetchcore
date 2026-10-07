"""Deterministic exact-anchor classification and repository scope validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote, unquote

from .contracts import Anchor, KnowledgeSearchError, SearchIntent, SearchResult, SearchScope

TargetedOutcome = Literal["exact_unique", "exact_ambiguous", "not_found", "unsupported"]


@dataclass(frozen=True)
class TargetedCandidate:
    anchor: Anchor
    path: str


def targetable_anchors(intent: SearchIntent) -> tuple[Anchor, ...]:
    """Only explicit top-level path/source_ref anchors; no text inference."""
    return tuple(anchor for anchor in intent.anchors if anchor.kind in ("path", "source_ref"))


def validate_target(anchor: Anchor, scope: SearchScope) -> TargetedCandidate:
    if anchor.kind not in ("path", "source_ref"):
        raise KnowledgeSearchError("unsupported exact key", category="unsupported_capability")
    if not scope.repository:
        raise KnowledgeSearchError(
            "exact lookup requires repository binding", category="repository_scope_required"
        )
    path = anchor.value
    if anchor.kind == "source_ref":
        prefix = f"repository://{quote(scope.repository, safe='/.:@')}/"
        if not path.startswith(prefix):
            raise KnowledgeSearchError(
                "source_ref does not match repository binding", category="unauthorized_repository"
            )
        path = unquote(path[len(prefix) :])
        # File-level canonical references only; fragments are not silently stripped.
        if anchor.value != prefix + quote(path, safe="/"):
            raise KnowledgeSearchError(
                "source_ref is not a canonical file reference", category="unauthorized_source"
            )
    normalized = PurePosixPath(path)
    if (
        not normalized.parts
        or normalized.is_absolute()
        or ".." in normalized.parts
        or normalized.as_posix() != path
        or "\\" in path
        or "\0" in path
    ):
        raise KnowledgeSearchError(
            "anchor contains an invalid repository path", category="unauthorized_source"
        )
    root = scope.workspace.resolve()
    candidate = root / path
    try:
        candidate.resolve().relative_to(root)
    except (ValueError, OSError, RuntimeError) as exc:
        raise KnowledgeSearchError(
            "anchor escapes repository root", category="unauthorized_source"
        ) from exc
    current = root
    for part in normalized.parts:
        current /= part
        if current.is_symlink():
            raise KnowledgeSearchError(
                "exact lookup does not follow symlinks", category="unauthorized_source"
            )
    if scope.repository_paths and path not in scope.repository_paths:
        raise KnowledgeSearchError(
            "anchor is outside repository path scope", category="unauthorized_source"
        )
    return TargetedCandidate(anchor, path)


def matches_target(result: SearchResult, target: TargetedCandidate) -> bool:
    if target.anchor.kind == "source_ref":
        return result.source_ref == target.anchor.value
    return result.source == target.path
