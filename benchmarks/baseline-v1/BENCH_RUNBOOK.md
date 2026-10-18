# baseline-v1 — BENCH run and publication runbook

Use this procedure for the frozen \`fetchcore.baseline-v1\` suite. This is an
**operational wrapper around the existing scripts**, not a new benchmark
protocol, retrieval engine or regression harness.

## Invariants

- Ground Truth: \`oalangomes/NeuroTrack_MS@74ce3244956a65baed61f1dae6f498cb2be4f3b7\`.
- Frozen manifest, measurement equations, arm IDs and ranking semantics do not
  change during operational standardization.
- Run and resume only through \`run_bench.py\`; do not write runs inside
  \`benchmarks/baseline-v1/artifacts/\`.
- Run inputs, local logs and \`.run-state.json\` stay outside the Git repository.
- BENCH indexing can write only to explicitly attested BENCH resources. Never
  point benchmark execution at DEV or OPERATIONAL.
- \`performance_comparable\` remains \`false\` unless both dedicated BENCH
  isolation and actual exclusive host execution were verified.
- Only publish complete, inspected runs. Publication is offline. Versions are
  immutable; never overwrite published \`V0.N\` artifacts.
- A new run number is chosen above both locally present runs and the
  **published** catalog; use one persistent output root, complete/publicize
  a run before launching the next one. Independent un-published output roots
  cannot guarantee globally unique reservations.

## 1. Preflight — no writes

Review \`AGENTS.md\`, \`benchmarks/baseline-v1/README.md\` and
\`docs/guides/shared-development-infrastructure.md\`.

1. Validate clean corpus checkout and exact frozen revision; check the FetchCore
   revision and tests before starting.
2. Inspect the BENCH environment/project, endpoints, resources, volume bindings
   and ownership with \`scripts/shared_infra.py\`. Check readiness separately
   from index readiness. Prefer the dedicated BENCH project.
3. Ensure sufficient memory/disk and correct Ollama embedding model/dimensions.
   Explicitly check the effective Ollama endpoint *from the invoking process*.
4. Rule out concurrent jobs/hosts or mark latency non-comparable.
5. Record exact configs and effective resource identities **outside Git**.
6. Confirm target resources will not collide with an existing consumer/snapshot.
   For \`--prepare-index\`, a rebuild is an explicit destructive replacement of
   the *selected BENCH projection*: stop if binding/snapshot is unexpected.

Preflight failure: **stop**, do not redirect to another environment or
silently recreate missing services.

## 2. One stable external workspace

For example:

\`\`\`bash
export BENCH_REPO="$HOME/projetos/GitHub/NeuroTrack_MS.worktrees/benchmark-fetchCore"
export BENCH_STATE="$HOME/.local/state/fetchcore/baseline-v1"
export BENCH_CONFIG="$BENCH_STATE/run-config.json"
export BENCH_OUTPUT="$BENCH_STATE/runs"
mkdir -p "$BENCH_OUTPUT"
\`\`\`

Copy and fill \`run-config.example.json\` to \`$BENCH_CONFIG\`. Do not commit the
config: the wrapper captures its fingerprint and effective settings separately.
Revalidate endpoints and repository bindings on **every** execution. Do not
reuse old configurations blindly after reboot/network changes.

## 3. New execution / interruption / resume

Choose \`explicit_rebuild\` in the BENCH config and pass \`--prepare-index\` only
when an explicit rebuild is intended and authorized.

\`\`\`bash
set -o pipefail
PYTHONUNBUFFERED=1 python benchmarks/baseline-v1/run_bench.py \
  --run-config "$BENCH_CONFIG" \
  --workspace "$BENCH_REPO" \
  --output "$BENCH_OUTPUT" \
  --prepare-index \
  2>&1 | tee "$BENCH_STATE/baseline-current.log"
\`\`\`

The runner allocates \`V0.N\` atomically within the single output root and also
skips numbers already present in the published catalog. It writes
\`indexing.json\` as soon as preparation finishes, then writes arm artifacts
after each completed arm. It does **not** checkpoint individual queries.

On unexpected interruption, **do not start a new run** and **do not** pass
\`--prepare-index\` on resume:

\`\`\`bash
PYTHONUNBUFFERED=1 python benchmarks/baseline-v1/run_bench.py \
  --run-config "$BENCH_CONFIG" \
  --workspace "$BENCH_REPO" \
  --output "$BENCH_OUTPUT" \
  --resume V0.N \
  2>&1 | tee -a "$BENCH_STATE/baseline-current.log"
\`\`\`

Replace \`V0.N\` with the actual incomplete directory name. Resume checks
compatibility and reuses completed arms. If preparation stopped *before*
\`indexing.json\`, inspect physical snapshots before doing any rebuild.
Completed \`run.json\` cannot be resumed.

## 4. Offline post-processing, validation and publication

After \`run.json\` reports \`status: complete\`, generate ContextYield **offline**:

\`\`\`bash
export BENCH_RUN="$BENCH_OUTPUT/V0.N"
python benchmarks/baseline-v1/context_yield.py --artifacts "$BENCH_RUN"
\`\`\`

Validate and preview publication without writing:

\`\`\`bash
python benchmarks/baseline-v1/publish_run.py --source "$BENCH_RUN"
\`\`\`

The validator requires:

- full frozen arm matrix and expected observation counts;
- consistent corpus, revision, protocol and Ground Truth identities;
- \`bench-run.json\` provenance and compatible indexing telemetry, if prepared;
- \`README.md\`, \`context-yield.json\` and \`context-yield.md\`;
- no symlinks for published input files; no accidental machine-local state.

If it passes, inspect the source and **especially** \`bench-run.json\` for
private or sensitive operator data. The tool does not redact or invent metadata.

\`\`\`bash
python benchmarks/baseline-v1/publish_run.py \
  --source "$BENCH_RUN" --publish
\`\`\`

This copies allowlisted files byte-for-byte to
\`benchmarks/baseline-v1/artifacts/V0.N/\`, checks SHA256 before publication,
and rejects an existing destination. \`--publish\` does **not** create a commit;
stage and commit the new directory separately after review. Do not commit
\`.run-state.json\`, \`run-config.json\` or logs.

For comparison, call existing \`compare_runs.py\` against frozen versions only
when identities/fingerprints are compatible. Incompatible environment or
snapshots: report **inconclusive**, do not bypass the comparator. Performance
claims additionally require verified host comparability.

## Historical versions (do not rewrite)

- **V0.1**: already migrated from pre-versioning layout; its \`run.json\`
  explicitly records reconstructed provenance.
- **V0.2**: nine measured arms; no indexing artifact; frozen byte-identical
  tests protect its artifacts.
- **V0.3**: nine arms in a complete run, with seven retrieval artifacts reused
  from the previous version; no new index preparation.
- **V0.4**: dedicated BENCH full rebuild, nine arms/3,600 observations and
  derived ContextYield. Publish its operator-generated files using this tool
  **without re-running or altering measurements**.

Do not backfill historical \`bench-run.json\` or rebuild missing snapshots from
assumptions. Unknown capture details stay explicitly unknown. Version folders
are execution provenance, not product release versions.

## Publication limitations

- The published catalog plus one stable external output root provides monotonic
  numbering for normal sequential use, not distributed coordination across
  multiple machines or independently unpublished roots.
- Publication uses an exclusive local lock and same-filesystem directory rename.
  A stale lock after a crash requires manual investigation; never delete blindly.
- The validator checks artifact structure, counts, provenance consistency and
  copied byte identities. It does **not** replay search, certify semantics,
  certify all metrics or attest host isolation independently.
