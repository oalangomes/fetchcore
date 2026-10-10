from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci" / "docker-pull.sh"
PINNED_IMAGE = "registry.example/fetchcore@sha256:" + ("a" * 64)


def _fake_docker(tmp_path: Path, outcomes: list[str]) -> tuple[Path, Path]:
    state = tmp_path / "attempts"
    executable = tmp_path / "docker"
    encoded = "|".join(outcomes)
    executable.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        f"state={state!s}\n"
        f"outcomes={encoded!r}\n"
        'attempt="$(cat "$state" 2>/dev/null || printf 0)"\n'
        'attempt=$((attempt + 1))\n'
        'printf "%s" "$attempt" > "$state"\n'
        "IFS='|' read -r -a values <<< \"$outcomes\"\n"
        'outcome="${values[$((attempt - 1))]}"\n'
        'if [[ "$outcome" == success ]]; then echo pulled; exit 0; fi\n'
        'printf "%s\\n" "$outcome" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, state


def _run(tmp_path: Path, outcomes: list[str], image: str = PINNED_IMAGE) -> tuple[subprocess.CompletedProcess[str], int]:
    docker, state = _fake_docker(tmp_path, outcomes)
    env = {
        **os.environ,
        "DOCKER_BIN": str(docker),
        "FETCHCORE_DOCKER_PULL_BASE_DELAY_SECONDS": "0",
    }
    completed = subprocess.run(
        [str(SCRIPT), image],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    attempts = int(state.read_text(encoding="utf-8")) if state.exists() else 0
    return completed, attempts


def test_pull_rejects_mutable_image_reference(tmp_path: Path) -> None:
    completed, attempts = _run(tmp_path, ["success"], image="solr:9.10.1")

    assert completed.returncode == 2
    assert attempts == 0
    assert "pinned by sha256" in completed.stderr


def test_pull_retries_transient_failures_then_succeeds(tmp_path: Path) -> None:
    completed, attempts = _run(
        tmp_path,
        ["504 Gateway Timeout", "context deadline exceeded", "success"],
    )

    assert completed.returncode == 0
    assert attempts == 3
    assert completed.stderr.count("transient registry failure") == 2


def test_pull_does_not_retry_permanent_failure(tmp_path: Path) -> None:
    completed, attempts = _run(tmp_path, ["unauthorized: access denied", "success"])

    assert completed.returncode == 1
    assert attempts == 1
    assert "without further retry" in completed.stderr


def test_pull_stops_after_bounded_attempts(tmp_path: Path) -> None:
    completed, attempts = _run(tmp_path, ["HTTP 503", "HTTP 503", "HTTP 503"])

    assert completed.returncode == 1
    assert attempts == 3


def test_container_inputs_are_immutable_and_match_dockerfile() -> None:
    values = {}
    for line in (ROOT / "infra" / "images.lock").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, value = line.split("=", 1)
            values[name] = value.strip("'\"")

    assert set(values) == {"FETCHCORE_PYTHON_IMAGE", "FETCHCORE_SOLR_IMAGE"}
    assert all("@sha256:" in value for value in values.values())
    assert values["FETCHCORE_PYTHON_IMAGE"] in (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert values["FETCHCORE_PYTHON_IMAGE"] in (
        ROOT / "tools" / "explorer" / "docker" / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert values["FETCHCORE_SOLR_IMAGE"] in (
        ROOT / "infra" / "compose" / "solr.yml"
    ).read_text(encoding="utf-8")
    assert "/opt/solr/server/solr/configsets/fetchcore:ro" in (
        ROOT / "infra" / "compose" / "solr.yml"
    ).read_text(encoding="utf-8")
    assert values["FETCHCORE_SOLR_IMAGE"] in (
        ROOT / ".github" / "workflows" / "repository-index.yml"
    ).read_text(encoding="utf-8")

    for path in (
        ROOT / ".env.example",
        ROOT / "infra" / "compose" / "app.yml",
        ROOT / "infra" / "compose" / "solr.yml",
        ROOT / "tools" / "explorer" / ".env.example",
        ROOT / "tools" / "explorer" / "compose.yaml",
    ):
        assert "fetchcore-lab" not in path.read_text(encoding="utf-8")


def test_workflows_are_valid_yaml_and_preserve_ci_boundaries() -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    for path in workflow_dir.glob("*.yml"):
        assert yaml.compose(path.read_text(encoding="utf-8")) is not None

    self_dogfood = (workflow_dir / "self-dogfood.yml").read_text(encoding="utf-8")
    operational_index = (workflow_dir / "operational-index.yml").read_text(encoding="utf-8")
    localization = (workflow_dir / "docs-localization.yml").read_text(encoding="utf-8")
    ci = (workflow_dir / "ci.yml").read_text(encoding="utf-8")
    explorer = (workflow_dir / "explorer.yml").read_text(encoding="utf-8")
    repository_index = (workflow_dir / "repository-index.yml").read_text(encoding="utf-8")

    assert "pull_request:" in self_dogfood
    assert "workflow_run:" not in self_dogfood
    assert '["ubuntu-latest"]' in self_dogfood
    assert "fetchcore-self-dogfood" in self_dogfood
    self_inputs = yaml.safe_load(self_dogfood)["jobs"]["self-index"]["with"]
    assert self_inputs["create_if_missing"] is True
    assert self_inputs["solr_provision_mode"] == "provision_if_missing"
    assert self_inputs["index_lifecycle"] == "rebuild"
    assert self_inputs["evaluate"] is True

    assert "workflow_run:" in operational_index
    assert "github.event.workflow_run.conclusion == 'success'" in operational_index
    assert "github.event.workflow_run.event == 'push'" in operational_index
    assert "head_repository.full_name == github.repository" in operational_index
    assert operational_index.count("github.event.workflow_run.head_sha || github.sha") == 2
    assert '["self-hosted","Linux","X64","fetchcore"]' in operational_index
    operational_inputs = yaml.safe_load(operational_index)["jobs"]["operational-index"]["with"]
    assert operational_inputs["collection"] == "fetchcore"
    assert operational_inputs["create_if_missing"] is False
    assert operational_inputs["solr_provision_mode"] == "reuse_only"
    assert operational_inputs["index_lifecycle"] == "sync"
    assert operational_inputs["evaluate"] is False

    assert "FETCHCORE_DOCS_LOCALIZATION_ENABLED == 'true'" in localization
    assert "Validate localization configuration" in localization
    assert ci.count("scripts/ci/docker-pull.sh") == 1
    assert "dogfood-smoke:" not in ci
    assert explorer.count("scripts/ci/docker-pull.sh") == 3
    assert "fetchcore-lab" not in explorer
    assert "workflow_call:" in repository_index
    assert "workflow_dispatch:" in repository_index
    assert "default: reuse_only" in repository_index
    assert "group: fetchcore-solr-resource-${{ inputs.collection }}" in repository_index
    assert "inputs.target_repository || github.repository" not in repository_index.split(
        "concurrency:", 1
    )[1].split("jobs:", 1)[0]
    assert "docker run" not in repository_index
    assert repository_index.count("fetchcore index \\") == 5
    assert 'sync "$REPOSITORY" --json' in repository_index
    assert 'rebuild "$REPOSITORY" --json' in repository_index
    assert "unavailable until #130" in repository_index


@pytest.mark.parametrize(
    ("input_name", "value", "message"),
    [
        ("SOLR_PORT", "8983", "solr_port is deprecated"),
        ("SOLR_VOLUME", "fetchcore-index-solr", "solr_volume is deprecated"),
    ],
)
def test_workflow_rejects_deprecated_inputs_with_actionable_error(
    input_name: str, value: str, message: str,
) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "repository-index.yml").read_text(encoding="utf-8")
    )
    policy = next(
        step["run"] for step in workflow["jobs"]["index"]["steps"]
        if step.get("name") == "Validate workflow policy"
    )
    env = {
        **os.environ,
        "DEPLOYMENT_MODE": "standalone",
        "PROVISION_MODE": "reuse_only",
        "INDEX_LIFECYCLE": "sync",
        "EVALUATION_MODES_JSON": '["lexical"]',
        "TARGET_REPOSITORY": "oalangomes/fetchcore",
        "SOLR_PORT": "",
        "SOLR_VOLUME": "",
    }
    accepted = subprocess.run(
        ["bash", "-c", policy], env=env, capture_output=True, text=True, check=False,
    )
    assert accepted.returncode == 0, accepted.stderr

    env[input_name] = value
    rejected = subprocess.run(
        ["bash", "-c", policy], env=env, capture_output=True, text=True, check=False,
    )
    assert rejected.returncode == 2
    assert message in rejected.stderr
