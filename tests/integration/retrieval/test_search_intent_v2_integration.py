"""Opt-in native lexical translation checks against disposable fixture records."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from fetchcore.adapters.retrieval.opensearch.backend import OpenSearchSettings
from fetchcore.adapters.retrieval.opensearch.query import _search_body
from fetchcore.adapters.retrieval.solr.backend import SolrSettings
from fetchcore.adapters.retrieval.solr.query import _search_params
from fetchcore.retrieval.contracts import AllOf, Anchor, AnyOf, SearchIntent, SearchScope, Term


def request(url, payload=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    with urlopen(
        Request(url, data=data, method=method, headers={"Content-Type": "application/json"}),
        timeout=20,
    ) as response:
        return json.load(response)


def fixtures(repository):
    return [
        {
            "id": repository + "-" + str(index),
            "record_kind": "knowledge",
            "repository": repository,
            "path": path,
            "identifiers_exact": [identifier],
            "content": content,
            "knowledge_type": "code",
        }
        for index, (path, identifier, content) in enumerate(
            [
                ("src/a.py", "ProductService", "checkout policy loaded validated"),
                ("src/b.py", "ProductFacade", "cart policy loaded validated"),
                ("src/c.py", "ProductService", "cart policy loaded validated"),
            ]
        )
    ]


def intents():
    branches = AnyOf(
        (
            AllOf((Anchor("identifier", "ProductService"), Term("checkout"))),
            AllOf((Anchor("identifier", "ProductFacade"), Term("cart"))),
        )
    )
    return [
        (SearchIntent(expression=branches), {"src/a.py", "src/b.py"}),
        (
            SearchIntent(canonical_query="Where is policy loaded and validated?"),
            {"src/a.py", "src/b.py", "src/c.py"},
        ),
        (
            SearchIntent(canonical_query="policy", anchors=(Anchor("path", "src/a.py"),)),
            {"src/a.py"},
        ),
        (SearchIntent(anchors=(Anchor("path", 'src/a.py") OR *:*'),)), set()),
        (
            SearchIntent(canonical_query="policy", hints=("totally unrelated",)),
            {"src/a.py", "src/b.py", "src/c.py"},
        ),
    ]


@pytest.mark.solr_integration
def test_solr_preserves_groups_and_native_analysis():
    url = os.environ.get("FETCHCORE_INTENT_SOLR_CORE_URL")
    if not url:
        pytest.skip("set FETCHCORE_INTENT_SOLR_CORE_URL to an opt-in test core")
    repository = "intent-" + uuid4().hex
    docs = fixtures(repository)
    scope = SearchScope(Path("."), None, repository, (), 10)
    try:
        request(url + "/update?commit=true", docs)
        for intent, expected in intents():
            params = _search_params(
                intent, scope, SimpleNamespace(repository=repository), SolrSettings()
            )
            response = request(url + "/select?" + urlencode(params, doseq=True))
            assert {doc["path"] for doc in response["response"]["docs"]} == expected
    finally:
        request(url + "/update?commit=true", {"delete": [{"id": doc["id"]} for doc in docs]})


@pytest.mark.opensearch_integration
def test_opensearch_preserves_groups_and_native_analysis():
    url = os.environ.get("FETCHCORE_INTENT_OPENSEARCH_URL")
    if not url:
        pytest.skip("set FETCHCORE_INTENT_OPENSEARCH_URL to an opt-in test service")
    index = "intent-" + uuid4().hex
    scope = SearchScope(Path("."), None, index, (), 10)
    request(
        url + "/" + index,
        {
            "mappings": {
                "properties": {
                    key: {"type": "keyword"}
                    for key in ("id", "record_kind", "repository", "path", "identifiers_exact")
                }
            }
        },
        "PUT",
    )
    try:
        for doc in fixtures(index):
            doc["identifiers_exact"] = [value.casefold() for value in doc["identifiers_exact"]]
            request(url + "/" + index + "/_doc/" + doc["id"] + "?refresh=true", doc, "PUT")
        for intent, expected in intents():
            body = _search_body(
                intent, scope, SimpleNamespace(repository=index), OpenSearchSettings()
            )
            response = request(url + "/" + index + "/_search", body)
            assert {hit["_source"]["path"] for hit in response["hits"]["hits"]} == expected
    finally:
        request(url + "/" + index, method="DELETE")
