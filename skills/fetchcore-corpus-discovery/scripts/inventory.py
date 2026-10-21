#!/usr/bin/env python3
"""Read-only corpus metadata and external policy review; never invoke an indexer.

DISCOVER needs Python 3.11+ and optionally Git. REVIEW also needs FetchCore in
the selected Python environment. No source contents or remote URLs are emitted.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import fields
import difflib
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading


POLICIES = (".fetchcore/indexing.yaml", ".agentsorch/indexing.yaml")
PRUNED = {".git", ".venv", "venv", "node_modules", "__pycache__"}
CODE = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go",
        ".rs", ".cs", ".rb", ".php", ".swift", ".c", ".h", ".cpp", ".sh", ".impex"}
DOCS = {".md", ".rst", ".txt", ".adoc"}
CONFIG = {".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg", ".properties"}
MARKERS = {"pyproject.toml": "Python project", "package.json": "JavaScript/TypeScript project",
           "Cargo.toml": "Rust", "go.mod": "Go", "pom.xml": "Maven/JVM",
           "build.gradle": "Gradle/JVM", "Dockerfile": "container build",
           "*.tf": "Terraform", "next.config.*": "Next.js configuration",
           "*.csproj": ".NET project"}


def git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    """Bound Git output and runtime without requiring writable temporary storage."""
    with subprocess.Popen(
        ["git", "--no-optional-locks", "-C", str(root), *args],
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ) as process:
        def feed() -> None:
            try:
                process.stdin.write(stdin)
                process.stdin.close()
            except OSError:
                pass

        writer = threading.Thread(target=feed, daemon=True) if stdin is not None else None
        timer = threading.Timer(10, process.kill)
        timer.start()
        try:
            if writer:
                writer.start()
            output = process.stdout.read(8_000_001)
            if len(output) > 8_000_000:
                process.kill()
                raise ValueError("Git metadata exceeded 8000000 bytes; narrow inventory externally")
            code = process.wait(timeout=10)
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            if writer:
                writer.join(timeout=1)
        if code not in (0, 1) or (code == 1 and args[0] != "check-ignore"):
            raise ValueError(f"Git metadata command failed: {args[0]}")
        return output


def safe_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("invalid relative path")
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("symlink: metadata only, needs_review")
    return current


def sensitive(relative: str) -> bool:
    """Conservative content-reading guard, independent of runtime admission."""
    name = Path(relative).name.lower()
    return (
        name.startswith(".env") or name in {"local.properties", ".npmrc", ".pypirc"}
        or any(word in name for word in ("secret", "credential", "token", "password"))
        or Path(name).suffix in {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}
        or any(part in {".ssh", ".aws", ".gnupg"} for part in Path(relative).parts)
    )


def classify(relative: str) -> dict:
    """Reproducible path heuristics, never an exclusion decision."""
    path = Path(relative)
    suffix = path.suffix.lower()
    if sensitive(relative):
        category, reason = "sensitive", "sensitive filename; do not acquire content"
    elif any(p in {".artifacts", "artifacts", "traces", "outputs", "backups", "generated"}
             for p in path.parts) or suffix in {".log", ".jsonl", ".bak"}:
        category, reason = "derived_candidate", "output-like path; verify producer and technical value"
    elif suffix in CODE:
        category = "tests" if any(p in {"tests", "test", "__tests__"} for p in path.parts) \
            or path.name.startswith("test_") or ".test." in path.name else "implementation"
        reason = "source extension; symbols and semantic role not inspected"
    elif suffix in DOCS:
        category, reason = "technical_document_candidate", "document extension; inspect technical value"
    elif suffix in CONFIG or path.name in {"Dockerfile", "Makefile"}:
        category, reason = "configuration_or_contract", "manifest/configuration extension or basename"
    else:
        category, reason = "unknown", "insufficient path evidence"
    return {"category": category, "status": "needs_review", "basis": "path_heuristic",
            "confidence": "low", "evidence": {"path": relative, "reason": reason}}


def read_policy(root: Path) -> tuple[str | None, str]:
    found = []
    for relative in POLICIES:
        path = safe_path(root, relative)
        if path.exists():
            if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > 65_536:
                raise ValueError("policy is not a bounded regular file (65536 bytes)")
            found.append((relative, path.read_text(encoding="utf-8")))
    if len(found) > 1:
        raise ValueError("both FetchCore and legacy policy exist; resolve ambiguity externally")
    return found[0] if found else (None, "")


def resolve_policy(text: str, explicit: bool, packs: tuple, *, workspace: Path | None = None):
    """Use the installed canonical parser and pack precedence, in an external sandbox."""
    from fetchcore.repository.index_policy import load_repository_index_policy
    from fetchcore.repository.knowledge_pack import configure_repository_knowledge_packs
    from fetchcore.repository.extractors.registry import default_repository_extractors

    if workspace is not None:
        resolved = load_repository_index_policy(workspace)
    else:
        with tempfile.TemporaryDirectory(prefix="fetchcore-policy-") as temporary:
            sandbox = Path(temporary)
            if explicit:
                policy = sandbox / POLICIES[0]
                policy.parent.mkdir()
                policy.write_text(text, encoding="utf-8")
            resolved = load_repository_index_policy(sandbox)
    resolved.select_extractors(default_repository_extractors())
    config = configure_repository_knowledge_packs(
        resolved.config, packs, apply_defaults=not explicit,
    )
    return resolved, config


def active_packs(root: Path) -> tuple:
    from fetchcore.repository.default_knowledge_packs import default_repository_knowledge_packs
    from fetchcore.repository.knowledge_pack import select_repository_knowledge_packs

    # Existing pack detection reads only manifest.json (<=1 MB) and marker metadata.
    for relative in ("manifest.json", "hybris/config/localextensions.xml",
                     "hybris/config/cloudextensions.xml", "hybris/bin/custom"):
        safe_path(root, relative)
    return select_repository_knowledge_packs(root, default_repository_knowledge_packs())


def inventory_paths(root: Path, limit: int) -> tuple[list[tuple[str, str, bool]], list[str], str | None]:
    limitations = []
    if (root / ".git").exists():
        # Do not silently inspect a parent repository when the requested root is not its root.
        top = Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
        if top != root:
            raise ValueError("invoke at the Git repository root")
        tracked = {os.fsdecode(p) for p in git(root, "ls-files", "-z", "--cached").split(b"\0") if p}
        untracked = {os.fsdecode(p) for p in git(
            root, "ls-files", "-z", "--others", "--exclude-standard").split(b"\0") if p}
        ignored = {os.fsdecode(p) for p in git(
            root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory",
        ).split(b"\0") if p}
        paths = sorted(tracked | untracked | ignored)
        if len(paths) > limit:
            limitations.append(f"inventory truncated to {limit} of {len(paths)} Git entries")
            paths = paths[:limit]
        checked = b"".join(os.fsencode(p) + b"\0" for p in paths if p in tracked)
        tracked_ignored = {os.fsdecode(p) for p in git(
            root, "check-ignore", "--no-index", "-z", "--stdin", stdin=checked,
        ).split(b"\0") if p} if checked else set()
        rows = [(p, "tracked" if p in tracked else "ignored" if p in ignored else "untracked",
                 p in ignored or p in tracked_ignored) for p in paths]
        try:
            revision = git(root, "rev-parse", "--verify", "HEAD").decode().strip()
        except ValueError:
            revision = None
            limitations.append("Git repository has no observed HEAD revision")
        return rows, limitations, revision

    # Git metadata unavailable for plain directories. Pruning is disclosed, never a policy claim.
    limitations.append("non-Git directory: ignore/tracking/revision unknown; dependency directories pruned")
    rows = []
    visited = 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        kept = []
        for name in dirs:
            visited += 1
            relative = (Path(parent) / name).relative_to(root).as_posix()
            if name in PRUNED or (Path(parent) / name).is_symlink():
                limitations.append(f"directory not traversed: {relative}")
            else:
                kept.append(name)
        dirs[:] = kept
        for name in files:
            rows.append(((Path(parent) / name).relative_to(root).as_posix(), "unknown", False))
            if len(rows) >= limit:
                limitations.append(f"inventory stopped at {limit} files")
                return sorted(rows), limitations, None
        if visited >= limit:
            limitations.append(f"directory traversal stopped at {limit} entries")
            break
    return sorted(rows), limitations, None


def admission(row: dict, config) -> str:
    # Reuse the canonical scanner's matching semantics; do not recreate glob rules.
    from fetchcore.repository.safe_scan import _excluded, _matches_file

    path = row["path"]
    if row["git_state"] == "ignored":
        return "git_ignored_untracked"
    if _excluded(path, config.exclude_patterns):
        return "excluded"
    if not _matches_file(path, config.include_patterns):
        return "not_included"
    if row["file_state"] != "regular":
        return "needs_review"
    if row["bytes"] > config.max_file_bytes:
        return "max_file_bytes"
    return "eligible_by_metadata"


def summarize(rows: list[dict], field: str) -> dict:
    values = sorted({row[field] for row in rows})
    return {value: {"entries": sum(row[field] == value for row in rows),
                    "regular_file_bytes": sum(row["bytes"] or 0 for row in rows
                                              if row[field] == value and row["file_state"] == "regular")}
            for value in values}


def discover(root: Path, *, max_paths: int = 10_000) -> dict:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("repository root must be a directory")
    paths, limitations, revision = inventory_paths(root, max_paths)
    source, text = read_policy(root)
    config = None
    packs = ()
    policy = {"source": source, "yaml": text,
              "sha256": hashlib.sha256(text.encode()).hexdigest() if source else None}
    try:
        packs = active_packs(root)
        resolved, config = resolve_policy(text, source is not None, packs, workspace=root)
        policy.update({"validation": "valid", "resolved": resolved.to_mapping()})
        policy["resolved"]["source"] = source
    except ImportError:
        policy["validation"] = "unavailable"
        limitations.append("FetchCore not installed: canonical admission/schema/pack validation unavailable")
    except (ValueError, OSError, UnicodeError) as exc:
        policy["validation"] = "invalid"
        # Parser messages may contain configured values; do not echo arbitrary secret-bearing YAML.
        policy["error_type"] = type(exc).__name__
        limitations.append("invalid policy or unsafe pack marker: proposal blocked; review locally")

    rows = []
    for path, git_state, ignored in paths:
        row = {"path": path.rstrip("/"), "git_state": git_state,
               "matches_gitignore": None if git_state == "unknown" else ignored, "bytes": None,
               "source_kind": Path(path).suffix.lower() or Path(path).name,
               "classification": classify(path)}
        try:
            candidate = safe_path(root, row["path"])
            info = candidate.stat(follow_symlinks=False)
            row["file_state"] = "regular" if stat.S_ISREG(info.st_mode) else "directory_or_special"
            if row["file_state"] == "regular":
                row["bytes"] = info.st_size
                if not info.st_mode & 0o444:
                    row["file_state"] = "unreadable"
        except (OSError, ValueError):
            row["file_state"] = "unsafe_or_unreadable"
        row["admission"] = admission(row, config) if config is not None else "unknown"
        rows.append(row)
    technologies = [{"technology": technology, "path": row["path"], "basis": "filename_marker"}
                    for row in rows for marker, technology in MARKERS.items()
                    if fnmatch.fnmatch(Path(row["path"]).name, marker)]
    return {
        "mode": "DISCOVER", "repository_root": str(root), "revision": revision,
        "policy": policy, "technologies": technologies,
        "knowledge_packs": [{"id": p.pack_id, "version": p.version,
                             "defaults_applied": source is None} for p in packs],
        "effective_selection": None if config is None else {
            "include": list(config.include_patterns), "exclude": list(config.exclude_patterns)},
        "summary": {"observed_entries": len(rows),
                    "observed_regular_file_bytes": sum(r["bytes"] or 0 for r in rows
                                                       if r["file_state"] == "regular"),
                    "by_admission": summarize(rows, "admission"),
                    "by_source_kind": summarize(rows, "source_kind"),
                    "by_category": dict(sorted(Counter(r["classification"]["category"]
                                                       for r in rows).items()))},
        "paths": rows,
        "limitations": limitations + [
            "Ignored directories are collapsed; their files and bytes are not measured.",
            "Eligibility is metadata-only, before API quarantine, decoding and aggregate read limits.",
            "No source content sampled; heuristics need bounded inspection by the coder.",
            "Only policy and pack manifest (at most 1 MB) content read; no remote/backend access.",
            "Working tree metadata observed; HEAD alone does not identify uncommitted contents.",
            "No chunks, tokens, duplicates, retrieval gains or snapshot compatibility measured.",
        ],
    }


def review(root: Path, proposal: Path, *, max_paths: int = 10_000) -> dict:
    root = root.resolve(strict=True)
    # Proposals are supplied externally; this command cannot apply or emit workspace files.
    proposal = proposal.resolve(strict=True)
    if proposal.is_relative_to(root):
        raise ValueError("proposal must be outside the analyzed repository")
    if not proposal.is_file() or proposal.stat().st_size > 65_536:
        raise ValueError("proposal must be a regular file of at most 65536 bytes")
    report = discover(root, max_paths=max_paths)
    if report["policy"]["validation"] != "valid":
        raise ValueError("canonical current policy validation required; review blocked")
    proposed_text = proposal.read_text(encoding="utf-8")
    packs = active_packs(root)
    resolved, proposed = resolve_policy(proposed_text, True, packs)
    current_policy, current = resolve_policy(
        report["policy"]["yaml"], report["policy"]["source"] is not None, packs,
    )
    removed, added = [], []
    for row in report["paths"]:
        row["proposed_admission"] = admission(row, proposed)
        if row["admission"] == "eligible_by_metadata" and row["proposed_admission"] != row["admission"]:
            removed.append(row["path"])
        if row["proposed_admission"] == "eligible_by_metadata" and row["admission"] != row["proposed_admission"]:
            added.append(row["path"])
    report.update({
        "mode": "REVIEW", "proposal_yaml": proposed_text,
        "proposal_sha256": hashlib.sha256(proposed_text.encode()).hexdigest(),
        "policy_diff": "".join(difflib.unified_diff(
            report["policy"]["yaml"].splitlines(keepends=True), proposed_text.splitlines(keepends=True),
            fromfile=report["policy"]["source"] or "/dev/null", tofile="proposed/.fetchcore/indexing.yaml",
        )),
        "impact": {"removed_eligible_paths": removed, "added_eligible_paths": added,
                   "removed_observed_bytes": sum(r["bytes"] for r in report["paths"] if r["path"] in removed),
                   "current": summarize(report["paths"], "admission"),
                   "proposed": summarize(report["paths"], "proposed_admission")},
        "existing_exclusions_removed": sorted(set(current.exclude_patterns) - set(proposed.exclude_patterns)),
        "existing_includes_removed": sorted(set(current.include_patterns) - set(proposed.include_patterns)),
        "changed_config_fields": [f.name for f in fields(current)
                                  if getattr(current, f.name) != getattr(proposed, f.name)],
        "effective_contract_changed": current != proposed or (
            current_policy.enabled_extractors != resolved.enabled_extractors),
        "applied": False,
        "review_status": "needs_human_review",
        "snapshot_review": "not inspected; changed indexing contracts may require rebuild; separate operator review",
    })
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("discover", "review"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--max-paths", type=int, default=10_000)
    args = parser.parse_args()
    if args.max_paths < 1:
        parser.error("--max-paths must be positive")
    if args.mode == "review" and args.proposal is None:
        parser.error("review requires --proposal outside the repository")
    try:
        report = review(args.root, args.proposal, max_paths=args.max_paths) \
            if args.mode == "review" else discover(args.root, max_paths=args.max_paths)
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": type(exc).__name__, "status": "blocked",
                          "reason": "metadata or policy validation failed; inspect inputs locally"}))
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
    return 2 if report["policy"]["validation"] == "invalid" else 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
