"""Git-aware incremental repository indexing with conservative full-scan fallback."""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from fetchcore.repository.indexing import (
    DocumentUpdate,
    KnowledgeDocument,
    RemovedKnowledgeDocument,
    RepositoryIndexChanges,
    RepositoryIndexTelemetry,
)
from fetchcore.repository.source_coverage import RepositorySourceCoverage


@dataclass(frozen=True)
class GitDeltaPlan:
    """A proven repository delta, or an explicit reason to use a full scan."""

    mode: str
    base_revision: str | None
    current_revision: str | None
    changed_paths: tuple[str, ...] = ()
    removed_paths: tuple[str, ...] = ()
    reason: str = "safe_git_delta"
    discovery_duration_ms: int | None = None
    git_diff_duration_ms: int | None = None

    @property
    def targeted_paths(self) -> int:
        """Return the number of unique repository paths targeted by this delta."""
        return len(set(self.changed_paths) | set(self.removed_paths))

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of the Git delta plan."""
        payload: dict[str, object] = {
            "mode": self.mode,
            "reason": self.reason,
            "base_revision": self.base_revision,
            "current_revision": self.current_revision,
            "changed_paths": len(self.changed_paths),
            "removed_paths": len(self.removed_paths),
            "targeted_paths": self.targeted_paths,
        }
        if self.discovery_duration_ms is not None:
            payload["discovery_duration_ms"] = self.discovery_duration_ms
        if self.git_diff_duration_ms is not None:
            payload["git_diff_duration_ms"] = self.git_diff_duration_ms
        return payload


class GitRepositoryChangeDetector:
    """Use Git history and working-tree state only when the delta can be proven safe."""

    def __init__(self, *, timer: Callable[[], float] = time.monotonic) -> None:
        self.timer = timer

    def plan(
        self,
        workspace: Path,
        *,
        previous_revision: str | None,
        current_revision: str | None,
    ) -> GitDeltaPlan:
        """Plan a safe repository delta from Git history and working-tree state."""
        started = self.timer()
        result = self._plan(workspace, previous_revision=previous_revision,
                            current_revision=current_revision)
        return replace(result, discovery_duration_ms=_elapsed_ms(started, self.timer()))

    def _plan(
        self,
        workspace: Path,
        *,
        previous_revision: str | None,
        current_revision: str | None,
    ) -> GitDeltaPlan:
        root = workspace.resolve(strict=True)
        if not root.is_dir():
            return _full_scan(previous_revision, current_revision, "workspace_not_directory")
        if not previous_revision:
            return _full_scan(previous_revision, current_revision, "previous_revision_missing")
        if not current_revision:
            return _full_scan(previous_revision, current_revision, "current_revision_missing")
        if previous_revision.startswith("tree:") or current_revision.startswith("tree:"):
            return _full_scan(previous_revision, current_revision, "non_git_revision")

        head = _git_text(root, ("rev-parse", "HEAD"))
        if head is None:
            return _full_scan(previous_revision, current_revision, "git_head_unavailable")
        if head != current_revision:
            return _full_scan(previous_revision, current_revision, "current_revision_not_head")
        if not _git_commit_exists(root, previous_revision):
            return _full_scan(previous_revision, current_revision, "previous_revision_unavailable")
        if not _git_commit_exists(root, current_revision):
            return _full_scan(previous_revision, current_revision, "current_revision_unavailable")
        if previous_revision != current_revision:
            ancestor_status = _git_returncode(
                root,
                ("merge-base", "--is-ancestor", previous_revision, current_revision),
            )
            if ancestor_status != 0:
                reason = "history_not_ancestor" if ancestor_status == 1 else "ancestor_check_failed"
                return _full_scan(previous_revision, current_revision, reason)

        changed: set[str] = set()
        removed: set[str] = set()
        git_diff_seconds = 0.0
        if previous_revision != current_revision:
            diff_started = self.timer()
            committed = _git_name_status(
                root,
                ("diff", "--name-status", "-z", "--find-renames", previous_revision, current_revision),
            )
            git_diff_seconds += max(0.0, self.timer() - diff_started)
            if committed is None:
                return _full_scan(previous_revision, current_revision, "commit_delta_unavailable")
            changed.update(committed[0])
            removed.update(committed[1])

        diff_started = self.timer()
        working = _git_name_status(
            root,
            ("diff", "--name-status", "-z", "--find-renames", current_revision),
        )
        git_diff_seconds += max(0.0, self.timer() - diff_started)
        if working is None:
            return _full_scan(previous_revision, current_revision, "working_tree_delta_unavailable")
        changed.update(working[0])
        removed.update(working[1])

        untracked_raw = _git_bytes(
            root,
            ("ls-files", "--others", "--exclude-standard", "-z"),
        )
        if untracked_raw is None:
            return _full_scan(previous_revision, current_revision, "untracked_delta_unavailable")
        untracked = _nul_paths(untracked_raw)
        if untracked is None:
            return _full_scan(previous_revision, current_revision, "untracked_path_invalid")
        changed.update(untracked)

        changed.difference_update(removed)
        if any(not _safe_relative_path(path) for path in changed | removed):
            return _full_scan(previous_revision, current_revision, "unsafe_delta_path")

        return GitDeltaPlan(
            mode="git-delta",
            base_revision=previous_revision,
            current_revision=current_revision,
            changed_paths=tuple(sorted(changed)),
            removed_paths=tuple(sorted(removed)),
            git_diff_duration_ms=max(0, round(git_diff_seconds * 1_000)),
        )


def _elapsed_ms(started: float, ended: float) -> int:
    return max(0, round((ended - started) * 1_000))


def _full_scan(
    previous_revision: str | None,
    current_revision: str | None,
    reason: str,
) -> GitDeltaPlan:
    return GitDeltaPlan(
        mode="full-scan",
        base_revision=previous_revision,
        current_revision=current_revision,
        reason=reason,
    )


def _git_commit_exists(workspace: Path, revision: str) -> bool:
    return _git_returncode(workspace, ("cat-file", "-e", f"{revision}^{{commit}}")) == 0


def _git_returncode(workspace: Path, args: Sequence[str]) -> int | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.returncode


def _git_text(workspace: Path, args: Sequence[str]) -> str | None:
    payload = _git_bytes(workspace, args)
    if payload is None:
        return None
    return payload.decode("utf-8", errors="replace").strip() or None


def _git_bytes(workspace: Path, args: Sequence[str]) -> bytes | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _git_name_status(
    workspace: Path,
    args: Sequence[str],
) -> tuple[set[str], set[str]] | None:
    payload = _git_bytes(workspace, args)
    if payload is None:
        return None
    tokens = [item for item in payload.split(b"\0") if item]
    changed: set[str] = set()
    removed: set[str] = set()
    index = 0
    while index < len(tokens):
        status = os.fsdecode(tokens[index])
        index += 1
        if not status:
            return None
        code = status[0]
        if code in {"R", "C"}:
            if index + 1 >= len(tokens):
                return None
            old_path = os.fsdecode(tokens[index])
            new_path = os.fsdecode(tokens[index + 1])
            index += 2
            if code == "R":
                removed.add(old_path)
            changed.add(new_path)
            continue
        if index >= len(tokens):
            return None
        path = os.fsdecode(tokens[index])
        index += 1
        if code == "D":
            removed.add(path)
        elif code in {"A", "M", "T"}:
            changed.add(path)
        else:
            return None
    return changed, removed


def _nul_paths(payload: bytes) -> tuple[str, ...] | None:
    paths = tuple(os.fsdecode(item) for item in payload.split(b"\0") if item)
    if any(not _safe_relative_path(path) for path in paths):
        return None
    return paths


def _safe_relative_path(path: str) -> bool:
    candidate = Path(path)
    return bool(path) and not candidate.is_absolute() and ".." not in candidate.parts


def _eligible_path(
    relative: str,
    includes: tuple[str, ...],
    excludes: tuple[str, ...],
) -> bool:
    parts = relative.split("/")
    if any(
        exclude in parts
        or fnmatch.fnmatch(relative, exclude)
        or fnmatch.fnmatch(relative, f"**/{exclude}/**")
        for exclude in excludes
    ):
        return False
    name = Path(relative).name
    return any(
        fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(relative, pattern)
        for pattern in includes
    )


def _logical_key(document: KnowledgeDocument) -> tuple[str, str, str]:
    discriminator = str(document.metadata.get("chunk_discriminator", document.chunk_ref))
    return document.repository, document.path, discriminator


def _canonical_payload(document: KnowledgeDocument) -> str:
    payload = document.to_mapping()
    for field in ("branch", "revision", "indexed_at"):
        payload.pop(field, None)
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        provenance.pop("revision", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _diff_documents(
    current: tuple[KnowledgeDocument, ...],
    previous: tuple[KnowledgeDocument, ...],
) -> RepositoryIndexChanges:
    previous_by_key = {_logical_key(item): item for item in previous}
    added: list[KnowledgeDocument] = []
    updated: list[DocumentUpdate] = []
    unchanged: list[KnowledgeDocument] = []
    matched_previous: set[str] = set()
    for document in current:
        prior = previous_by_key.get(_logical_key(document))
        if prior is None:
            added.append(document)
            continue
        matched_previous.add(prior.id)
        if _canonical_payload(prior) == _canonical_payload(document):
            unchanged.append(document)
        else:
            updated.append(DocumentUpdate(previous_document_id=prior.id, document=document))
    removed = tuple(
        RemovedKnowledgeDocument.from_document(item)
        for item in sorted(previous, key=lambda value: (value.path, value.chunk_ref))
        if item.id not in matched_previous
    )
    return RepositoryIndexChanges(
        added=tuple(added),
        updated=tuple(updated),
        removed=removed,
        unchanged=tuple(unchanged),
        removal_detection_complete=True,
    )


def _delta_telemetry(
    *,
    repository: str,
    revision: str,
    documents: tuple[KnowledgeDocument, ...],
    changes: RepositoryIndexChanges,
    delta: RepositoryIndexTelemetry | None,
) -> RepositoryIndexTelemetry:
    by_type = Counter(item.knowledge_type for item in documents)
    by_role = Counter(item.technical_role or "unknown" for item in documents)
    per_source = Counter(item.source_ref for item in documents)
    base = delta or RepositoryIndexTelemetry(
        repository=repository,
        revision=revision,
        files_discovered=0,
        files_evaluated=0,
        files_eligible=0,
        files_read=0,
        files_indexed=0,
        files_changed=0,
        files_skipped=0,
        files_unvisited_due_to_truncation=0,
        skip_reasons={},
        documents_produced=0,
        documents_by_knowledge_type={},
        documents_by_technical_role={},
        documents_per_source={},
        source_coverage=RepositorySourceCoverage(
            files_read=0,
            files_indexed=0,
            files_without_documents=0,
            implementation_files_read=0,
            implementation_files_indexed=0,
            implementation_files_structural_capability=0,
            implementation_files_textual_only=0,
            implementation_files_with_symbol_chunks=0,
            knowledge_files_read=0,
            by_language={},
            discovered_by_source_kind={},
            eligible_by_source_kind={},
            read_by_source_kind={},
            files_without_documents_by_source_kind={},
            not_included_by_source_kind={},
            excluded_by_source_kind={},
            unvisited_by_source_kind={},
        ),
        truncated_files=0,
        scan_truncated=False,
        scan_traversal_complete=True,
        truncation_reasons=(),
        added=0,
        updated=0,
        removed=0,
        unchanged=0,
        removal_detection_complete=True,
        removal_detection_blockers=(),
        duration_ms=0,
    )
    return replace(
        base,
        repository=repository,
        revision=revision,
        documents_produced=len(documents),
        documents_by_knowledge_type=dict(sorted(by_type.items())),
        documents_by_technical_role=dict(sorted(by_role.items())),
        documents_per_source=dict(sorted(per_source.items())),
        files_changed=len(
            {
                *(item.path for item in changes.added),
                *(item.document.path for item in changes.updated),
                *(item.path for item in changes.removed),
            }
        ),
        added=len(changes.added),
        updated=len(changes.updated),
        removed=len(changes.removed),
        unchanged=len(changes.unchanged),
        removal_detection_complete=True,
        removal_detection_blockers=(),
        scan_traversal_complete=True,
    )


def __getattr__(name: str) -> object:
    """Keep historical Solr integration imports without a core vendor dependency."""
    if name in {"RepositorySyncOutcome", "GitAwareSolrSynchronizer"}:
        from fetchcore.backends import solr_incremental

        return getattr(solr_incremental, name)
    raise AttributeError(name)
