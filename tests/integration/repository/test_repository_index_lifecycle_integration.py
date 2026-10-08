"""Registered CLI -> canonical runtime -> real Solr adapter, opt-in only.

This is the real-backend counterpart of the mocked CLI lifecycle contract
tests in
``tests/contract/repository/test_repository_index_lifecycle.py``. It is
skipped unless an opt-in disposable Solr instance is configured, matching
the ``solr_integration`` marker contract.
"""
from __future__ import annotations

import os
import subprocess
from uuid import uuid4

import pytest

from fetchcore.application import indexing
from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.adapters.retrieval.solr.backend import SolrIndexStore, SolrSettings, UrllibSolrTransport


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def commit(root, message):
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Lifecycle Test")
    git(root, "config", "user.email", "test@example.test")
    git(root, "remote", "add", "origin", "https://example.test/acme/widgets.git")
    (root / "a.py").write_text("def first():\n    return 1\n")
    (root / "removed.py").write_text("def old():\n    return 0\n")
    commit(root, "initial")
    return root


@pytest.mark.solr_integration
def test_native_registered_lifecycle(workspace, tmp_path):
    url = os.environ.get("FETCHCORE_TARGETED_SOLR_URL")
    collection = os.environ.get("FETCHCORE_TARGETED_SOLR_COLLECTION")
    if not url or not collection:
        pytest.skip("requires opt-in disposable Solr")
    git(workspace, "remote", "set-url", "origin", f"https://example.test/lifecycle/{uuid4().hex}.git")
    repositories = RepositoryApplicationService(cache_root=tmp_path / "cache", allowed_roots=(tmp_path,))
    registered = repositories.add(str(workspace))
    service = indexing.RepositoryIndexApplicationService(repositories, backend_settings={
        "url": url, "collection": collection,
    })
    settings = SolrSettings.from_mapping({"url": url, "collection": collection})
    store = SolrIndexStore(UrllibSolrTransport(settings), settings)
    try:
        assert service.run("plan", registered.repository)["mode"] == "initial"
        service.run("rebuild", registered.repository)
        assert service.run("status", registered.repository)["currentness"] == "current"
        assert service.run("sync", registered.repository)["mode"] == "noop"
        (workspace / "a.py").write_text("def first():\n    return 42\n")
        commit(workspace, "delta")
        assert service.run("plan", registered.repository)["mode"] == "incremental"
        assert service.run("sync", registered.repository)["mode"] == "incremental"
        assert service.run("status", registered.repository)["currentness"] == "current"
    finally:
        store._delete_repository(registered.repository)
        store._commit()
