from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.repository.git_source import GitRepositorySourceError
from fetchcore.repository.registry import RegisteredRepository, RepositoryRegistryError
from fetchcore.repository.sources import MaterializedRepository


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def repository(path: Path, remote: str | None = None) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Registry Test")
    (path / "README.md").write_text("Repository evidence\n")
    git(path, "add", ".")
    git(path, "commit", "-m", "Initial")
    if remote:
        git(path, "remote", "add", "origin", remote)
    return path


@pytest.fixture
def service(tmp_path: Path) -> RepositoryApplicationService:
    return RepositoryApplicationService(cache_root=tmp_path / "cache", allowed_roots=(tmp_path,))


def test_empty_reads_do_not_create_registry_or_cache(service):
    assert service.list() == ()
    with pytest.raises(RepositoryRegistryError, match="not registered") as error:
        service.inspect("unknown")
    assert error.value.category == "repository_not_found"
    assert not service.registry.path.parent.exists()


def test_local_roundtrip_identity_provenance_and_no_refresh(service, tmp_path, monkeypatch):
    workspace = repository(tmp_path / "workspace", "https://example.com/team/project.git")
    record = service.add(str(workspace))
    assert record.repository == "team/project"
    assert record.revision == git(workspace, "rev-parse", "HEAD")
    assert record.branch == "main"
    assert record.source_ref == workspace.as_uri()
    assert record.cache_path is None
    assert RegisteredRepository.from_mapping(record.to_mapping()) == record
    persisted = service.registry.path.read_bytes()
    git(workspace, "commit", "--allow-empty", "-m", "Changed")
    monkeypatch.setattr(
        "fetchcore.repository.sources.LocalWorkspaceSourceProvider.materialize",
        lambda *_: pytest.fail("repeat add must not materialize"),
    )
    assert service.add(str(workspace)) == record
    assert service.registry.path.read_bytes() == persisted
    reloaded = RepositoryApplicationService(
        cache_root=tmp_path / "cache", allowed_roots=(tmp_path,)
    )
    assert reloaded.inspect("team/project") == record
    assert reloaded.inspect("project") == record
    assert reloaded.list() == (record,)
    request = record.source_request()
    assert (request.repository, request.source, request.revision) == (
        record.repository,
        str(workspace),
        None,
    )


def test_non_git_workspace_has_no_fabricated_revision(service, tmp_path):
    workspace = tmp_path / "plain"
    workspace.mkdir()
    record = service.add(str(workspace))
    assert record.repository == "plain"  # Existing provider fallback, not a CLI alias.
    assert record.branch is record.revision is None


def test_sorted_records_and_ambiguous_leaf(service, tmp_path):
    z = repository(tmp_path / "z", "https://example.com/z/project.git")
    a = repository(tmp_path / "a", "https://example.com/a/project.git")
    service.add(str(z))
    service.add(str(a))
    assert [r.repository for r in service.list()] == ["a/project", "z/project"]
    with pytest.raises(RepositoryRegistryError) as error:
        service.inspect("project")
    assert error.value.category == "ambiguous_repository"
    assert service.inspect("a/project").workspace == str(a)


def test_identity_collision_is_not_overwritten(service, tmp_path):
    a = repository(tmp_path / "a", "https://one.example/team/project.git")
    b = repository(tmp_path / "b", "https://two.example/team/project.git")
    first = service.add(str(a))
    with pytest.raises(RepositoryRegistryError) as error:
        service.add(str(b))
    assert error.value.category == "ambiguous_repository"
    assert service.list() == (first,)


@pytest.mark.parametrize(
    "reference",
    ["", "../project", "/tmp/project", "a/../b", "a//b", "-option", "a\\b", "a?secret", "a\nb"],
)
def test_malformed_references_fail(service, reference):
    with pytest.raises(RepositoryRegistryError) as error:
        service.inspect(reference)
    assert error.value.category == "invalid_repository_reference"


@pytest.mark.parametrize(
    ("source", "category"),
    [
        ("../escape", "unsafe_source"),
        ("https://example.com/a/../b", "unsafe_source"),
        ("https://example.com/a/%2e%2e/b", "unsafe_source"),
        ("https://user:secret@example.com/team/repo", "unsafe_source"),
        ("https://secret@example.com/team/repo", "unsafe_source"),
        ("ssh://git:secret@example.com/team/repo", "unsafe_source"),
        ("https://example.com/team/repo?token=secret", "unsafe_source"),
        ("https://example.com/team/repo#secret", "unsafe_source"),
        ("https://example.com:bad/team/repo", "unsafe_source"),
        ("https://example.com:0/team/repo", "unsafe_source"),
        ("file://server/path", "unsafe_source"),
        ("ftp://example.com/team/repo", "unsupported_source"),
        ("ext::sh command", "unsupported_source"),
        ("--upload-pack=evil", "unsupported_source"),
        ("https://example.com/a\nb", "unsafe_source"),
    ],
)
def test_source_safety_before_writes(service, source, category):
    with pytest.raises(RepositoryRegistryError) as error:
        service.add(source)
    assert error.value.category == category
    assert "secret" not in str(error.value)
    assert not service.registry.path.exists()


def test_local_paths_require_explicit_allowed_root(service, tmp_path):
    with pytest.raises(RepositoryRegistryError) as error:
        service.add(str(tmp_path.parent))
    assert error.value.category == "unsafe_source"
    link = tmp_path / "escape"
    link.symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(RepositoryRegistryError):
        service.add(str(link))
    with pytest.raises(RepositoryRegistryError):
        service.add(tmp_path.parent.as_uri())


def test_missing_source_and_unsupported_provider(service, tmp_path):
    with pytest.raises(RepositoryRegistryError) as error:
        service.add(str(tmp_path / "missing"))
    assert error.value.category == "materialization_failure"
    with pytest.raises(RepositoryRegistryError) as error:
        service.add(str(tmp_path), provider="archive")
    assert error.value.category == "unsupported_source"


def test_missing_workspace_reads_and_repeat_add(service, tmp_path):
    workspace = tmp_path / "plain"
    workspace.mkdir()
    record = service.add(str(workspace))
    workspace.rmdir()
    assert service.present(service.list()[0])["materialization_state"] == "absent"
    for call in (lambda: service.inspect(record.repository), lambda: service.add(str(workspace))):
        with pytest.raises(RepositoryRegistryError) as error:
            call()
        assert error.value.category == "missing_materialization"


def test_git_real_materialization_reuse_and_worktree_cleanup(service, tmp_path, monkeypatch):
    source = repository(tmp_path / "remote")
    record = service.add(source.as_uri())
    assert record.provider == "git"
    assert record.revision == git(source, "rev-parse", "HEAD")
    assert record.branch == "main"
    assert record.workspace is None
    assert Path(record.cache_path).is_dir()
    assert list((service.git.cache_root / "worktrees").iterdir()) == []
    # A subsequent consumer uses the registered source/revision with the existing provider.
    with service.git.materialize(record.source_request()) as materialized:
        assert materialized.repository == record.repository
        assert materialized.revision == record.revision
        assert (materialized.workspace / "README.md").is_file()
    monkeypatch.setattr(
        service.git, "_run", lambda *_: pytest.fail("read/repeat add must not run Git")
    )
    assert service.add(source.as_uri()) == record
    assert service.inspect(record.repository) == record
    assert service.list() == (record,)


def test_git_failure_is_sanitized_and_not_registered(service, monkeypatch):
    def fail(*_):
        raise GitRepositorySourceError("stderr containing secret")

    monkeypatch.setattr(service.git, "materialize", fail)
    with pytest.raises(RepositoryRegistryError) as error:
        service.add("https://example.com/team/repo.git")
    assert error.value.category == "materialization_failure"
    assert "secret" not in str(error.value)
    assert service.list() == ()


def test_git_url_spellings_reuse_cache_identity(service, monkeypatch):
    @contextmanager
    def materialize(request):
        mirror = service.git.cache_path_for(str(request.source))
        mirror.mkdir(parents=True)
        yield MaterializedRepository(
            mirror, "team/repo", "main", "a" * 40, "git", "git:team/repo@" + "a" * 40
        )

    monkeypatch.setattr(service.git, "materialize", materialize)
    record = service.add("https://example.com/team/repo.git")
    monkeypatch.setattr(service.git, "materialize", lambda *_: pytest.fail("not idempotent"))
    assert service.add("https://example.com/team/repo/") == record


@pytest.mark.parametrize("target", ["root", "git", "mirror", "worktrees", "registry"])
def test_unsafe_cache_targets_fail_before_materialization(tmp_path, target):
    outside = tmp_path / "outside"
    outside.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    source = "https://example.com/team/repo.git"
    service = RepositoryApplicationService(cache_root=cache, allowed_roots=(tmp_path,))
    if target == "root":
        cache.rmdir()
        cache.symlink_to(outside, target_is_directory=True)
    elif target == "registry":
        service.registry.path.symlink_to(outside / "registry.json")
    else:
        service.git.cache_root.mkdir()
        path = {
            "git": service.git.cache_root,
            "mirror": service.git.cache_path_for(source),
            "worktrees": service.git.cache_root / "worktrees",
        }[target]
        if path == service.git.cache_root:
            path.rmdir()
        path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RepositoryRegistryError) as error:
        service.add(source)
    assert error.value.category == "unsafe_source"
    assert list(outside.iterdir()) == []


def test_existing_git_mirror_cannot_redirect_fetch(service, tmp_path):
    source = repository(tmp_path / "source")
    mirror = service.git.cache_path_for(source.as_uri())
    mirror.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "clone", "--mirror", source.as_uri(), str(mirror)], check=True, capture_output=True
    )
    git(mirror, "remote", "set-url", "origin", "https://wrong.example/repo")
    with pytest.raises(RepositoryRegistryError) as error:
        service.add(source.as_uri())
    assert error.value.category == "materialization_failure"
    assert service.list() == ()


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        "[]",
        '{"version":2,"repositories":[]}',
        '{"version":true,"repositories":[]}',
        '{"version":1,"repositories":[{}]}',
    ],
)
def test_corrupt_registry_fails_closed(service, payload):
    service.registry.path.parent.mkdir(parents=True)
    service.registry.path.write_text(payload)
    with pytest.raises(RepositoryRegistryError) as error:
        service.list()
    assert error.value.category == "registry_persistence_failure"
    assert service.registry.path.read_text() == payload


def test_corrupt_source_never_leaks_credentials(service, tmp_path):
    source = tmp_path / "plain"
    source.mkdir()
    service.add(str(source))
    raw = json.loads(service.registry.path.read_text())
    raw["repositories"][0]["source"] = "https://user:secret@example.com/repo"
    service.registry.path.write_text(json.dumps(raw))
    with pytest.raises(RepositoryRegistryError) as error:
        service.list()
    assert "secret" not in str(error.value)


def test_atomic_write_failure_preserves_previous_state(service, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    service.add(str(a))
    before = service.registry.path.read_bytes()

    def fail(*_):
        raise OSError("disk failure")

    monkeypatch.setattr("fetchcore.repository.registry.os.replace", fail)
    with pytest.raises(RepositoryRegistryError) as error:
        service.add(str(b))
    assert error.value.category == "registry_persistence_failure"
    assert service.registry.path.read_bytes() == before
    assert [p.name for p in service.registry.path.parent.iterdir()] == ["registry.json"]


def test_competing_writer_fails_explicitly(service, tmp_path):
    source = tmp_path / "plain"
    source.mkdir()
    with service.registry.writer():
        with pytest.raises(RepositoryRegistryError) as error:
            service.add(str(source))
        assert error.value.category == "registry_persistence_failure"
    assert service.list() == ()


def test_local_source_request_reobserves_head_instead_of_mislabeling_new_content(service, tmp_path):
    from fetchcore.repository.sources import LocalWorkspaceSourceProvider

    workspace = repository(tmp_path / "local")
    record = service.add(str(workspace))
    git(workspace, "commit", "--allow-empty", "-m", "New HEAD")
    with LocalWorkspaceSourceProvider().materialize(record.source_request()) as snapshot:
        assert snapshot.repository == record.repository
        assert snapshot.revision != record.revision
        assert snapshot.revision == git(workspace, "rev-parse", "HEAD")


def test_missing_git_mirror_is_not_refetched(service, tmp_path):
    import shutil

    source = repository(tmp_path / "remote")
    record = service.add(source.as_uri())
    shutil.rmtree(record.cache_path)
    assert service.present(service.list()[0])["materialization_state"] == "absent"
    with pytest.raises(RepositoryRegistryError) as error:
        service.add(source.as_uri())
    assert error.value.category == "missing_materialization"
    assert not Path(record.cache_path).exists()


def test_local_symlink_loop_is_a_typed_failure(service, tmp_path):
    path = tmp_path / "loop"
    path.symlink_to(path)
    with pytest.raises(RepositoryRegistryError) as error:
        service.add(str(path))
    assert error.value.category == "unsafe_source"


@pytest.mark.parametrize("change", ["duplicate", "cache", "unknown_field", "source_ref"])
def test_persisted_contract_corruption_fails_closed(service, tmp_path, change):
    source = tmp_path / "plain"
    source.mkdir()
    service.add(str(source))
    raw = json.loads(service.registry.path.read_text())
    if change == "duplicate":
        raw["repositories"].append(raw["repositories"][0].copy())
    elif change == "cache":
        raw["repositories"][0]["cache_path"] = "/outside"
    elif change == "unknown_field":
        raw["repositories"][0]["alias"] = "new-identity"
    else:
        raw["repositories"][0]["source_ref"] = "file:///outside"
    service.registry.path.write_text(json.dumps(raw))
    with pytest.raises(RepositoryRegistryError) as error:
        service.list()
    assert error.value.category == "registry_persistence_failure"


def test_inspect_git_file_source_rechecks_allowed_roots(service, tmp_path):
    source = repository(tmp_path / "remote")
    record = service.add(source.as_uri())
    restricted = RepositoryApplicationService(
        cache_root=tmp_path / "cache",
        allowed_roots=(tmp_path / "other",),
    )
    with pytest.raises(RepositoryRegistryError) as error:
        restricted.inspect(record.repository)
    assert error.value.category == "unsafe_source"
