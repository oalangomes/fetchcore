"""GraphProjection compiles only canonical, resolved relation observations."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from fetchcore.application.index_operations import prepare_repository_index_runtime
from fetchcore.knowledge.graph_projection import (
    GRAPH_PROJECTION_FINGERPRINT, GraphProjection, project_graph,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _fixture(root: Path):
    (root / "a.py").write_text(
        "from b import beta\ndef alpha():\n    beta()\n    beta()\n", encoding="utf-8",
    )
    (root / "b.py").write_text(
        "from c import gamma\ndef beta():\n    gamma()\n", encoding="utf-8",
    )
    (root / "c.py").write_text("def gamma():\n    return 1\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.name", "Graph Projection Test")
    _git(root, "config", "user.email", "graph@example.test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    runtime = prepare_repository_index_runtime(
        root, repository="owner/fixture", branch=None, revision=None,
    )
    return runtime.indexer.index(root), runtime.fingerprint


def test_resolved_observations_are_deterministic_and_parallel(tmp_path: Path) -> None:
    result, fingerprint = _fixture(tmp_path)
    first = project_graph(result.documents, generation="g1", indexing_fingerprint=fingerprint)
    reverse = project_graph(
        reversed(result.documents), generation="g1", indexing_fingerprint=fingerprint,
    )
    assert first.serialize() == reverse.serialize()
    assert GraphProjection.from_mapping(first.to_mapping()) == first
    calls = [edge for edge in first.edges if edge.relation_kind == "calls"]
    assert len(calls) == 3
    assert len({edge.id for edge in calls}) == 3
    assert len([edge for edge in calls if edge.source_ref.endswith("a.py")]) == 2
    assert all(edge.source_checksum and edge.target_checksum for edge in calls)
    assert all(edge.repository == "owner/fixture" and edge.revision == result.revision
               for edge in calls)
    assert first.fingerprint == GRAPH_PROJECTION_FINGERPRINT

    duplicated = tuple(result.documents) + (result.documents[0],)
    with pytest.raises(ValueError, match="duplicate document IDs"):
        project_graph(duplicated, generation="g1", indexing_fingerprint=fingerprint)


def test_unresolved_and_missing_target_never_become_edges(tmp_path: Path) -> None:
    result, fingerprint = _fixture(tmp_path)
    baseline = project_graph(result.documents, generation="g1", indexing_fingerprint=fingerprint)
    assert all(edge.target_identity for edge in baseline.edges)
    removed = tuple(doc for doc in result.documents if doc.path != "c.py")
    projection = project_graph(removed, generation="g1", indexing_fingerprint=fingerprint)
    assert not any(edge.target_path == "c.py" for edge in projection.edges)
    assert len(projection.edges) < len(baseline.edges)

    changed = baseline.to_mapping()
    changed["fingerprint"] = "sha256:incompatible"
    with pytest.raises(ValueError, match="incompatible"):
        GraphProjection.from_mapping(changed)


def test_duplicate_observation_does_not_erase_distinct_lines(tmp_path: Path) -> None:
    result, fingerprint = _fixture(tmp_path)
    baseline = project_graph(result.documents, generation="g1", indexing_fingerprint=fingerprint)
    doc = next(doc for doc in result.documents if doc.path == "a.py")
    semantic = doc.evidence.relations.semantic
    assert semantic
    repeated = replace(
        doc,
        evidence=replace(doc.evidence, relations=replace(
            doc.evidence.relations, semantic=semantic + (semantic[0],),
        )),
    )
    documents = tuple(repeated if item.id == doc.id else item for item in result.documents)
    projected = project_graph(documents, generation="g1", indexing_fingerprint=fingerprint)
    assert projected.edges == baseline.edges


def test_source_shift_revision_and_repository_binding(tmp_path: Path) -> None:
    result, fingerprint = _fixture(tmp_path)
    before = project_graph(result.documents, generation="g1", indexing_fingerprint=fingerprint)
    source_before = [edge for edge in before.edges if edge.source_ref.endswith("a.py")
                     and edge.relation_kind == "calls"]
    assert {edge.line for edge in source_before} == {3, 4}

    path = tmp_path / "a.py"
    path.write_text("# shifted\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "shift source")
    runtime = prepare_repository_index_runtime(
        tmp_path, repository="owner/fixture", branch=None, revision=None,
    )
    after_result = runtime.indexer.index(tmp_path)
    after = project_graph(after_result.documents, generation="g2",
                          indexing_fingerprint=runtime.fingerprint)
    source_after = [edge for edge in after.edges if edge.source_ref.endswith("a.py")
                    and edge.relation_kind == "calls"]
    assert {edge.line for edge in source_after} == {4, 5}
    assert {edge.source_identity for edge in source_before} == {
        edge.source_identity for edge in source_after
    }
    assert {edge.id for edge in source_before}.isdisjoint(edge.id for edge in source_after)
    assert before.revision != after.revision

    other_runtime = prepare_repository_index_runtime(
        tmp_path, repository="owner/other", branch=None, revision=None,
    )
    other_result = other_runtime.indexer.index(tmp_path)
    other = project_graph(other_result.documents, generation="g1",
                          indexing_fingerprint=other_runtime.fingerprint)
    assert {edge.source_identity for edge in after.edges}.isdisjoint(
        edge.source_identity for edge in other.edges
    )


def test_cycle_and_homonym_keep_exact_identities(tmp_path: Path) -> None:
    _fixture(tmp_path)
    (tmp_path / "c.py").write_text(
        "from a import alpha\ndef gamma():\n    alpha()\n", encoding="utf-8",
    )
    (tmp_path / "other.py").write_text(
        "def beta():\n    pass\n", encoding="utf-8",
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "cycle and homonym")
    runtime = prepare_repository_index_runtime(
        tmp_path, repository="owner/fixture", branch=None, revision=None,
    )
    result = runtime.indexer.index(tmp_path)
    graph = project_graph(result.documents, generation="g1",
                          indexing_fingerprint=runtime.fingerprint)
    calls = [edge for edge in graph.edges if edge.relation_kind == "calls"]
    assert len(calls) == 4
    alpha = next(edge.source_identity for edge in calls if edge.source_ref.endswith("a.py"))
    assert any(edge.target_identity == alpha for edge in calls)
    assert all(edge.target_path != "other.py" for edge in calls)


def test_target_rename_invalidates_old_relation(tmp_path: Path) -> None:
    _fixture(tmp_path)
    (tmp_path / "b.py").rename(tmp_path / "renamed.py")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "rename target without changing import")
    runtime = prepare_repository_index_runtime(
        tmp_path, repository="owner/fixture", branch=None, revision=None,
    )
    result = runtime.indexer.index(tmp_path)
    projection = project_graph(result.documents, generation="g2",
                               indexing_fingerprint=runtime.fingerprint)
    assert not any(edge.relation_kind == "calls" and edge.source_ref.endswith("a.py")
                   for edge in projection.edges)
