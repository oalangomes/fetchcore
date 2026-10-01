"""Incremental safety seam for repository document knowledge."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from fetchcore.backends.solr_incremental import GitAwareSolrSynchronizer
from fetchcore.repository.incremental import GitDeltaPlan
from fetchcore.repository.indexing import KnowledgeDocument, RepositoryIndexResult


class DocumentAwareGitSolrSynchronizer(GitAwareSolrSynchronizer):
    """Fall back to a full canonical scan when document-source paths change."""

    def _index_delta(
        self,
        workspace: Path,
        previous: tuple[KnowledgeDocument, ...],
        plan: GitDeltaPlan,
    ) -> RepositoryIndexResult | None:
        raw_policy = getattr(self.indexer, "requires_full_scan_for_paths", None)
        if callable(raw_policy):
            requires_full_scan = cast(
                Callable[[Sequence[str], Sequence[str]], bool],
                raw_policy,
            )
            if requires_full_scan(plan.changed_paths, plan.removed_paths):
                return None
        return super()._index_delta(workspace, previous, plan)
