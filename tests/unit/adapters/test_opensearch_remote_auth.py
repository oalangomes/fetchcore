"""Remote OpenSearch transport authentication is opt-in and fail-closed."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Self
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from fetchcore.adapters.retrieval.opensearch import backend as os_backend
from fetchcore.adapters.retrieval.opensearch.backend import (
    OpenSearchSettings,
    UrllibOpenSearchTransport,
)
from fetchcore.application.runtime_config import load_runtime_config
from fetchcore.application.runtime_inspection import RuntimeInspectionService
from fetchcore.retrieval.contracts import KnowledgeSearchError


class FakeResponse:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"acknowledged":true}'


def test_authenticated_https_request_uses_basic_auth_and_never_uses_urlopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[Request, float]] = []
    handlers: list[object] = []

    class FakeOpener:
        def open(self, request: Request, *, timeout: float) -> FakeResponse:
            seen.append((request, timeout))
            return FakeResponse()

    def fake_build_opener(*args: object) -> FakeOpener:
        handlers.extend(args)
        return FakeOpener()

    monkeypatch.setattr(os_backend, "build_opener", fake_build_opener)
    monkeypatch.setattr(
        os_backend, "urlopen",
        lambda *_args, **_kwargs: pytest.fail("authenticated path must use safe opener"),
    )
    settings = OpenSearchSettings.from_mapping({
        "url": "https://search.example.test:1234",
        "index": "fetchcore-test",
        "username": "operator",
        "password": "secret:with-colon",
    })
    result = UrllibOpenSearchTransport(settings).request("GET", "_cluster/health")
    assert result["acknowledged"] is True
    assert len(handlers) == 1
    assert isinstance(handlers[0], os_backend._NoAuthenticatedRedirects)
    assert len(seen) == 1
    request, timeout = seen[0]
    assert timeout == settings.timeout_seconds
    assert request.full_url == "https://search.example.test:1234/_cluster/health"
    expected_token = base64.b64encode(b"operator:secret:with-colon").decode("ascii")
    assert request.get_header("Authorization") == f"Basic {expected_token}"
    assert "secret:with-colon" not in repr(settings)
    assert "secret:with-colon" not in repr(request)


def test_authenticated_redirects_are_rejected() -> None:
    handler = os_backend._NoAuthenticatedRedirects()
    request = Request("https://search.example.test/")
    assert handler.redirect_request(
        request, None, 302, "Moved", {}, "https://other.example.test/"
    ) is None


def test_anonymous_local_opensearch_remains_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Request] = []

    def fake_urlopen(request: Request, *, timeout: float) -> FakeResponse:
        assert timeout > 0
        seen.append(request)
        return FakeResponse()

    monkeypatch.setattr(os_backend, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        os_backend, "build_opener",
        lambda *_args: pytest.fail("anonymous path should not require an opener"),
    )
    settings = OpenSearchSettings(url="http://localhost:9200")
    assert UrllibOpenSearchTransport(settings).request("GET", "_cluster/health")
    assert seen[0].get_header("Authorization") is None


@pytest.mark.parametrize(
    "url, username, password",
    [
        ("http://search.example.test:9200", "operator", "secret"),
        ("https://search.example.test", "operator", None),
        ("https://search.example.test", None, "secret"),
        ("https://search.example.test", "", "secret"),
        ("https://search.example.test", "operator", ""),
        ("https://search.example.test", "name:extra", "secret"),
        ("https://search.example.test", "operator\nHeader: x", "secret"),
        ("https://search.example.test", "operator", "secret\rHeader: x"),
        ("https://user:secret@search.example.test", None, None),
    ],
)
def test_invalid_remote_auth_fails_closed(
    url: str, username: str | None, password: str | None,
) -> None:
    with pytest.raises(KnowledgeSearchError) as exc:
        OpenSearchSettings.from_mapping({
            "url": url, "username": username, "password": password,
        })
    assert exc.value.category == "schema_mismatch"
    assert "secret" not in str(exc.value)


def test_http_401_does_not_echo_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    class UnauthorizedOpener:
        def open(self, request: Request, *, timeout: float) -> FakeResponse:
            del request, timeout
            raise HTTPError(
                "https://search.example.test/_cluster/health", 401, "Unauthorized", {}, None
            )

    monkeypatch.setattr(os_backend, "build_opener", lambda *_args: UnauthorizedOpener())
    settings = OpenSearchSettings.from_mapping({
        "url": "https://search.example.test",
        "username": "operator",
        "password": "not-in-error",
    })
    with pytest.raises(KnowledgeSearchError) as exc:
        UrllibOpenSearchTransport(settings).request("GET", "_cluster/health")
    assert exc.value.category == "authentication_denied"
    assert "not-in-error" not in str(exc.value)
    assert exc.value.metadata["http_status"] == 401


def test_runtime_env_auth_precedence_and_redacted_inspection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "operator.yaml"
    path.write_text(
        "version: 1\nruntime:\n  backend: opensearch\n"
        "backends:\n  opensearch:\n"
        "    url: https://file.example.test:1234\n"
        "    index: fetchcore-test\n",
        encoding="utf-8",
    )
    config = load_runtime_config(path=path, environ={
        "FETCHCORE_OPENSEARCH_URL": "https://env.example.test:1234",
        "FETCHCORE_OPENSEARCH_USERNAME": "operator",
        "FETCHCORE_OPENSEARCH_PASSWORD": "never-print-this",
    })
    assert config.backend_settings["url"] == "https://env.example.test:1234"
    assert config.backend_settings["username"] == "operator"
    assert config.backend_settings["password"] == "never-print-this"
    assert config.sources["backends.opensearch.password"] == "environment"
    assert "never-print-this" not in repr(config)

    report = RuntimeInspectionService(config).resolved_config()
    serialized = json.dumps(report)
    assert "never-print-this" not in serialized
    assert report["config"]["backends"]["opensearch"]["password"] == "********"
    assert report["sources"]["backends.opensearch.password"] == "environment"


def test_runtime_refuses_secret_on_unencrypted_endpoint() -> None:
    with pytest.raises(KnowledgeSearchError) as exc:
        load_runtime_config(environ={
            "FETCHCORE_BACKEND": "opensearch",
            "FETCHCORE_OPENSEARCH_URL": "http://public.example.test:9200",
            "FETCHCORE_OPENSEARCH_USERNAME": "operator",
            "FETCHCORE_OPENSEARCH_PASSWORD": "example-secret",
        })
    assert exc.value.category == "schema_mismatch"
    assert "example-secret" not in str(exc.value)
