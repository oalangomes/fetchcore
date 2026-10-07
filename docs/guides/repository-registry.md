# Register and inspect repositories

Registration acquires repository sources through the existing local-workspace or
Git provider. It does not scan, index, embed, or contact a retrieval backend.

```bash
fetchcore repo add .
fetchcore repo list
fetchcore repo inspect owner/repository

fetchcore repo add https://github.com/owner/repository.git --json
fetchcore repo list --json
fetchcore repo inspect owner/repository --json
```

Paths select `local-workspace`; URLs and scp-style SSH remotes select `git`.
`--provider git` also permits a local Git path. Supported Git transports are
HTTP(S), SSH (`ssh://git@host/owner/repository` or `git@host:owner/repository`),
Git protocol, and local `file:///absolute/path` URLs. These use the existing
Git provider, not new source implementations. Credential-bearing URLs, query
strings, fragments, unsupported schemes, traversal and malformed references
fail before acquisition. Use Git credential helpers for authentication.

## Operator paths and storage

Local sources must resolve inside an explicitly allowed root; the default is
the current directory. Repeat `--allowed-root` to authorize additional roots.
Absolute paths outside those roots and symlinks escaping them fail. `..` is
rejected before normalization, including encoded URL traversal.

```bash
fetchcore repo --allowed-root /srv/repos add /srv/repos/project
fetchcore repo --allowed-root /srv/repos inspect owner/project
fetchcore repo --cache-root /srv/fetchcore-cache list --json
```

Group options precede the subcommand. The existing source-provider cache
precedence remains: explicit `--cache-root`, `FETCHCORE_REPOSITORY_CACHE`, legacy
`AGENTSORCH_REPOSITORY_CACHE`, then `~/.cache/fetchcore/repositories`. Registrations
live in `registry.json` within that cache root. No repository-owned configuration
or new endpoint settings are loaded. Serving configuration continues to belong
to the [operator runtime boundary](runtime-configuration.md).

The registry is a version `1` JSON object containing `version` and a
`repositories` array sorted by canonical identity. Writes use a private temporary
file and atomic replacement; a competing writer fails explicitly through an
exclusive `registry.lock`. A process terminated during a write can leave that
lock; only remove it after verifying no registration writer remains. Corrupt or
incompatible state is never silently reset. Cache/registry symlink targets are
rejected. Moving this operator cache requires deliberate re-registration; stored
cache locations are validated against the active provider cache identity.

## Identity and materialization

The provider's `MaterializedRepository.repository` is canonical. For common Git
remotes this is `owner/repository`; local workspaces resolve `origin`, falling
back to the directory name under the **existing provider contract** when origin
is absent. No CLI alias overrides identity. Different sources producing the
same identity are rejected, including identical owner/repository paths on
different hosts. `inspect` first matches exact canonical identity; otherwise a
single leaf name may resolve if unique. Ambiguity fails and requires the full
identity.

Local sources are not copied or mutated. Their absolute `workspace`, source URI,
branch and revision observed at registration are retained. Branch or revision
is `null` when unavailable. These observations do not claim a clean working tree
or continuing revision currentness.

Git acquisition maintains the existing persistent mirror and creates a detached
temporary worktree for the resolved commit. The worktree is removed before `add`
returns. The registry retains the mirror `cache_path`, source and observed
branch/revision; `workspace` is `null`, never a dead temporary path. Mirror origin
must match the requested source before fetching an existing cache.

Repeated `add` of the same source returns the original registration without Git,
refresh, revision changes, or duplicate records. Git spellings sharing the
provider's existing mirror key (for example a trailing `.git`) reuse that entry.
There is no implicit repair or refresh. `inspect` and repeated `add` fail when
the registered workspace/mirror is missing. `list` still exposes the registration
with `materialization_state: "absent"`. Read operations do not run Git, rescan,
clone, or index. `present` means the directory exists, not that its content still
matches the recorded revision.

SDK consumers can use
`fetchcore.application.repositories.RepositoryApplicationService.inspect()` to
obtain a `RegisteredRepository`. Its `source_request()` reconstructs the existing
`RepositorySourceRequest` with canonical identity, ready for the corresponding
provider and later indexing integration. Git requests pin the observed revision;
local requests re-observe HEAD so an old observation cannot label newer workspace
content. This slice does
not add repository-targeted indexing commands, search, or diagnostics.

## Output and failures

Each command accepts `--json`. Successful output has `operation`,
`status: "succeeded"`, and `result` (one record for add/inspect, an array for list).
JSON keys and list ordering are deterministic. Records expose:

- `repository`, `provider`, `source`, `source_ref`;
- `branch`, `revision` (registration observations);
- `workspace`, `cache_path` (intended absolute local location contracts);
- `registration_status: "registered"`, `materialization_state: "present" | "absent"`.

Human output renders the same fields in one line per repository, using
`unavailable` for JSON `null`. Empty lists print `No registered repositories.`.
Credentials, environment dumps, and raw Git stderr are never included.

Operational failures exit `1`. JSON failures expose `operation`,
`status: "failed"`, and `error: {"category": ..., "message": ...}`. Categories are
`unsupported_source`, `invalid_repository_reference`, `repository_not_found`,
`ambiguous_repository`, `unsafe_source`, `materialization_failure`,
`missing_materialization`, and `registry_persistence_failure`. Click syntax
errors use its existing usage-error exit `2` contract. A failed registration may
leave a reusable mirror, but never a successful registry entry.

The [existing operational indexing commands](self-hosted-indexing.md) and their
JSON contracts remain unchanged.
