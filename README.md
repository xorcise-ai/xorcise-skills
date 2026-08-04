# xorcise-skills

<p>
  <a href="LICENSE"><img alt="License Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-e8b84b?style=flat-square&labelColor=0f0c07"></a>
  <a href="https://github.com/xorcise-ai/xorcise"><img alt="Requires XORCISE" src="https://img.shields.io/badge/requires-XORCISE-e8b84b?style=flat-square&labelColor=0f0c07"></a>
  <a href="https://xorcise.ai"><img alt="xorcise.ai" src="https://img.shields.io/badge/web-xorcise.ai-e8b84b?style=flat-square&labelColor=0f0c07"></a>
</p>

Agent skills for **XORCISE** — the platform for benchmarking AI agents on hands-on
cybersecurity missions.

Each skill under [`skills/`](./skills) is a self-contained `SKILL.md` plus its scripts,
usable by any AI coding agent that supports the skill format. More skills will be added
over time.

## Skills

| Skill | Status | What it does |
|---|---|---|
| [`xorcise-playbooks`](./skills/xorcise-playbooks) | experimental — first release | Run a standardized XORCISE agent benchmark end to end: pick a ready-made playbook (or build one from the missions your platform already offers), point it at any OpenHands-supported model(s), and get a polished eval-card HTML report of how each model performed across the missions. Warns you about cost before spending anything. |

## Requirements

These skills drive XORCISE — they do not replace it, and they will not install it for you.

- **A running XORCISE engine** — [xorcise-ai/xorcise](https://github.com/xorcise-ai/xorcise),
  with its REST API reachable (default `http://127.0.0.1:3001`). See
  [docs.xorcise.ai](https://docs.xorcise.ai).
- **Docker**, with permission to build images and run containers.
- **`/dev/net/tun`**, so a sandbox can join the mission network.
- **`python3` + PyYAML**.
- **An API key** for each model provider you want to benchmark.

Per-skill prerequisites live in the skill's own `README.md`.

## Installing a skill

Place the skill's directory where your agent discovers skills, then invoke it by name.
For agents that load skills from `~/.claude/skills/`:

```bash
cp -r skills/xorcise-playbooks ~/.claude/skills/
# …or symlink it, to keep tracking this repo
ln -s "$PWD/skills/xorcise-playbooks" ~/.claude/skills/xorcise-playbooks
```

The skill is then available as `/xorcise-playbooks`.

See the skill's own [`SKILL.md`](./skills/xorcise-playbooks/SKILL.md) and
[`README.md`](./skills/xorcise-playbooks/README.md) for the full walkthrough and
requirements.

## What leaves your machine

- **Your API keys stay on your host.** They are typed into a `chmod 600`, git-ignored file
  and read from it directly. Provider variables are handed to the sandbox by *name*, so
  docker inherits the values and the skill never reads them.
- **One key is visible to local process listing.** OpenHands takes a single `LLM_API_KEY`,
  and the runner passes that one value on the `docker run` command line — so it can be read
  from `/proc/<pid>/cmdline` by other accounts on the same machine while a run is in
  progress. Use a dedicated host. See
  [`SECURITY.md`](SECURITY.md#what-leaves-your-machine).
- **Prompts and agent activity go to the model provider you configure** — and only to that
  provider. Their terms govern that data.
- **Telemetry goes to your own XORCISE instance**, over OTLP (default port `4318`) on the
  host running the benchmark.
- **Reports stay local.** Eval cards are written wherever you point `--outdir` and are never
  uploaded. Keep them outside a git checkout — run artefacts contain agent transcripts and
  mission flags.
- **XORCISE.AI receives nothing.** No vendor telemetry, no licence check, no phone-home.

Full detail in [`SECURITY.md`](SECURITY.md). Our
[Privacy Policy](https://xorcise.ai/policies/privacy) covers the website and hosted platform,
not these skills — they run entirely on your machine.

## Security

**Never report a security vulnerability in a public issue.** Use GitHub's private advisory
form at
<https://github.com/xorcise-ai/xorcise-skills/security/advisories/new>, or email
**security@xorcise.ai**. Scope, the disclosure key and what to include are in
[`SECURITY.md`](SECURITY.md).

These skills run untrusted agent code against deliberately vulnerable targets. Run them on
infrastructure you are willing to lose — see
[the operational note](SECURITY.md#operational-note-for-users).

## Contributing

Issues and pull requests are welcome. Start with [`CONTRIBUTING.md`](CONTRIBUTING.md) — it
covers how a skill is laid out, how to test a change, and what gets a pull request rejected.

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). Maintainers are
listed in [`MAINTAINERS.md`](MAINTAINERS.md).

**This GitHub repository is canonical.** Development up to the first public release happened
in a private repository, which is now archived.

## Licence and acceptable use

Licensed under the [Apache License 2.0](LICENSE), © 2026 Fifth Domain Pty Ltd
(ACN 606 251 585), trading as XORCISE.AI. [NOTICE](NOTICE) carries the attribution and
trademark terms. Contributors sign the [CLA](CLA.md) — see
[CONTRIBUTING.md](CONTRIBUTING.md).

XORCISE is offensive-security tooling. **Only point it at systems you own, or that you have
specific written authorisation to test.** The [Acceptable Use Policy](ACCEPTABLE_USE.md) sets
out what that means in practice, including export-control and sanctions obligations. That
policy is not an additional licence condition and does not narrow the rights Apache-2.0
grants you.

Use of the XORCISE name and logo is governed by the
[Trademark Policy](https://xorcise.ai/policies/trademark).
