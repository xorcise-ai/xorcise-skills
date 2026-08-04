# Security Policy

This repository publishes **agent skills** for XORCISE. The XORCISE engine itself lives at
[xorcise-ai/xorcise](https://github.com/xorcise-ai/xorcise) and has its own security policy —
if the flaw you found is in the engine, the CLI, the runner or the console, report it there
instead.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues, pull requests,
or discussions.**

Report privately using GitHub's **Private Vulnerability Reporting**:

> **Security** tab → **Advisories** → **Report a vulnerability**
>
> — or go straight there:
> <https://github.com/xorcise-ai/xorcise-skills/security/advisories/new>

This creates a private advisory that only you and the maintainers can see, so the problem can
be fixed before it becomes public.

If you cannot use GitHub's advisory form, email **security@xorcise.ai** with the subject line
`XORCISE security report`.

### Encrypted disclosure

If you would rather not send it in the clear, encrypt to our disclosure key:

| | |
| --- | --- |
| Key | [`.github/security/xorcise-security.asc`](https://raw.githubusercontent.com/xorcise-ai/xorcise-skills/main/.github/security/xorcise-security.asc) |
| Fingerprint | `7669 9A0C 221D D2F8 4358  E871 FC6A E63E B8E3 EB95` |
| User ID | `XORCISE.AI Security <security@xorcise.ai>` |
| Expires | 2029-07-30 |

```bash
curl -sL https://raw.githubusercontent.com/xorcise-ai/xorcise-skills/main/.github/security/xorcise-security.asc | gpg --import
gpg --fingerprint security@xorcise.ai   # check it against the value above
```

This is the same key the engine repository publishes. Verify the fingerprint before you trust
it. It is published here, in
[the engine repository](https://github.com/xorcise-ai/xorcise/blob/main/SECURITY.md), and on
<https://xorcise.ai/security>; checking more than one tells you that you have the right key,
not that nobody replaced it.

We ask for coordinated disclosure: give us a chance to ship a fix before you publish, and we
will credit you unless you would rather we did not. We do not run a bug bounty, and we will
not threaten anyone who reports a finding in good faith.

### What to include

The more of this you can provide, the faster a fix lands:

- **Which skill** — the directory under `skills/`, and the commit you were on.
- **What the vulnerability is** — the class of issue (credential exposure, command injection,
  sandbox escape, isolation bypass, HTML injection into a report, etc.).
- **Where it is** — the file and, if you can, the line. A script name and a function beats a
  general description.
- **How to reproduce it** — exact commands, in order, and any proof-of-concept. A working
  reproduction is by far the most valuable part of a report.
- **What an attacker gains** — the impact, and who is exposed.
- **Your environment** — operating system, the agent you ran the skill from, Docker version,
  the XORCISE engine version (`xorcise --version`), and whether the engine was local or
  remote.

**Redact your own secrets before sending.** Provider API keys, tailnet auth keys and tokens
have no place in a report, even a private one.

### What to expect

XORCISE is maintained by a small team, and we do not yet publish a turnaround commitment we
could be held to. We will acknowledge your report, tell you whether it is in scope, and keep
you posted while a fix is developed. If you have not heard back and think you should have,
send a follow-up — it means something went wrong with the notification, not that your report
was ignored.

We will credit you in the published advisory unless you ask us not to. Please give us a
reasonable opportunity to release a fix before disclosing publicly.

## Scope

The skills here drive offensive-security tooling against deliberately vulnerable targets. The
boundary between "working as designed" and "vulnerability" therefore needs stating explicitly.

### ✅ In scope

Anything in this repository that lets a skill reach beyond what its design permits, or that
exposes something the operator expected to stay private:

- **Command injection, path traversal or unsafe deserialisation** in the skill's scripts —
  `scripts/run_playbook.py`, `scripts/gen_report.py`, `scripts/list-missions.py`,
  `scripts/status.py`, `scripts/check-connectivity.py`, `scripts/harness-info.sh`. A mission
  name, a model string, a playbook field or a trace value that escapes into a shell or a file
  path is a real flaw.
- **Credential exposure** — a provider API key reaching a generated report, a log, an agent
  transcript, a container environment dump, or the terminal. This includes the redaction in
  `scripts/run_playbook.py` failing open on a variable it should have masked, and
  `scripts/setup-creds.sh` creating or leaving a credentials file with permissions wider than
  `600`.
- **Weakened run isolation** — anything in `sandbox/Dockerfile` or `sandbox/join-and-run.sh`
  that lets a sandbox reach the host, reach a network other than its own mission network, or
  see another concurrent run's resources.
- **Injection into the eval-card report** — untrusted model output, mission metadata or trace
  content rendering as active HTML or script in the report produced by `scripts/gen_report.py`
  and `templates/eval-card-template.html`. The report is opened in a browser, so this matters.
- **Watchdog and lifecycle flaws** — `scripts/watchdog.sh` restarting or acting on something
  it should not, or leaving credentials or containers behind after a run.
- **Anything that spends money without consent** — a path through a skill that starts a paid
  run without the cost check and confirmation the skill promises.

### ❌ Not in scope

- **Vulnerabilities in the XORCISE engine, CLI, runner, control plane or console.** Report
  those against [xorcise-ai/xorcise](https://github.com/xorcise-ai/xorcise/security/advisories/new).
- **Vulnerabilities inside mission bundles.** They are intentional and they are the content:
  a mission exists so an agent has something real to find. SQL injection, weak crypto,
  hardcoded flags and outdated components in a mission are not flaws.

  *However*: if a mission's vulnerability lets an attacker escape the mission's isolation
  boundary and affect the host or another run, **that is in scope** — the escape is a defect
  even though the vulnerability that enabled it is deliberate.

- **Vulnerabilities in third-party software** — OpenHands, litellm, Docker, provider SDKs,
  PyYAML — with no skill-specific exploit path. Report those upstream.
- **The fact that these skills drive an autonomous agent against exploitable targets.** That
  is the product. Reports amounting to "this can attack things" will be closed.
- **Missing hardening with no demonstrated impact**, and **findings from automated scanners
  with no manual validation or working reproduction.**
- **Social engineering, physical attacks, or denial of service** against project
  infrastructure.

## What leaves your machine

These skills run on your infrastructure and talk to services you choose. Stated plainly, so
you can decide with the facts:

- **Provider API keys are entered and stored locally.** `scripts/setup-creds.sh` scaffolds a
  credentials file and opens it in your editor, so the value is typed into the *file* — not
  onto a shell command line and not into an agent transcript. The file is `chmod 600` and
  git-ignored, and the runner reads it via `--creds`.
- **Provider variables are forwarded to the sandbox by name.** `scripts/run_playbook.py`
  selects provider-relevant variable *names* (`*_API_KEY`, `*_API_BASE`, `*_BASE_URL`,
  `AWS_*`, and the per-provider prefixes) and passes them as `docker -e NAME`. Docker
  inherits each value from the runner's own environment, so the script never reads or prints
  those values.
- **Known issue — one key does reach the command line.** OpenHands authenticates through a
  single `LLM_API_KEY`, and for the ten API-key providers (everything except Bedrock and
  Vertex, which use cloud auth) `scripts/run_playbook.py` passes that one value as
  `docker run -e LLM_API_KEY=<value>`. It is therefore present in the `docker` client
  process's arguments for the duration of a run, where any other account on the same host
  can read it via `ps` or `/proc/<pid>/cmdline`, and where host process-accounting or
  endpoint-monitoring software may record it.

  It is not written to disk by the skill and does not leave the host. But if you are running
  on a machine you share with anyone you would not hand the key to, that is a real exposure —
  use a dedicated host, and prefer a scoped, revocable, spend-limited key. We would rather
  tell you this than let you find it in the source.
- **Prompts, agent actions and mission content go to the model provider you configure** —
  OpenAI, Anthropic, Google, AWS Bedrock, Azure, OpenRouter, Mistral, Groq, Together,
  DeepSeek or Cohere, depending on the litellm model string you choose. That provider's terms
  and data-handling govern what happens to it. We are not in that path and cannot see it.
- **Telemetry and run-control stay on your host.** The runner itself calls the XORCISE REST
  API at `XORCISE_BASE`, which defaults to `http://127.0.0.1:3001`. Each sandbox container
  reaches back to the *same* host for run-control and for OTLP telemetry (default port
  `4318`) via `host.docker.internal`, which the runner maps to the host gateway with
  `--add-host host.docker.internal:host-gateway` so it resolves on native Linux Docker as
  well as Docker Desktop. Apart from your configured model provider, your own XORCISE
  instance is the only thing the skills contact.
- **Reports stay local.** The eval-card HTML and run data are written wherever you point
  `--outdir`, and are never uploaded anywhere.

  **Mind the path if you work inside a clone of this repository.** The `.gitignore` here
  excludes a directory named `results/`, which covers the runner's built-in default. The
  walkthrough in `SKILL.md` deliberately uses a different, more descriptive `--outdir`
  (`./xorcise-results/…`), and **that name is not covered** — so run output placed there
  inside a checkout would show up as untracked files. Run artefacts contain agent
  transcripts and mission flags. Keep them outside the repository, or add your chosen output
  directory to `.gitignore` before you start.
- **XORCISE.AI receives nothing.** There is no vendor telemetry, no licence check, no usage
  reporting and no phone-home in this repository. If you want us to see a result, you have to
  send it to us yourself.

Our [Privacy Policy](https://xorcise.ai/policies/privacy) covers the website and the hosted
platform. It does not describe these skills, because these skills run entirely on your
machine and send us nothing.

## Operational note for users

- **These skills build and run Docker containers that execute untrusted agent code**, and
  need `/dev/net/tun` so a sandbox can join the mission network. Run them on infrastructure
  you are willing to lose — a dedicated VM or an isolated cloud environment, never a
  workstation holding credentials you care about, and never on a network segment you would
  not want a compromised agent to reach.
- **They require a running XORCISE engine, and a default local install is unauthenticated.**
  The REST API and console (`:3001`) and OTLP ingest (`:4318`) bind to loopback — plus, on
  Linux, the Docker bridge gateway, because the sandbox container has to reach them. Any local
  process, and any container on that bridge, can reach them too. The bind widens to the IPv4
  wildcard only if you set `XORCISE_HOST=0.0.0.0`, or on Linux when the Docker bridge gateway
  cannot be determined at boot. See <https://xorcise.ai/security> for the full port table and
  the current list of gaps.
- **The mission library is vulnerable on purpose.** It deliberately ships software with known,
  exploitable vulnerabilities. That is the point; an agent needs something real to find.
- **Benchmarks spend real money.** A run calls paid provider APIs. The skills show an
  estimated cost and ask before spending anything, but the estimate is a ballpark and the
  ceiling is whatever your provider account allows. Set your own spend limits with your
  provider; we cannot enforce one for you.

## Acceptable use

XORCISE is offensive-security tooling. **Only point it at systems you own, or that you have
specific written authorisation to test.** See [ACCEPTABLE_USE.md](ACCEPTABLE_USE.md) for what
that means in practice, including export-control and sanctions obligations.

That policy is not an additional licence condition and does not narrow the rights Apache-2.0
grants you.
