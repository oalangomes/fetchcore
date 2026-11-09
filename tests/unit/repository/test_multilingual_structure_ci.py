"""Required multilingual structural contracts for the dedicated CI job."""

from pathlib import Path

import pytest

from fetchcore.repository.extractors.registry import default_repository_extractors
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.navigation import find_symbol


@pytest.mark.parametrize(("language", "suffix", "content", "name", "qualified"), [
    ("python", ".py", "def ping():\n    pass\n", "ping", "ping"),
    ("javascript", ".js", "export function ping() {}\n", "ping", "ping"),
    ("typescript", ".ts", "export function ping(): void {}\n", "ping", "ping"),
    ("java", ".java", "package demo;\nclass Client { void ping() {} }\n", "Client", "demo.Client"),
    ("groovy", ".groovy", "package demo\nclass Client { void ping() {} }\n", "Client", "demo.Client"),
    ("dart", ".dart", "class Client { void ping() {} }\n", "Client", "Client"),
    ("go", ".go", "package demo\nfunc ping() {}\n", "ping", "demo.ping"),
    ("rust", ".rs", "fn ping() {}\n", "ping", "ping"),
    ("csharp", ".cs", "namespace Demo;\nclass Client { void ping() {} }\n", "ping", "Demo.Client.ping"),
    ("kotlin", ".kt", "package demo\nfun ping() {}\n", "ping", "demo.ping"),
    ("ruby", ".rb", "module Demo\n def ping; end\nend\n", "ping", "Demo.ping"),
    ("php", ".php", "<?php namespace Demo; function ping() {}\n", "ping", "Demo.ping"),
    ("swift", ".swift", "func ping() {}\n", "ping", "ping"),
    ("scala", ".scala", "package demo\ndef ping(): Unit = {}\n", "ping", "demo.ping"),
])
def test_two_files_preserve_exact_homonyms(
    tmp_path: Path, language: str, suffix: str, content: str,
    name: str, qualified: str,
) -> None:
    extractors = default_repository_extractors()
    assert language in extractors, f"required extractor unavailable: {language}"
    for directory in ("first", "second"):
        target = tmp_path / directory
        target.mkdir()
        (target / f"client{suffix}").write_text(content, encoding="utf-8")

    indexed = RepositoryIndexer(
        RepositoryIndexConfig(repository="sample/multilingual", revision="rev-a"),
        symbol_extractors=extractors,
    ).index(tmp_path)
    found = find_symbol(indexed.documents, name, language=language)
    assert len(found) == 2
    assert {item.path for item in found} == {
        f"first/client{suffix}", f"second/client{suffix}",
    }
    assert all(item.qualified_name.endswith(qualified) for item in found)
    assert all(item.revision == "rev-a" for item in found)
