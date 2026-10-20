#!/usr/bin/env python3
"""Operational envelope for FetchCore's existing benchmark runners.

This module does not evaluate retrieval, change measurements, or provision backends.
Every native runner retains its own protocol and safety checks.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
SUITES = {
    "baseline-v1": ("baseline-v1/run_bench.py", "directory", "fetchcore.baseline-v1"),
    "search-intent-v2": ("search-intent-v2/evidence.py", "directory",
                         "fetchcore.search-intent-v2-evidence"),
    "targeted-exact": ("targeted-exact/run.py", "file", "fetchcore.targeted-exact-v2"),
    "typed-knowledge-retrieval": ("typed-knowledge-retrieval/run.py", "file",
                                   "fetchcore.typed-knowledge-retrieval-v1"),
}
RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]{7,47}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
STATES = {"running", "completed", "blocked", "failed"}
KINDS = {"full", "smoke", "diagnostic", "preflight"}


class BundleError(ValueError):
    """Invalid, incomplete or unsafe benchmark run."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(path: Path, value: object) -> None:
    """Atomically replace one metadata file, never a published result."""
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    fd, filename = tempfile.mkstemp(prefix=".bundle-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BundleError(f"expected object: {path.name}")
    return value


def _git(*params: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *params], text=True).strip()


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def output_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if _inside(resolved, ROOT):
        raise BundleError("operational runs must stay outside the source repository")
    return resolved


def capture(run_dir: Path) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    for path in sorted((run_dir / "native").rglob("*")):
        if path.is_symlink():
            raise BundleError(f"symlink in measurements: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(run_dir).as_posix()
        data = path.read_bytes()
        artifacts.append({"path": relative, "bytes": len(data), "sha256": digest(data)})
    return artifacts


def _native_result(suite: str, run_dir: Path) -> dict[str, object]:
    if SUITES[suite][1] == "file":
        return load(run_dir / "native" / "measurement.json")
    runs = list((run_dir / "native").glob("V0.*/run.json"))
    if len(runs) != 1:
        raise BundleError("native runner did not produce exactly one versioned run")
    return load(runs[0])


def _native_status(suite: str, payload: dict[str, object], kind: str) -> str:
    status = payload.get("status")
    if status in {"BLOCKED", "blocked"}:
        return "blocked"
    if status not in {"complete", "MEASURED"}:
        return "failed"
    if kind == "full":
        if payload.get("full_protocol") is False:
            return "failed"
        if suite == "targeted-exact" and (
            payload.get("repetitions") != 20 or payload.get("warmups") != 1
        ):
            return "failed"
    return "completed"


def _safe_arguments(native: list[str], write_authorized: bool) -> None:
    for item in native:
        flag = item.split("=", 1)[0]
        if flag in {"--output", "--resume"}:
            raise BundleError("output and resume are owned by the existing runner/envelope")
        if flag in {"--prepare-index", "--execute-authorized"} and not write_authorized:
            raise BundleError("backend writes require --authorize-writes on the envelope")


def run(args: argparse.Namespace) -> int:
    if args.kind not in KINDS:
        raise BundleError("unknown run kind")
    if args.suite not in SUITES:
        raise BundleError("unknown suite")
    native = list(args.native_args)
    if native and native[0] == "--":
        native.pop(0)
    _safe_arguments(native, args.authorize_writes)
    if args.kind == "preflight" and args.authorize_writes:
        raise BundleError("preflight may not authorize backend writes")
    root = output_root(args.output_root)
    run_id = args.run_id or uuid.uuid4().hex
    if not RUN_ID.fullmatch(run_id):
        raise BundleError("invalid run identifier")
    run_dir = root / args.suite / run_id
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir()  # must not adopt/overwrite an existing run
    (run_dir / "native").mkdir()
    source, shape, protocol = SUITES[args.suite]
    source_path = ROOT / "benchmarks" / source
    config_hashes: dict[str, str] = {}
    for index, item in enumerate(native[:-1]):
        if item in {"--run-config", "--config", "--ground-truth"}:
            candidate = Path(native[index + 1])
            config_hashes[item[2:]] = digest(candidate.read_bytes())
    plan_sha: str | None = None
    if args.infra_plan is not None:
        plan = load(args.infra_plan)
        if (plan.get("suite") != args.suite or plan.get("run_id") != run_id
                or plan.get("mode") != "performance"):
            raise BundleError("infrastructure plan does not match suite and run identity")
        plan_sha = digest(args.infra_plan.read_bytes())
    elif args.mode == "performance":
        raise BundleError("performance run requires an explicit ephemeral infrastructure plan")
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION, "suite": args.suite, "protocol": protocol,
        "run_id": run_id, "kind": args.kind, "mode": args.mode,
        "status": "running", "native_status": None, "started_at": now(),
        "completed_at": None, "review": {"status": "unreviewed", "note": None},
        "identity": {
            "product_revision": _git("rev-parse", "HEAD"),
            "product_tree_dirty": bool(_git("status", "--porcelain")),
            "runner_sha256": digest(source_path.read_bytes()),
            "arguments_sha256": digest(json.dumps(native).encode()),
            "config_hashes": config_hashes,
            "infrastructure_plan_sha256": plan_sha,
        },
        "artifacts": [], "exit_code": None, "error": None,
    }
    save(run_dir / "bundle.json", manifest)
    result_code = 1
    try:
        output = run_dir / "native" / ("measurement.json" if shape == "file" else "")
        command = [sys.executable, str(source_path), *native, "--output", str(output)]
        result_code = subprocess.run(command, cwd=ROOT, check=False).returncode
        payload = _native_result(args.suite, run_dir)
        manifest["native_status"] = payload.get("status")
        manifest["status"] = _native_status(args.suite, payload, args.kind)
        if result_code != 0:
            manifest["status"] = "failed"
            manifest["error"] = "native runner returned nonzero exit code"
        elif manifest["status"] == "failed":
            manifest["error"] = "native measurement is incomplete or incompatible"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        manifest["completed_at"] = now()
        manifest["exit_code"] = result_code
        try:
            manifest["artifacts"] = capture(run_dir)
        except BundleError as exc:
            manifest["status"] = "failed"
            manifest["error"] = str(exc)
        save(run_dir / "bundle.json", manifest)
    print(json.dumps({"bundle": str(run_dir / "bundle.json"),
                      "status": manifest["status"], "native_status": manifest["native_status"]}))
    return 0 if manifest["status"] == "completed" else 2


def validate(run_dir: Path, *, publish: bool = False) -> dict[str, object]:
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise BundleError("run must be an ordinary directory")
    bundle = load(run_dir / "bundle.json")
    suite = bundle.get("suite")
    if suite not in SUITES or bundle.get("protocol") != SUITES[str(suite)][2]:
        raise BundleError("suite/protocol mismatch")
    if (bundle.get("schema_version") != SCHEMA_VERSION or
            bundle.get("run_id") != run_dir.name or
            not RUN_ID.fullmatch(run_dir.name)):
        raise BundleError("unknown schema or run identity mismatch")
    if bundle.get("status") not in STATES or bundle.get("kind") not in KINDS:
        raise BundleError("unknown status or run kind")
    entries = bundle.get("artifacts")
    if not isinstance(entries, list):
        raise BundleError("invalid artifact manifest")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise BundleError("invalid artifact entry")
        name = entry.get("path")
        if (not isinstance(name, str) or not name.startswith("native/")
                or "\\" in name or
                any(part in {"", ".", ".."} for part in name.split("/")) or
                name in seen):
            raise BundleError("unsafe or duplicate artifact path")
        seen.add(name)
        path = run_dir / name
        if path.is_symlink() or not path.is_file():
            raise BundleError(f"missing or unsafe artifact: {name}")
        data = path.read_bytes()
        if entry.get("bytes") != len(data) or entry.get("sha256") != digest(data):
            raise BundleError(f"artifact checksum mismatch: {name}")
    actual = {entry["path"] for entry in capture(run_dir)}
    if seen != actual:
        raise BundleError("artifact inventory mismatch")
    if publish:
        review = bundle.get("review")
        if (bundle["status"] != "completed" or bundle["kind"] != "full"
                or not isinstance(review, dict)
                or review.get("status") != "accepted"
                or not isinstance(review.get("note"), str) or not review["note"].strip()
                or not entries):
            raise BundleError("publication requires complete reviewed full measurement")
        result = _native_result(str(suite), run_dir)
        if _native_status(str(suite), result, "full") != "completed":
            raise BundleError("native result is not a full completed measurement")
    return bundle


def review(args: argparse.Namespace) -> int:
    run_dir = args.run.resolve()
    bundle = validate(run_dir)
    if bundle["status"] != "completed" or bundle["kind"] != "full":
        raise BundleError("only completed full runs may be reviewed for publication")
    bundle["review"] = {"status": args.decision, "note": args.note.strip(),
                        "reviewed_at": now()}
    if not args.note.strip():
        raise BundleError("review note must be nonempty")
    save(run_dir / "bundle.json", bundle)
    return 0


def publish(args: argparse.Namespace) -> int:
    source = args.run.resolve()
    bundle = validate(source, publish=True)
    catalog = output_root(args.catalog)
    destination = catalog / str(bundle["suite"]) / str(bundle["run_id"])
    if destination.exists() or destination.is_symlink():
        raise BundleError("published execution already exists")
    if not args.write:
        print(json.dumps({"status": "validated", "destination": str(destination)}))
        return 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = catalog / ".publication.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise BundleError("publication locked; operator must investigate") from exc
    try:
        os.close(descriptor)
        if destination.exists() or destination.is_symlink():
            raise BundleError("published execution already exists")
        with tempfile.TemporaryDirectory(prefix=".publication-", dir=destination.parent) as tmp:
            staging = Path(tmp) / source.name
            staging.mkdir()
            for item in bundle["artifacts"]:
                rel = Path(str(item["path"]))
                target = staging / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / rel, target)
            shutil.copyfile(source / "bundle.json", staging / "bundle.json")
            validate(staging, publish=True)
            staging.rename(destination)
    finally:
        lock.unlink(missing_ok=True)
    print(json.dumps({"status": "published", "destination": str(destination)}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    p = sub.add_parser("run")
    p.add_argument("--suite", required=True, choices=sorted(SUITES))
    p.add_argument("--kind", choices=sorted(KINDS), required=True)
    p.add_argument("--mode", choices=("functional", "performance"), default="functional")
    p.add_argument("--output-root", type=Path, default=Path.home() / ".local/state/fetchcore/benchmarks/runs")
    p.add_argument("--run-id")
    p.add_argument("--infra-plan", type=Path)
    p.add_argument("--authorize-writes", action="store_true")
    p.add_argument("native_args", nargs=argparse.REMAINDER)
    p = sub.add_parser("inspect")
    p.add_argument("--run", type=Path, required=True)
    p = sub.add_parser("review")
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--decision", choices=("accepted", "rejected"), required=True)
    p.add_argument("--note", required=True)
    p = sub.add_parser("publish")
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        if args.operation == "run":
            return run(args)
        if args.operation == "inspect":
            bundle = validate(args.run.resolve())
            print(json.dumps({"status": bundle["status"], "kind": bundle["kind"],
                              "suite": bundle["suite"], "run_id": bundle["run_id"],
                              "artifacts": len(bundle["artifacts"])}))
            return 0
        if args.operation == "review":
            return review(args)
        return publish(args)
    except (BundleError, OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
