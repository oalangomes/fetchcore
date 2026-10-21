"""Developer skill invariants: read-only metadata, canonical policy and precise impact."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from fetchcore.repository.indexing import DEFAULT_EXCLUDE_PATTERNS


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills/fetchcore-corpus-discovery/scripts/inventory.py"
spec = importlib.util.spec_from_file_location("corpus_discovery_inventory", SCRIPT)
assert spec and spec.loader
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


def write(root: Path, relative: str, text: str = "technical evidence\n") -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-q")
    write(root, "src/main.py", "def main():\n    return 1\n")
    write(root, "docs/architecture.md", "# Architecture\nA contract.\n")
    write(root, "tests/test_main.py", "def test_main():\n    assert True\n")
    write(root, "studies/runner.py", "# Authored harness\n")
    write(root, "studies/README.md", "# Protocol\n")
    git(root, "add", ".")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "fixture")
    return root


def rows(report: dict) -> dict:
    return {r["path"]: r for r in report["paths"]}


def snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def test_no_policy_uses_canonical_defaults_and_deterministic_metadata(repo: Path) -> None:
    report = inventory.discover(repo)
    assert report == inventory.discover(repo)
    assert report["policy"]["source"] is None
    assert report["policy"]["validation"] == "valid"
    assert report["revision"]
    assert rows(report)["src/main.py"]["admission"] == "eligible_by_metadata"
    assert set(DEFAULT_EXCLUDE_PATTERNS) <= set(report["effective_selection"]["exclude"])
    for path in ("docs/architecture.md", "tests/test_main.py", "studies/runner.py", "studies/README.md"):
        assert rows(report)[path]["admission"] == "eligible_by_metadata"
        assert rows(report)[path]["classification"]["status"] == "needs_review"


def test_tracked_ignore_matches_are_still_eligible(repo: Path) -> None:
    write(repo, "results/tracked.json", "{}\n")
    git(repo, "add", "results/tracked.json")
    write(repo, ".gitignore", "results/*.json\ncache/\n")
    write(repo, "results/untracked.json", "{}\n")
    write(repo, "cache/ignored.py")
    observed = rows(inventory.discover(repo))
    tracked = observed["results/tracked.json"]
    assert tracked["matches_gitignore"] is True
    assert tracked["git_state"] == "tracked"
    assert tracked["admission"] == "eligible_by_metadata"
    assert observed["results/untracked.json"]["admission"] == "git_ignored_untracked"
    assert observed["cache"]["file_state"] == "directory_or_special"
    assert observed["cache"]["bytes"] is None


def test_existing_policy_is_preserved_with_minimal_external_proposal(repo: Path, tmp_path: Path) -> None:
    original = (ROOT / ".fetchcore/indexing.yaml").read_text()
    write(repo, ".fetchcore/indexing.yaml", original)
    artifact = "studies/repository-learning/.artifacts/run/direct.json"
    write(repo, artifact, '{"generated": true}\n')
    proposal = write(tmp_path, "proposal.yaml", original.replace(
        '    - "dogfood/scenarios/**"',
        '    - "dogfood/scenarios/**"\n    - "studies/repository-learning/.artifacts/**/*.json"',
    ))
    before = snapshot(repo)
    report = inventory.review(repo, proposal)
    assert snapshot(repo) == before
    assert report["impact"]["removed_eligible_paths"] == [artifact]
    assert report["impact"]["added_eligible_paths"] == []
    assert report["impact"]["removed_observed_bytes"] == len('{"generated": true}\n')
    assert report["changed_config_fields"] == ["exclude_patterns"]
    assert report["existing_exclusions_removed"] == []
    assert report["existing_includes_removed"] == []
    assert report["applied"] is False
    assert report["review_status"] == "needs_human_review"
    assert '+    - "studies/' in report["policy_diff"]
    assert rows(report)["studies/README.md"]["proposed_admission"] == "eligible_by_metadata"
    assert rows(report)[artifact]["classification"]["category"] == "derived_candidate"


def test_broad_excludes_make_valuable_knowledge_loss_visible(repo: Path, tmp_path: Path) -> None:
    proposal = write(tmp_path, "proposal.yaml", "version: 1\nselection:\n  exclude: [docs/**, tests/**]\n")
    report = inventory.review(repo, proposal)
    assert report["impact"]["removed_eligible_paths"] == ["docs/architecture.md", "tests/test_main.py"]
    assert report["review_status"] == "needs_human_review"


def test_sensitive_and_symlink_contents_are_not_acquired(repo: Path, tmp_path: Path, monkeypatch) -> None:
    write(repo, "config/credentials.json", "DO_NOT_READ_SENSITIVE")
    write(repo, ".env", "DO_NOT_READ_ENV")
    external = write(tmp_path, "outside.py", "DO_NOT_READ_EXTERNAL")
    (repo / "link.py").symlink_to(external)
    (repo / "alias").symlink_to(external.parent, target_is_directory=True)
    read_text = Path.read_text

    def guarded_read(path: Path, *args, **kwargs):
        assert path.name not in {"credentials.json", ".env", "outside.py", "link.py"}
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    report = inventory.discover(repo)
    assert rows(report)["link.py"]["admission"] == "needs_review"
    assert rows(report)["config/credentials.json"]["classification"]["category"] == "sensitive"
    assert "DO_NOT_READ" not in json.dumps(report)


def test_mandatory_exclusions_survive_explicit_includes(repo: Path) -> None:
    write(repo, ".fetchcore/indexing.yaml", "version: 1\nselection:\n  include: ['*']\n  exclude: []\n")
    write(repo, "generated/schema.py")
    write(repo, "node_modules/library.js")
    report = inventory.discover(repo)
    assert rows(report)["generated/schema.py"]["admission"] == "excluded"
    assert rows(report)["node_modules/library.js"]["admission"] == "excluded"


@pytest.mark.parametrize("text", [
    "version: [\n", "version: 99\n", "unsupported: true\n", "selection: {unknown: true}\n",
    "corpus: {origin: [{pattern: '*', value: magic}]}\n",
    "chunking: {max_lines: 10, overlap_lines: 10}\n",
    "selection: {replace_default_includes: true}\n",
    "extractors: {enabled: [nonexistent-language]}\n",
])
def test_invalid_proposal_fails_canonical_validation(repo: Path, tmp_path: Path, text: str) -> None:
    proposal = write(tmp_path, "invalid.yaml", text)
    with pytest.raises(ValueError):
        inventory.review(repo, proposal)


def test_invalid_existing_policy_does_not_become_defaults(repo: Path, tmp_path: Path) -> None:
    write(repo, ".fetchcore/indexing.yaml", "version: 300\n")
    report = inventory.discover(repo)
    assert report["policy"]["validation"] == "invalid"
    assert report["effective_selection"] is None
    assert all(r["admission"] == "unknown" for r in report["paths"])
    with pytest.raises(ValueError, match="current policy validation"):
        inventory.review(repo, write(tmp_path, "valid.yaml", "version: 1\n"))


def test_unknown_unreadable_and_generated_technical_sources_remain_reviewable(repo: Path) -> None:
    write(repo, "domain.contract")
    unreadable = write(repo, "src/unreadable.py")
    unreadable.chmod(0)
    write(repo, "schemas/autogenerated.ts", "// generated public contract\n")
    try:
        report = inventory.discover(repo)
    finally:
        unreadable.chmod(0o644)
    assert rows(report)["domain.contract"]["classification"]["category"] == "unknown"
    assert rows(report)["domain.contract"]["classification"]["status"] == "needs_review"
    assert rows(report)["src/unreadable.py"]["admission"] == "needs_review"
    assert rows(report)["schemas/autogenerated.ts"]["admission"] == "eligible_by_metadata"


def test_sap_conditional_defaults_yield_to_explicit_policy(repo: Path, tmp_path: Path) -> None:
    write(repo, "manifest.json", '{"commerceSuiteVersion": "2211"}\n')
    write(repo, "resources/types.impex", "# technical import definition\n")
    write(repo, "local.properties", "DO_NOT_READ\n")
    discovered = inventory.discover(repo)
    assert discovered["knowledge_packs"][0]["id"] == "sap.commerce"
    assert discovered["knowledge_packs"][0]["defaults_applied"] is True
    assert rows(discovered)["resources/types.impex"]["admission"] == "eligible_by_metadata"
    proposal = write(tmp_path, "first-policy.yaml", "version: 1\n")
    reviewed = inventory.review(repo, proposal)
    assert "resources/types.impex" in reviewed["impact"]["removed_eligible_paths"]
    assert "*.impex" in reviewed["existing_includes_removed"]
    write(repo, ".fetchcore/indexing.yaml", "version: 1\nselection:\n  include: ['*.impex']\n")
    explicit = inventory.discover(repo)
    assert explicit["knowledge_packs"][0]["defaults_applied"] is False
    assert rows(explicit)["resources/types.impex"]["admission"] == "eligible_by_metadata"
    assert rows(explicit)["local.properties"]["admission"] == "excluded"
    assert explicit["policy"]["resolved"]["limits"]["max_files"] == 1000


def test_read_only_cli_leaves_repository_and_git_index_unchanged(repo: Path, tmp_path: Path) -> None:
    before = snapshot(repo)
    result = subprocess.run([sys.executable, "-B", str(SCRIPT), "discover", str(repo)],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["mode"] == "DISCOVER"
    assert before == snapshot(repo)
    proposal = write(tmp_path, "proposal.yaml", "version: 1\n")
    subprocess.run([sys.executable, "-B", str(SCRIPT), "review", str(repo), "--proposal", str(proposal)],
                   capture_output=True, check=True)
    assert before == snapshot(repo)
    with pytest.raises(ValueError, match="outside"):
        inventory.review(repo, write(repo, "proposal.yaml", "version: 1\n"))


def test_legacy_policy_ambiguity_and_unsafe_policy_fail_closed(repo: Path) -> None:
    write(repo, ".agentsorch/indexing.yaml", "version: 1\nselection: {exclude: [docs/**]}\n")
    assert inventory.discover(repo)["policy"]["source"] == ".agentsorch/indexing.yaml"
    write(repo, ".fetchcore/indexing.yaml", "version: 1\n")
    with pytest.raises(ValueError, match="both"):
        inventory.discover(repo)
    (repo / ".fetchcore/indexing.yaml").unlink()
    (repo / ".fetchcore/indexing.yaml").symlink_to(repo / ".agentsorch/indexing.yaml")
    with pytest.raises(ValueError, match="symlink"):
        inventory.discover(repo)


def test_partial_inventory_is_disclosed_and_plain_directories_work(repo: Path, tmp_path: Path) -> None:
    report = inventory.discover(repo, max_paths=2)
    assert len(report["paths"]) == 2
    assert any("truncated" in item for item in report["limitations"])
    plain = tmp_path / "plain"
    write(plain, "docs/design.md")
    report = inventory.discover(plain)
    assert report["revision"] is None
    assert rows(report)["docs/design.md"]["git_state"] == "unknown"


def test_discover_needs_no_writable_temporary_storage(repo: Path, monkeypatch) -> None:
    # A real Codex read-only sandbox exposed this requirement during forward testing.
    def forbidden(*args, **kwargs):
        raise AssertionError("DISCOVER must not create temporary files")

    monkeypatch.setattr(inventory.tempfile, "TemporaryDirectory", forbidden)
    monkeypatch.setattr(inventory.tempfile, "TemporaryFile", forbidden)
    assert inventory.discover(repo)["policy"]["validation"] == "valid"


def test_inventory_runs_without_fetchcore_or_third_party_packages(repo: Path) -> None:
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(SCRIPT), "discover", str(repo)],
                            capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report["policy"]["validation"] == "unavailable"
    assert rows(report)["src/main.py"]["admission"] == "unknown"
    assert rows(report)["src/main.py"]["bytes"] > 0


def test_nul_delimited_git_paths_preserve_whitespace_and_shell_characters(repo: Path) -> None:
    relative = "src/odd name\n$(never-execute).py"
    write(repo, relative, "# evidence\n")
    git(repo, "add", "--", relative)
    observed = rows(inventory.discover(repo))
    assert observed[relative]["git_state"] == "tracked"
    assert observed[relative]["admission"] == "eligible_by_metadata"


def test_fetchcore_historical_runs_are_distinct_from_harness_and_study_docs(tmp_path: Path) -> None:
    # Use the actual tracked FetchCore paths and payload sizes, not an invented directory layout.
    root = tmp_path / "fetchcore-fixture"
    paths = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z",
                                     "studies/repository-learning"])
    for raw in paths.split(b"\0"):
        if raw:
            relative = raw.decode()
            write(root, relative, (ROOT / relative).read_text(encoding="utf-8"))
    report = inventory.discover(root)
    observed = rows(report)
    prefix = "studies/repository-learning/"
    assert observed[prefix + "run.py"]["classification"]["category"] == "implementation"
    assert observed[prefix + "STUDY.md"]["classification"]["category"] == "technical_document_candidate"
    artifacts = [p for p in observed if "/.artifacts/" in p and p.endswith(".json")]
    assert artifacts
    assert all(observed[p]["classification"]["category"] == "derived_candidate" for p in artifacts)
    proposal = write(tmp_path, "narrow.yaml", "version: 1\nselection:\n  exclude:\n"
                     "    - studies/repository-learning/.artifacts/**/*.json\n")
    reviewed = inventory.review(root, proposal)
    assert reviewed["impact"]["removed_eligible_paths"] == sorted(artifacts)
    assert observed[prefix + "README.md"]["admission"] == "eligible_by_metadata"
    assert any(".artifacts/" in p and p.endswith(".md") for p in observed)


def test_skill_frontmatter_and_relative_references_are_portable() -> None:
    skill = SCRIPT.parents[1] / "SKILL.md"
    frontmatter = yaml.safe_load(skill.read_text().split("---", 2)[1])
    assert frontmatter["name"] == skill.parent.name
    assert isinstance(frontmatter["description"], str)
    assert set(frontmatter) == {"name", "description"}


def test_worked_example_is_a_valid_preserving_canonical_policy() -> None:
    example = json.loads((SCRIPT.parents[1] / "references/fetchcore-example.json").read_text())
    current, current_config = inventory.resolve_policy(example["observations"]["policy"]["yaml"], True, ())
    proposed, proposed_config = inventory.resolve_policy(example["proposal_yaml"], True, ())
    assert set(current_config.exclude_patterns) <= set(proposed_config.exclude_patterns)
    assert current.config.max_files == proposed.config.max_files
    assert len(example["impact"]["removed_eligible_paths"]) == 21
    assert example["applied"] is False


@pytest.mark.parametrize("text", [
    'version: 1\nsecret: [SENSITIVE_SENTINEL\n',
    'version: 1\nunsupported: SENSITIVE_SENTINEL\n',
    'version: 1\nSENSITIVE_SENTINEL: unsupported\n',
])
def test_invalid_policy_values_never_leave_discover_or_cli(repo: Path, text: str) -> None:
    write(repo, ".fetchcore/indexing.yaml", text)
    before = snapshot(repo)
    report = inventory.discover(repo)
    assert report["policy"]["validation"] == "invalid"
    assert report["policy"]["yaml"] is None
    assert report["policy"]["yaml_status"] == "withheld_invalid"
    assert report["policy"]["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert report["policy"]["error_category"] == "policy_validation_failed"
    assert report["policy"]["error_type"]
    assert "SENSITIVE_SENTINEL" not in json.dumps(report)
    result = subprocess.run([sys.executable, "-B", str(SCRIPT), "discover", str(repo)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert json.loads(result.stdout)["policy"]["validation"] == "invalid"
    assert "SENSITIVE_SENTINEL" not in result.stdout + result.stderr
    assert snapshot(repo) == before


def test_valid_policy_text_is_available_without_changing_original_yaml(repo: Path) -> None:
    text = "# Retain technical docs\nversion: 1\nselection:\n  include: ['*.adoc']\n"
    write(repo, ".fetchcore/indexing.yaml", text)
    report = inventory.discover(repo)
    assert report["policy"]["validation"] == "valid"
    assert report["policy"]["yaml"] == text
    assert report["policy"]["yaml_status"] == "visible"
    assert report["effective_selection"]


@pytest.mark.parametrize("where", ["current", "proposal", "both"])
def test_sensitive_valid_yaml_is_withheld_from_every_policy_surface(
    repo: Path, tmp_path: Path, where: str,
) -> None:
    # Even schema-valid strings or comments can contain credentials.
    text = "version: 1\nselection:\n  exclude: ['private/password=SENSITIVE_SENTINEL']\n"
    if where in {"current", "both"}:
        write(repo, ".fetchcore/indexing.yaml", text)
    proposal = write(tmp_path, "external.yaml", text if where in {"proposal", "both"} else "version: 1\n")
    report = inventory.review(repo, proposal)
    assert report["policy"]["validation"] == "valid"
    assert report["policy_diff"] is None
    assert report["policy_diff_status"] == "withheld_potentially_sensitive"
    assert report["existing_exclusions_removed"] is None
    assert report["existing_includes_removed"] is None
    if where != "proposal":
        assert report["policy"]["yaml"] is None
        assert "resolved" not in report["policy"]
        assert report["effective_selection"] is None
    if where != "current":
        assert report["proposal_yaml"] is None
    assert "SENSITIVE_SENTINEL" not in json.dumps(report)
    assert report["impact"]["removed_eligible_paths"] == []


def test_policy_comment_credentials_and_unvalidated_yaml_are_not_rendered(repo: Path) -> None:
    write(repo, ".fetchcore/indexing.yaml", "version: 1\n# api_key=SENSITIVE_SENTINEL\n")
    report = inventory.discover(repo)
    assert report["policy"]["yaml"] is None
    assert "SENSITIVE_SENTINEL" not in json.dumps(report)
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(SCRIPT), "discover", str(repo)],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["policy"]["yaml_status"] == "withheld_unavailable"
    assert "SENSITIVE_SENTINEL" not in result.stdout + result.stderr


def test_invalid_external_proposal_does_not_leak_error_values(repo: Path, tmp_path: Path) -> None:
    proposal = write(tmp_path, "external.yaml", "version: SENSITIVE_SENTINEL\n")
    with pytest.raises(ValueError) as error:
        inventory.review(repo, proposal)
    assert "SENSITIVE_SENTINEL" not in str(error.value)
    result = subprocess.run([sys.executable, "-B", str(SCRIPT), "review", str(repo), "--proposal", str(proposal)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "SENSITIVE_SENTINEL" not in result.stdout + result.stderr


def test_clean_git_working_tree_distinguishes_head_from_workspace(repo: Path) -> None:
    report = inventory.discover(repo)
    assert report["working_tree_dirty"] is False
    assert report["working_tree"]["available"] is True
    assert report["working_tree"]["complete"] is True
    assert report["working_tree"]["head_represents_workspace"] is True
    assert report["working_tree"]["tracked_changes"] == []


@pytest.mark.parametrize("staged", [False, True])
def test_same_size_tracked_edit_changes_dirty_observation_without_touching_index(repo: Path, staged: bool) -> None:
    clean = inventory.discover(repo)
    original = (repo / "src/main.py").read_text()
    write(repo, "src/main.py", original.replace("return 1", "return 2"))
    if staged:
        git(repo, "add", "src/main.py")
    before = snapshot(repo)
    report = inventory.discover(repo)
    assert snapshot(repo) == before
    assert report != clean
    assert rows(report)["src/main.py"]["bytes"] == rows(clean)["src/main.py"]["bytes"]
    assert report["revision"] == clean["revision"]
    assert report["working_tree_dirty"] is True
    assert report["working_tree"]["head_represents_workspace"] is False
    change = report["working_tree"]["tracked_changes"][0]
    assert change == {"path": "src/main.py", "index_status": "M" if staged else " ",
                      "workspace_status": " " if staged else "M"}
    assert report["working_tree"]["staged_paths"] == (["src/main.py"] if staged else [])
    assert report["working_tree"]["unstaged_paths"] == ([] if staged else ["src/main.py"])


def test_combined_staged_and_unstaged_changes_are_both_reported(repo: Path) -> None:
    write(repo, "src/main.py", "# staged\n")
    git(repo, "add", "src/main.py")
    write(repo, "src/main.py", "# staged plus unstaged\n")
    report = inventory.discover(repo)
    assert report["working_tree"]["staged_paths"] == ["src/main.py"]
    assert report["working_tree"]["unstaged_paths"] == ["src/main.py"]


def test_added_removed_renamed_paths_and_nul_status_parsing(repo: Path) -> None:
    renamed = "src/new name\n$(do-not-run).py"
    git(repo, "mv", "src/main.py", renamed)
    write(repo, "added source.py", "# new\n")
    git(repo, "add", "added source.py")
    (repo / "docs/architecture.md").unlink()
    before = snapshot(repo)
    report = inventory.discover(repo)
    assert snapshot(repo) == before
    changes = {change["path"]: change for change in report["working_tree"]["tracked_changes"]}
    assert changes[renamed]["original_path"] == "src/main.py"
    assert changes[renamed]["index_status"] == "R"
    assert changes["added source.py"]["index_status"] == "A"
    assert changes["docs/architecture.md"]["workspace_status"] == "D"
    assert report["working_tree_dirty"] is True


def test_unborn_head_has_status_but_never_claims_workspace_equals_head(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    write(tmp_path, "new.py", "# new\n")
    git(tmp_path, "add", "new.py")
    report = inventory.discover(tmp_path)
    assert report["revision"] is None
    assert report["working_tree_dirty"] is True
    assert report["working_tree"]["head_represents_workspace"] is False
    assert report["working_tree"]["staged_paths"] == ["new.py"]


def test_git_status_failure_is_unknown_not_clean(repo: Path, monkeypatch) -> None:
    original = inventory.git

    def unavailable(root, command, *args, **kwargs):
        if command == "status":
            raise ValueError("unavailable")
        return original(root, command, *args, **kwargs)

    monkeypatch.setattr(inventory, "git", unavailable)
    report = inventory.discover(repo)
    assert report["working_tree_dirty"] is None
    assert report["working_tree"]["complete"] is False
    assert report["working_tree"]["incompleteness_reasons"] == ["git_status_unavailable"]
    assert report["working_tree"]["head_represents_workspace"] is False


def test_complete_and_partial_review_have_structured_observation_scope(repo: Path, tmp_path: Path) -> None:
    proposal = write(tmp_path, "external.yaml", "version: 1\nselection:\n  exclude: ['docs/**']\n")
    full = inventory.review(repo, proposal)
    assert full["observation"]["complete"] is True
    assert full["observation"]["unvisited_entries"] == 0
    assert full["impact"]["complete"] is True
    assert full["impact"]["scope"] == "repository_paths"
    assert full["impact"]["incompleteness_reasons"] == []
    partial = inventory.review(repo, proposal, max_paths=1)
    assert partial["observation"]["complete"] is False
    assert partial["observation"]["unvisited_entries"] == 4
    assert partial["impact"]["complete"] is False
    assert partial["impact"]["scope"] == "observed_paths"
    assert partial["impact"]["incompleteness_reasons"] == ["inventory_limit_reached"]
    assert partial["summary"]["scope"] == "observed_paths"
    assert partial["impact"]["removed_eligible_paths"] == ["docs/architecture.md"]
    assert any("observed paths only" in item for item in partial["limitations"])


def test_ignored_directories_remain_unknown_not_invented_file_totals(repo: Path, tmp_path: Path) -> None:
    write(repo, ".gitignore", "cache/\n")
    write(repo, "cache/deep/hidden.json", "{}\n")
    proposal = write(tmp_path, "external.yaml", "version: 1\n")
    report = inventory.review(repo, proposal)
    assert report["observation"]["complete"] is False
    assert report["observation"]["collapsed_ignored_directories"] == ["cache"]
    assert "ignored_directories_collapsed" in report["impact"]["incompleteness_reasons"]
    assert report["impact"]["complete"] is False
    assert rows(report)["cache"]["bytes"] is None
    assert "cache/deep/hidden.json" not in rows(report)
    assert report["working_tree"]["head_represents_workspace"] is False


@pytest.mark.parametrize("by_directories", [False, True])
def test_non_git_traversal_limits_have_unknown_unvisited_counts(tmp_path: Path, by_directories: bool) -> None:
    root = tmp_path / "plain"
    if by_directories:
        (root / "a").mkdir(parents=True)
        (root / "b").mkdir()
    else:
        write(root, "a.py")
        write(root, "b.py")
    report = inventory.discover(root, max_paths=1)
    reason = "directory_limit_reached" if by_directories else "inventory_limit_reached"
    assert report["observation"]["complete"] is False
    assert report["observation"]["unvisited_entries"] is None
    assert report["observation"]["enumerated_entries"] is None
    assert reason in report["observation"]["incompleteness_reasons"]
    assert report["working_tree_dirty"] is None
    assert report["working_tree"]["head_represents_workspace"] is False


def test_metadata_read_limitations_are_distinct_from_enumeration(repo: Path) -> None:
    unreadable = write(repo, "src/noaccess.py")
    unreadable.chmod(0)
    try:
        report = inventory.discover(repo)
    finally:
        unreadable.chmod(0o644)
    assert report["observation"]["enumeration_complete"] is True
    assert report["observation"]["complete"] is False
    assert report["observation"]["metadata_unavailable_paths"] == ["src/noaccess.py"]
    assert report["observation"]["content_inspection"] == "not_performed"


def test_real_dirty_invalid_policy_command_is_read_only_and_fail_closed(repo: Path) -> None:
    write(repo, ".fetchcore/indexing.yaml", "version: 1\nunsupported: SENSITIVE_SENTINEL\n")
    write(repo, "src/main.py", "def main():\n    return 2\n")
    before = snapshot(repo)
    result = subprocess.run([sys.executable, "-B", str(SCRIPT), "discover", str(repo)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["working_tree_dirty"] is True
    assert report["working_tree"]["unstaged_paths"] == ["src/main.py"]
    assert report["working_tree"]["head_represents_workspace"] is False
    assert report["policy"]["validation"] == "invalid"
    assert "SENSITIVE_SENTINEL" not in result.stdout + result.stderr
    assert snapshot(repo) == before


def test_non_git_complete_and_pruned_observations_use_distinct_scopes(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    write(root, "docs/technical.md")
    complete = inventory.discover(root)
    assert complete["observation"]["complete"] is True
    assert complete["observation"]["enumerated_entries"] == 1
    assert complete["observation"]["unvisited_entries"] == 0
    write(root, ".venv/dependency.py")
    partial = inventory.discover(root)
    assert partial["observation"]["complete"] is False
    assert partial["observation"]["unvisited_entries"] is None
    assert partial["observation"]["untraversed_directories"] == [".venv"]
    assert partial["observation"]["incompleteness_reasons"] == ["directory_pruned"]
