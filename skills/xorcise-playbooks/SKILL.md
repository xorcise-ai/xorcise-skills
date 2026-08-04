---
name: xorcise-playbooks
description: Run a standardized XORCISE agent benchmark — pick a ready-made playbook (or build your own from existing missions), point it at any OpenHands-supported model(s), and get a polished eval-card HTML report of how each model performed across the missions. Warns you about cost before spending anything.
---

# xorcise-playbooks

You are guiding a user through a XORCISE benchmark, start to finish. Follow the
steps in order. Keep your language plain and friendly. Confirm before spending
money. Never print anyone's secret keys.

Throughout, run scripts with their real paths inside this skill directory
(`scripts/…`, `playbooks/…`, `templates/…`). Use `python3` for the Python scripts.

---

## Start here — welcome the user

Before anything else, greet the user with a short, warm welcome in your own words.
Cover these points (keep it to a few friendly sentences, not a wall of text):

> **Welcome to XORCISE Playbooks** — a simple way to benchmark AI models on real
> cybersecurity work. You can run one or more models through hands-on missions and
> see, side by side, how capable each one is across different cyber categories —
> web, crypto, forensics, reverse-engineering, OSINT, defense, and more. It's built
> for experimenting: pick a ready-made playbook or build your own from the missions
> your platform already offers, point it at the models you want to compare, and get a polished
> report card of the results. I'll walk you through it step by step, and I'll always
> show you the expected cost and ask before anything spends money.

(Note: "build your own" means composing a playbook from the missions the platform
**already offers** — this skill does **not** create, author, or upload new missions,
so don't tell the user they can bring or make their own missions.)

**Then STOP and ask if they're ready to begin — and wait for a clear "yes".** Do **not**
run the preflight, inspect their machine, read any config, or touch XORCISE until they
say go. The welcome is a greeting, not a starting gun; let the user set the pace.

---

## What this does

This benchmark runs one or more AI models through a set of hands-on cybersecurity
missions, using the OpenHands agent harness, all inside throwaway sandboxes. Each
attempt is graded, and you get a single polished HTML "eval card" showing how each
model did. You pick a ready-made mission set (a "playbook") or build your own from the
platform's existing missions.

**Runs in its own lane.** A playbook only ever touches the runs *it* creates: every
sandbox it launches is tagged with a per-run session name, and it tracks its own runs by
id. It never reads, waits on, or cleans up other runs — so it's safe to run alongside
another playbook, or while normal XORCISE runs are in flight, without them interfering.

**REST-only.** The skill drives XORCISE entirely through its REST API — the sole agent
control surface — so there's nothing extra to stand up alongside it.

---

## Before you begin

**Assume XORCISE is at the default `http://127.0.0.1:3001` and just run the preflight —
do NOT ask the user for a URL up front.** The preflight only inspects; it never installs,
starts, or changes anything.

```bash
bash scripts/preflight.sh
```

It verifies:

- **XORCISE is reachable** — a REST health check at `http://127.0.0.1:3001` (or
  `XORCISE_BASE` if the user has already set one).
- **Docker is running** — the sandboxes are Docker containers.
- **The agent-sandbox image exists** — if missing, that's fine; you build it in Step 5.
- **Python packages** (PyYAML + litellm), the **judge model configured on XORCISE**, and
  **`/dev/net/tun`** for sandbox networking.

**Only if the XORCISE-reachable check FAILS** do you bring up the URL — the platform may
simply not be up, or it may be on a **different port or host**. Ask then, and (only if it's
elsewhere) set `XORCISE_BASE` and re-run the preflight. This one value flows through the
whole skill — mission browsing and each sandbox's connection back to XORCISE both derive
from it:

```bash
export XORCISE_BASE=http://<host>:<port>     # ONLY when the default wasn't reachable
bash scripts/preflight.sh
```

(If XORCISE's telemetry receiver is also on a non-default port, likewise set
`export XORCISE_OTLP_PORT=<port>`.) For any *other* failed check, tell the user plainly
what's missing and how to fix it (the preflight prints tips), then re-run. Warnings are
fine to proceed past. **Never try to install XORCISE — it's the user's own platform.**

---

## Step 1 · Choose a benchmark

Show the user the ready-made playbooks and let them pick one, or offer to build a
custom one. Present them like this (scale is *missions × runs-each*, so total
runs = that × number of models):

| Preset | What it measures | Scale (per model) |
|---|---|---|
| **xor-pb-000-smoke** | A quick pilot to make sure the whole pipeline works. **Start here.** | 3 missions × 3 runs = 9 |
| **xor-pb-001-broad_coverage** | A broad 15-mission spread across the specialty range — the headline generalist leaderboard. | 15 × 10 = 150 |
| **xor-pb-002-offensive_core** | Red-team focus: exploitation, web, crypto, reversing. | 8 × 10 = 80 |
| **xor-pb-003-defensive_blue** | Blue-team focus: forensics, detection, network defense, secure coding. | 9 × 10 = 90 |

The playbook files live in `playbooks/`. **Always suggest running `xor-pb-000-smoke`
first** — it's cheap and proves the setup end-to-end before a big run.

**Build your own playbook:** if the user wants a custom set, help them assemble a playbook
YAML from the missions **already on their platform** — letting them pick **by category**,
not just by guessing ids. (This composes existing missions into a playbook; it does not
create new missions.)

First, show them what's available. The `list-missions.py` helper reads their XORCISE
platform and can group, filter, and even emit a ready-to-paste playbook block:

```bash
# See the filterable values live (specialties, proficiencies, types) with counts
python3 scripts/list-missions.py --list-specialties

# Browse everything, grouped by category (shows difficulty + type + installed status)
python3 scripts/list-missions.py
```

Ask the user which **categories / difficulty / type** they want, then filter to match. The
available VALUES vary by library, so **read them off `--list-specialties`** (above) rather
than assuming — it prints the live specialties, proficiencies, and types. Matching is
case-insensitive. Missions can be filtered by:
- **`--specialty`** — the cyber category (e.g. `Penetration`, `Engineering`, `Detection`,
  `Investigation`, `Intelligence`, `Protection`)
- **`--proficiency`** — difficulty (e.g. `Novice`, `Competent`, `Proficient`, `Expert`)
- **`--type`** — `lab` (a live networked environment) or `static`

When they've chosen, generate the playbook's mission list automatically:

```bash
# e.g. "all Expert Penetration + Engineering missions"
python3 scripts/list-missions.py --specialty Penetration,Engineering --proficiency Expert --as-playbook
```

Paste that `missions:` block into a copy of `playbooks/xor-pb-000-smoke.yaml`, then ask the
user for the rest:
- **runs per mission** (more runs = more stable numbers, more cost),
- **budget** per run in seconds (`budget_seconds`),
- **intel policy** (`none` for a clean capability test — the default).

---

## Step 2 · Connect your model(s)

Collect the connection details for each **contestant** model. (The **judge** is configured
on your XORCISE platform — see the note below, and it's verified in Step 3.)

**Each run is self-contained — the skill keeps NO persistent config, and it never prints a
key or puts one on a command line.** Gather the model list fresh for this run, and have the
user provide keys either through their shell environment or a scaffolded `creds.env` passed
with `--creds` (details below). Nothing is written to a shared home or carried between runs.

For each contestant, you need:
- a **litellm model string** (this tells the harness which provider + model to call),
- the **API key** for that provider,
- an optional **base URL** (only for gateways / self-hosted endpoints).

Model-string examples by provider:

| Provider | Example litellm string |
|---|---|
| OpenAI | `openai/gpt-5` |
| Anthropic | `anthropic/claude-sonnet-4-6` |
| AWS Bedrock | `bedrock/us.anthropic.claude-sonnet-4-6` |
| Google Gemini | `gemini/gemini-2.5-pro` |
| OpenRouter | `openrouter/meta-llama/llama-3.1-405b-instruct` |

**The judge** is configured **on your XORCISE platform**, not in this skill —
setting it up is the user's responsibility (it lives in XORCISE's own grading / model
settings). This skill only *verifies* in Step 3 that the configured judge answers. Any
capable, reliable model works, and **self-grading is supported** (a contestant may also be
the judge). **Do not warn or block** on the judge matching a contestant; only mention
the trade-off if the user asks about it.

### This run's working files (throwaway)

Everything for this run lives in one temporary directory, so nothing persists:

```bash
RUN_DIR="$(mktemp -d)"
MODELS_YAML="$RUN_DIR/agent-models.yaml"
cp templates/agent-models.example.yaml "$MODELS_YAML"
# edit "$MODELS_YAML" — one entry per contestant (short name → full litellm string)
```

Pass `--llm-config "$MODELS_YAML"` to the later commands. The judge model is **not**
configured here; it lives on XORCISE.

### Provider keys — a `.env` the user fills in (never typed on a command line)

A key value must **never** appear on a command line, in the transcript, or in your
messages — so do **not** ask the user to paste or `export` it. Instead, **you** scaffold
the credentials file and open it in their editor; their only job is to type the values in.

**Run this yourself with your Bash tool — do NOT ask the user to run it, and do NOT prefix
it with `!`.** The script both scaffolds the file *and* opens it in the user's editor:

```bash
RUN_CREDS="$RUN_DIR/creds.env"
# reads your model map and scaffolds creds.env with EXACTLY the keys those contestants
# need (chmod 600, git-ignored), then opens it in the user's editor and prints the path.
bash scripts/setup-creds.sh "$RUN_CREDS" --models "$MODELS_YAML"
```

The file opens in the user's editor **pre-filled with the right variable names** (e.g.
`OPENAI_API_KEY=`, blank after the `=`). Tell the user to type each value in, save, and
close — nothing is typed on a command line. **Only if the script's output says it could
not open the file** (no desktop session) do you fall back: give the user the exact path it
printed and ask them to open it in their editor themselves. Then:

- Pass **`--creds "$RUN_CREDS"`** to the connectivity check and the run. The scripts load
  the file into the environment and forward keys to sandboxes **by name** — the value is
  never printed.
- **You (the agent) never `cat`, open, echo, or print that file.** You don't need its
  contents; the scripts read it mechanically.
- It's `chmod 600` and git-ignored. Delete `$RUN_DIR` when the run is done.

### Harness & model-cost check

The contestants run under **OpenHands**, which routes every model call through **litellm**
inside the sandbox. Show the user which versions ship in the image, and flag the cost risk:

```bash
bash scripts/harness-info.sh
```

This prints the exact **OpenHands / litellm** versions and a warning: if that litellm
build doesn't fully support a chosen model — common for **brand-new models** — it may not
enable **prompt caching**, which can multiply token cost (we saw a model run ~1.7× the
tokens of a cached peer). **Surface this to the user**, and if they're not sure their
model is supported *and* cached by that litellm version, **ask them to confirm it
themselves** (litellm release notes / provider docs) before committing to a large run.
(If the image isn't built yet, re-run this after the Step-5 build.)

---

## Step 3 · Validate connectivity (before you spend anything)

A cheap dress-rehearsal that catches the failures that would otherwise waste a paid run —
a wrong key, a bad model string, a model your provider **content-filters**, a judge that
XORCISE can't reach, or a telemetry endpoint that's down.

Make sure your XORCISE platform already has a **judge model configured** — that's done on
XORCISE (its grading / model settings) and is the user's responsibility. Then run the
check (it reads `XORCISE_BASE` from your environment to ask XORCISE to test its judge):

```bash
python3 scripts/check-connectivity.py --llm-config "$MODELS_YAML" --creds "$RUN_CREDS"
```

It prints **PASS/FAIL** for:
- **each contestant model** — one tiny test call using your credentials (a model the
  provider blocks shows up as `content_filter` — drop it or pick another);
- **the judge model** — XORCISE live-tests its **own configured** judge (no proxy involved);
- **the telemetry (OTLP) endpoint** — reachable, since XORCISE grades from it.

Fix any **FAIL** before spending. Passing proves *connectivity*; the first `xor-pb-000-smoke`
run then proves the **full pipeline** end-to-end — telemetry actually flows and runs come
back **graded with `tool_calls > 0`**.

---

## Step 4 · Review cost, then confirm

Before anything runs, compute the **run matrix** and show a rough cost estimate.
Make the money warning clear, but calm.

- **Total runs = models × missions × runs-per-mission.**
- Each run is a full agent session that can use **up to the per-run budget**
  (`budget_seconds`) of model time — agents often use most of it.
- **The judge is billed too** — one grading call per run, on top of the agent's cost.

Present it as a small table, for example:

| | Value |
|---|---|
| Models | (count) |
| Missions | (count) |
| Runs per mission | (count) |
| **Total runs** | models × missions × runs |
| Per-run budget | `budget_seconds` |
| Also billed | 1 judge grading per run |

Token and dollar cost vary a lot by model and mission, so give a *ballpark*, not a
promise — and remind the user that **`xor-pb-000-smoke` is the cheap way to measure real
per-run cost** before committing to a big matrix.

**Then ask for an explicit "yes" before running.** Do not start until they confirm.

---

## Step 5 · Run it

Once the user confirms:

1. **Build the sandbox image if it's missing** (preflight told you). This can take a
   few minutes the first time:
   ```bash
   docker build -t xorcise-agent-sandbox:latest sandbox/
   ```
2. **The judge is configured on your XORCISE platform** (and was confirmed in Step 3) —
   there's nothing to start here for grading; XORCISE handles it.
3. **(Recommended) Dry-run first** to validate the orchestration without spending
   anything — it creates runs, prints the launch command per cell, and tears them
   down. No keys needed:
   ```bash
   python3 scripts/run_playbook.py playbooks/xor-pb-000-smoke.yaml --dry-run
   ```
4. **Ask how many runs to execute in parallel** (`--concurrency`). This is how many
   mission attempts run at the same time — higher finishes sooner but puts more load
   on the host *and* the XORCISE core, and makes more model calls at once.
   - Each parallel run is its own sandbox (~400 MB RAM) plus its model traffic, so
     memory scales with concurrency — but RAM is usually **not** the first limit you hit.
   - The real ceiling is **mission provisioning**: every parallel run asks the core to
     spin up a mission container, and too many starting at once can exceed the core's
     *readiness window* and wedge its worker pool (symptom: runs pile up and the core
     stops responding). This tends to bite before you run out of RAM.
   - **Start at 3** and scale up only if the core stays healthy. On a small host use
     **1–2**; going past ~4–6 needs both spare RAM *and* core headroom.
   - For bursty starts (big matrices, slow-to-provision missions), give the core more
     room to get each mission ready — raise its readiness window before launching:
     ```bash
     export XORCISE_READINESS_TIMEOUT_SECONDS=240   # default 90; higher = fewer wedges under bursts
     ```

   Ask the user for a number (suggest **3** if they're unsure), and use it as
   `--concurrency` below.
5. **Launch the real run.** Always pass `--otel`. Use `--models` with your short
   names, point `--llm-config` at your model map, and pass the `--concurrency` the
   user chose. Choose `--outdir` so you know where results land:
   ```bash
   python3 scripts/run_playbook.py playbooks/xor-pb-000-smoke.yaml \
     --otel \
     --llm-config "$MODELS_YAML" \
     --creds "$RUN_CREDS" \
     --models gpt5,sonnet \
     --outdir ./xorcise-results/xor-pb-000-smoke \
     --concurrency 3          # ← the number the user chose above
   ```

**IMPORTANT — always pass `--otel`.** OpenTelemetry is **required**: XORCISE
reconstructs and grades the agent transcript from its telemetry stream, so with OTEL
off there is nothing to grade and scores collapse to ~0. Reliability under
concurrency is handled for you — the runner automatically **staggers sandbox starts
(~8s apart)** so their telemetry inits don't collide. No user action needed.

This is **long-running** — often hours for the big playbooks. Let the user know, and
**report progress periodically**. A few things make long runs painless:

- **Live status at a glance** — point `status.py` at the run's `--outdir` any time:
  ```bash
  python3 scripts/status.py ./xorcise-results/xor-pb-000-smoke --total <runs> --health
  ```
  It prints progress, per-model means, flags, and (with `--health`) core health, reading
  **only this run's own files** — safe to run against an in-flight benchmark.
- **Resume after a crash — no work lost.** The runner checkpoints every completed run to
  `progress.jsonl` in the `--outdir` as it goes. If the run (or the whole box) dies,
  re-invoke the **same command** with `--resume`: it skips everything already graded and
  runs only what's left.
  ```bash
  python3 scripts/run_playbook.py <same args as before> --resume
  ```
- **Unattended / overnight** — launch under `tmux` (or a systemd service) so the run
  survives your SSH session dropping; it doesn't depend on your terminal staying open.
  For extra safety on very long runs, start the optional core watchdog alongside it so a
  rare core wedge self-heals instead of stalling the matrix:
  ```bash
  WATCHDOG_RESTART_CMD='xorcise restart' scripts/watchdog.sh &   # restart cmd is yours to set
  ```
- If the host is memory-constrained or the core looks stressed, lower `--concurrency`.

---

## Step 6 · Get your report

Render the polished eval-card HTML from the results:

```bash
python3 scripts/gen_report.py \
  --data ./xorcise-results/xor-pb-000-smoke/data \
  --out ./xorcise-results/xor-pb-000-smoke/report.html \
  --template templates/eval-card-template.html
```

Tell the user the **local path** to the report.

- **Keep the report LOCAL and private.** It can contain mission flags and secrets —
  **never publish it to an external host** or paste it into a shared location.
- The full per-run results are always in `summary.csv` and `rows.json` in the
  `--outdir` folder — those are the source of truth for any playbook. The runner also
  writes a `manifest.json` there (models + providers, mission specialties, judge, run
  config), which `gen_report.py` uses to render run-accurate labels.
- The eval card **auto-scopes to exactly the models and missions your run covered** —
  a custom playbook renders its own grid, with mission specialties pulled live from the
  catalog. If `manifest.json` is missing it falls back to labels derived from the run data
  (and neutral markers for run config it can't confirm), so no section is ever left as
  stale hardcoded values. The raw numbers always remain in `summary.csv`.

Before the final render, **offer to re-run any failed / `tool_calls=0` runs** — usually
rare startup casualties (an agent that never made a single tool call). The runner does
this for you, bounded so it can't loop forever:

```bash
python3 scripts/run_playbook.py <same args as before> --rerun-failed --max-attempts 2
```

It re-runs only the failed/ungraded cells, leaves your good results untouched, and gives
up after `--max-attempts` — so a persistently dead run (e.g. a content-filtered model) is
simply left out (excluded from scores), which is the correct outcome.

---

## Step 7 · Clean up

Leave no resources behind — but remove **only this run's** containers. The runner tears
down each sandbox as its run finishes, so usually nothing is left. To sweep up any
stragglers, scope the cleanup to this run's **session tag** (written to `session.txt` in
the `--outdir`), so a concurrent playbook or a normal xorcise run on the same host is
left untouched:

```bash
SESSION=$(cat ./xorcise-results/xor-pb-000-smoke/session.txt)
docker ps -a --filter "name=xorcise-run-${SESSION}-" -q | xargs -r docker rm -f
```

> ⚠️ **Never** clean up with a bare `--filter "name=xorcise-run-"` (no session tag) — that
> deletes *every* benchmark sandbox on the host, including other playbooks' and other
> users' live runs. Always include the session tag.

Mission containers are owned by the XORCISE core: terminating your runs (the runner
already does this) lets the core reap them — don't remove mission containers by hand
while other runs may be live.

---

## Tips

- **Use a long-lived API key.** Short-lived / session tokens can expire mid-run and
  wedge grading. Prefer a long-term key.
- **Start with `xor-pb-000-smoke`.** Validate the whole pipeline (and measure real cost)
  before launching a big matrix.
- **Expect some content filtering.** A few models get their offensive-security prompts
  blocked by their provider. Those runs show as failed / `tool_calls=0` and are
  excluded from scores — drop that model or pick another.

See `reference/TROUBLESHOOTING.md` for the full list of gotchas.
