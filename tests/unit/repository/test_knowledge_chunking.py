from __future__ import annotations

from fetchcore.repository.evidence import extract_source_structure
from fetchcore.repository.structure import SourceStructure, StructuralSymbolSpan


def test_overlapping_rich_chunk_spans_fall_back_to_safe_chunking() -> None:
    class UnsafeRichExtractor:
        def extract_structure(
            self,
            content: str,
            *,
            path: str | None = None,
        ) -> SourceStructure:
            assert content
            assert path == "unsafe.go"
            symbols = (
                StructuralSymbolSpan("first", "class", 1, 3),
                StructuralSymbolSpan("second", "class", 2, 4),
            )
            return SourceStructure(
                language="go",
                symbols=symbols,
                chunk_symbols=symbols,
            )

    structure = extract_source_structure(
        UnsafeRichExtractor(),
        "one\ntwo\nthree\nfour\n",
        path="unsafe.go",
    )

    assert structure is not None
    assert tuple(item.name for item in structure.symbols) == ("first", "second")
    assert structure.chunk_symbols == ()
