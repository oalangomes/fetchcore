"""Explicit composition point for built-in source extractors.

Callers may replace or extend this mapping before passing it to the canonical
RepositoryIndexer. No extractor knows a retrieval backend.
"""

from __future__ import annotations

from fetchcore.repository.indexing import SymbolExtractor
from fetchcore.repository.structure import default_structural_symbol_extractors

from .languages.html import HtmlStructuralExtractor
from .languages.shell import ShellStructuralExtractor


def default_repository_extractors() -> dict[str, SymbolExtractor]:
    """Return a fresh registry so callers can customize it independently."""

    return {
        **default_structural_symbol_extractors(),
        "shell": ShellStructuralExtractor(),
        "html": HtmlStructuralExtractor(),
    }
