# Public exposure review — 2026-10-22

**Decision: NO-GO for closing [#110](https://github.com/oalangomes/fetchcore/issues/110).**
The MIT license and contributor/security entry points already exist. This review
sanitizes operator paths and completes the technical scans described below.
Benchmark artifacts derived from a private corpus remain committed. The maintainer
stated that the benchmarks will be removed later; that statement does not authorize
redistribution of their current contents. Removal and a new exposure decision are
still required. Repository visibility and Git history were not changed.

## Scope and reproducibility

- Audited HEAD and fetched remote `master`:
  `890608f215522e912bb8a1411e118cebdaf2a845`.
- Full local object database, not a shallow checkout: 236 initial refs,
  1,004 reachable commits, 3,858 trees and 2,008 blobs (210,625,946 raw blob bytes).
- A temporary mirror was cloned with `--mirror --no-local`. Remote branches and
  tags were fetched over HTTPS into separate audit refs: 108 remote refs; the
  reachable commit count remained 1,004 and remote `master` matched HEAD.
- The HEAD archive contained 738 tracked files. A separate copy of tracked working
  files was scanned during remediation. Untracked files, dependencies,
  active environment configuration and unrelated worktrees were outside scope.
- All reachable blobs were materialized with `git cat-file`, including removed
  files. All 48 gzip blobs found by their magic bytes were decompressed and scanned
  separately. Forty current gzip artifacts also contain operator paths inside
  fingerprinted benchmark configurations; these remain pending benchmark removal.
- Scans ran locally in an isolated temporary directory, without Docker or CI.
  Raw reports, logs, object/ref inventories and directed-review results are stored
  outside the repository in a directory restricted to the operator. They must not
  be attached to a public issue or committed.

Gitleaks **8.24.2**, Linux x64 release archive SHA-256:
`fa0500f6b7e41d28791ebc680f5dd9899cd42b58629218a5f041efa899151a8e`.
The archive was compared with the upstream release checksum file. Version-specific
CLI help was checked. The documented scan modes are
[Gitleaks git and dir](https://github.com/gitleaks/gitleaks/tree/v8.24.2).

The configuration explicitly extended the built-in default rules. No baseline,
repository ignore file or inline `gitleaks:allow` suppression was applied. Common
flags for each scan were:

```text
--config <private-audit-dir>/default.toml
--gitleaks-ignore-path <private-audit-dir>/empty-ignore
--ignore-gitleaks-allow --max-decode-depth 3 --redact=100
--no-banner --no-color --report-format json
--report-path <private-audit-dir>/<scan>.json
```

The config contained `[extend]` followed by `useDefault = true`.
Commands were `gitleaks git <mirror> --log-opts="--all --full-history"` and
`gitleaks dir <directory>` for the HEAD archive, sanitized tracked-file copy,
all raw blobs and decompressed gzip payloads. No file-size exclusion was added.
For reproduction, inventory objects using `git rev-list --objects --all` and
`git cat-file --batch-check` before materializing each blob. Preserve private
reports and triage results separately from this sanitized record.

## Scanner results and directed review

| Scan | Exit | Alerts | Reviewed classification |
| --- | ---: | ---: | --- |
| Git history | 1 | 23 | 11 source SHA-256 values; 12 analyzer version constants |
| HEAD tracked tree | 1 | 5 | 4 source SHA-256 values; 1 analyzer version constant |
| Final working tree, including this review | 1 | 5 | Same classifications as HEAD |
| All 2,008 raw blobs | 1 | 15 | 12 source SHA-256 values; 3 analyzer version constants |
| 48 decompressed gzip blobs | 1 | 6 | Source SHA-256 values |

All alerts used the `generic-api-key` rule. Each was compared with its source
line, including the historical revision for Git findings. These are false
positives; **no credential was confirmed**. Exit 1 records findings, not a clean
scanner exit. Reports overlap and their counts must not be added as unique leaks.
Gitleaks reported 900 commits with scanned patches; that is not the reachable
commit count. The separate complete blob pass covered snapshot contents as well.

Supplementary pattern review over historical text blobs and expanded gzip data
found 131 operator-home occurrences, 52 URL-userinfo occurrences, 99 internal-host
URL occurrences and five copyright/notice indicators. These counts include
multiple historical versions. Notice indicators mapped to `LICENSE` and
`CONTRIBUTING.md`. Internal URLs were Docker host aliases or an illustrative Solr
hostname in historical self-hosting documentation. Current URL-userinfo examples
were confined to tests. Current email matches were synthetic test identities or
an example-domain Git identity in the developer acceptance script. These targeted
checks are supporting evidence, not a general classifier for private data.

No font binaries or vendored dependency archives were found in the current tracked
file inventory. The ten current PNGs are the three authored brand exports and
seven launch renders; SVGs use authored geometry and fallback font references.
All eight brand SVGs were parsed: no embedded font elements, data references or
external asset references were found. No additional third-party notice requirement
was identified by this review.
Absence of a notice marker does not establish rights over corpus-derived content.

## Remediation and behavior equivalence

The operator home prefix was replaced by `/home/operator` in **seven files / 25
occurrences**: five historical reports and two corpus advisor reference documents.
Application routes beginning with `/home` were preserved. Historical Git objects
were not rewritten.

Each edited file was compared with its HEAD version to verify that the only
change was that exact prefix substitution. A trial substitution in benchmark JSON
was rejected during validation: the workspace is inside the configuration covered
by `config_fingerprint`. All benchmark artifacts were restored byte for byte to
HEAD rather than publishing a configuration with an invalid fingerprint. The
remaining 36 plain-JSON and 40 gzip artifacts with operator paths are pending the
maintainer's later benchmark removal. Numeric measurements, rankings, source
references, repository revisions, fingerprints and artifact bytes remain unchanged.
In the edited reports, `/home/operator` is a privacy placeholder, not the original
execution location.
No retrieval code, persisted identity or retrieval behavior changed. This was not
a benchmark rerun or a new performance claim.

Validation passed: `make docs-check PYTHON=.venv/bin/python` (75 Markdown files),
`scripts/brand/verify_launch_assets.py` (all seven checksums and dimensions),
`git diff --check`, exact substitution checks on the seven sanitized documents,
and byte equivalence for every benchmark artifact against HEAD. The ordinary
localization check passed with 40 missing and two stale translations reported;
strict localization completeness was not claimed and generated translations were
not edited. Runtime tests were not rerun because only documentation changed.

## Rights declarations and remaining decision

- Upstream extraction ownership was already explicitly confirmed by the maintainer
  on 2026-10-21 in [the issue declaration](https://github.com/oalangomes/fetchcore/issues/110#issuecomment-6328444745).
  No generic ownership confirmation was requested again.
- On 2026-10-22, the maintainer explicitly confirmed rights to redistribute the
  SVG logomark and all seven launch renders under MIT, without unauthorized
  third-party elements or fonts. The [brand notice](../../assets/brand/README.md)
  and [launch notice](../../assets/launch/README.md) record this declaration.
  Image bytes and the launch manifest remain unchanged.
- Asked specifically about the private benchmark corpus, the maintainer instead
  stated that benchmarks will be removed from the repository later. This review
  therefore leaves that content in place and does not record legal approval for it.

Before closing #110, remove or explicitly authorize the corpus-derived benchmark
content, assess any remaining historical exposure, rescan the resulting revision,
and record the maintainer's explicit final GO/NO-GO. A later tree deletion does
not erase published history. No history rewrite or destructive remediation is
authorized by this record.

## Limits

Coverage is reachable Git refs fetched for this review plus the local refs, not
server-only pull-request refs, unreachable/dangling objects, reflogs, GitHub issue
attachments, external corpus repositories or unpublished remote refs. The scanner
uses its built-in rules/allowlists and three decoding levels; it cannot prove the
absence of secrets or personal data. Gzip payloads were explicitly expanded;
other encrypted/unsupported encodings and image OCR were not audited. Copyright
and redistribution rights cannot be inferred from scanner scores, commit authors,
checksums or visual approval. The maintainer declarations above are recorded
separately from technical observations.
