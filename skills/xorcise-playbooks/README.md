# xorcise-playbooks

A self-contained, distributable skill for running a **standardized XORCISE agent
benchmark**. Pick a ready-made playbook (or build your own from existing missions), point it at any
OpenHands-supported model(s), and get a polished eval-card HTML report of how each
model performed — with a clear cost check before anything is spent.

The harness is fixed (the standardized sandboxed OpenHands CLI), so you benchmark
*models*, not harnesses. Every agent runs in its own throwaway Docker sandbox that
joins the mission network directly, so runs are clean and parallel-safe.

## The flow at a glance

The skill opens with a short **welcome** explaining what it's for, then a read-only
`scripts/preflight.sh` check, then seven guided steps:

1. **Choose a benchmark** — a preset playbook (the `xor-pb-*` set), or build your own playbook by
   picking from the platform's **existing** missions **by category**
   (`scripts/list-missions.py` filters by specialty,
   difficulty, and type, and emits a ready-to-paste mission list).
2. **Connect your model(s)** — litellm model strings + keys for contestants. (The
   judge model is configured on your XORCISE platform, not here.)
3. **Validate connectivity** — one cheap test call per model, plus XORCISE live-testing
   its own configured judge, plus the telemetry endpoint — so a wrong key, a filtered
   model, or an unconfigured judge is caught before you spend.
4. **Review cost, then confirm** — see the run matrix and a ballpark spend; approve.
5. **Run it** — choose how many runs go in **parallel** (`--concurrency`), build the
   sandbox, launch the runner with `--otel` (telemetry is required; XORCISE grades
   from it, and the runner staggers sandbox starts for reliability).
6. **Get your report** — render the eval-card HTML (keep it local).
7. **Clean up** — remove leftover containers.

## Prerequisites

- **XORCISE** installed and running (its REST API reachable, default
  `http://127.0.0.1:3001`). This skill never installs XORCISE for you.
- **Docker** running, with permission to build images and run containers.
- **`/dev/net/tun`** available (sandboxes join the mission network in TUN mode).
- **python3 + PyYAML** for the runner and report generator.
- **A long-lived API key** for each provider you want to benchmark.

Run `bash scripts/preflight.sh` to check all of the above at once.

## How to invoke

This is an agent skill. Load `SKILL.md` and follow it — it opens with a welcome, then
walks you (and the user) through all seven steps in plain language, confirming cost
before spending anything.

## What's in here

```
xorcise-playbooks/
├── SKILL.md                              # the step-by-step operator guide (start here)
├── README.md                             # this file
├── scripts/
│   ├── run_playbook.py                   # the benchmark runner/orchestrator
│   ├── gen_report.py                     # renders the eval-card HTML from results
│   ├── list-missions.py                # browse/filter missions by category (read-only)
│   ├── check-connectivity.py             # pre-run model/judge/telemetry check
│   └── preflight.sh                      # checks prerequisites (read-only)
├── sandbox/
│   ├── Dockerfile                        # the per-run agent sandbox image
│   └── join-and-run.sh                   # sandbox entrypoint (joins tailnet, runs agent)
├── templates/
│   ├── eval-card-template.html           # the report template
│   └── agent-models.example.yaml         # short-name → litellm model-string map
├── playbooks/
│   ├── xor-pb-000-smoke.yaml                     # quick pilot — run first
│   ├── xor-pb-001-broad_coverage.yaml            # generalist leaderboard (one per specialty)
│   ├── xor-pb-002-offensive_core.yaml            # red-team focus
│   └── xor-pb-003-defensive_blue.yaml            # blue-team focus
└── reference/
    └── TROUBLESHOOTING.md                # the hard-won gotchas
```

## Notes on cost and safety

- **Total runs = models × missions × runs-per-mission.** Each run can use up to
  the per-run budget of model time, and the judge is billed once per run. Start with
  `xor-pb-000-smoke` to measure real cost before a big matrix.
- **Keep reports local.** The eval-card HTML can contain mission flags and secrets.
- **Never print keys.** Put provider keys in your shell **environment**, or in a scaffolded
  `creds.env` (git-ignored, `chmod 600`) passed with `--creds`. Either way the runner loads
  them mechanically — never printing a value or putting one on a command line — and forwards
  them to sandboxes **by name**. Each run is self-contained; nothing persists between runs.
