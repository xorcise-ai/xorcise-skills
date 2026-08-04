# Troubleshooting — hard-won lessons

These are the gotchas that actually bite people during a XORCISE benchmark run.
Skim this before a big run; come back to it if something looks off.

---

## 1. Use a long-lived API key

Short-lived or STS **session credentials expire mid-run**. A benchmark can take
hours, so a token that was valid at launch can go stale halfway through — and an
expired credential on the grading path can wedge runs (they finish but never get
a grade).

- Prefer a **long-term API key** for every provider.
- For AWS Bedrock, use a **long-term Bedrock API key** (`AWS_BEARER_TOKEN_BEDROCK`)
  rather than `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN`.
  The bearer key doesn't ride the STS expiry treadmill.
- Provide provider keys one of two ways: **export them in your shell** before the run, or put
  them in a scaffolded **`creds.env`** (git-ignored, `chmod 600`) and pass **`--creds`**. The
  runner loads them mechanically and passes them to sandboxes **by name** — it never prints a
  key or puts one on a command line. Nothing carries between runs; the `creds.env` is yours to
  keep, rotate, or delete.

## 2. Content filtering (offensive-security prompts)

Some providers **hard-filter offensive-security prompts** for certain models.
When that happens the model returns `finish_reason=content_filter`, empty output,
and makes **zero tool calls** — so the run does nothing useful.

- These runs show up as `tool_calls=0` and are **excluded from scores** (they are
  counted as failed, not graded).
- If a model is consistently filtered on the offensive playbooks (xor-pb-001 / xor-pb-002),
  it can't compete there — **drop it** or pick a different model.
- Example seen in practice: one frontier model was filtered on Bedrock for the
  offensive missions and had to be removed from the pool.

## 3. Telemetry is required; rare startup stalls

OpenTelemetry is **mandatory** — XORCISE reconstructs and grades the agent
transcript from its telemetry stream, so with OTEL off there is nothing to grade and
scores collapse to ~0. **Always run with `--otel`.**

The one real hazard is a cold-start race: when several sandboxes start at the same
instant, their telemetry inits can collide and an agent may hang before its first
model call. The runner defuses this by **staggering sandbox starts (~8s apart)**, so
you don't need to do anything.

- A rare run may still stall and show `tool_calls=0`. Those are **excluded from
  scores** and should simply be **re-run** (the skill's cleanup / re-run step does this).
- Do **not** disable OTEL to work around a stall — that just makes every run
  ungradable.

## 4. Grade-wait (grading lands after the run finishes)

Grading can arrive **seconds to minutes after** a run goes terminal. Large or
verbose transcripts grade slowly. The runner already accounts for this: it polls
`/result` and **waits up to ~5 minutes** for a real grade before recording the row.

- If you see rows with `overall=None`, the grade may simply not have landed in time,
  or the judge may have been unavailable (very large transcript). Consider raising
  the judge's transcript token ceiling or the per-run budget.

## 5. Concurrency: provisioning is the real ceiling, not RAM

Each sandbox is small (~400 MB), so it's tempting to size `--concurrency` off free RAM.
But the limit you hit **first** is usually **mission provisioning**: every parallel run
asks the core to spin up a mission container, and too many starting at once can exceed
the core's *readiness window* and wedge its worker pool. Progress then stalls and the core
stops responding — well before memory is exhausted.

- **Start at `--concurrency 3`** and scale up only if the core stays healthy. Use 1–2 on
  a small host; past ~4–6 you need both spare RAM *and* core headroom.
- For bursty starts (big matrices, slow-to-provision missions), raise the core's
  readiness window before launching:
  `export XORCISE_READINESS_TIMEOUT_SECONDS=240` (default 90).
- On a genuinely memory-constrained host, lower `--concurrency` too — memory still scales
  with it.
- If the core does wedge on a long run, the optional `scripts/watchdog.sh` restarts it
  automatically after a sustained outage (see §8).

## 6. Keep reports local

The generated eval-card HTML can contain **mission flags and secrets**.

- Keep the report **local / private**. Do **not** upload it to any external host or
  paste it into a shared/public location.
- Share findings as numbers or screenshots with the sensitive parts removed instead.

## 7. XORCISE on a non-default port or host

If your XORCISE isn't at `http://127.0.0.1:3001`, you **must** tell the skill where it
is — otherwise the runner creates runs on the right port but each sandbox still tries to
reach run-control on the old default and the runs fail.

- Set it **once, in the shell you run everything from**, before the preflight:
  ```bash
  export XORCISE_BASE=http://<host>:<port>
  ```
  This flows everywhere — preflight, `list-missions.py`, and the sandbox's connection
  back to XORCISE (the run-control port is derived from `XORCISE_BASE`).
- If XORCISE's **telemetry (OTLP) receiver** is also on a non-default port, set
  `export XORCISE_OTLP_PORT=<port>` (default `4318`) too.
- Symptom when this is missed: runs are created but every one fails/stalls, and the
  sandbox log shows it can't fetch `.../connect` from run-control.

## 8. Long runs: resume, live status, unattended

A big matrix runs for hours; make it robust:

- **Resume after a crash.** The runner checkpoints each completed run to `progress.jsonl`
  in the `--outdir`. Re-run the **same command** with `--resume` to skip everything
  already graded and finish only what's left — nothing lost, nothing repeated.
- **Re-run failures only.** `--rerun-failed --max-attempts 2` re-runs just the
  failed/ungraded cells, bounded so a permanently-dead run (e.g. content-filtered) is
  given up on rather than retried forever.
- **Live status.** `scripts/status.py <outdir> --health` prints progress + per-model
  means/flags + core health, reading only that run's own files (safe on an in-flight run).
- **Unattended.** Run under tmux/systemd so it survives a dropped SSH session; optionally
  run `scripts/watchdog.sh` (set `WATCHDOG_RESTART_CMD`) so a core wedge self-heals.

## 9. Running alongside other runs (isolation)

The skill acts **only on the runs it creates**. Its sandboxes are named
`xorcise-run-<session>-<id>` (the session tag is in `session.txt` in the `--outdir`), and
it tracks its runs by id — so a second playbook or a normal xorcise run won't interfere
with it, and it won't interfere with them.

- When cleaning up (Step 7), **always scope to the session tag**:
  `--filter "name=xorcise-run-<session>-"`. A bare `name=xorcise-run-` filter deletes
  *every* benchmark sandbox on the host, including other people's live runs.
- Let the core reap mission containers (it owns them); don't remove them by hand while
  other runs may be live.

---

### Quick symptom → cause table

| Symptom | Likely cause | Fix |
|---|---|---|
| Run finishes but `overall=None` | Grade not landed yet, or judge unavailable | Wait; raise judge tokens / budget |
| `tool_calls=0` on some runs | Cold-start race, or content filtering | `--rerun-failed --max-attempts 2`; drop filtered models |
| Whole wave stalls at startup | Telemetry-init collision on simultaneous starts | Runner staggers starts (~8s); `--rerun-failed` the `tool_calls=0` ones |
| Auth errors partway through a long run | Short-lived key expired | Use a long-term key; rotate the creds file |
| Core wedges; runs pile up, core stops responding | Concurrency too high → readiness timeout | Lower `--concurrency`; raise `XORCISE_READINESS_TIMEOUT_SECONDS`; use `watchdog.sh` |
| Host swapping / OOM | Concurrency too high for RAM | Lower `--concurrency` |
| Run or box died mid-matrix | Crash / OOM / reboot | Re-run the same command with `--resume` |
| Another playbook's containers vanished | Cleanup used a bare `xorcise-run-` filter | Scope cleanup to the session tag (Step 7 / §9) |
| Every run fails; sandbox can't reach `/connect` | XORCISE on a non-default port/host | `export XORCISE_BASE=http://host:port` before running |
| Sandbox `curl (7)` to run-control/OTEL; agent never starts, `overall=0.0` | Sandbox→host address wrong for your Docker networking | Default `host.docker.internal` works on macOS/Windows/Linux; override with `--run-control-host <addr>` |
