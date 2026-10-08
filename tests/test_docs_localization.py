"""Contracts of the repository-only documentation localization pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.docs import localize
from scripts.docs.localize import (
    FakeTranslator,
    LocalizationError,
    check_generated,
    classify,
    render,
    sha,
    sync,
    target_for,
)


class CountingTranslator(FakeTranslator):
    def __init__(self) -> None:
        self.calls = 0

    def translate(self, lines: list[str], glossary: dict[str, object]) -> list[str]:
        self.calls += 1
        return super().translate(lines, glossary)


class FailingTranslator(FakeTranslator):
    def translate(self, lines: list[str], glossary: dict[str, object]) -> list[str]:
        raise RuntimeError("provider failed")


class BrokenTranslator(FakeTranslator):
    def translate(self, lines: list[str], glossary: dict[str, object]) -> list[str]:
        return [line.replace("FC_TOKEN_0000", "missing", 1) for line in lines]


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    (tmp_path / "docs/i18n").mkdir(parents=True)
    (tmp_path / "docs/architecture").mkdir()
    (tmp_path / "docs/history").mkdir()
    (tmp_path / "docs/i18n/glossary.pt-BR.yaml").write_text(
        "locale: pt-BR\npreserve:\n  - SearchIntent\nterms:\n  evidence: evidência\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "# Home\n\n[Architecture](docs/architecture/overview.md#runtime-configuration)\n",
        encoding="utf-8",
    )
    (tmp_path / "docs/README.md").write_text(
        "# Docs\n\n[Architecture](architecture/overview.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "docs/architecture/overview.md").write_text(
        "# Overview\n\n## Runtime configuration\n\nFetchCore uses `SearchIntent` and "
        "FETCHCORE_CONFIG. See [history](../history/old.md).\n\n"
        "```bash\nfetchcore search --repo owner/repo\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "docs/history/old.md").write_text("# Old\n", encoding="utf-8")
    return tmp_path


def test_classification_and_path_mapping() -> None:
    assert classify("docs/architecture/overview.md") == "TRANSLATE"
    assert classify("docs/history/extraction/old.md") == "HISTORICAL"
    assert classify("docs/pt-BR/README.md") == "GENERATED"
    assert classify("docs/benchmarks/protocol.md") == "TRANSLATE"
    assert classify("docs/benchmarks/results/run.md") == "HISTORICAL"
    assert classify("docs/releases/initial-release.md") == "RELEASE_SPECIFIC"
    assert target_for("README.md") == "README.pt-BR.md"
    assert target_for("docs/guides/searching.md") == "docs/pt-BR/guides/searching.md"


def test_incremental_links_anchors_and_protected_content(repository: Path) -> None:
    translator = CountingTranslator()
    first = sync(repository, translator)
    assert len(first["translated"]) == 3
    first_calls = translator.calls
    assert first_calls > 0
    assert sync(repository, translator)["translated"] == []
    assert translator.calls == first_calls
    home = (repository / "README.pt-BR.md").read_text()
    overview = (repository / "docs/pt-BR/architecture/overview.md").read_text()
    assert "docs/pt-BR/architecture/overview.md#runtime-configuration" in home
    assert '<a id="runtime-configuration"></a>' in overview
    assert "../../history/old.md" in overview
    assert "`SearchIntent`" in overview
    assert "FETCHCORE_CONFIG" in overview
    assert "```bash\nfetchcore search --repo owner/repo\n```" in overview
    assert check_generated(repository, strict=True)["valid"] == 3

    source = repository / "docs/architecture/overview.md"
    source.write_text(source.read_text() + "\nChanged prose.\n", encoding="utf-8")
    assert check_generated(repository)["stale"] == 1
    assert len(sync(repository, translator)["translated"]) == 1
    glossary = repository / "docs/i18n/glossary.pt-BR.yaml"
    glossary.write_text(glossary.read_text().replace("terms:\n", "  - RRF\nterms:\n"),
                        encoding="utf-8")
    assert len(sync(repository, translator)["translated"]) == 3
    assert len(sync(repository, translator, force=True)["translated"]) == 3


def test_deleted_source_removes_generated_file(repository: Path) -> None:
    sync(repository, FakeTranslator())
    (repository / "docs/architecture/overview.md").unlink()
    report = sync(repository, FakeTranslator())
    assert report["removed"] == ["docs/pt-BR/architecture/overview.md"]
    assert not (repository / "docs/pt-BR/architecture/overview.md").exists()


def test_validation_failure_makes_no_file_changes(repository: Path) -> None:
    before = sorted(repository.rglob("*.md"))
    with pytest.raises(LocalizationError, match="placeholder"):
        sync(repository, BrokenTranslator())
    assert sorted(repository.rglob("*.md")) == before


def test_provider_failure_makes_no_file_changes(repository: Path) -> None:
    before = {p: sha(p.read_bytes()) for p in repository.rglob("*.md")}
    with pytest.raises(RuntimeError, match="provider failed"):
        sync(repository, FailingTranslator())
    assert {p: sha(p.read_bytes()) for p in repository.rglob("*.md")} == before


def test_corrupt_provenance_fails_closed(repository: Path) -> None:
    sync(repository, FakeTranslator())
    target = repository / "README.pt-BR.md"
    target.write_text(target.read_text() + "unexpected edit", encoding="utf-8")
    with pytest.raises(LocalizationError, match="modified body"):
        check_generated(repository)
    with pytest.raises(LocalizationError, match="modified body"):
        sync(repository, FakeTranslator())


def test_schema_change_regenerates(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sync(repository, FakeTranslator())
    monkeypatch.setattr(localize, "SCHEMA", 2)
    assert check_generated(repository)["stale"] == 3
    assert len(sync(repository, FakeTranslator())["translated"]) == 3
    assert check_generated(repository, strict=True)["stale"] == 0


def test_broken_link_and_markdown_fail_closed(repository: Path) -> None:
    source = "# Topic\n\n[missing](does-not-exist.md)\n"
    with pytest.raises(LocalizationError, match="broken source link"):
        render(source, "docs/README.md", "docs/pt-BR/README.md", {"docs/README.md"},
               FakeTranslator(), {"preserve": []}, repository)
    with pytest.raises(LocalizationError, match="unclosed Markdown fence"):
        render("# Topic\n```\n", "docs/README.md", "docs/pt-BR/README.md",
               {"docs/README.md"}, FakeTranslator(), {"preserve": []}, repository)
