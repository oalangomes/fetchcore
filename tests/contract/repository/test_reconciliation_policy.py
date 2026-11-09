"""Opt-in policy and fail-closed ownership observations for reconciliation."""

from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import cast

import pytest

from fetchcore.adapters.retrieval.solr.backend import (
    SolrIndexStore, SolrSettings, _CRITICAL_SCHEMA_FIELD_PROPERTIES,
    _REQUIRED_SCHEMA_FIELDS, _VECTOR_SCHEMA_FIELDS,
)
from fetchcore.adapters.retrieval.solr.reconciliation import inspect_ownership
from fetchcore.application.indexing import IndexContext
from fetchcore.application.reconciliation import _resource_lock, _source_state
from fetchcore.application.runtime_config import ReconciliationSettings
from fetchcore.retrieval.contracts import KnowledgeSearchError


class SolrCountTransport:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def get(self, path: str, params: dict[str, object]) -> dict[str, object]:
        assert path == "select"
        query = str(params["q"])
        if query == "*:*":
            rows = self.rows
        elif query.startswith("{!term f=repository}"):
            repository = query.removeprefix("{!term f=repository}")
            rows = [row for row in self.rows if row.get("repository") == repository]
        else:
            rows = [row for row in self.rows if row.get("record_kind") == "snapshot"]
        return {"response": {"numFound": len(rows), "numFoundExact": True,
                             "docs": rows[:int(params["rows"])]}}


def test_solr_ownership_requires_one_matching_snapshot() -> None:
    rows = [{"record_kind": "knowledge", "repository": "acme/widgets"},
            {"record_kind": "snapshot", "repository": "acme/widgets",
             "snapshot_status": "ready", "document_count": 1,
             "snapshot_revision": "abc", "corpus_checksum": "sha256:abc"}]
    store = cast(object, SimpleNamespace(transport=SolrCountTransport(rows)))
    owned = inspect_ownership(store, "acme/widgets")  # type: ignore[arg-type]
    assert owned.total_records == 2
    assert owned.snapshot_signature is not None
    with pytest.raises(KnowledgeSearchError, match="ownership"):
        inspect_ownership(store, "another/repository")  # type: ignore[arg-type]
    rows.pop()
    with pytest.raises(KnowledgeSearchError, match="ownership"):
        inspect_ownership(store, "acme/widgets")  # type: ignore[arg-type]


def test_reconciliation_source_must_be_clean_and_pinned(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path,
                   check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "source.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "source"], cwd=tmp_path, check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                                       text=True).strip()
    context = cast(IndexContext, SimpleNamespace(
        workspace=tmp_path, runtime=SimpleNamespace(revision=revision),
    ))
    assert _source_state(context) == (True, "source_revision_verified")
    (tmp_path / "untracked.py").write_text("value = 2\n")
    assert _source_state(context) == (False, "source_worktree_dirty")


def test_reconciliation_policy_defaults_to_read_only() -> None:
    assert ReconciliationSettings.from_mapping({}).mode == "inspect"
    assert not ReconciliationSettings.from_mapping({}).managed
    with pytest.raises(ValueError, match="mode"):
        ReconciliationSettings.from_mapping({"mode": "auto"})
    with pytest.raises(ValueError, match="managed"):
        ReconciliationSettings.from_mapping({"managed": "true"})


def test_solr_field_property_difference_is_exact() -> None:
    fields = [
        {"name": name, **_CRITICAL_SCHEMA_FIELD_PROPERTIES.get(name, {})}
        for name in sorted(_REQUIRED_SCHEMA_FIELDS - _VECTOR_SCHEMA_FIELDS)
    ]
    next(field for field in fields if field["name"] == "structural_names")["stored"] = False

    class SchemaTransport:
        def get(self, path: str, params: dict[str, object]) -> dict[str, object]:
            assert path == "schema"
            return {"schema": {"uniqueKey": "id", "fields": fields}}

    settings = SolrSettings.from_mapping({"url": "http://127.0.0.1:8983/solr",
                                          "collection": "dedicated"})
    store = SolrIndexStore(cast(object, SchemaTransport()), settings)  # type: ignore[arg-type]
    with pytest.raises(KnowledgeSearchError) as failed:
        store.validate_schema()
    assert failed.value.category == "schema_mismatch"
    assert failed.value.metadata["missing_fields"] == []
    assert failed.value.metadata["incompatible_fields"] == [{
        "field": "structural_names",
        "properties": {"stored": {"expected": True, "actual": False}},
    }]


def test_reconciliation_lock_rejects_concurrent_writer() -> None:
    context = cast(IndexContext, SimpleNamespace(
        backend="solr", runtime=SimpleNamespace(repository="acme/widgets"),
        store=SimpleNamespace(settings=SimpleNamespace(
            url="http://127.0.0.1:8983/solr", collection="dedicated",
        )),
    ))
    def contend() -> None:
        with _resource_lock(context):
            pytest.fail("a concurrent reconciler acquired the same lock")

    with _resource_lock(context), pytest.raises(KnowledgeSearchError) as failed:
        contend()
    assert failed.value.category == "concurrent_write"
