# Corpus Advisor policy-critic comparison — 2026-10-21

This is a small external behavioral assessment of the **skill**, not a
FetchCore retrieval benchmark or product contract. The baseline was the
one-invocation, no-critic exercise in [validation.md](validation.md), recorded
before this change. The experiment reused its two repository revisions and
canonical CLI observations, then used one native, read-only Codex subagent to
challenge the parent's preliminary decisions. The parent retained policy
ownership. No target policy or index was changed.

## Frozen inputs and criteria

| Target | Git revision | Policy at revision | SCAN / DEEP |
| --- | --- | --- | --- |
| `oalangomes/RunnerOps` | `5a72fa122d5664e217299e16420fcd7d48b67a92` | absent; effective defaults | 134 discovered, 123 read; 1,788 documents, no duplicate group |
| `oalangomes/fetchcore` | `600651415055e625b2d98c57321263a68846455d` | `.fetchcore/indexing.yaml` at that revision | 723 discovered, 587 read; 5,420 documents, five duplicate groups |

Both checkouts were clean detached worktrees. The same local FetchCore
`0.1.0.dev0` CLI performed SCAN once and DEEP once per target. The canonical
JSON and final proposal responses are frozen in
[evaluation-2026-10-21](evaluation-2026-10-21); the complete proposed policies
are [RunnerOps](evaluation-2026-10-21/runnerops-indexing.yaml) and
[FetchCore](evaluation-2026-10-21/fetchcore-indexing.yaml). The corresponding
minimal unified diffs are
[RunnerOps](evaluation-2026-10-21/runnerops-indexing.diff) and
[FetchCore](evaluation-2026-10-21/fetchcore-indexing.diff). The old exercise
records Baseline A's evidence, policies and path impact. The
[predeclared plan](evaluation-2026-10-21/predeclared-plan.md) was written before
the critic responded. Baseline A's FetchCore tree had an in-progress Advisor
SKILL.md edit at observation time; B used the clean frozen commit. Canonical
counts and candidate admission match, but selected skill content is a small
uncontrolled difference. Raw JSON SHA-256:

| Target | SCAN | DEEP | final proposal |
| --- | --- | --- | --- |
| RunnerOps | `d03434d73a89e38ea6fdd01b7da4f1179756313c279b3f8139af74a6d65d939b` | `51bb0a1a2f3662c1a50813f34ba95ccbafffdea67f12e78c9ce87de5c2f53b32` | `f3c23d46e8b6135e4294e3db5432f326f7fadbfe02eeb24977f0b761ff86cd44` |
| FetchCore | `a3247f80eff089004b8c229991a29a07aae67a599adacd06a6894a794f0339a7` | `b408c0aacd2959539d12052f0d28283991429ed255d8f31e2fd529e679aae8ec` | `49966481cefaedee1c4095e3bc84f0fb87a977df8c68141ee9469a54a6c05ecc` |

Criteria were written before the critic responded: inclusion needs a concrete
engineering question, knowledge missing from selected sources, source-line
evidence and no unresolved sensitive-data issue. Exclusion needs a specific
noise/safety rationale and acceptable knowledge loss. A consumer, extension,
checksum or exact-path question alone is insufficient. Count an inclusion
false positive only when the final parent decision rejects a baseline
admission with evidence; mark unresolved cases as such. An important gap is a
source of demonstrated unique behavior omitted by both current policy and
baseline proposal. Objections need paths, lines and an alternative-source
check. Agent agreement is not Ground Truth. Ambiguous cases need human review.

## Delegation observed

The installed Codex CLI was `0.163.0`, with its native `multi_agent` feature
enabled. This run used **one** `/root/corpus_policy_critic` session, with no
child agents. It first received repository purpose, revision, policy,
SCAN/DEEP summaries, candidate paths, raw canonical JSON locations and a
limited read/search budget, without the parent's proposal or old validation
document. It returned its own assessment; only then did the parent send the
Baseline A draft to the **same** critic. It made nine focused searches and 20
bounded source reads in total, without rerunning SCAN/DEEP or writing to the
target worktrees. The two-stage exchange produced objections, including one
the parent did not accept. These are actual subagent observations, not a
simulated second opinion.

## Reconciliation

`supported` and `rejected` below are the critic's verdicts on the **parent's
preliminary decision**. `keep out` means no inclusion proposal; artifacts
still stay in Git. All final decisions are recommendations only.

| Target and candidate | Baseline A draft | Critic | Final B and reason |
| --- | --- | --- | --- |
| RunnerOps `runnerctl` | include | supported | **include**: public command dispatch and safeguards are in `runnerctl:21-97,402-439,1614-1681`; `README.md:101-123` lists commands without implementation. |
| RunnerOps `runners.conf.example` | include | supported, marginal | **keep out**: `README.md:720-738` already gives the six-field schema and example. The second sample at `runners.conf.example:7-8` adds no distinct contract. The critic's exact-file argument establishes provenance, not enough marginal retrieval value. |
| RunnerOps `.env.example` | include | supported | **include**: XDG roots, ephemeral absence timing and boot policy form a compact operator template (`.env.example:1-22`); narrow admission avoids a live `.env`. |
| RunnerOps `scripts/systemd/runnerops-systemctl` | include | supported | **include**: privileged action allowlist and unit check at `:9-35` are otherwise missing implementation evidence. |
| RunnerOps `site/styles.css` | keep out | rejected | **include exact path** after human adjudication: `:75-89,772-878` contain skip-link, reduced-motion and responsive behavior missing from selected sources. The parent initially judged the site peripheral, but the user chose to retain this specific accessibility/layout evidence. Import by `site/index.html:18` alone was not the reason. No broad CSS rule is proposed. |
| FetchCore `infra/images.lock` | include | supported | **include**: `:1-4` is the central pin/update contract; CI sources it at `.github/workflows/ci.yml:94`. Individual digests also appear in Compose. |
| FetchCore root `.env.example` | include | supported | **include**: `:1-62` consolidates current and legacy runtime settings; inspected credential fields are empty or commented placeholders. This does not authorize indexing a live `.env`. |
| FetchCore `tools/explorer/.env.example` | include | rejected | **keep out**: seven keys/defaults at `:4-9` repeat root `.env.example:7-12`; no consumer of this particular template was found. The parent accepts this objection. |
| FetchCore `tools/explorer/docker/nginx.conf` | include | supported | **include**: `/api/` proxy/timeouts and SPA fallback at `:1-18` are absent from the selected Dockerfile, which only copies the file (`Dockerfile:22`). |
| FetchCore `tools/explorer/web/src/style.css` | keep out | rejected | **include exact path**: `:69-109` contains Explorer keyboard focus, result selection and layout behavior absent from TSX. Import at `main.tsx:5` only established use; the specific UI behavior supplied the value argument. A broad `*.css` rule is unwarranted. |
| FetchCore four `artifacts/**/queries.json` copies | keep selected | rejected | **exclude these four exact paths from search, keep files in Git**: DEEP reports one checksum group; the 20 query IDs/text match `benchmarks/search-intent-v2/evidence-queries.json` after normalizing its list to the run copies' mapping. `evidence.py:852-860` needs the copies for resume; `README.md:295-319` documents run artifacts. Version/fingerprint remain in each selected `run.json`. Reassess future copies independently. |
| FetchCore `dogfood/scenarios/**` | preserve exclusion | supported after challenge | **preserve exclusion**: the scenarios hold unique expected-path labels (`fetchcore-self.yaml:7-46`), but benchmark code uses them as evaluation truth (`benchmarks/search-intent-v2/run.py:45,105-125`). Indexing them into the self-search corpus risks fixture leakage and changed ranking; this is an inference, not a measured effect. Files remain in Git for reproduction. |

The critic initially favored including both CSS files and the scenarios. It
reversed its scenario view after tracing benchmark consumers. The parent
accepted the Explorer CSS objection, initially rejected the RunnerOps site CSS
objection on product-scope grounds, and rejected the critic's marginal support
for the registry template. Human adjudication then favored the RunnerOps CSS
because its accessibility and responsive rules are specific technical
evidence. The user also affirmed excluding the four duplicate query copies
only from search, keeping them in Git. There was no forced consensus or
attempt to count the user's preference as a retrieval-quality measurement.

## Canonical final-policy validation

The parent validated the reconciled external YAMLs with
`fetchcore index --workspace "$PWD" corpus --proposal FILE --json`. The first
FetchCore proposal unexpectedly admitted `tools/explorer/.env.example` because
the include matcher checks both basename and relative path. The parent added
an explicit exclusion and revalidated. The final exact impact was:

| Target | Read before → after | Added / removed | Fingerprint before → after |
| --- | ---: | --- | --- |
| RunnerOps | 123 → 127 | add `.env.example`, `runnerctl`, `scripts/systemd/runnerops-systemctl`, `site/styles.css`; none removed | `sha256:05b062d902618c1017a6548124dd606bedf2635ec903a4ac0019655a18f9d885` → `sha256:9b4981da505a2fe9e57d5ae282b8c698bdb3d63be8c0429b6131ed11b8532087` |
| FetchCore | 587 → 587 | add root `.env.example`, `infra/images.lock`, `tools/explorer/docker/nginx.conf`, `tools/explorer/web/src/style.css`; remove four specified run `queries.json` paths | `sha256:3dd558e6c73848fa2b5e38738a50f48981a226f27e39b3dc27c2bc364bb57747` → `sha256:f4912ce9b5e5ae1faf3bd2b1c16562e1b7ca75b0866bda74eb0308a2afd4a304` |

Both final comparisons succeeded without truncation and reported
`impact.applied: false`. Minimal policy diffs are one include block for
RunnerOps, and one include block plus five targeted exclusions for FetchCore
(the nested Explorer template and four current run copies). Existing limits,
chunking, classification and three FetchCore exclusions remain unchanged.

## Outcome and cost

Using the predeclared criteria, B corrected **two likely false-positive
inclusions** (`runners.conf.example`, nested Explorer `.env.example`), found
**two important technical gaps** (RunnerOps site accessibility/responsive CSS
and Explorer focus/selection CSS), and made
**one evidence-backed search exclusion** (four duplicate run query copies).
The critic raised six substantive objections: both CSS omissions, the
Explorer template admission, duplicate query admission, scenario exclusion,
and marginal registry-template admission. After human adjudication, four
objections changed the final recommendation, one marginal objection was
rejected, and the scenario exclusion was retained after the critic's own
reversal. These are qualitative case judgments, not measured retrieval gains.

Baseline A used 19 focused searches, 26 bounded source reads and 15 canonical
path checks across the two targets. B reused the same canonical diagnostic
conditions and added the critic's nine searches and 20 bounded reads; parent
reconciliation also performed focused reads and CLI proposal validation.
Agent token usage and comparable end-to-end elapsed times were not exposed,
so no numeric token/time delta is claimed. B incurred one subagent session,
two exchanges, one additional FetchCore proposal validation after detecting
the unintended nested inclusion, and a final RunnerOps proposal validation
after human adjudication. No isolated retrieval test was run, so the
direction of actual Recall@K, precision, latency or context cost is unknown.

Human review resolved the two explicitly contested choices: include the
RunnerOps site CSS and exclude the four exact run-query copies from search
while preserving them for reproduction. The small two-repository sample
cannot establish general improvement; it does show that independent challenge
changed technically consequential decisions without changing FetchCore core
or applying a policy.
