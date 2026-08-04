<!--
Thanks for contributing to XORCISE Skills.

Nothing here is busywork: each item maps to a check a reviewer would otherwise have to run by
hand. Delete any section that genuinely does not apply, and say why.
-->

## What changed

<!-- The change itself, in a sentence or two. -->

## Why

<!-- The problem this solves. Link an issue with "Closes #123" if there is one. If this
     changes how a skill behaves, say what a user could not do before and can do now. -->

## Which skills does this touch

<!-- e.g. skills/xorcise-playbooks — or "none, repository docs only". -->

## Testing

<!-- What you actually ran, and what it printed. "Tested, works" is not evidence; a command
     and its output is. -->

- [ ] `scripts/preflight.sh` — read-only, spends nothing
- [ ] `python3 scripts/run_playbook.py playbooks/xor-pb-000-smoke.yaml --dry-run` — validates
      orchestration, needs no API keys
- [ ] A real run against at least one model
- [ ] Rendered the eval-card report and opened it (only if this touches reporting)
- [ ] **I could not run these** — explained below why, and what I checked instead

```
# paste the command you ran and what it printed
```

> Running a real benchmark needs a XORCISE engine, Docker and `/dev/net/tun`. If you do not
> have that, say so — an honest "I could not test this" is useful. A claim that it works when
> it was never run is not.

## Checklist

- [ ] No credentials, API keys, tokens or `.env` content anywhere in the diff — including
      fixtures, comments and example files
- [ ] No `results/` output committed
- [ ] No internal hostnames, private tracker references or internal process names
- [ ] `SKILL.md` frontmatter still parses and the skill still loads
- [ ] The skill stays self-contained — no new dependency on a path outside its own directory
- [ ] Nothing spends money without showing a cost and asking the user first
- [ ] Nothing prints, logs or writes a secret — including into a generated report
- [ ] Documentation updated: the skill's `README.md` and/or the skills table in the root
      [`README.md`](../README.md)

---

- [ ] I have signed the [Contributor License Agreement](../CLA.md), or will when the CLA
      assistant comments below. If I am contributing in the course of employment, my employer
      has signed the Corporate CLA.

By opening this pull request you agree that your contribution is licensed under the
[Apache License 2.0](../LICENSE) and on the terms of the [CLA](../CLA.md), and that you have read
the [Code of Conduct](../CODE_OF_CONDUCT.md).

**Found a security vulnerability?** Do not open a pull request or a public issue — follow
[SECURITY.md](../SECURITY.md) instead.
