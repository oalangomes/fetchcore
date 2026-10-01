from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from fetchcore.backends.solr import (
    PersistentSolrTransport,
    SolrBackend,
    SolrSettings,
)


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
