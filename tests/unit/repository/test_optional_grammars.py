"""Grammar adapters report only observed declarations."""

import pytest

from fetchcore.repository.extractors.languages.tree_sitter import TreeSitterStructuralExtractor
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.structure import default_structural_symbol_extractors


@pytest.mark.parametrize(
    ("language", "content", "qualified"),
    [
        ("rust", "mod demo { struct Foo {} }", "demo.Foo"),
        ("csharp", "namespace Demo { class Foo {} }", "Demo.Foo"),
        ("csharp", "namespace Demo;\nclass Foo { void Bar() {} }", "Demo.Foo.Bar"),
        ("kotlin", "package demo\nclass Foo { fun bar() {} }", "demo.Foo"),
        ("ruby", "module Demo\n class Foo\n end\nend", "Demo.Foo"),
        ("php", "<?php namespace Demo; class Foo {}", "Demo.Foo"),
        ("swift", "struct Foo { func bar() {} }", "Foo.bar"),
        ("scala", "package demo\nclass Foo { def bar(): Unit = {} }", "demo.Foo"),
    ],
)
def test_declarations(language: str, content: str, qualified: str) -> None:
    pytest.importorskip("tree_sitter_language_pack")
    observed = TreeSitterStructuralExtractor(language).extract_structure(content)
    assert qualified in {symbol.qualified_name for symbol in observed.symbols}


@pytest.mark.parametrize(("language", "content", "target"), [
    ("rust", "use std::fmt;", "std::fmt"),
    ("csharp", "using System.Text;", "System.Text"),
    ("kotlin", "import foo.Bar", "foo.Bar"),
    ("php", "<?php use Demo\\Foo;", "Demo\\Foo"),
    ("swift", "import Foundation", "Foundation"),
    ("scala", "import scala.util.Try", "scala.util.Try"),
])
def test_observed_imports(language: str, content: str, target: str) -> None:
    pytest.importorskip("tree_sitter_language_pack")
    extractor = TreeSitterStructuralExtractor(language)
    assert extractor.extract_structure(content).imports == (target,)
    assert extractor.structural_capabilities == ("declarations", "imports")


def test_rust_import_reaches_canonical_evidence(tmp_path) -> None:
    pytest.importorskip("tree_sitter_language_pack")
    (tmp_path / "lib.rs").write_text("use std::fmt;\nstruct Foo {}\n", encoding="utf-8")
    indexed = RepositoryIndexer(
        RepositoryIndexConfig(repository="sample/repo", revision="rev-a"),
        symbol_extractors=default_structural_symbol_extractors(),
    ).index(tmp_path)
    assert any(
        imported.source == "std::fmt"
        for document in indexed.documents
        for imported in document.evidence.structure.imports
    )
