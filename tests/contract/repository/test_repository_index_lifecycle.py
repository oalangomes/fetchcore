"""Registered CLI -> canonical runtime -> mocked adapter, with observable writes.

The opt-in real-Solr case lives separately in
``tests/integration/repository/test_repository_index_lifecycle_integration.py``
because it exercises a real external backend rather than this file's
deterministic ``LifecycleTransport`` fake.
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from fetchcore import cli
from fetchcore.application import indexing
from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.adapters.retrieval.solr.backend import SolrIndexStore
from fetchcore.repository.git_source import GitRepositorySourceProvider
from fetchcore.retrieval.contracts import KnowledgeSearchError
from memory_solr_transport import MemoryTransport
from datetime import UTC


class LifecycleTransport(MemoryTransport):
    """Extend the existing persistence fixture with rebuild staging/rollback."""
    def __init__(self):
        super().__init__()
        self.committed = {}

    def get(self, path, params=None):
        if path == "get":
            ids = params["ids"].split(",")
            return {"response": {"docs": [copy.deepcopy(self.documents[i])
                                          for i in ids if i in self.documents]}}
        return super().get(path, params)

    def post(self, path, payload, params=None):
        if isinstance(payload, dict) and "rollback" in payload:
            self.post_calls.append((path, payload, params))
            self.documents = copy.deepcopy(self.committed)
            return {}
        result = super().post(path, payload, params)
        if isinstance(payload, dict) and "commit" in payload:
            self.committed = copy.deepcopy(self.documents)
        return result


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


@pytest.fixture
def backend(monkeypatch):
    transport = LifecycleTransport()
    monkeypatch.setattr(indexing, "UrllibSolrTransport", lambda settings: transport)
    # Native schema validation is exercised by the opt-in real-backend case in
    # tests/integration/repository/test_repository_index_lifecycle_integration.py.
    monkeypatch.setattr(SolrIndexStore, "validate_schema", lambda *a, **k: None)
    return transport


@pytest.fixture
def invoke(tmp_path):
    runner = CliRunner()
    def call(operation, reference="widgets", *, success=True, extra=(), as_json=True):
        args = ["index", "--cache-root", str(tmp_path / "cache"),
                "--allowed-root", str(tmp_path), *extra, operation, reference]
        if as_json:
            args.append("--json")
        output = runner.invoke(cli.main, args)
        assert (output.exit_code == 0) == success, output.output
        return json.loads(output.output) if as_json else output.output
    return call


def register(root, tmp_path):
    output = CliRunner().invoke(cli.main, ["repo", "--cache-root", str(tmp_path / "cache"),
        "--allowed-root", str(tmp_path), "add", str(root), "--json"])
    assert output.exit_code == 0, output.output
    return json.loads(output.output)["result"]


def tree_bytes(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_registered_lifecycle_initial_noop_incremental_rebuild(workspace, tmp_path, backend, invoke):
    registered = register(workspace, tmp_path)
    before = tree_bytes(tmp_path)
    first = invoke("plan")["result"]
    assert first["repository"] == registered["repository"] == "acme/widgets"
    assert first["mode"] == "initial"
    assert first["currentness"] == "absent"
    assert first["planned_mutations"]["upsert_documents"] == 2
    assert first == invoke("plan")["result"]
    assert tree_bytes(tmp_path) == before
    assert backend.post_calls == []
    assert invoke("sync", success=False)["error"]["category"] == "snapshot_missing"
    assert backend.post_calls == []
    rebuilt = invoke("rebuild")["result"]
    assert rebuilt["execution"]["snapshot"]["revision"] == registered["revision"]
    assert invoke("status")["result"]["currentness"] == "current"
    backend.post_calls.clear()
    noop_plan = invoke("plan")["result"]
    noop_sync = invoke("sync")["result"]
    assert noop_plan["mode"] == "noop"
    assert noop_sync["mode"] == "noop"
    assert noop_plan["stage_timings_ms"]["snapshot_load_ms"] >= 0
    assert noop_sync["execution"]["stage_timings_ms"]["snapshot_load_ms"] >= 0
    assert "previous_documents_load_ms" not in noop_plan["stage_timings_ms"]
    assert "previous_documents_load_ms" not in noop_sync["execution"]["stage_timings_ms"]
    assert "persistence_call_ms" not in noop_sync["execution"]["stage_timings_ms"]
    assert backend.post_calls == []

    (workspace / "a.py").write_text("def first():\n    return 42\n")
    (workspace / "removed.py").unlink()
    (workspace / "new.py").write_text("def added():\n    return 3\n")
    revision = commit(workspace, "delta")
    plan = invoke("plan")["result"]
    assert plan["mode"] == "incremental"
    assert plan["registered_revision"] == registered["revision"]
    assert plan["target_revision"] == revision
    assert plan["discovery"]["changed_path_values"] == ["a.py", "new.py"]
    assert plan["discovery"]["removed_path_values"] == ["removed.py"]
    assert plan["planned_mutations"]["updated"] == 1
    assert plan["planned_mutations"]["deleted"] == 1
    for stage in ("snapshot_load_ms", "previous_documents_load_ms",
                  "canonical_delta_index_ms"):
        assert isinstance(plan["stage_timings_ms"][stage], int)
        assert plan["stage_timings_ms"][stage] >= 0
    assert "persistence_call_ms" not in plan["stage_timings_ms"]
    assert backend.post_calls == []
    outcome = invoke("sync")["result"]
    assert outcome["mode"] == "incremental"
    assert outcome["execution"]["snapshot"]["revision"] == revision
    assert outcome["execution"]["updated"] == 1
    for stage in ("snapshot_load_ms", "previous_documents_load_ms",
                  "canonical_delta_index_ms", "persistence_call_ms"):
        assert isinstance(outcome["execution"]["stage_timings_ms"][stage], int)
        assert outcome["execution"]["stage_timings_ms"][stage] >= 0
    assert all("rollback" not in payload for _, payload, _ in backend.post_calls)
    assert invoke("status")["result"]["currentness"] == "current"
    canonical = copy.deepcopy(backend.documents)
    invoke("rebuild")
    # Observation timestamps differ; all content, identity and provenance must agree.
    def payloads(records):
        result = {}
        for key, record in records.items():
            if record["record_kind"] == "knowledge":
                payload = json.loads(record["canonical_payload"])
                payload.pop("indexed_at")
                result[key] = payload
        return result
    assert payloads(backend.documents) == payloads(canonical)



@pytest.mark.parametrize("corruption,reason", [
    ("indexing", "indexing_contract_changed"), ("projection", "schema_mismatch"),
    ("payload", "response_parsing_failure"),
    ("not_ready", "index_stale"),
])
def test_incompatible_sync_never_rebuilds(workspace, tmp_path, backend, invoke, corruption, reason):
    register(workspace, tmp_path)
    invoke("rebuild")
    snapshot = next(v for v in backend.documents.values() if v["record_kind"] == "snapshot")
    if corruption == "indexing":
        snapshot["canonical_payload"] = '{"indexing_fingerprint":"changed"}'
    elif corruption == "projection":
        snapshot["projection_fingerprint"] = "changed"
    elif corruption == "payload":
        snapshot["canonical_payload"] = "{invalid"
    else:
        snapshot["snapshot_status"] = "building"
    backend.committed = copy.deepcopy(backend.documents)
    backend.post_calls.clear()
    plan = invoke("plan")["result"]
    assert plan["mode"] == "rebuild_required"
    assert plan["reason"] == reason
    assert invoke("status")["result"]["currentness"] == "incompatible"
    assert invoke("sync", success=False)["error"]["category"] == "rebuild_required"
    assert backend.post_calls == []


def test_schema_failure_is_read_only_even_for_rebuild(workspace, tmp_path, backend, invoke, monkeypatch):
    register(workspace, tmp_path)
    def fail(*a, **k):
        raise KnowledgeSearchError("secret backend detail", category="schema_mismatch")
    monkeypatch.setattr(SolrIndexStore, "validate_schema", fail)
    assert invoke("plan")["result"]["schema_compatible"] is False
    assert invoke("sync", success=False)["error"]["category"] == "rebuild_required"
    failure = invoke("rebuild", success=False)
    assert "secret" not in str(failure)
    assert failure["error"]["category"] == "schema_mismatch"
    assert backend.post_calls == []


@pytest.mark.parametrize("operation", ["plan", "status", "sync", "rebuild"])
def test_unsupported_backend_is_typed(invoke, operation):
    result = invoke(operation, success=False, extra=("--backend", "qdrant-vector"))
    assert result["error"]["category"] == "unsupported_backend"


def test_registry_failures(workspace, tmp_path, backend, invoke):
    assert invoke("plan", success=False)["error"]["category"] == "repository_not_found"
    register(workspace, tmp_path)
    other = tmp_path / "other"
    shutil.copytree(workspace, other)
    git(other, "remote", "set-url", "origin", "https://example.test/other/widgets.git")
    register(other, tmp_path)
    assert invoke("plan", success=False)["error"]["category"] == "ambiguous_repository"
    assert invoke("plan", "acme/widgets")["result"]["repository"] == "acme/widgets"
    shutil.rmtree(workspace)
    assert invoke("plan", "acme/widgets", success=False)["error"]["category"] == "missing_materialization"
    assert backend.post_calls == []


def test_git_plan_uses_pinned_cached_revision_and_cleans_worktree(workspace, tmp_path, backend, monkeypatch):
    repositories = RepositoryApplicationService(cache_root=tmp_path / "cache", allowed_roots=(tmp_path,))
    record = repositories.add(str(workspace), provider="git")
    (workspace / "a.py").write_text("value = 'new revision'\n")
    commit(workspace, "upstream changed")
    before = tree_bytes(tmp_path / "cache")
    def no_fetch(*a, **k):
        raise AssertionError("registered materialization must not refresh")
    monkeypatch.setattr(GitRepositorySourceProvider, "_fetch_mirror", no_fetch)
    service = indexing.RepositoryIndexApplicationService(repositories)
    plan = service.run("plan", record.repository)
    assert plan["target_revision"] == record.revision
    assert plan["source_ref"] == record.source_ref
    assert tree_bytes(tmp_path / "cache") == before
    assert list((tmp_path / "cache/git/worktrees").iterdir()) == []
    assert backend.post_calls == []


def test_human_and_json_present_same_facts(workspace, tmp_path, backend, invoke):
    register(workspace, tmp_path)
    facts = invoke("status")["result"]
    human = invoke("status", as_json=False)
    for key, value in facts.items():
        assert f"{key}={value if value is not None else 'unavailable'}" in human


def test_explicit_legacy_options_cannot_override_registered_identity(workspace, tmp_path, invoke):
    register(workspace, tmp_path)
    result = invoke("plan", success=False, extra=("--repository", "wrong/repo"))
    assert result["error"]["category"] == "invalid_repository_reference"


def test_registered_target_ignores_legacy_identity_environment(workspace, tmp_path, backend, invoke, monkeypatch):
    register(workspace, tmp_path)
    monkeypatch.setenv("FETCHCORE_INDEX_REPOSITORY", "wrong/repo")
    monkeypatch.setenv("FETCHCORE_INDEX_REVISION", "old")
    result = invoke("plan")["result"]
    assert result["repository"] == "acme/widgets"
    assert result["target_revision"] == git(workspace, "rev-parse", "HEAD")


def test_explicit_rebuild_repairs_indexing_fingerprint(workspace, tmp_path, backend, invoke):
    register(workspace, tmp_path)
    invoke("rebuild")
    snapshot = next(v for v in backend.documents.values() if v["record_kind"] == "snapshot")
    snapshot["canonical_payload"] = '{"indexing_fingerprint":"old"}'
    backend.committed = copy.deepcopy(backend.documents)
    assert invoke("plan")["result"]["mode"] == "rebuild_required"
    invoke("rebuild")
    assert invoke("status")["result"]["currentness"] == "current"


def test_commit_failure_restores_indexed_revision(workspace, tmp_path, backend, invoke):
    register(workspace, tmp_path)
    invoke("rebuild")
    previous = copy.deepcopy(backend.documents)
    (workspace / "a.py").write_text("def first():\n    return 99\n")
    commit(workspace, "delta")
    backend.fail_next_commit = True
    assert invoke("sync", success=False)["error"]["category"] == "index_update_failure"
    assert backend.documents == previous
    assert invoke("status")["result"]["currentness"] == "stale"


def test_strict_synchronizer_blocks_late_rebuild(workspace, tmp_path, backend, invoke, monkeypatch):
    register(workspace, tmp_path)
    invoke("rebuild")
    backend.post_calls.clear()
    observe = indexing._observe
    def changed_after_observation(context):
        observation = observe(context)
        snapshot = next(v for v in backend.documents.values() if v["record_kind"] == "snapshot")
        snapshot["canonical_payload"] = '{"indexing_fingerprint":"raced"}'
        return observation
    monkeypatch.setattr(indexing, "_observe", changed_after_observation)
    assert invoke("sync", success=False)["error"]["category"] == "rebuild_required"
    assert backend.post_calls == []


def test_dirty_workspace_same_head_is_not_noop(workspace, tmp_path, backend, invoke):
    register(workspace, tmp_path)
    invoke("rebuild")
    backend.post_calls.clear()
    (workspace / "a.py").write_text("def first():\n    return 999\n")
    plan = invoke("plan")["result"]
    assert plan["mode"] == "incremental"
    assert invoke("status")["result"]["currentness"] == "unknown"
    assert plan["currentness"] == "stale"
    assert plan["planned_mutations"]["updated"] == 1
    assert backend.post_calls == []


def test_non_git_revision_is_unknown_not_current(workspace, tmp_path, backend, invoke):
    shutil.rmtree(workspace / ".git")
    register(workspace, tmp_path)
    rebuilt = invoke("rebuild", "workspace")
    from fetchcore.application.index_artifacts import IndexRunArtifact, compare_index_artifacts
    artifact = IndexRunArtifact.from_mapping({
        "schema_version": 1, "kind": "fetchcore.index-run", "run_id": "non-git",
        "observed_at": "2026-10-15T12:00:00Z", "repository": "workspace", "backend": "solr",
        "target": "non-git-fixture", "configuration_fingerprint": "a" * 64, "command": rebuilt,
    })
    assert artifact.command["result"]["target_revision"] is None
    assert artifact.command["result"]["execution"]["snapshot"]["revision"]
    assert compare_index_artifacts(artifact, artifact)["source_revision_changed"] is None
    result = invoke("status", "workspace")["result"]
    assert result["target_revision"] is None
    assert result["currentness"] == "unknown"
    plan = invoke("plan", "workspace")["result"]
    assert "changed_paths" not in plan["discovery"]


def test_cli_has_no_adapter_imports():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(cli.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("fetchcore.backends", "fetchcore.adapters"))


def test_cached_git_failure_cleans_worktree_without_changing_registry(workspace, tmp_path, backend, monkeypatch):
    repositories = RepositoryApplicationService(cache_root=tmp_path / "cache", allowed_roots=(tmp_path,))
    record = repositories.add(str(workspace), provider="git")
    before = tree_bytes(tmp_path / "cache")
    def fail(*a, **k):
        raise KnowledgeSearchError("private backend response", category="connection_failure")
    monkeypatch.setattr(SolrIndexStore, "validate_schema", fail)
    with pytest.raises(KnowledgeSearchError, match="private backend"):
        indexing.RepositoryIndexApplicationService(repositories).run("plan", record.repository)
    assert tree_bytes(tmp_path / "cache") == before
    assert list((tmp_path / "cache/git/worktrees").iterdir()) == []
    shutil.rmtree(record.cache_path)
    with pytest.raises(ValueError) as failure:
        indexing.RepositoryIndexApplicationService(repositories).run("plan", record.repository)
    assert failure.value.category == "missing_materialization"
    assert not Path(record.cache_path).exists()
    assert backend.post_calls == []


def test_backend_failure_output_redacts_raw_details(workspace, tmp_path, backend, invoke, monkeypatch):
    register(workspace, tmp_path)
    def fail(*a, **k):
        raise KnowledgeSearchError("https://user:secret@host/private", category="connection_failure")
    monkeypatch.setattr(SolrIndexStore, "validate_schema", fail)
    result = invoke("plan", success=False)
    assert result["error"]["category"] == "connection_failure"
    assert "secret" not in str(result)
    assert "private" not in str(result)
    assert backend.post_calls == []


def test_plan_reports_canonical_noop_for_already_indexed_dirty_content(workspace, tmp_path, backend, invoke):
    register(workspace, tmp_path)
    invoke("rebuild")
    (workspace / "a.py").write_text("def first():\n    return 999\n")
    invoke("sync")
    backend.post_calls.clear()
    plan = invoke("plan")["result"]
    assert plan["mode"] == "noop"
    assert plan["reason"] == "canonical_noop"
    assert invoke("sync")["result"]["mode"] == "noop"
    assert backend.post_calls == []


def test_plan_reports_canonical_full_scan_reason(workspace, tmp_path, backend, invoke, monkeypatch):
    from fetchcore.repository.semantic_indexing import SemanticDocumentRepositoryIndexer
    # An active document/semantic provider can require a full canonical scan.
    monkeypatch.setattr(SemanticDocumentRepositoryIndexer, "requires_full_scan_for_paths",
                        lambda *args: True)
    register(workspace, tmp_path)
    invoke("rebuild")
    (workspace / "README.md").write_text("# Updated repository documentation\n")
    commit(workspace, "document changed")
    backend.post_calls.clear()
    plan = invoke("plan")["result"]
    assert plan["mode"] == "incremental"
    assert plan["discovery"]["mode"] == "full-scan"
    assert plan["reason"] == plan["discovery"]["reason"] == "delta_scan_unsafe"
    assert plan["planned_mutations"]["replace_repository"] is False
    assert backend.post_calls == []


def test_index_artifacts_follow_real_cli_lifecycle(workspace, tmp_path, backend, invoke):
    """Exercise the importer against actual application outputs, including rollback failure."""
    from fetchcore.application.index_artifacts import (
        IndexRunArtifact, compare_index_artifacts, load_index_artifact,
    )
    register(workspace, tmp_path)
    commands = [invoke('status'), invoke('plan'), invoke('sync', success=False),
                invoke('rebuild'), invoke('sync')]
    (workspace / 'a.py').write_text('def changed():\n    return 2\n')
    (workspace / 'removed.py').unlink()
    commit(workspace, 'delta')
    commands.extend([invoke('plan'), invoke('sync')])
    previous = copy.deepcopy(backend.documents)
    (workspace / 'a.py').write_text('def changed_again():\n    return 3\n')
    commit(workspace, 'rollback test')
    backend.fail_next_commit = True
    commands.append(invoke('sync', success=False))
    assert backend.documents == previous
    snapshot = next(v for v in backend.documents.values() if v['record_kind'] == 'snapshot')
    snapshot['canonical_payload'] = '{"indexing_fingerprint":"changed"}'
    commands.append(invoke('plan'))
    import hashlib
    from datetime import datetime
    configuration = {'backend': 'solr', 'collection': 'fetchcore', 'transport': 'LifecycleTransport'}
    configuration_fingerprint = hashlib.sha256(json.dumps(
        configuration, sort_keys=True, separators=(',', ':'),
    ).encode()).hexdigest()
    artifacts = []
    for i, command in enumerate(commands):
        artifact = IndexRunArtifact.from_mapping({
            'schema_version': 1, 'kind': 'fetchcore.index-run', 'run_id': f'run-{i}',
            'observed_at': datetime.now(UTC).isoformat(), 'repository': 'acme/widgets',
            'backend': 'solr', 'target': 'contract-fixture',
            'configuration_fingerprint': configuration_fingerprint, 'command': command,
        })
        assert load_index_artifact(json.dumps(artifact.to_mapping())) == artifact
        artifacts.append(artifact)
    comparison = compare_index_artifacts(artifacts[4], artifacts[6])
    assert comparison['comparable'] is True
    assert comparison['source_revision_changed'] is True
    assert artifacts[5].command['result']['planned_mutations']['deleted'] > 0
    assert artifacts[6].command['result']['execution']['deleted'] > 0
    # Optional, explicit fixture refresh for browser tests; never on normal test runs.
    import os
    if os.environ.get('FETCHCORE_REFRESH_INDEX_FIXTURES') == '1':
        destination = Path('tools/explorer/fixtures/index-runs')
        destination.mkdir(exist_ok=True)
        names = ['initial-status', 'initial-plan', 'failure', 'rebuild', 'noop', 'delta-plan', 'incremental', 'rollback-failure', 'incompatible-plan']
        for name, artifact in zip(names, artifacts):
            (destination / (name + '.json')).write_text(
                json.dumps(artifact.to_mapping(), sort_keys=True, indent=2) + '\n')
