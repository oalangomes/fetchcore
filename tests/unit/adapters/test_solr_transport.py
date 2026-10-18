from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from fetchcore.backends.solr import (
    PersistentSolrTransport,
    SolrBackend,
    SolrSettings,
)
from fetchcore.adapters.retrieval.solr.backend import _solr_dense_candidates
from fetchcore.retrieval.contracts import KnowledgeSearchError, SearchScope


class _KeepAliveHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    client_ports: ClassVar[list[int]] = []
    paths: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        type(self).client_ports.append(self.client_address[1])
        type(self).paths.append(self.path)
        body = json.dumps({"responseHeader": {"status": 0, "QTime": 1}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class _MemoryTransport:
    def get(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        del path, params
        return {}

    def post(
        self,
        path: str,
        payload: object,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        del path, payload, params
        return {}


def test_persistent_solr_transport_reuses_connection() -> None:
    _KeepAliveHandler.client_ports = []
    _KeepAliveHandler.paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _KeepAliveHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = SolrSettings.from_mapping(
        {
            "url": f"http://127.0.0.1:{server.server_port}/solr",
            "collection": "fetchcore-knowledge",
        }
    )
    transport = PersistentSolrTransport(settings)

    try:
        assert transport.get("select", {"fq": ["a", "b"]})["responseHeader"] == {
            "status": 0,
            "QTime": 1,
        }
        assert transport.get("select", {"q": "*:*"})["responseHeader"] == {
            "status": 0,
            "QTime": 1,
        }
    finally:
        transport.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert len(_KeepAliveHandler.client_ports) == 2
    assert len(set(_KeepAliveHandler.client_ports)) == 1
    assert _KeepAliveHandler.paths == [
        "/solr/fetchcore-knowledge/select?fq=a&fq=b",
        "/solr/fetchcore-knowledge/select?q=%2A%3A%2A",
    ]


def test_solr_backend_reuses_transport_for_equal_settings() -> None:
    created: list[_MemoryTransport] = []

    def factory(settings: SolrSettings) -> _MemoryTransport:
        del settings
        transport = _MemoryTransport()
        created.append(transport)
        return transport

    backend = SolrBackend(transport_factory=factory)
    first = SolrSettings.from_mapping({"collection": "first"})
    equal = SolrSettings.from_mapping({"collection": "first"})
    changed = SolrSettings.from_mapping({"collection": "second"})

    assert backend._transport_for(first) is backend._transport_for(equal)
    assert backend._transport_for(changed) is not backend._transport_for(first)
    assert len(created) == 3


@pytest.mark.parametrize("breadth", [True, 0, -1, 5001, 80.0, "160", []])
def test_solr_ann_search_breadth_rejects_invalid_values(breadth: object) -> None:
    with pytest.raises(KnowledgeSearchError) as exc:
        SolrSettings.from_mapping({"ann_search_breadth": breadth})
    assert exc.value.category == "schema_mismatch"


class _DenseCaptureTransport(_MemoryTransport):
    def __init__(self) -> None:
        self.params: dict[str, object] | None = None

    def post(
        self,
        path: str,
        payload: object,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        assert path == "select"
        assert isinstance(payload, dict)
        self.params = payload
        return {"response": {"numFound": 0, "docs": []}}


@pytest.mark.parametrize("breadth,expected", [(None, 80), (160, 160)])
def test_solr_dense_separates_ann_breadth_from_candidate_window(
    breadth: int | None, expected: int,
) -> None:
    transport = _DenseCaptureTransport()
    settings = SolrSettings.from_mapping({
        "diagnostic_candidate_window": 80,
        **({"ann_search_breadth": breadth} if breadth is not None else {}),
    })
    scope = SearchScope(workspace=Path("."), repository="owner/repo", revision=None,
                        knowledge_types=(), max_results=20)
    candidates, found, params, requested = _solr_dense_candidates(
        transport=transport, scope=scope, snapshot=type("Snapshot", (), {"repository": "owner/repo"})(),
        settings=settings, vector=(0.1, 0.2),
    )
    assert candidates == () and found == 0
    assert requested == expected
    assert params["query"] == f"{{!knn f=embedding_vector topK={expected}}}[0.1,0.2]"
    assert params["limit"] == 80
    assert transport.params == params


@pytest.mark.parametrize("breadth,window,maximum", [(79, 80, 20), (321, 80, 20), (160, 20, 20)])
def test_solr_dense_rejects_inconsistent_breadth_before_transport(
    breadth: int, window: int, maximum: int,
) -> None:
    transport = _DenseCaptureTransport()
    settings = SolrSettings.from_mapping({
        "diagnostic_candidate_window": window, "ann_search_breadth": breadth,
    })
    scope = SearchScope(workspace=Path("."), repository="owner/repo", revision=None,
                        knowledge_types=(), max_results=maximum)
    with pytest.raises(KnowledgeSearchError) as exc:
        _solr_dense_candidates(
            transport=transport, scope=scope,
            snapshot=type("Snapshot", (), {"repository": "owner/repo"})(),
            settings=settings, vector=(0.1,),
        )
    assert exc.value.category == "schema_mismatch"
    assert transport.params is None


def test_solr_dense_propagates_typed_transport_error() -> None:
    class FailingTransport(_DenseCaptureTransport):
        def post(self, path: str, payload: object,
                 params: Mapping[str, object] | None = None) -> dict[str, object]:
            raise KnowledgeSearchError("Solr unavailable", category="backend_unavailable")

    settings = SolrSettings.from_mapping({"diagnostic_candidate_window": 80,
                                          "ann_search_breadth": 160})
    scope = SearchScope(workspace=Path("."), repository="owner/repo", revision=None,
                        knowledge_types=(), max_results=20)
    with pytest.raises(KnowledgeSearchError) as exc:
        _solr_dense_candidates(
            transport=FailingTransport(), scope=scope,
            snapshot=type("Snapshot", (), {"repository": "owner/repo"})(),
            settings=settings, vector=(0.1,),
        )
    assert exc.value.category == "backend_unavailable"
