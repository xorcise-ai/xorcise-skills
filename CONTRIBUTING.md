# Contributing to XORCISE Skills

Thanks for wanting to help. This document covers what this repository is, how a skill is put
together, and what a change needs to look like to get merged.

If you are looking for the XORCISE engine — the CLI, the runner, the control plane, the
console — it lives at [xorcise-ai/xorcise](https://github.com/xorcise-ai/xorcise) and has its
own contributing guide. Changes to the engine belong there, not here.

## What this repository is

A distribution point for **XORCISE agent skills**. Each skill is a self-contained directory:
a `SKILL.md` telling an AI agent how to do something, plus the scripts, playbooks and
templates it needs. Any agent that supports the skill format can use them.

Nothing here is built, compiled, packaged or released. There is no version number, no
lockfile and no publish step. A skill is a directory of files, and a change to a skill is a
change to those files.

**This GitHub repository is canonical.** Development happened on a private GitLab project up
to the first public release; that project is archived and read-only. Everything now happens
here.

## Repository layout

```
skills/
  <skill-name>/
    SKILL.md          # required — the instructions the agent follows
    README.md         # what it does, prerequisites, the walkthrough for a human
    scripts/          # anything the skill executes
    playbooks/        # for xorcise-playbooks: the xor-pb-* benchmark definitions
    templates/        # example configs, report templates
    reference/        # troubleshooting and deeper documentation
    sandbox/          # container definition, if the skill builds one
```

`SKILL.md` is the only required file. It begins with YAML frontmatter carrying `name` and
`description`:

```markdown
---
name: xorcise-playbooks
description: Run a standardized XORCISE agent benchmark — pick a ready-made playbook …
---
```

The `description` is what an agent matches against when deciding whether the skill applies, so
write it for that purpose: say what the skill does and when to reach for it, concretely.

## Before you start

- **A new skill, or a change to how a skill behaves** — open an issue first. It is much
  cheaper to agree on the shape before the work than to rewrite it afterwards.
- **A bug fix, a typo, a broken path, clearer wording** — go straight to a pull request.

## Changing an existing skill

- Keep the `SKILL.md` frontmatter valid. A malformed header means the agent never loads the
  skill, and nothing announces the failure.
- **Keep the skill self-contained.** Scripts, templates and playbooks are referenced by paths
  relative to the skill's own directory. A skill must never depend on a file outside itself,
  on this repository's layout, or on something a user is expected to have placed somewhere
  by hand.
- If you change what a script expects — a new flag, a renamed file, a different config key —
  update `SKILL.md` and the skill's `README.md` in the same pull request. Instructions that
  have drifted from the scripts are worse than no instructions.
- Preserve the confirm-before-spending behaviour. If your change touches the path that starts
  a run, the cost estimate and the explicit confirmation stay.

## Adding a new skill

1. Create `skills/<skill-name>/` — lowercase, hyphenated, matching the `name` in the
   frontmatter.
2. Write `SKILL.md`. Address the agent directly, in steps, in plain language. Say when to
   stop and ask the user something.
3. Write a `README.md` for humans: what it does, prerequisites, what it will cost, and the
   walkthrough.
4. Add a row to the **Skills** table in the root [`README.md`](README.md).
5. If the skill needs credentials, follow the pattern in `xorcise-playbooks` — scaffold a
   `600` file and have the user type the value into it. Never take a secret as a command-line
   argument, and never echo one.

## What gets a pull request rejected on sight

Not style preferences — these are the things that cause real harm:

- **Any credential in the diff.** API keys, tokens, tailnet auth keys, certificates, `.env`
  contents — including in example files, fixtures and comments. If it looks like a real
  secret, it is treated as one.
- **Committed `results/` output.** Run artefacts are local; `.gitignore` already excludes
  them.
- **Internal hostnames, private tracker references, or internal process names** in code or
  documentation.
- **Anything that spends money without asking first.** A skill that can start a paid run
  without showing a cost and getting a yes is a defect, not a convenience.
- **Anything that prints, logs or writes a secret** — including into a generated report.

## Testing a change

There is no test suite. What there is instead is the real thing, which is better evidence.

Run these from inside the skill's own directory — the scripts resolve their siblings by
relative path:

```bash
cd skills/xorcise-playbooks
```

1. **Preflight** — read-only, changes nothing, spends nothing:

   ```bash
   scripts/preflight.sh
   ```

2. **Dry run** — validates the whole orchestration without spending anything. It creates the
   runs, prints the launch command for each cell, and tears them down. **No API keys needed**,
   which makes this the first thing to try:

   ```bash
   python3 scripts/run_playbook.py playbooks/xor-pb-000-smoke.yaml --dry-run
   ```

3. **The smallest real exercise** — the smoke playbook against one cheap model. `--otel` is
   required; XORCISE grades from the telemetry, so a run without it produces nothing to grade:

   ```bash
   python3 scripts/run_playbook.py playbooks/xor-pb-000-smoke.yaml \
     --otel \
     --llm-config "$MODELS_YAML" \
     --creds "$RUN_CREDS" \
     --models <short-name> \
     --outdir ./results/smoke \
     --concurrency 1
   ```

   `--llm-config` is the model-map YAML; `--models` takes the short names defined in it.

4. If your change touches reporting, render the eval card and open it in a browser.

This needs a running XORCISE engine, Docker, and `/dev/net/tun`. **If you could not run it,
say so plainly in the pull request** and describe what you did check. An honest "I could not
test this, here is why" is useful; a claim that it works when it was never run is not.

In the pull request, write down the command you ran and what it printed. "Tested, works" is
not evidence.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/), imperative mood:

```
feat(playbooks): add a defensive-blue benchmark preset
fix(report): correct flag counting for multi-flag missions
docs(readme): explain the /dev/net/tun requirement
```

Put the *why* in the body. The diff already shows the what.

## Proposing a change

1. Fork the repository and branch from `main`.
2. Make the change. Keep it focused — one concern per pull request.
3. Open a pull request against `main` on
   [xorcise-ai/xorcise-skills](https://github.com/xorcise-ai/xorcise-skills). Fill in the
   template; each item maps to something a reviewer would otherwise check by hand.
4. `main` is protected: every change lands through a pull request. Both maintainers are
   automatically requested for review via [`CODEOWNERS`](.github/CODEOWNERS).

Maintainers are listed in [`MAINTAINERS.md`](MAINTAINERS.md).

## Security

**Never open a public issue or pull request for a security vulnerability.** Use the private
advisory flow described in [`SECURITY.md`](SECURITY.md) so the problem can be fixed before it
is disclosed.

## Contributor License Agreement

XORCISE is published by **Fifth Domain Pty Ltd (ACN 606 251 585)**, which owns the project and
holds the rights needed to license it as a whole.

Before your first pull request can be merged, you need to sign the
[Contributor License Agreement](CLA.md). The CLA assistant will comment on your pull request
with a link; signing takes a minute and covers all your future contributions. You keep the
copyright in your own work — the CLA grants Fifth Domain the licence it needs to distribute
and, if it ever chooses to, relicense the project.

It is the same agreement the engine repository uses, word for word, and signatures are recorded
in one shared store for the whole organisation — so if you have already signed on another
xorcise-ai repository, you are done here too.

If you are contributing in the course of employment, or your employer otherwise holds rights in
your work, your employer needs to sign the Corporate CLA instead. Email <legal@xorcise.ai>.

## Licensing and acceptable use

Contributions are licensed under the [Apache License 2.0](LICENSE), the same licence as the
rest of the repository. By opening a pull request you confirm you have the right to contribute
the code and that it may be published under that licence. [NOTICE](NOTICE) carries the
attribution and trademark terms.

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). These skills drive
offensive-security tooling, so the [Acceptable Use Policy](ACCEPTABLE_USE.md) applies to what
you build with them as much as to what you contribute here.
