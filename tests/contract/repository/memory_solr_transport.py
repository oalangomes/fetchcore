"""Shared in-memory Solr transport fake.

``MemoryTransport`` is reused by both the indexing-persistence contract
tests and the CLI lifecycle contract tests: both exercise the same
mocked Solr read/write protocol and benefit from a single, demonstrably
shared fake instead of duplicated or cross-imported test modules.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import cast

from fetchcore.search import KnowledgeSearchError


class MemoryTransport:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, object]] = {}
        self.get_calls: list[tuple[str, dict[str, object]]] = []
        self.post_calls: list[tuple[str, object, dict[str, object]]] = []
        self.fail_next_commit = False

    def get(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        normalized = dict(params or {})
        self.get_calls.append((path, normalized))
        if path != "select":
            raise AssertionError(f"unexpected GET path: {path}")
        docs = [copy.deepcopy(item) for item in self.documents.values()]
        filters = normalized.get("fq", [])
        if isinstance(filters, str):
            filters = [filters]
        for raw_filter in cast(list[str], filters):
            if raw_filter == "record_kind:snapshot":
                docs = [item for item in docs if item.get("record_kind") == "snapshot"]
            elif raw_filter == "record_kind:knowledge":
                docs = [item for item in docs if item.get("record_kind") == "knowledge"]
            elif raw_filter.startswith("{!term f=repository}"):
                repository = raw_filter.removeprefix("{!term f=repository}")
                docs = [item for item in docs if item.get("repository") == repository]
            elif raw_filter.startswith("{!terms f=id}"):
                ids = set(raw_filter.removeprefix("{!terms f=id}").split(","))
                docs = [item for item in docs if str(item.get("id")) in ids]
            else:
                raise AssertionError(f"unexpected filter: {raw_filter}")
        docs.sort(key=lambda item: str(item["id"]))
        start = int(cast(int, normalized.get("start", 0)))
        rows = int(cast(int, normalized.get("rows", 10)))
        return {
            "response": {
                "numFound": len(docs),
                "docs": docs[start : start + rows],
            }
        }

    def post(
        self,
        path: str,
        payload: object,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        normalized = dict(params or {})
        self.post_calls.append((path, copy.deepcopy(payload), normalized))
        assert path == "update"
        if isinstance(payload, list):
            for raw_item in payload:
                item = cast(dict[str, object], raw_item)
                self.documents[str(item["id"])] = copy.deepcopy(item)
        elif isinstance(payload, dict) and "delete" in payload:
            delete = payload["delete"]
            assert isinstance(delete, list)
            for raw_item in delete:
                item = cast(dict[str, object], raw_item)
                self.documents.pop(str(item["id"]), None)
        elif isinstance(payload, dict) and "commit" in payload:
            if self.fail_next_commit:
                self.fail_next_commit = False
                raise KnowledgeSearchError(
                    "injected commit failure",
                    category="index_update_failure",
                )
        else:
            raise AssertionError(f"unexpected update payload: {payload!r}")
        return {"responseHeader": {"status": 0}}
