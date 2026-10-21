# Installation and migration

Install one copy of the skill. The analyzed repository can be any language and
does not need FetchCore source; the coding environment needs a compatible
`fetchcore` executable on PATH or in a known virtual environment. Its local
`index corpus` command works without a registered repository or backend.
Verify with `fetchcore index corpus --help`.

For repository-local discovery:

```bash
mkdir -p .agents/skills
ln -s /absolute/fetchcore/skills/fetchcore-corpus-advisor .agents/skills/fetchcore-corpus-advisor
```

For a personal installation, use `~/.agents/skills/fetchcore-corpus-advisor`
(or the discovery root supported by the coding agent). Point it to the same
skill directory; do not maintain divergent copies. These commands are
installation instructions, not part of a corpus review.

If `fetchcore-corpus-discovery` was previously installed as a symlink, remove
that old symlink manually after verifying its target, then create the new
`fetchcore-corpus-advisor` link. Restart the coding agent if it caches skill
discovery. Do not leave both installed, and do not automatically edit anyone's
personal installation during a repository change.

Invoke with `$fetchcore-corpus-advisor` or ask the agent to read this SKILL.md.
For example: “Use fetchcore-corpus-advisor to complete a corpus review of this
repository in one invocation. Investigate the diagnostics and propose a policy;
do not apply it.”

The source of truth for command availability is the installed CLI help. An
older FetchCore installation without `index corpus` cannot provide this skill's
canonical local scan; upgrade that installation separately or report the
diagnostic gap.
