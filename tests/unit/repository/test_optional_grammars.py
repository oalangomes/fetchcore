"""Grammar adapters report only observed declarations."""

import pytest

from fetchcore.repository.extractors.languages.tree_sitter import TreeSitterStructuralExtractor


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
