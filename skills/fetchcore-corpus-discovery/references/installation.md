# Installation and invocation

Keep one instruction source: the entire `fetchcore-corpus-discovery` directory,
including references and the optional helper. Installing a skill is a separate
development action; DISCOVER/PROPOSE/REVIEW never install or edit anything.

## Repository installation

Both current Codex CLI and Copilot CLI document `.agents/skills/` as a discovery
root. From the target repository, link a trusted, pinned FetchCore checkout:

```bash
mkdir -p .agents/skills
ln -s /absolute/fetchcore/skills/fetchcore-corpus-discovery .agents/skills/fetchcore-corpus-discovery
```

Do not overwrite an existing installation. In FetchCore itself the relative link
target would be `../../skills/fetchcore-corpus-discovery`. Copy the whole directory
instead when symlinks are unavailable, and record the source revision; maintain
one installed copy rather than divergent coder-specific instructions. A symlink
tracks edits in its source checkout, so pin or deliberately update that checkout.

`skills/` alone is a distribution location, not an automatic discovery promise.
Restart the coder after installing and inspect `/skills`. If the skill is absent,
use manual invocation. Do not create two discovered copies of the same skill.

## Global installation

For both CLIs' documented shared personal root:

```bash
mkdir -p "$HOME/.agents/skills"
ln -s /absolute/fetchcore/skills/fetchcore-corpus-discovery "$HOME/.agents/skills/fetchcore-corpus-discovery"
```

Copilot also documents `~/.copilot/skills` and repository `.github/skills`;
Codex supports legacy `$CODEX_HOME/skills`. Prefer the shared `.agents` location
for this portable package. Global paths are machine-local and do not imply cloud
agent availability. Never change the user's global configuration as part of an audit.

## Invoke from the repository root

In Codex CLI, explicitly name the skill:

```text
$fetchcore-corpus-discovery DISCOVER this repository. Read-only; report uncertainty.
$fetchcore-corpus-discovery PROPOSE a minimal policy diff from the observed inventory. Do not apply it.
$fetchcore-corpus-discovery REVIEW the YAML below against this repository. Do not apply it.
```

In Copilot CLI, use `/skills` to confirm discovery, then name the skill in a prompt
(`Use fetchcore-corpus-discovery in DISCOVER mode ...`). Current CLI documentation
also describes `/fetchcore-corpus-discovery` invocation. Check the installed
version's command help; the slash alias was not exercised in our quota-blocked smoke.

For any coder, the portable manual fallback is:

```text
Read /absolute/fetchcore/skills/fetchcore-corpus-discovery/SKILL.md and its relevant
references. Apply DISCOVER to this repository root, read-only. Use metadata first,
bounded samples and path-grounded evidence. Emit your report in the response.
```

For PROPOSE, supply the DISCOVER report/current revision and request YAML plus a
diff in the response. For REVIEW, supply the proposed YAML or an explicitly chosen
external file. The coder must resolve helper/reference paths relative to the
installed skill, not the target repository. No runtime adapter is needed.

## Optional deterministic helper

```bash
python3 -B /absolute/skill/scripts/inventory.py discover "$PWD"
python3 -B /absolute/skill/scripts/inventory.py review "$PWD" --proposal /external/proposal.yaml
```

Requires Python 3.11+; Git is optional for plain directories. The helper's output
is stdout JSON; it has no apply/output option. DISCOVER works without FetchCore,
reporting admission/schema/pack validation as unavailable. REVIEW requires a
compatible installed FetchCore environment; choose that environment's Python.
Do not install dependencies into the analyzed repository during discovery.
External policy validation uses temporary files outside the target repository.
Python `-B` prevents import bytecode writes, including when FetchCore is installed
editable in the repository. No new third-party dependency is added by this skill.
For model context, use a local JSON filter over stdout to select summary/policy
and relevant paths; the full inventory is tooling data. Do not load thousands of
repeated per-path records into a conversation. Preserve nonzero helper exit codes.

## Verification and limits

Observed executables: Codex CLI **0.163.0**, Copilot CLI **1.0.98**. See
[validation.md](validation.md) for actual smoke outcomes; documentation support,
executable presence and successful execution are distinct observations. Other
coders and Windows symlinks were not tested. Tool permissions, authentication and
model availability can prevent execution even when discovery is supported.

Primary references consulted for discovery and invocation:
[Codex skill loader](https://github.com/openai/codex/blob/main/codex-rs/ext/skills/src/host_roots.rs),
[Codex symlink discovery](https://github.com/openai/codex/blob/main/codex-rs/ext/skills/src/loader/discovery.rs),
[GitHub agent skills](https://docs.github.com/en/copilot/concepts/customizations/agent-skills),
[Copilot CLI command definitions](https://github.com/github/copilot-cli/blob/main/_autodocs/api-reference/slash-commands.md).
These were checked against current Context7 documentation and local CLI help;
the shared `.agents/skills` symlink is exercised in isolated fixture smokes, with
outcomes recorded separately. Documentation can evolve; rerun discovery checks
for another installed version instead of assuming compatibility.
