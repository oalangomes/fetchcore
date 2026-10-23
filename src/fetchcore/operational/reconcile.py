"""Explicit OPERATIONAL Compose serving binding reconciliation.

This operator command reads the active snapshot through application readiness.
It never indexes, changes repository policy, or mutates a backend resource.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import tempfile

import yaml

from fetchcore.application.readiness import ReadinessReport, inspect_readiness
from fetchcore.application.runtime_config import (
    OllamaRuntimeSettings, load_runtime_config,
)
from fetchcore.application.runtime_inspection import RuntimeInspectionService
from fetchcore.retrieval.contracts import KnowledgeSearchError


_FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
_CONTAINER_CONFIG = "/etc/fetchcore/operational-mcp.yml"


def _rejection_category(report: ReadinessReport, revision: str, repository: str) -> str:
    if report.repository != repository:
        return "repository_binding_mismatch"
    # Prefer observed causes over checks that could not run after a probe failed.
    for name in ("service", "schema", "snapshot", "indexing_fingerprint",
                 "embedding", "retrieval"):
        fact = report.checks.get(name)
        if (fact is not None and fact.state not in {"ready", "not_required"}
                and fact.category not in {None, "not_checked", "probe_blocked"}):
            return fact.category
    if report.indexed_revision is not None and report.indexed_revision != revision:
        return "snapshot_revision_mismatch"
    fact = report.checks.get("currentness")
    if (fact is not None and fact.state not in {"ready", "not_required"}
            and fact.category not in {None, "not_checked", "probe_blocked"}):
        return fact.category
    return "currentness_unproven"


def materialized_head(workspace: Path) -> str:
    """Return the authorized workspace's exact HEAD, with no fetch or checkout."""
    toplevel = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=workspace,
        capture_output=True, text=True, check=True, timeout=10,
    ).stdout.strip()
    if Path(toplevel).resolve(strict=True) != workspace.resolve(strict=True):
        raise ValueError("materialization_unavailable: workspace must be the Git root")
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=workspace,
        capture_output=True, text=True, check=True, timeout=10,
    )
    head = result.stdout.strip()
    if not _FULL_SHA.fullmatch(head):
        raise ValueError("materialization_unavailable: workspace HEAD is not a full commit SHA")
    return head


def validate_candidate(
    config_path: Path, *, workspace: Path, solr_url: str,
    ollama_url: str | None = None,
) -> tuple[str, ReadinessReport]:
    """Prove candidate snapshot/revision/fingerprint compatibility without writes."""
    config = load_runtime_config(path=config_path, environ={})
    if (config.backend != "solr" or config.repository is None
            or config.retrieval is None or config.retrieval.mode != "hybrid"):
        raise ValueError("unsupported_serving_profile: expected a repository-bound Solr hybrid runtime")
    root = workspace.resolve(strict=True)
    head = materialized_head(root)
    settings = dict(config.backend_settings)
    settings["url"] = solr_url
    ollama = (config.ollama if ollama_url is None else
              OllamaRuntimeSettings.from_mapping({"url": ollama_url}))
    candidate = replace(
        config, workspace=root, revision=head,
        backend_settings=settings, ollama=ollama,
    )
    report = inspect_readiness(RuntimeInspectionService(candidate))
    if (not report.ready or report.indexed_revision != head
            or report.repository != config.repository):
        failed = {
            name: fact.to_mapping() for name, fact in report.checks.items()
            if fact.state not in {"ready", "not_required"}
        }
        category = _rejection_category(report, head, config.repository)
        raise ValueError(
            f"serving_reconciliation_rejected/{category}: "
            + json.dumps({
                "requested_revision": head,
                "indexed_revision": report.indexed_revision,
                "failed_checks": failed,
                "observations": report.observations,
            }, sort_keys=True)
        )
    return head, report


def write_binding(config_path: Path, output: Path, revision: str) -> None:
    """Atomically write an operator-owned config without changing corpus policy."""
    source = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(source, dict) or not isinstance(source.get("runtime"), dict):
        raise ValueError("invalid_runtime_configuration")
    source["runtime"]["revision"] = revision
    parent = output.parent.resolve(strict=True)
    if output.is_symlink() or not parent.is_dir():
        raise ValueError("invalid_output_path")
    content = yaml.safe_dump(source, sort_keys=False)
    fd, temporary = tempfile.mkstemp(prefix=".fetchcore-serving-", suffix=".yml", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validate_serving_mounts(
    model: object, *, workspace: Path, container_workspace: Path, output: Path,
) -> None:
    """Check normalized Compose mounts, including overlapping mounts, fail-closed."""
    if not isinstance(model, dict) or not isinstance(model.get("services"), dict):
        raise ValueError("serving_binding_mismatch: invalid Compose services")
    expected = {
        container_workspace: workspace.resolve(strict=True),
        Path(_CONTAINER_CONFIG): output.resolve(strict=False),
    }
    if not container_workspace.is_absolute():
        raise ValueError("serving_binding_mismatch: runtime workspace must be absolute")
    for name in ("mcp", "api"):
        service = model["services"].get(name)
        if not isinstance(service, dict):
            raise ValueError(f"serving_binding_mismatch: {name} service missing")
        environment = service.get("environment", {})
        if (not isinstance(environment, dict)
                or environment.get("FETCHCORE_CONFIG") != _CONTAINER_CONFIG
                or any(key.startswith("FETCHCORE_") and key not in {
                    "FETCHCORE_CONFIG", "FETCHCORE_OLLAMA_URL",
                } for key in environment)):
            raise ValueError(f"serving_binding_mismatch: {name} runtime environment differs")
        if service.get("volumes_from"):
            raise ValueError(f"serving_binding_mismatch: {name} inherited mounts unproven")
        for kind in ("configs", "secrets"):
            file_mounts = service.get(kind, [])
            if not isinstance(file_mounts, list):
                raise ValueError(f"serving_binding_mismatch: {name} {kind} mounts invalid")
            for mount in file_mounts:
                if (not isinstance(mount, dict)
                        or not isinstance(mount.get("source"), str) or not mount["source"]):
                    raise ValueError(f"serving_binding_mismatch: {name} {kind} mount invalid")
                target_value = mount.get("target", "")
                if not isinstance(target_value, str):
                    raise ValueError(f"serving_binding_mismatch: {name} {kind} target invalid")
                if kind == "secrets":
                    target_value = target_value or mount["source"]
                    if not target_value.startswith("/"):
                        target_value = f"/run/secrets/{target_value}"
                else:
                    target_value = target_value or f"/{mount['source']}"
                if not target_value.startswith("/") or "\0" in target_value:
                    raise ValueError(f"serving_binding_mismatch: {name} {kind} target invalid")
                # Normalize container paths lexically, without resolving them on the host.
                target = Path(posixpath.normpath("/" + target_value.lstrip("/")))
                if any(target.is_relative_to(path) or path.is_relative_to(target)
                       for path in expected):
                    raise ValueError(f"serving_binding_mismatch: {name} overlapping {kind} mount")
        tmpfs = service.get("tmpfs", [])
        if not isinstance(tmpfs, list) or any(not isinstance(item, str) for item in tmpfs):
            raise ValueError(f"serving_binding_mismatch: {name} tmpfs mounts invalid")
        for item in tmpfs:
            target = Path(item.split(":", 1)[0])
            if any(target.is_relative_to(path) or path.is_relative_to(target)
                   for path in expected):
                raise ValueError(f"serving_binding_mismatch: {name} overlapping tmpfs mount")
        volumes = service.get("volumes", [])
        if not isinstance(volumes, list):
            raise ValueError(f"serving_binding_mismatch: {name} mounts unavailable")
        seen: set[Path] = set()
        for volume in volumes:
            if not isinstance(volume, dict) or not isinstance(volume.get("target"), str):
                raise ValueError(f"serving_binding_mismatch: {name} mount invalid")
            target = Path(volume["target"])
            if target in expected:
                source = volume.get("source")
                if (target in seen or volume.get("type") != "bind"
                        or volume.get("read_only") is not True
                        or not isinstance(source, str) or not Path(source).is_absolute()
                        or Path(source).resolve(strict=False) != expected[target]):
                    raise ValueError(f"serving_binding_mismatch: {name} bind mount differs")
                seen.add(target)
            elif any(target.is_relative_to(path) or path.is_relative_to(target)
                     for path in expected):
                raise ValueError(f"serving_binding_mismatch: {name} overlapping mount")
        if seen != expected.keys():
            raise ValueError(f"serving_binding_mismatch: {name} required bind mount missing")


def activate_serving(
    compose_file: Path, output: Path, *, config_path: Path, workspace: Path, revision: str,
) -> None:
    """Validate the deployment before writing and recreating serving processes."""
    environment = dict(os.environ)
    environment["FETCHCORE_OPERATIONAL_RUNTIME_CONFIG"] = str(output.resolve(strict=False))
    command = ["docker", "compose", "-f", str(compose_file.resolve(strict=True))]
    resolved = subprocess.run(
        [*command, "config", "--format", "json"], env=environment,
        capture_output=True, text=True, check=True, timeout=30,
    )
    config = load_runtime_config(path=config_path, environ={})
    _validate_serving_mounts(
        json.loads(resolved.stdout), workspace=workspace,
        container_workspace=config.workspace, output=output,
    )
    write_binding(config_path, output, revision)
    subprocess.run(
        [*command, "up", "-d", "--no-deps", "--force-recreate", "mcp", "api"],
        env=environment, check=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True,
                        help="Existing OPERATIONAL serving runtime YAML")
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Authorized materialized repository on the host")
    parser.add_argument("--solr-url", required=True,
                        help="Host-reachable URL for the existing Solr service")
    parser.add_argument("--ollama-url", help="Host-reachable Ollama URL")
    parser.add_argument("--output", type=Path, required=True,
                        help="Operator-owned runtime YAML outside the repository")
    parser.add_argument("--compose-file", type=Path,
                        default=Path("infra/compose/operational-mcp.yml"))
    parser.add_argument("--activate", action="store_true",
                        help="Write binding and recreate only MCP/API serving containers")
    args = parser.parse_args(argv)
    try:
        workspace = args.workspace.resolve(strict=True)
        output = args.output.expanduser().absolute()
        if output.resolve(strict=False).is_relative_to(workspace):
            raise ValueError("invalid_output_path: use an operator-owned path outside the workspace")
        revision, report = validate_candidate(
            args.config, workspace=workspace, solr_url=args.solr_url,
            ollama_url=args.ollama_url,
        )
        if args.activate:
            activate_serving(
                args.compose_file, output, config_path=args.config,
                workspace=workspace, revision=revision,
            )
        print(json.dumps({
            "status": "activated" if args.activate else "compatible",
            "repository": report.repository,
            "revision": revision,
            "output": str(output) if args.activate else None,
            "readiness": report.to_mapping(),
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, yaml.YAMLError,
            KnowledgeSearchError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, sort_keys=True),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
