"""Exact, source-observed local reference resolution and negative joins."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from fetchcore.projection import ProjectionV3CanonicalDocumentReader, ProjectionV3Compiler
from fetchcore.repository.api_contracts import ApiEvidenceV1
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import RepositoryIndexConfig
from fetchcore.repository.local_references import (
    LocalReferenceBudget,
    LocalReferenceResolver,
    LocalResolutionCoverageV1,
)
from fetchcore.repository.local_references import LocalResolutionObservationV1
from fetchcore.repository.postman_indexing import ApiContractRepositoryIndexer


def _spec(route: str = "/customers/{id}", reference: str = "#/components/schemas/Customer",
          *, schema: bool = True) -> str:
    value: dict[str, object] = {
        "openapi": "3.0.0",
        "paths": {route: {"get": {"responses": {"200": {"content": {
            "application/json": {"schema": {"$ref": reference}}
        }}}}}},
    }
    if schema:
        value["components"] = {"schemas": {"Customer": {"type": "object"}}}
    return json.dumps(value)


def _index(path: Path, *, revision: str = "r1", previous=()):
    indexer = ApiContractRepositoryIndexer(
        RepositoryIndexConfig(repository="fixture/s5", revision=revision),
        resolve_local_references=True,
    )
    return indexer.index(path, previous_documents=previous)


def _observations(result, kind: str):
    return [
        (document, item.payload())
        for document in result.documents
        for item in document.evidence.semantic
        if item.schema_id == "local_reference/resolution@2"
        and item.payload()["kind"] == kind
    ]


def test_api_pointer_route_projection_and_reindex_removal(tmp_path: Path) -> None:
    (tmp_path / "api.json").write_text(_spec(), encoding="utf-8")
    (tmp_path / "app.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n'
        '@app.get("/customers/{id}")\ndef get_customer(id: str):\n    pass\n',
        encoding="utf-8",
    )
    first = _index(tmp_path)
    api = _observations(first, "api_reference")
    route = _observations(first, "route_correspondence")
    assert len(api) == len(route) == 1
    assert api[0][1]["status"] == route[0][1]["status"] == "resolved"
    assert api[0][1]["target_document_id"]
    assert route[0][1]["target_path"] == "app.py"
    assert route[0][1]["target_scope"] == "document"
    assert route[0][1]["target_revision"] == "r1"
    assert route[0][1]["target_source_ref"]
    assert route[0][1]["target_checksum"]
    assert route[0][1]["target_locator"] == "3"
    assert route[0][1]["evidence_basis"] == "observed_method_and_path"
    assert {item.kind for item in route[0][0].evidence.relations.semantic} == {
        "route_correspondence",
    }
    projected = ProjectionV3Compiler().compile(api[0][0]).to_mapping()
    hydrated = ProjectionV3CanonicalDocumentReader().read(projected)
    assert hydrated.evidence.relations.semantic == api[0][0].evidence.relations.semantic

    (tmp_path / "app.py").unlink()
    (tmp_path / "api.json").write_text(_spec(schema=False), encoding="utf-8")
    second = _index(tmp_path, previous=first.documents)
    assert _observations(second, "api_reference")[0][1]["status"] == "unresolved"
    assert _observations(second, "route_correspondence")[0][1]["status"] == "unresolved"
    assert not any(doc.evidence.relations.semantic for doc in second.documents)
    assert second.changes.removed and second.changes.updated
    third = _index(tmp_path, previous=second.documents)
    assert not third.changes.added and not third.changes.updated and not third.changes.removed


def test_relative_pointer_escaping_and_safe_quarantine(tmp_path: Path) -> None:
    (tmp_path / "specs").mkdir()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "specs" / "api.json").write_text(
        _spec(reference="../schemas/models.json#/components/schemas/Customer~1V2", schema=False),
        encoding="utf-8",
    )
    (tmp_path / "schemas" / "models.json").write_text(json.dumps({
        "openapi": "3.0.0", "paths": {},
        "components": {"schemas": {"Customer/V2": {"type": "object"}}},
    }), encoding="utf-8")
    result = _index(tmp_path)
    reference = next(
        ApiEvidenceV1.from_envelope(item)
        for doc in result.documents for item in doc.evidence.semantic
        if item.schema_id == "api_contract/api_reference@1"
    )
    assert reference.target == "schemas/models.json#/components/schemas/Customer~1V2"
    assert _observations(result, "api_reference")[0][1]["status"] == "resolved"

    (tmp_path / "specs" / "api.json").write_text(
        _spec(reference="../../outside.json#/components/schemas/Customer", schema=False),
        encoding="utf-8",
    )
    blocked = _index(tmp_path)
    assert not _observations(blocked, "api_reference")
    assert all("outside.json" not in json.dumps(doc.to_mapping()) for doc in blocked.documents)


def test_markdown_file_fragment_external_and_traversal(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "target.md").write_text("# Retrieval\n\nEvidence.\n", encoding="utf-8")
    (tmp_path / "docs" / "source.md").write_text(
        "# Source\n\n[file](./target.md)\n", encoding="utf-8",
    )
    initial = _index(tmp_path)
    target = next(doc for doc in initial.documents if doc.path == "docs/target.md")
    fragment = target.evidence.section.fragment_id
    assert fragment
    (tmp_path / "docs" / "source.md").write_text(
        "# Source\n\n"
        f"[section](./target.md#{fragment})\n"
        "[missing](./target.md#not-a-fetchcore-id)\n"
        "[remote](https://example.org/docs)\n"
        "[escape](../../outside.md)\n",
        encoding="utf-8",
    )
    result = _index(tmp_path)
    by_target = {item[1]["declaration"]: item[1] for item in _observations(
        result, "markdown_reference",
    )}
    assert by_target[f"./target.md#{fragment}"]["status"] == "resolved"
    assert by_target["./target.md#not-a-fetchcore-id"]["status"] == "unresolved"
    assert by_target["https://example.org/docs"]["status"] == "blocked"
    assert by_target["../../outside.md"]["status"] == "blocked"
    file_observation = _observations(initial, "markdown_reference")[0][1]
    assert file_observation["target_scope"] == "file"
    assert file_observation["target_document_id"] is None
    assert file_observation["target_path"] == "docs/target.md"


def test_duplicate_routes_budget_and_revision_are_inconclusive(tmp_path: Path) -> None:
    (tmp_path / "api.json").write_text(_spec(), encoding="utf-8")
    for name in ("one.py", "two.py"):
        (tmp_path / name).write_text(
            'from fastapi import FastAPI\napp = FastAPI()\n'
            '@app.get("/customers/{id}")\ndef handler(id: str):\n    pass\n',
            encoding="utf-8",
        )
    result = _index(tmp_path)
    observation = _observations(result, "route_correspondence")[0][1]
    assert observation["status"] == "ambiguous"
    assert observation["candidate_count"] == 2
    assert not _observations(result, "route_correspondence")[0][0].evidence.relations.semantic

    source_only = tuple(replace(doc, evidence=replace(
        doc.evidence,
        semantic=tuple(item for item in doc.evidence.semantic
                       if item.schema_id not in {"local_reference/resolution@2",
                                                 "local_reference/coverage@1"}),
        relations=replace(doc.evidence.relations, semantic=()),
    )) for doc in result.documents)
    bounded = LocalReferenceResolver(LocalReferenceBudget(max_candidates=1)).resolve(source_only)
    assert _observations(replace(result, documents=bounded), "route_correspondence")[0][1][
        "status"
    ] == "truncated"

    changed_revision = _index(tmp_path, revision="r2")
    assert all(doc.revision == "r2" for doc in changed_revision.documents)
    assert _observations(changed_revision, "route_correspondence")[0][1]["status"] == "ambiguous"
    assert indexing_contract_fingerprint(ApiContractRepositoryIndexer(
        RepositoryIndexConfig(repository="fixture/s5"), resolve_local_references=True,
    )) != indexing_contract_fingerprint(ApiContractRepositoryIndexer(
        RepositoryIndexConfig(repository="fixture/s5"), resolve_local_references=False,
    ))


def test_swagger_pointer_and_markdown_cycle_stay_bounded(tmp_path: Path) -> None:
    (tmp_path / "api.json").write_text(json.dumps({
        "swagger": "2.0", "paths": {"/x": {"get": {"responses": {
            "200": {"schema": {"$ref": "#/definitions/Customer"}},
        }}}}, "definitions": {"Customer": {"type": "object"}},
    }), encoding="utf-8")
    (tmp_path / "a.md").write_text("# A\n\n[B](./b.md)\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# B\n\n[A](./a.md)\n", encoding="utf-8")
    result = _index(tmp_path)
    assert _observations(result, "api_reference")[0][1]["status"] == "resolved"
    assert len(_observations(result, "markdown_reference")) == 2
    assert all(item["status"] == "resolved"
               for _, item in _observations(result, "markdown_reference"))


def test_no_cross_repository_or_revision_join_and_wrong_method(tmp_path: Path) -> None:
    contract = tmp_path / "contract"
    code = tmp_path / "code"
    contract.mkdir()
    code.mkdir()
    (contract / "api.json").write_text(_spec(), encoding="utf-8")
    (code / "app.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n'
        '@app.post("/customers/{id}")\ndef get_customer(id: str):\n    pass\n',
        encoding="utf-8",
    )
    api_docs = ApiContractRepositoryIndexer(
        RepositoryIndexConfig(repository="a", revision="r1"), structured_python=True,
    ).index(contract).documents
    code_docs = ApiContractRepositoryIndexer(
        RepositoryIndexConfig(repository="b", revision="r1"), structured_python=True,
    ).index(code).documents
    mixed = LocalReferenceResolver().resolve(api_docs + code_docs)
    assert not any(relation.kind == "route_correspondence"
                   for doc in mixed for relation in doc.evidence.relations.semantic)

    (code / "app.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n'
        '@app.get("/customers/{id}")\ndef get_customer(id: str):\n    pass\n',
        encoding="utf-8",
    )
    revised_code = ApiContractRepositoryIndexer(
        RepositoryIndexConfig(repository="a", revision="r2"), structured_python=True,
    ).index(code).documents
    mixed_revisions = LocalReferenceResolver().resolve(api_docs + revised_code)
    assert not any(relation.kind == "route_correspondence"
                   for doc in mixed_revisions for relation in doc.evidence.relations.semantic)


def test_resolution_schema_rejects_incompatible_or_false_target() -> None:
    observation = LocalResolutionObservationV1(
        kind="api_reference", declaration="#/definitions/Customer",
        source_locator="#/paths/~1x/get/responses/200/schema/$ref",
        status="unresolved", evidence_basis="exact_api_pointer", candidate_count=0,
    )
    assert LocalResolutionObservationV1.from_envelope(observation.to_envelope()) == observation
    payload = observation.to_envelope().payload()
    payload["unexpected"] = True
    from fetchcore.repository.semantic import SemanticEvidenceEnvelope
    malformed = SemanticEvidenceEnvelope.from_payload(
        namespace="local_reference", kind="resolution", version="2", payload=payload,
    )
    import pytest
    with pytest.raises(ValueError, match="missing or unknown"):
        LocalResolutionObservationV1.from_envelope(malformed)
    with pytest.raises(ValueError, match="inconclusive"):
        replace(observation, target_document_id="fake")


def test_split_section_uses_one_section_identity(tmp_path: Path) -> None:
    (tmp_path / "target.md").write_text(
        "# Long section\n\n" + "evidence line\n" * 320,
        encoding="utf-8",
    )
    first = _index(tmp_path)
    target_docs = [doc for doc in first.documents if doc.path == "target.md"]
    assert len(target_docs) > 1
    fragment = target_docs[0].evidence.section.fragment_id
    (tmp_path / "source.md").write_text(
        f"# Source\n\n[section](./target.md#{fragment})\n",
        encoding="utf-8",
    )
    result = _index(tmp_path)
    observation = _observations(result, "markdown_reference")[0][1]
    assert observation["status"] == "resolved"
    assert observation["candidate_count"] == len(target_docs)
    assert observation["target_document_id"] == target_docs[0].id


def test_sanitized_api_reference_cannot_carry_secret(tmp_path: Path) -> None:
    sentinel = "secret-token-super-private-000"
    (tmp_path / "api.json").write_text(
        _spec(reference=f"https://example.org/{sentinel}", schema=False),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "# Links\n\n[external](https://example.org/docs)\n",
        encoding="utf-8",
    )
    result = _index(tmp_path)
    assert sentinel not in json.dumps([doc.to_mapping() for doc in result.documents])
    assert not _observations(result, "api_reference")
    assert _observations(result, "markdown_reference")[0][1]["status"] == "blocked"


def test_added_renamed_and_changed_targets_refresh_canonical_relations(tmp_path: Path) -> None:
    (tmp_path / "api.json").write_text(_spec(schema=False), encoding="utf-8")
    (tmp_path / "source.md").write_text("# Links\n\n[go](./first.md)\n", encoding="utf-8")
    first = _index(tmp_path)
    assert _observations(first, "api_reference")[0][1]["status"] == "unresolved"
    assert _observations(first, "markdown_reference")[0][1]["status"] == "unresolved"

    (tmp_path / "api.json").write_text(_spec(), encoding="utf-8")
    (tmp_path / "first.md").write_text("# First\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n'
        '@app.get("/customers/{id}")\ndef handler(id: str):\n    pass\n',
        encoding="utf-8",
    )
    second = _index(tmp_path, previous=first.documents)
    assert all(_observations(second, kind)[0][1]["status"] == "resolved" for kind in (
        "api_reference", "markdown_reference", "route_correspondence",
    ))

    (tmp_path / "first.md").rename(tmp_path / "second.md")
    (tmp_path / "app.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n'
        '@app.get("/customers")\ndef handler():\n    pass\n',
        encoding="utf-8",
    )
    third = _index(tmp_path, previous=second.documents)
    assert _observations(third, "markdown_reference")[0][1]["status"] == "unresolved"
    assert _observations(third, "route_correspondence")[0][1]["status"] == "unresolved"
    assert third.changes.removed and third.changes.updated

    (tmp_path / "source.md").write_text("# Links\n\n[go](./second.md)\n", encoding="utf-8")
    fourth = _index(tmp_path, previous=third.documents)
    assert _observations(fourth, "markdown_reference")[0][1]["status"] == "resolved"


def test_global_reference_budget_reports_all_omissions(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A\n\n[x](./b.md) [y](./c.md) [z](./d.md)\n", encoding="utf-8")
    for name in ("b.md", "c.md", "d.md"):
        (tmp_path / name).write_text(f"# {name}\n", encoding="utf-8")
    source = ApiContractRepositoryIndexer(
        RepositoryIndexConfig(repository="fixture/s5", revision="r1"),
    ).index(tmp_path).documents
    bounded = LocalReferenceResolver(LocalReferenceBudget(max_references=1)).resolve(source)
    observations = [item for doc in bounded for item in doc.evidence.semantic
                    if item.schema_id == "local_reference/resolution@2"]
    summaries = [item.payload() for doc in bounded for item in doc.evidence.semantic
                 if item.schema_id == "local_reference/coverage@1"]
    assert len(observations) == 1
    assert summaries == [{"examined": 1, "resolved": 1, "inconclusive": 0,
                          "omitted_by_budget": 2, "resolution_complete": False,
                          "scope": "corpus"}]
    assert sum(len(doc.evidence.relations.semantic) for doc in bounded) == 1
    assert bounded == LocalReferenceResolver(LocalReferenceBudget(max_references=1)).resolve(source)
    reversed_result = LocalReferenceResolver(LocalReferenceBudget(max_references=1)).resolve(
        tuple(reversed(source)),
    )
    assert {doc.id: doc.evidence for doc in bounded} == {
        doc.id: doc.evidence for doc in reversed_result
    }


def test_unrelated_fastapi_receiver_never_joins_contract(tmp_path: Path) -> None:
    (tmp_path / "api.json").write_text(_spec(schema=False), encoding="utf-8")
    (tmp_path / "app.py").write_text(
        'from fastapi import FastAPI\nclass Unrelated:\n'
        '    def get(self, path):\n        return lambda fn: fn\n'
        'app = Unrelated()\n@app.get("/customers/{id}")\n'
        'def not_an_api_handler(id: str):\n    pass\n', encoding="utf-8",
    )
    result = _index(tmp_path)
    route = _observations(result, "route_correspondence")[0]
    assert route[1]["status"] == "unsupported"
    assert route[1]["evidence_basis"] == "unproven_framework_receiver"
    assert not route[0].evidence.relations.semantic


def test_budget_exact_and_above_corpus_are_complete(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A\n\n[b](./b.md) [c](./c.md)\n", encoding="utf-8")
    for name in ("b.md", "c.md"):
        (tmp_path / name).write_text(f"# {name}\n", encoding="utf-8")
    source = ApiContractRepositoryIndexer(RepositoryIndexConfig(
        repository="fixture/s5", revision="r1",
    )).index(tmp_path).documents
    for limit in (2, 10):
        resolved = LocalReferenceResolver(LocalReferenceBudget(max_references=limit)).resolve(
            source,
        )
        coverage = [LocalResolutionCoverageV1.from_envelope(item)
                    for doc in resolved for item in doc.evidence.semantic
                    if item.schema_id == "local_reference/coverage@1"]
        assert len(coverage) == 1
        assert coverage[0] == LocalResolutionCoverageV1(2, 2, 0, 0, True)


def test_mixed_documents_budget_stops_in_middle_and_round_trips(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A\n\n[b](./b.md) [c](./c.md)\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# B\n\n[a](./a.md)\n", encoding="utf-8")
    (tmp_path / "c.md").write_text("# C\n", encoding="utf-8")
    (tmp_path / "api.json").write_text(_spec(), encoding="utf-8")
    source = ApiContractRepositoryIndexer(RepositoryIndexConfig(
        repository="fixture/s5", revision="r1",
    )).index(tmp_path).documents
    resolved = LocalReferenceResolver(LocalReferenceBudget(max_references=1)).resolve(source)
    observations = [item for doc in resolved for item in doc.evidence.semantic
                    if item.schema_id == "local_reference/resolution@2"]
    coverage = [item for doc in resolved for item in doc.evidence.semantic
                if item.schema_id == "local_reference/coverage@1"]
    assert len(observations) == 1
    assert len(coverage) == 1
    assert LocalResolutionCoverageV1.from_envelope(coverage[0]).examined == 1
    assert LocalResolutionCoverageV1.from_envelope(coverage[0]).omitted_by_budget == 4
    assert sum(len(doc.evidence.relations.semantic) for doc in resolved) <= 1
    carrier = next(doc for doc in resolved if coverage[0] in doc.evidence.semantic)
    hydrated = ProjectionV3CanonicalDocumentReader().read(
        ProjectionV3Compiler().compile(carrier).to_mapping(),
    )
    assert coverage[0] in hydrated.evidence.semantic


def test_receiver_alias_reassignment_and_unknown_binding(tmp_path: Path) -> None:
    (tmp_path / "api.json").write_text(_spec(schema=False), encoding="utf-8")
    cases = {
        "alias": ('from fastapi import FastAPI as API\napp = API()\n', "resolved"),
        "reassigned": ('from fastapi import FastAPI\napp = FastAPI()\n'
                       + 'app = object()\n', "unsupported"),
        "unused": ('from fastapi import FastAPI\napp = object()\n', "unsupported"),
        "function": ('from fastapi import FastAPI\ndef make_app():\n'
                     + '    return FastAPI()\napp = make_app()\n', "unsupported"),
        "router": ('from fastapi import FastAPI\nrouter = object()\n', "unsupported"),
    }
    for name, (preamble, expected) in cases.items():
        receiver = "router" if name == "router" else "app"
        (tmp_path / "app.py").write_text(
            preamble + f'@{receiver}.get("/customers/{{id}}")\n'
            'def handler(id: str):\n    pass\n', encoding="utf-8",
        )
        result = _index(tmp_path)
        observation = _observations(result, "route_correspondence")[0][1]
        assert observation["status"] == expected, name
        assert bool(_observations(result, "route_correspondence")[0][0]
                    .evidence.relations.semantic) == (expected == "resolved")


def test_resolution_target_scopes_reject_incomplete_claims() -> None:
    import pytest

    base = LocalResolutionObservationV1(
        kind="markdown_reference", declaration="./target.md", source_locator="3",
        status="resolved", evidence_basis="canonical_path", candidate_count=2,
        target_scope="file", target_path="target.md", target_revision="r1",
    )
    assert LocalResolutionObservationV1.from_envelope(base.to_envelope()) == base
    with pytest.raises(ValueError, match="scope"):
        replace(base, target_scope=None)
    with pytest.raises(ValueError, match="identity"):
        replace(base, target_document_id="arbitrary")
    with pytest.raises(ValueError, match="incomplete"):
        replace(base, target_scope="document", target_document_id="one")
    with pytest.raises(ValueError, match="identity"):
        replace(base, target_path="")
    with pytest.raises(ValueError, match="inconclusive"):
        replace(base, status="ambiguous")


def test_repeated_partial_pass_removes_prior_relations(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A\n\n[b](./b.md) [c](./c.md)\n", encoding="utf-8")
    for name in ("b.md", "c.md"):
        (tmp_path / name).write_text(f"# {name}\n", encoding="utf-8")
    source = ApiContractRepositoryIndexer(RepositoryIndexConfig(
        repository="fixture/s5", revision="r1",
    )).index(tmp_path).documents
    full = LocalReferenceResolver().resolve(source)
    partial = LocalReferenceResolver(LocalReferenceBudget(max_references=1)).resolve(full)
    assert sum(len(doc.evidence.relations.semantic) for doc in partial) == 1
    assert sum(item.schema_id == "local_reference/resolution@2"
               for doc in partial for item in doc.evidence.semantic) == 1
    assert sum(item.schema_id == "local_reference/coverage@1"
               for doc in partial for item in doc.evidence.semantic) == 1
    assert partial == LocalReferenceResolver(LocalReferenceBudget(max_references=1)).resolve(
        partial,
    )


def test_shadowed_receiver_and_duplicate_same_file_are_inconclusive(tmp_path: Path) -> None:
    (tmp_path / "api.json").write_text(_spec(schema=False), encoding="utf-8")
    (tmp_path / "app.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n'
        'def outer():\n    app = object()\n'
        '    @app.get("/customers/{id}")\n'
        '    def nested(id: str):\n        pass\n', encoding="utf-8",
    )
    shadowed = _index(tmp_path)
    assert _observations(shadowed, "route_correspondence")[0][1]["status"] != "resolved"
    (tmp_path / "app.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n'
        '@app.get("/customers/{id}")\ndef first(id: str):\n    pass\n'
        '@app.get("/customers/{id}")\ndef second(id: str):\n    pass\n',
        encoding="utf-8",
    )
    duplicate = _index(tmp_path)
    route = _observations(duplicate, "route_correspondence")[0]
    assert route[1]["status"] == "ambiguous"
    assert route[1]["candidate_count"] == 2
    assert not route[0].evidence.relations.semantic


def test_same_path_with_different_method_is_not_a_route_match(tmp_path: Path) -> None:
    (tmp_path / "api.json").write_text(_spec(schema=False), encoding="utf-8")
    (tmp_path / "app.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n'
        '@app.post("/customers/{id}")\ndef handler(id: str):\n    pass\n',
        encoding="utf-8",
    )
    result = _index(tmp_path)
    route = _observations(result, "route_correspondence")[0]
    assert route[1]["status"] == "unresolved"
    assert not route[0].evidence.relations.semantic
