#!/usr/bin/env python3
"""
XORCISE P1 eval-card report generator.

Reads a running/partial P1 benchmark run directory and fills the self-contained
HTML eval-card template with real aggregates. Pure Python 3 stdlib.

Data model
----------
The playbook log (``<outdir>.log``) has one line per *completed* run:

    done <model>/<mission>#<n>: overall=<float|None> flag=<True|False>

That log is the authoritative set of completed runs (so counts stay consistent
with it). Each completed run is then enriched from its per-run data files:

    data/<model>-<mission>-<n>.stats.json    counts / tokens / timing
    data/<model>-<mission>-<n>.result.json   grade{overall, breakdown{det,judge}}

``rows.json`` / ``summary.csv`` at the outdir root are preferred as the run
source if present (neither exists in the current run, so the log path is used).

Runs with ``tool_calls == 0`` are cold-start failures; they are excluded from
score statistics but counted and reported per model.

Usage
-----
    python3 gen_report.py [--data DIR] [--out FILE] [--template FILE] [--date STR]
"""

import argparse
import csv
import datetime
import glob
import html
import json
import os
import re
import statistics
import urllib.request
import uuid

# --------------------------------------------------------------------------- #
# Canonical benchmark facts (from the P1 playbook / run conditions).
# opus-5 was dropped: Bedrock hard content-filters offensive-security prompts
# for it (finish_reason=content_filter). It is intentionally absent below.
# --------------------------------------------------------------------------- #

# name, provider label, bedrock provider-map id
MODELS = [
    ("sonnet-5",   "Anthropic",   "bedrock/au.anthropic.claude-sonnet-5"),
    ("sonnet-4-6", "Anthropic",   "bedrock/au.anthropic.claude-sonnet-4-6"),
    ("glm-5",      "Z.ai",        "bedrock/converse/zai.glm-5"),
    ("kimi-2.5",   "Moonshot AI", "bedrock/converse/moonshotai.kimi-k2.5"),
]

# name, heatmap code, short specialty (heatmap), full specialty (table)
MISSIONS = [
    ("definer-cascade",        "DEF-CAS", "web",     "web"),
    ("chrono-canary",          "CHR",    "pwn",      "pwn"),
    ("aviary-access",          "AVI",    "vuln",     "vulnerability-assessment"),
    ("process-of-elimination", "PROC",   "detect",   "detection"),
    ("inline-sentinel",        "IN-SEN", "net-def",  "network-defense"),
    ("derelict-manifest",      "DER",    "rev-eng",  "reverse-engineering"),
    ("vanishing-point",        "VAN",    "swe",      "software-engineering"),
    ("sparse-signal",          "SPR",    "crypto",   "crypto"),
    ("ghostwire",              "GHOST",  "osint",    "osint"),
    ("turnstile-gauntlet",     "TURN",   "auto",     "automation"),
    ("rogue-uplink",           "RG-UPL", "net",      "network"),
    ("jumbo-overrun",          "JMB",    "sec-code", "secure-coding"),
    ("pipe-dream",             "PIPE",   "forensic", "forensics"),
    ("cipher-beacon",          "CIP",    "malware",  "malware-analysis"),
    ("guardrail-proxy",        "GRD",    "ai-sec",   "ai-security"),
]

MODEL_NAMES = [m[0] for m in MODELS]
PROVIDER = {m[0]: m[1] for m in MODELS}
MISSION_NAMES = [c[0] for c in MISSIONS]
MISSION_FULLSPEC = {c[0]: c[3] for c in MISSIONS}

# Snapshot the built-in defaults BEFORE derive_grid() reassigns the grid globals, so they
# stay available as a last-resort metadata source (after live XORCISE + the run manifest).
_BUILTIN_MISSION_META = {c[0]: (c[1], c[3]) for c in MISSIONS}   # name -> (code, specialty)
_BUILTIN_MODEL_META = {m[0]: (m[1], m[2]) for m in MODELS}       # name -> (provider, litellm)

TOTAL_PLANNED = len(MISSIONS) * len(MODELS) * 3  # 15 * 4 * 3 = 180

# judge_status values where the judge produced no score (its half folds in as 0.0):
# "unavailable" (call failed) and "model-not-configured" (no judge set). "partial"
# is tracked separately below — it scored some criteria, so it's a conservative lower
# bound rather than a zero.
_JUDGE_ZERO_STATUSES = ("unavailable", "model-not-configured")

# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

DASH = "&mdash;"


def esc(x):
    return html.escape(str(x))


def fmt_pct1(x):
    """0..100 -> '78.4', None -> em dash."""
    return DASH if x is None else "{:.1f}".format(x)


def fmt_int(x):
    return DASH if x is None else "{:d}".format(int(round(x)))


def fmt_frac2(x):
    return DASH if x is None else "{:.2f}".format(x)


def fmt_tokens(t):
    if t is None:
        return DASH
    t = float(t)
    if t >= 1e9:
        return "{:.1f}B".format(t / 1e9)
    if t >= 1e6:
        return "{:.1f}M".format(t / 1e6)
    if t >= 1e3:
        return "{:.0f}K".format(t / 1e3)
    return "{:d}".format(int(t))


def fmt_dur_short(sec):
    """Per-run elapsed -> '19m 44s'."""
    if sec is None:
        return DASH
    sec = int(round(sec))
    m, s = divmod(sec, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return "{:d}h {:02d}m".format(h, m)
    return "{:d}m {:02d}s".format(m, s)


def fmt_wallclock(sec):
    """Cumulative compute -> '71h 12m' / '4h 05m' / '48m'."""
    if sec is None:
        return DASH
    total_m = int(round(sec / 60.0))
    h, m = divmod(total_m, 60)
    if h:
        return "{:d}h {:02d}m".format(h, m)
    return "{:d}m".format(m)


def score_color(s):
    """s on 0..100 -> template CSS var for bar/meter fill."""
    if s >= 80:
        return "var(--data)"
    if s >= 50:
        return "var(--warn)"
    return "var(--err)"


def band_class(s):
    if s >= 80:
        return "b-high"
    if s >= 50:
        return "b-mid"
    return "b-low"


def mean(xs):
    return statistics.fmean(xs) if xs else None


def stdev(xs):
    return statistics.stdev(xs) if len(xs) >= 2 else None


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #

LOG_RE = re.compile(
    r"^\s*done\s+(?P<model>[^/]+)/(?P<mission>[^#]+)#(?P<n>\d+):\s*"
    r"overall=(?P<overall>\S+)\s+flag=(?P<flag>\w+)"
)


def parse_log(log_path):
    """Return list of dicts: {model, mission, n, log_overall, flag} from done lines."""
    runs = []
    if not os.path.exists(log_path):
        return runs
    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = LOG_RE.match(line)
            if not m:
                continue
            ov = m.group("overall")
            log_overall = None if ov == "None" else float(ov)
            runs.append({
                "model": m.group("model").strip(),
                "mission": m.group("mission").strip(),
                "n": int(m.group("n")),
                "log_overall": log_overall,
                "flag": (m.group("flag") == "True"),
            })
    return runs


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return json.load(fh)
    except Exception:
        return None


def enrich_run(run, data_dir):
    """Attach stats/result fields to a run dict (graceful on missing/partial)."""
    base = "{}-{}-{}".format(run["model"], run["mission"], run["n"])
    stats = _load_json(os.path.join(data_dir, base + ".stats.json")) or {}
    result = _load_json(os.path.join(data_dir, base + ".result.json")) or {}

    counts = stats.get("counts") or {}
    tokens = stats.get("tokens") or {}
    timing = stats.get("timing") or {}

    run["tool_calls"] = counts.get("tool_calls")          # int or None
    run["model_calls"] = counts.get("model_calls", run.get("model_calls"))  # int or None
    run["tokens_total"] = tokens.get("total")             # int or None
    run["elapsed"] = timing.get("elapsed_seconds")        # float or None
    run["has_stats"] = bool(stats)

    grade = result.get("grade") or {}
    breakdown = grade.get("breakdown") or {}
    run["overall"] = grade.get("overall", run.get("log_overall"))
    run["deterministic"] = breakdown.get("deterministic")
    run["judge"] = breakdown.get("judge")
    run["judge_status"] = grade.get("judge_status")
    run["has_grade"] = bool(grade)

    # Flag from the SEALED grade (authoritative): a flag counts ONLY if its check passed.
    # This overrides rows.json's flag_pass, which historically counted a submitted value
    # (even a WRONG flag) as a pass — so the report is correct even on un-re-derived data.
    if grade:
        fc = [c for c in (grade.get("check_breakdown") or [])
              if "flag" in ((c.get("id") or "") + str(c.get("check_id") or "")).lower()]
        if fc:
            run["flag"] = bool(any(c.get("passed") for c in fc))

    # A run is valid for score stats iff it actually made tool calls.
    tc = run["tool_calls"]
    run["valid"] = (tc is not None and tc > 0)
    run["excluded_zero_tools"] = (tc == 0)
    return run


def load_runs(data_dir):
    """
    Load completed runs. Prefer rows.json / summary.csv at the outdir root if
    present; otherwise reconstruct from the playbook log + per-run data files.
    """
    outdir = os.path.dirname(os.path.abspath(data_dir.rstrip("/")))
    root_rows = os.path.join(outdir, "rows.json")
    root_csv = os.path.join(outdir, "summary.csv")

    runs = None
    if os.path.exists(root_rows):
        rows = _load_json(root_rows) or []
        runs = []
        for r in rows:
            runs.append({
                "model": r.get("model"),
                "mission": r.get("mission"),
                # this runner writes the run index as `run_idx` and the flag as `flag_pass`;
                # keep the older `n`/`run`/`flag` names as fallbacks for other producers.
                "n": int(r.get("n") or r.get("run") or r.get("run_idx") or 0),
                "log_overall": r.get("overall"),
                "flag": bool(r.get("flag") if r.get("flag") is not None else r.get("flag_pass")),
                "error": r.get("error"),            # orchestration/create failure (no data)
                "model_calls": r.get("model_calls"),  # to separate refusals from cold-starts
            })
    elif os.path.exists(root_csv):
        runs = []
        with open(root_csv, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                runs.append({
                    "model": r.get("model"),
                    "mission": r.get("mission"),
                    "n": int(r.get("n") or r.get("run") or 0),
                    "log_overall": (None if r.get("overall") in (None, "", "None")
                                    else float(r["overall"])),
                    "flag": str(r.get("flag")).strip().lower() in ("true", "1", "yes"),
                })

    source = "rows.json/summary.csv"
    if runs is None:
        log_path = outdir + ".log"
        runs = parse_log(log_path)
        source = os.path.basename(log_path)

    for run in runs:
        enrich_run(run, data_dir)
    return runs, source


# --------------------------------------------------------------------------- #
# Grid scoping — render the models x missions that ACTUALLY ran, not the
# hardcoded P1 grid.  Priority:
#   1. a manifest.json in the run's output dir (rich: provider, specialty, run
#      config for the methodology table)
#   2. the built-in P1 grid, IF every model + mission that ran belongs to it
#      (keeps P1's curated labels; scopes down to only what ran)
#   3. otherwise derive names straight from the run rows (+ provider heuristic)
# --------------------------------------------------------------------------- #

RUNS_PER = 3            # runs-per-cell for this batch (set by derive_grid)
METHOD_FACTS = {}       # optional methodology overrides from a manifest
GRID_SOURCE = "builtin"  # "manifest" | "builtin" | "derived"

# Batch identity + timing (set from manifest.json by derive_grid) — referenceable evidence.
REPORT_UUID = None       # str UUID
STARTED_AT = None        # ISO-8601 UTC
COMPLETED_AT = None      # ISO-8601 UTC
REPORT_TITLE = "XORCISE Evaluation"   # report h1 + <title>; set from the manifest playbook
REPORT_DESC = ""         # playbook one-line description, for the summary lead
PLAYBOOK_NAME = ""       # full playbook name (e.g. xor-pb-002-offensive_core) for the repro table
_DTG_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _parse_iso(s):
    """ISO-8601 (with optional Z / offset) -> aware UTC datetime, or None."""
    if not s:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def fmt_dtg(s):
    """ISO timestamp -> military DTG, e.g. '310430Z JUL 26' (DDHHMM Z MON YY, UTC)."""
    dt = _parse_iso(s)
    if not dt:
        return DASH
    return "{:02d}{:02d}{:02d}Z {} {:02d}".format(
        dt.day, dt.hour, dt.minute, _DTG_MONTHS[dt.month - 1], dt.year % 100)


def fmt_duration_between(start_s, end_s):
    """Wall-clock between two ISO timestamps -> '8h 30m' / '48m' (xorcise-style)."""
    a, b = _parse_iso(start_s), _parse_iso(end_s)
    if not a or not b:
        return DASH
    sec = (b - a).total_seconds()
    return fmt_wallclock(sec) if sec >= 0 else DASH

_PROVIDER_HINTS = [
    ("sonnet", "Anthropic"), ("opus", "Anthropic"), ("haiku", "Anthropic"),
    ("claude", "Anthropic"), ("gpt", "OpenAI"), ("o1", "OpenAI"),
    ("o3", "OpenAI"), ("o4", "OpenAI"), ("gemini", "Google"),
    ("glm", "Z.ai"), ("zai", "Z.ai"), ("kimi", "Moonshot AI"),
    ("moonshot", "Moonshot AI"), ("llama", "Meta"), ("mistral", "Mistral"),
    ("deepseek", "DeepSeek"), ("grok", "xAI"), ("qwen", "Alibaba"),
    ("command", "Cohere"),
]


def _provider_for(name):
    n = (name or "").lower()
    for key, val in _PROVIDER_HINTS:
        if key in n:
            return val
    return ""


def _code_for(name):
    parts = [p for p in re.split(r"[-_ ]+", name or "") if p]
    if not parts:
        return (name or "?")[:5].upper()
    if len(parts) == 1:
        return parts[0][:5].upper()
    return "-".join(p[:3].upper() for p in parts[:2])


def _fetch_catalog_specialties(base):
    """Best-effort {mission_id: specialty} from XORCISE's LIVE catalog. Returns {} when base
    is empty or the fetch fails (offline report generation, XORCISE down, non-default port) —
    the caller then falls back to the run's manifest.json and the built-in defaults."""
    if not base:
        return {}
    try:
        url = base.rstrip("/") + "/api/missions"
        with urllib.request.urlopen(url, timeout=4) as r:
            data = json.loads(r.read().decode())
        rows = data if isinstance(data, list) else (data.get("missions") or [])
        out = {}
        for m in rows:
            mid = m.get("mission_id") or m.get("name")
            if mid:
                out[mid] = m.get("specialty") or ""
        return out
    except Exception:
        return {}


def _derive_title(name):
    """Turn a playbook name into a report title: 'xor-pb-002-offensive_core' -> 'P2 — Offensive Core'.
    Non-matching labels are humanized ('offensive' -> 'Offensive')."""
    if not name:
        return ""
    m = re.match(r"xor-pb-0*(\d+)-(.+)", name)
    if m:
        return "P{} — {}".format(m.group(1), m.group(2).replace("_", " ").replace("-", " ").title())
    return name.replace("_", " ").replace("-", " ").title()


def build_report_lead(scope):
    """A factual, playbook-agnostic summary lead (replaces the P1-specific template prose)."""
    nm, nc, r = len(scope["models"]), len(scope["missions"]), scope["runs_per"]
    _d = esc(REPORT_DESC).strip()
    desc = (_d.rstrip(".") + ". ") if _d else ""
    return ("{t} evaluates {nm} models on {nc} XORCISE missions. {d}Each model is run {r}&times; "
            "per mission, <b>unaided</b> (no authored intel disclosed), inside per-run Docker "
            "sandboxes joined to the mission tailnet through the OpenHands-direct harness. Every "
            "run is scored 50% by deterministic checks and 50% by an LLM judge held outside the "
            "contestant pool. Figures are computed from the run data."
            ).format(t=esc(REPORT_TITLE), nm=nm, nc=nc, r=r, d=desc)


def derive_grid(runs, data_dir, base=None):
    """Scope the grid globals to the models/missions that actually ran, sourcing mission
    specialty + model provider metadata per item with a priority chain:
    live XORCISE catalog (base) -> the run's manifest.json -> built-in defaults -> derived."""
    global MODELS, MISSIONS, MODEL_NAMES, PROVIDER, MISSION_NAMES, MISSION_FULLSPEC
    global TOTAL_PLANNED, RUNS_PER, METHOD_FACTS, GRID_SOURCE
    global REPORT_UUID, STARTED_AT, COMPLETED_AT, REPORT_TITLE, REPORT_DESC, PLAYBOOK_NAME
    from collections import Counter, OrderedDict

    models = list(OrderedDict.fromkeys(r["model"] for r in runs if r.get("model")))
    missions = list(OrderedDict.fromkeys(r["mission"] for r in runs if r.get("mission")))
    if not models or not missions:
        return  # nothing loaded — keep the built-in defaults

    cells = Counter((r["model"], r["mission"]) for r in runs)
    RUNS_PER = max(cells.values()) if cells else RUNS_PER

    outdir = os.path.dirname(os.path.abspath(data_dir.rstrip("/")))
    man = _load_json(os.path.join(outdir, "manifest.json")) or {}
    REPORT_UUID = man.get("report_uuid") or REPORT_UUID
    STARTED_AT = man.get("started_at") or STARTED_AT
    COMPLETED_AT = man.get("completed_at") or COMPLETED_AT
    PLAYBOOK_NAME = man.get("playbook_name") or man.get("playbook") or PLAYBOOK_NAME
    REPORT_TITLE = (man.get("title") or _derive_title(man.get("playbook_name") or "")
                    or _derive_title(man.get("playbook") or "") or REPORT_TITLE)
    REPORT_DESC = man.get("description") or REPORT_DESC
    man_missions = {c.get("name"): c for c in (man.get("missions") or [])}
    man_models = {m.get("name"): m for m in (man.get("models") or [])}
    live_spec = _fetch_catalog_specialties(base)   # {mission_id: specialty}, {} if unavailable

    # per-item metadata resolution: live XORCISE -> run manifest -> built-in -> derived
    def spec_of(name):
        return (live_spec.get(name)
                or man_missions.get(name, {}).get("specialty")
                or _BUILTIN_MISSION_META.get(name, (None, None))[1]
                or "")

    def code_of(name):
        return (man_missions.get(name, {}).get("code")
                or _BUILTIN_MISSION_META.get(name, (None, None))[0]
                or _code_for(name))

    def prov_of(name):
        return (man_models.get(name, {}).get("provider")
                or _BUILTIN_MODEL_META.get(name, (None, None))[0]
                or _provider_for(name))

    def litellm_of(name):
        return (man_models.get(name, {}).get("litellm")
                or _BUILTIN_MODEL_META.get(name, (None, None))[1]
                or "")

    MISSIONS = [(c, code_of(c), spec_of(c), spec_of(c)) for c in missions]
    MODELS = [(m, prov_of(m), litellm_of(m)) for m in models]
    RUNS_PER = man.get("runs_per_cell", RUNS_PER)
    METHOD_FACTS = man.get("methodology", {}) or {}

    # GRID_SOURCE gates the methodology table's run-specific facts (build_methodology_rows):
    # trust manifest facts when present, the P1 defaults when the run fits the built-in grid,
    # else neutralize (routing/judge/budget can't be confirmed). Mission labels above are
    # resolved per-item regardless, so this only affects the methodology block.
    if man:
        GRID_SOURCE = "manifest"
    elif set(models) <= set(_BUILTIN_MODEL_META) and set(missions) <= set(_BUILTIN_MISSION_META):
        GRID_SOURCE = "builtin"
    else:
        GRID_SOURCE = "derived"

    MODEL_NAMES = [m[0] for m in MODELS]
    PROVIDER = {m[0]: m[1] for m in MODELS}
    MISSION_NAMES = [c[0] for c in MISSIONS]
    MISSION_FULLSPEC = {c[0]: c[3] for c in MISSIONS}
    TOTAL_PLANNED = len(MODELS) * len(MISSIONS) * RUNS_PER


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

def cell_stats(runs):
    """Aggregate a set of runs (already filtered to a model/mission as needed)."""
    valid = [r for r in runs if r["valid"]]
    return valid


def model_aggregate(runs, model):
    mruns = [r for r in runs if r["model"] == model]
    valid = [r for r in mruns if r["valid"]]
    excluded = [r for r in mruns if r["excluded_zero_tools"]]

    overalls = [r["overall"] * 100 for r in valid if r["overall"] is not None]
    dets = [r["deterministic"] * 100 for r in valid if r["deterministic"] is not None]
    judges = [r["judge"] * 100 for r in valid if r["judge"] is not None]
    toks = [r["tokens_total"] for r in valid if r["tokens_total"] is not None]
    elapsed = [r["elapsed"] for r in valid if r["elapsed"] is not None]

    # per-run flag success (pass@1) over valid runs
    flag_valid = [r for r in valid if r["flag"] is not None]
    n_flag = len(flag_valid)
    n_flag_pass = sum(1 for r in flag_valid if r["flag"])

    # mission-level solve (pass@k): missions with >=1 valid run, and how many
    # were solved in >=1 valid run.
    mission_valid = {}
    for r in valid:
        mission_valid.setdefault(r["mission"], []).append(r)
    attempted = len(mission_valid)
    solved = sum(1 for c, rs in mission_valid.items()
                 if any(x["flag"] for x in rs if x["flag"] is not None))

    return {
        "model": model,
        "provider": PROVIDER.get(model, ""),
        "n_total": len(mruns),
        "n_valid": len(valid),
        "n_excluded": len(excluded),
        "mean_overall": mean(overalls),
        "std_overall": stdev(overalls),
        "mean_det": mean(dets),
        "mean_judge": mean(judges),
        "total_tokens": sum(toks) if toks else None,
        "mean_elapsed": mean(elapsed),
        "pass1": (n_flag_pass / n_flag) if n_flag else None,
        "attempted": attempted,
        "solved": solved,
        "passk": (solved / attempted) if attempted else None,
        "has_data": len(valid) > 0,
    }


def mission_aggregate(runs, mission, model_cell_means):
    """model_cell_means: {model: mean_overall_pct or None} for this mission."""
    cruns = [r for r in runs if r["mission"] == mission]
    valid = [r for r in cruns if r["valid"]]

    # mean across models = mean of the per-model cell means that have data
    cell_vals = [v for v in model_cell_means.values() if v is not None]
    mean_across = mean(cell_vals)

    # best model by cell mean
    best_model, best_score = None, None
    for mdl in MODEL_NAMES:
        v = model_cell_means.get(mdl)
        if v is None:
            continue
        if best_score is None or v > best_score:
            best_model, best_score = mdl, v

    flag_valid = [r for r in valid if r["flag"] is not None]
    n_flag = len(flag_valid)
    n_pass = sum(1 for r in flag_valid if r["flag"])

    return {
        "mission": mission,
        "specialty": MISSION_FULLSPEC.get(mission, ""),
        "mean_across": mean_across,
        "best_model": best_model,
        "best_score": best_score,
        "solve_num": n_pass,
        "solve_den": n_flag,
    }


def matrix_cell_mean(runs, model, mission):
    valid = [r for r in runs
             if r["model"] == model and r["mission"] == mission and r["valid"]
             and r["overall"] is not None]
    if not valid:
        return None
    return mean([r["overall"] * 100 for r in valid])


# --------------------------------------------------------------------------- #
# HTML fragment builders
# --------------------------------------------------------------------------- #

def _chip_val(key, default):
    """Concise chip value from the manifest methodology, e.g. 'gpt-5.4' from
    'gpt-5.4 &mdash; held outside ...', or '1800s' from '1800s / run &middot; concurrency 5'."""
    v = (METHOD_FACTS.get(key) or "").strip()
    if not v:
        return default
    for sep in (" &mdash;", " &middot;", " / ", " — ", " · "):
        if sep in v:
            v = v.split(sep, 1)[0].strip()
    return v or default


def build_run_meta_chips(n_done, date_str):
    prelim = (n_done < TOTAL_PLANNED)
    chips = []
    if prelim:
        chips.append(
            '    <span class="pill tmpl">PRELIMINARY &mdash; run in progress '
            '({}/{} done)</span>'.format(n_done, TOTAL_PLANNED)
        )
    else:
        chips.append(
            '    <span class="pill chip"><span class="k">status</span> '
            'complete ({}/{} runs)</span>'.format(n_done, TOTAL_PLANNED)
        )
    chips += [
        '    <span class="pill chip"><span class="k">date</span> {}</span>'.format(esc(date_str)),
        '    <span class="pill chip"><span class="k">duration</span> {}</span>'.format(
            fmt_duration_between(STARTED_AT, COMPLETED_AT)),
        '    <span class="pill chip"><span class="k">id</span> {}</span>'.format(
            esc(REPORT_UUID[:8]) if REPORT_UUID else DASH),
        '    <span class="pill chip"><span class="k">harness</span> openhands 1.16.0</span>',
        '    <span class="pill chip"><span class="k">intel</span> {}</span>'.format(esc(_chip_val("intel_policy", "none"))),
        '    <span class="pill chip"><span class="k">budget</span> {}</span>'.format(esc(_chip_val("budget", DASH))),
        '    <span class="pill chip"><span class="k">judge</span> {}</span>'.format(esc(_chip_val("judge", DASH))),
        '    <span class="pill chip"><span class="k">sandbox</span> docker + TUN</span>',
    ]
    return "\n" + "\n".join(chips) + "\n  "


def build_summary_kpis(runs, model_aggs, n_done):
    valid = [r for r in runs if r["valid"]]
    overalls = [r["overall"] * 100 for r in valid if r["overall"] is not None]
    toks = [r["tokens_total"] for r in valid if r["tokens_total"] is not None]
    wall = [r["elapsed"] for r in runs if r["elapsed"] is not None]

    with_data = [a for a in model_aggs if a["has_data"]]
    top = max(with_data, key=lambda a: a["mean_overall"]) if with_data else None
    top_name = top["model"] if top else DASH

    mean_all = mean(overalls)
    total_tok = sum(toks) if toks else None
    wall_total = sum(wall) if wall else None

    cells = [
        ('Runs', '{}<small>/{}</small>'.format(n_done, TOTAL_PLANNED)),
        ('Models', str(len(MODELS))),
        ('Missions', str(len(MISSIONS))),
        ('Runs / cell', '3'),
        ('Top model', '<span style="font-size:.95rem">{}</span>'.format(esc(top_name))),
        ('Mean overall', '{}<small>%</small>'.format(fmt_pct1(mean_all))),
        ('Total tokens', fmt_tokens(total_tok)),
        ('Wall-clock', fmt_wallclock(wall_total)),
    ]
    parts = ["\n"]
    for cap, val in cells:
        parts.append(
            '      <div><span class="cap">{}</span><div class="v">{}</div></div>'.format(
                esc(cap), val)
        )
    return "\n".join([""] + [p for p in parts if p.strip()]) + "\n    "


def build_leaderboard_rows(model_aggs):
    with_data = sorted(
        [a for a in model_aggs if a["has_data"]],
        key=lambda a: a["mean_overall"], reverse=True,
    )
    without = [a for a in model_aggs if not a["has_data"]]
    ordered = with_data + without

    rows = ["\n"]
    for i, a in enumerate(ordered, start=1):
        top = (i == 1 and a["has_data"])
        tr_cls = ' class="top"' if top else ''
        mean_ov = a["mean_overall"]
        if mean_ov is None:
            bar = ('<div class="bar-wrap"><span class="bar-track">'
                   '<span style="width:0%;background:var(--active)"></span></span>'
                   '<span class="bar-num">{}</span></div>'.format(DASH))
        else:
            bar = ('<div class="bar-wrap"><span class="bar-track">'
                   '<span style="width:{w:.1f}%;background:{c}"></span></span>'
                   '<span class="bar-num">{v}</span></div>'.format(
                       w=max(0.0, min(100.0, mean_ov)), c=score_color(mean_ov),
                       v=fmt_pct1(mean_ov)))
        flags = ('{}/{}'.format(a["solved"], a["attempted"]) if a["attempted"]
                 else DASH)
        rows.append(
            '          <tr{tr}>\n'
            '            <td class="num rank">{rank}</td>\n'
            '            <td><span class="model-name">{name}</span> '
            '<span class="prov">{prov}</span></td>\n'
            '            <td class="barcell">{bar}</td>\n'
            '            <td class="num">{p1}</td><td class="num">{pk}</td>'
            '<td class="num">{std}</td>\n'
            '            <td class="num">{flags}</td><td class="num">{tok}</td>\n'
            '          </tr>'.format(
                tr=tr_cls, rank=i, name=esc(a["model"]), prov=esc(a["provider"]),
                bar=bar, p1=fmt_frac2(a["pass1"]), pk=fmt_frac2(a["passk"]),
                std=fmt_pct1(a["std_overall"]), flags=flags,
                tok=fmt_tokens(a["total_tokens"]))
        )
    return "\n".join(rows) + "\n        "


def build_heatmap_header():
    parts = ["\n", '            <th class="model-col">Model</th>']
    for name, code, short, _full in MISSIONS:
        parts.append(
            '            <th title="{name}"><span class="hcode">{code}</span>'
            '<span class="hspec">{short}</span></th>'.format(
                name=esc(name), code=esc(code), short=esc(short))
        )
    return "\n".join(parts) + "\n          "


def build_heatmap_rows(matrix):
    """matrix[model][mission] = mean_pct or None."""
    rows = ["\n"]
    for name in MODEL_NAMES:
        cells = [
            '            <td class="model-col">'
            '<span class="model-name">{}</span></td>'.format(esc(name))
        ]
        for cname in MISSION_NAMES:
            v = matrix[name].get(cname)
            if v is None:
                cells.append('            <td class="hm">{}</td>'.format(DASH))
            else:
                s = int(round(v))
                cells.append(
                    '            <td class="hm {band}" data-score="{s}">{s}</td>'.format(
                        band=band_class(s), s=s)
                )
        rows.append("          <tr>\n" + "\n".join(cells) + "\n          </tr>")
    return "\n".join(rows) + "\n        "


def _dial_svg(name, mean_ov):
    circ = 2 * 3.141592653589793 * 52  # r=52 => ~326.726
    if mean_ov is None:
        filled, rem, color, num = 0.0, circ, "var(--active)", DASH
        aria = "{} mean overall not available".format(name)
    else:
        frac = max(0.0, min(1.0, mean_ov / 100.0))
        filled = circ * frac
        rem = circ - filled
        color = score_color(mean_ov)
        num = fmt_pct1(mean_ov)
        aria = "{} mean overall {} percent".format(name, num)
    return (
        '        <svg class="dial" viewBox="0 0 120 120" role="img" aria-label="{aria}">\n'
        '          <circle cx="60" cy="60" r="52" fill="none" stroke="var(--active)" stroke-width="10"/>\n'
        '          <circle cx="60" cy="60" r="52" fill="none" stroke="{color}" stroke-width="10" stroke-linecap="round"\n'
        '            stroke-dasharray="{filled:.2f} {rem:.2f}" transform="rotate(-90 60 60)"/>\n'
        '          <text class="dial-num" x="60" y="60" text-anchor="middle" dominant-baseline="central">{num}</text>\n'
        '          <text class="dial-unit" x="60" y="78" text-anchor="middle">MEAN %</text>\n'
        '        </svg>'.format(aria=esc(aria), color=color, filled=filled, rem=rem, num=num)
    )


def _meter(label, val_pct):
    if val_pct is None:
        return ('      <div class="meter"><div class="meter-head">'
                '<span class="cap">{lab}</span><span class="val">{d}</span></div>'
                '<div class="track"><span style="width:0%;background:var(--active)">'
                '</span></div></div>'.format(lab=esc(label), d=DASH))
    w = max(0.0, min(100.0, val_pct))
    return ('      <div class="meter"><div class="meter-head">'
            '<span class="cap">{lab}</span><span class="val">{v}</span></div>'
            '<div class="track"><span style="width:{w:.1f}%;background:{c}"></span>'
            '</div></div>'.format(lab=esc(label), v=fmt_int(val_pct), w=w,
                                  c=score_color(val_pct)))


def build_model_cards(model_aggs):
    aggs = {a["model"]: a for a in model_aggs}
    cards = ["\n"]
    for name in MODEL_NAMES:
        a = aggs[name]
        flags = ('{}/{}'.format(a["solved"], a["attempted"]) if a["attempted"]
                 else DASH)
        card = (
            '    <div class="model-card">\n'
            '      <div class="mc-head"><span class="mc-name">{name}</span>'
            '<span class="tag kind">{prov}</span></div>\n'
            '      <div class="dial-wrap">\n{dial}\n      </div>\n'
            '      <div class="dial-cap">mean overall</div>\n'
            '{det}\n{judge}\n'
            '      <div class="mini-kpi">\n'
            '        <div><span class="cap">pass@1</span><span class="v">{p1}</span></div>\n'
            '        <div><span class="cap">flags</span><span class="v">{flags}</span></div>\n'
            '        <div><span class="cap">tokens</span><span class="v">{tok}</span></div>\n'
            '        <div><span class="cap">avg elapsed</span><span class="v">{el}</span></div>\n'
            '      </div>\n'
            '    </div>'.format(
                name=esc(name), prov=esc(a["provider"]),
                dial=_dial_svg(name, a["mean_overall"]),
                det=_meter("Deterministic", a["mean_det"]),
                judge=_meter("Judge", a["mean_judge"]),
                p1=fmt_frac2(a["pass1"]), flags=flags,
                tok=fmt_tokens(a["total_tokens"]), el=fmt_dur_short(a["mean_elapsed"]))
        )
        cards.append(card)
    return "\n".join(cards) + "\n  "


def build_mission_rows(mission_aggs):
    rows = ["\n"]
    for ca in mission_aggs:
        ma = ca["mean_across"]
        if ma is None:
            bar = ('<div class="bar-wrap"><span class="bar-track">'
                   '<span style="width:0%;background:var(--active)"></span></span>'
                   '<span class="bar-num">{}</span></div>'.format(DASH))
        else:
            bar = ('<div class="bar-wrap"><span class="bar-track">'
                   '<span style="width:{w:.1f}%;background:{c}"></span></span>'
                   '<span class="bar-num">{v}</span></div>'.format(
                       w=max(0.0, min(100.0, ma)), c=score_color(ma), v=fmt_pct1(ma)))
        if ca["best_model"] is None:
            best = DASH
        else:
            best = '{m} <span class="dimtxt">({s})</span>'.format(
                m=esc(ca["best_model"]), s=fmt_int(ca["best_score"]))
        solve = ('{}/{}'.format(ca["solve_num"], ca["solve_den"]) if ca["solve_den"]
                 else DASH)
        rows.append(
            '          <tr>\n'
            '            <td class="model-name">{name}</td>'
            '<td><span class="tag kind">{spec}</span></td>\n'
            '            <td class="barcell">{bar}</td>\n'
            '            <td>{best}</td><td class="num">{solve}</td>\n'
            '          </tr>'.format(
                name=esc(ca["mission"]), spec=esc(ca["specialty"]), bar=bar,
                best=best, solve=solve)
        )
    return "\n".join(rows) + "\n        "


def build_methodology_rows():
    f = METHOD_FACTS or {}
    derived = (GRID_SOURCE == "derived")

    def fact(key, p1_default, run_specific=True):
        """Manifest value if present; else the P1 default -- but for a derived run
        (config not captured) a run-specific fact shows a neutral marker instead of
        falsely asserting the P1 conditions (e.g. Bedrock for a non-Bedrock run)."""
        if f.get(key):
            return f[key]
        if run_specific and derived:
            return '&mdash; <small>(see run config)</small>'
        return p1_default

    prov_rows = "".join(
        '              <tr><td>{name}</td><td><span class="mono">{pid}</span></td></tr>\n'.format(
            name=esc(m[0]), pid=(esc(m[2]) if m[2] else "&mdash;"))
        for m in MODELS
    )
    return (
        "\n"
        '          <tr><td>Harness</td><td>{}</td></tr>\n'.format(
            fact("harness", "OpenHands 1.16.0 &mdash; headless, one throwaway Docker sandbox per run (built, joined, torn down)", run_specific=False))
        + '          <tr><td>Model routing</td><td>{}</td></tr>\n'.format(
            fact("routing", 'OpenHands-direct via <span class="mono">litellm</span> &rarr; AWS Bedrock, region <span class="mono">ap-southeast-2</span>'))
        + '          <tr><td>Auth</td><td>{}</td></tr>\n'.format(
            fact("auth", 'long-term Bedrock API key (<span class="mono">AWS_BEARER_TOKEN_BEDROCK</span>)'))
        + '          <tr><td>Provider map</td><td>\n'
        '            <table class="sub"><tbody>\n'
        + prov_rows +
        '            </tbody></table>\n'
        '          </td></tr>\n'
        + '          <tr><td>Judge model</td><td>{}</td></tr>\n'.format(
            fact("judge", "claude-sonnet-4-5 &mdash; held outside the contestant pool to avoid self-grading bias"))
        + '          <tr><td>Intel policy</td><td>{}</td></tr>\n'.format(
            fact("intel_policy", "none &mdash; no authored intel disclosed to the agent (clean capability run)"))
        + '          <tr><td>Budget</td><td>{}</td></tr>\n'.format(
            fact("budget", "1200s / run &middot; concurrency 3"))
        + '          <tr><td>Runs per cell</td><td>{}</td></tr>\n'.format(RUNS_PER)
        + '          <tr><td>Networking</td><td>{}</td></tr>\n'.format(
            fact("networking", "kernel / TUN tailscale, host-networked; sandbox joins the mission tailnet", run_specific=False))
        + '          <tr><td>Scoring</td><td>{}</td></tr>\n'.format(
            fact("scoring", "0.5 &times; deterministic checks + 0.5 &times; LLM judge", run_specific=False))
        + '        '
    )


def run_scope(runs):
    """Data-derived shape of the batch: models, missions, runs-per-cell, completeness."""
    from collections import Counter
    models = sorted({r["model"] for r in runs})
    missions = sorted({r["mission"] for r in runs})
    cells = Counter((r["model"], r["mission"]) for r in runs)
    runs_per = max(cells.values()) if cells else 0
    short = [k for k, v in cells.items() if v < runs_per]
    complete = bool(cells) and not short and len(cells) == len(models) * len(missions)
    return {"models": models, "missions": missions, "runs_per": runs_per,
            "short": short, "complete": complete, "n": len(runs)}


def build_caveats(runs, model_aggs):
    """A — auto-derived facts about THIS run (never hardcoded, never stale)."""
    s = run_scope(runs)
    n_excl = sum(a["n_excluded"] for a in model_aggs)
    n_unavail = sum(1 for r in runs if r.get("judge_status") in _JUDGE_ZERO_STATUSES)
    n_notcfg = sum(1 for r in runs if r.get("judge_status") == "model-not-configured")
    n_partial = sum(1 for r in runs if r.get("judge_status") == "partial")
    items = []

    # what actually ran (replaces the old always-on "preliminary" line)
    if s["complete"]:
        items.append("<li><b>Scope.</b> Complete run &mdash; all {} attempts finished: "
                     "{} model(s) &times; {} mission(s) &times; {} run(s) each.</li>".format(
                         s["n"], len(s["models"]), len(s["missions"]), s["runs_per"]))
    else:
        items.append("<li><b>Scope.</b> Partial run &mdash; {} attempt(s) so far across "
                     "{} model(s) &times; {} mission(s); {} cell(s) have fewer than {} "
                     "runs, so those figures are less stable.</li>".format(
                         s["n"], len(s["models"]), len(s["missions"]), len(s["short"]), s["runs_per"]))

    # Runs that produced no valid score, broken out per model &times; mission. This is
    # reporting only — the tool_calls==0 exclusion is UNCHANGED. We separate three cases
    # using the signals we capture (error / model_calls / tool_calls):
    #   - failed:    the runner errored before grading (create/spawn failure)
    #   - refusal:   model_calls>0 but tool_calls==0 (the model answered, agent didn't act —
    #                a refusal, provider content-filter, or stall)
    #   - cold-start: no model call at all (tool_calls==0, a startup/telemetry race)
    def _group(rs):
        from collections import defaultdict
        d = defaultdict(set)
        for r in rs:
            d[r.get("model") or "?"].add(r.get("mission") or "?")
        return "; ".join("{} ({})".format(esc(m), ", ".join(esc(x) for x in sorted(v)))
                         for m, v in sorted(d.items()))

    errored = [r for r in runs if r.get("error")]
    zero = [r for r in runs if r.get("tool_calls") == 0]
    refusal = [r for r in zero if (r.get("model_calls") or 0) > 0]
    coldstart = [r for r in zero if not (r.get("model_calls") or 0)]

    if errored:
        items.append("<li><b>Failed runs.</b> {} run(s) errored before grading "
                     "(orchestration / create failure): {}.</li>".format(len(errored), _group(errored)))
    if refusal:
        items.append("<li><b>No-action runs (likely refusals).</b> {} run(s) called the model "
                     "but recorded <span class=\"mono\">tool_calls=0</span> &mdash; the model "
                     "responded yet the agent took no action (a refusal, provider content-filter, "
                     "or stall). Counted but excluded from scores: {}.</li>".format(len(refusal), _group(refusal)))
    if coldstart:
        items.append("<li><b>Cold-start runs.</b> {} run(s) made no model call at all "
                     "(<span class=\"mono\">tool_calls=0</span>, a startup / telemetry race). "
                     "Excluded from scores: {}.</li>".format(len(coldstart), _group(coldstart)))
    if not (errored or zero):
        items.append("<li><b>Excluded / failed runs.</b> None &mdash; every one of the {} runs "
                     "produced tool calls and graded.</li>".format(s["n"]))

    # judge availability: a run whose judge produced no score folds in as 0 on the judge
    # half (halving a solved run). "model-not-configured" means no judge was set at all.
    if n_unavail:
        detail = (" &mdash; {} because no judge model was configured".format(n_notcfg)
                  if n_notcfg else "")
        items.append("<li><b>Judge availability.</b> {} run(s) had no judge score{} "
                     "(folded in as 0 on the judge half) &mdash; these depress overall and "
                     "are worth inspecting if the count is large.</li>".format(n_unavail, detail))
    # partial judge coverage: scored some criteria, so overall is a conservative lower bound
    if n_partial:
        items.append("<li><b>Partial judge coverage.</b> {} run(s) were graded on partial "
                     "judge coverage, so their overall is a conservative lower bound (unscored "
                     "rubric weight earns 0).</li>".format(n_partial))

    # universal, parameterised
    items.append("<li><b>Small n.</b> {} run(s) per model&times;mission cell &mdash; read "
                 "cells as mean&nbsp;&plusmn;&nbsp;std, not point estimates.</li>".format(s["runs_per"]))
    items.append("<li><b>Single LLM judge.</b> Scoring is 0.5&times; deterministic + 0.5&times; "
                 "one LLM judge; a single grader carries its own biases.</li>")
    items.append("<li><b>Unaided.</b> Figures reflect the run's intel policy (a clean-capability "
                 "run discloses no authored intel to the agent).</li>")
    return "\n      " + "\n      ".join(items) + "\n    "


def build_tldr(model_aggs):
    """C — one-line auto summary: top model, its margin, and the spread."""
    ranked = sorted([a for a in model_aggs if a["has_data"]],
                    key=lambda a: a["mean_overall"], reverse=True)
    if not ranked:
        return ""
    top = ranked[0]
    parts = ["<b>{}</b> leads at {}% overall".format(esc(top["model"]), fmt_pct1(top["mean_overall"]))]
    if top.get("attempted"):
        parts[0] += " ({}/{} flags)".format(top["solved"], top["attempted"])
    if len(ranked) > 1:
        margin = top["mean_overall"] - ranked[1]["mean_overall"]
        parts.append("{:.1f} pts ahead of <b>{}</b>".format(margin, esc(ranked[1]["model"])))
    if len(ranked) > 2:
        low = ranked[-1]
        parts.append("<b>{}</b> lowest at {}%".format(esc(low["model"]), fmt_pct1(low["mean_overall"])))
    return ('<p class="subtitle" style="color:var(--heading);font-weight:600;'
            'margin:.1rem 0 1rem">'
            'TL;DR — ' + "; ".join(parts) + ".</p>")


def build_consistency_rows(runs):
    """Compare an agent's replicate runs on the SAME mission to flag inconsistency. For each
    model x mission cell we take the RANGE of overall across its runs (max - min, in points);
    a cell whose range exceeds 30 pts is surfaced (the agent is unreliable there). Widest first."""
    from collections import defaultdict
    cells = defaultdict(list)
    for r in runs:
        if r.get("valid") and isinstance(r.get("overall"), (int, float)):
            cells[(r.get("model"), r.get("mission"))].append(r["overall"] * 100.0)
    flagged = []
    for (m, ms), vals in cells.items():
        if len(vals) < 2:
            continue
        rng = max(vals) - min(vals)
        if rng > 30.0:
            flagged.append((rng, m, ms, sorted(vals, reverse=True)))
    flagged.sort(key=lambda x: x[0], reverse=True)  # widest spread first
    if not flagged:
        return ('\n          <tr><td colspan="4" class="prov">No inconsistent cells &mdash; every '
                'model&times;mission cell held its runs within a 30-pt range.</td></tr>\n        ')
    rows = []
    for rng, m, ms, vals in flagged:
        scores = " / ".join("{:.0f}".format(v) for v in vals)
        rows.append('          <tr><td>{}</td><td>{}</td><td class="mono">{}</td>'
                    '<td class="mono bar-num">{:.0f} pts</td></tr>'.format(
                        esc(m), esc(ms), scores, rng))
    return "\n" + "\n".join(rows) + "\n        "


def build_repro_rows(date_str):
    rows = [
        ("Evidence UUID", '<span class="mono">{}</span>'.format(esc(REPORT_UUID) if REPORT_UUID else DASH)),
        ("Started (DTG)", fmt_dtg(STARTED_AT)),
        ("Completed (DTG)", fmt_dtg(COMPLETED_AT)),
        ("Duration", fmt_duration_between(STARTED_AT, COMPLETED_AT)),
        ("Playbook", '<span class="mono">{}</span>'.format(esc(PLAYBOOK_NAME + ".yaml") if PLAYBOOK_NAME else DASH)),
        ("Runner", '<span class="mono">run_playbook.py</span>'),
        ("Raw data", '<span class="mono">rows.json</span> + <span class="mono">data/*.json</span> (per run) &middot; <span class="mono">manifest.json</span>'),
        ("Report generator", '<span class="mono">gen_report.py</span> (stdlib only)'),
        ("Generated", esc(date_str)),
    ]
    return ("\n" + "".join(
        '          <tr><td>{}</td><td>{}</td></tr>\n'.format(k, v) for k, v in rows) + "        ")


# --------------------------------------------------------------------------- #
# Template splicing
# --------------------------------------------------------------------------- #

def replace_region(html_text, marker, end_lookahead, new_inner):
    """Replace `marker` + trailing sample content (up to `end_lookahead`) with new_inner.

    Removes the marker comment entirely (it contains `{{`), so no markers remain.
    """
    pat = re.compile(re.escape(marker) + r".*?(?=" + re.escape(end_lookahead) + r")",
                     re.DOTALL)
    new_text, count = pat.subn(lambda _m: new_inner, html_text, count=1)
    if count != 1:
        raise RuntimeError("injection point not found / not unique: {}".format(marker))
    return new_text


def simple_replace(html_text, needle, replacement, required=False):
    if needle in html_text:
        return html_text.replace(needle, replacement, 1)
    if required:
        raise RuntimeError("expected literal not found: {!r}".format(needle))
    return html_text


def render(template, runs, source, date_str):
    n_done = len(runs)

    model_aggs = [model_aggregate(runs, m) for m in MODEL_NAMES]

    # model x mission matrix
    matrix = {m: {} for m in MODEL_NAMES}
    for m in MODEL_NAMES:
        for c in MISSION_NAMES:
            matrix[m][c] = matrix_cell_mean(runs, m, c)

    mission_aggs = [
        mission_aggregate(runs, c, {m: matrix[m][c] for m in MODEL_NAMES})
        for c in MISSION_NAMES
    ]

    out = template

    # 1. RUN_META_CHIPS  (keep the closing </div> of .chips)
    out = replace_region(
        out, "<!-- {{RUN_META_CHIPS}} -->",
        "\n  </div>\n\n  <!-- 1. SUMMARY",
        build_run_meta_chips(n_done, date_str))

    # 2. SUMMARY_KPIS
    out = replace_region(
        out, "<!-- {{SUMMARY_KPIS}} -->",
        "\n    </div>\n  </div>\n\n  <!-- 2. LEADERBOARD",
        build_summary_kpis(runs, model_aggs, n_done))

    # 3. LEADERBOARD_ROWS
    out = replace_region(
        out, "<!-- {{LEADERBOARD_ROWS}} -->", "\n        </tbody>",
        build_leaderboard_rows(model_aggs))

    # 4. HEATMAP_HEADER
    out = replace_region(
        out, "<!-- {{HEATMAP_HEADER}} -->", "\n          </tr>",
        build_heatmap_header())

    # 5. HEATMAP_ROWS
    out = replace_region(
        out, "<!-- {{HEATMAP_ROWS}} -->", "\n        </tbody>",
        build_heatmap_rows(matrix))

    # 6. MODEL_CARDS  (keep the closing </div> of .model-grid)
    out = replace_region(
        out, "<!-- {{MODEL_CARDS}} -->",
        "\n  </div>\n\n  <!-- 5. MISSIONS",
        build_model_cards(model_aggs))

    # 7. MISSION_ROWS
    out = replace_region(
        out, "<!-- {{MISSION_ROWS}} -->", "\n        </tbody>",
        build_mission_rows(mission_aggs))

    # 7b. CONSISTENCY_ROWS  (cross-run agreement analysis)
    out = replace_region(
        out, "<!-- {{CONSISTENCY_ROWS}} -->", "\n        </tbody>",
        build_consistency_rows(runs))

    # 8. METHODOLOGY_ROWS
    out = replace_region(
        out, "<!-- {{METHODOLOGY_ROWS}} -->", "\n        </tbody>",
        build_methodology_rows())

    # 9. CAVEATS
    out = replace_region(
        out, "<!-- {{CAVEATS}} -->", "\n    </ul>",
        build_caveats(runs, model_aggs))

    # 10. REPRO_ROWS
    out = replace_region(
        out, "<!-- {{REPRO_ROWS}} -->", "\n        </tbody>",
        build_repro_rows(date_str))

    # 11. GENERATED_DATE
    out = simple_replace(
        out, "<!-- {{GENERATED_DATE}} -->2026-07-28", esc(date_str), required=True)

    # Neutralise the {{ ... }} mention in the template's own header comment so
    # that a strict `grep '{{'` on the generated report finds nothing.
    out = simple_replace(
        out,
        "A generator replaces each {{ ... }} block below with real run data.",
        "This file was generated by report/gen_report.py from real run data.")
    out = simple_replace(
        out,
        "This file ships with SAMPLE data so it renders as a finished visual.",
        "Generated from real run data.")

    # C. TL;DR headline (auto summary, injected right under the subtitle)
    out = replace_region(
        out, "<!-- {{TLDR}} -->", '\n\n  <div class="chips">',
        build_tldr(model_aggs))

    # --- data-driven prose: subtitle + template note reflect the ACTUAL run ---
    scope = run_scope(runs)
    subt = "{} models · {} missions · {} runs each".format(
        len(scope["models"]), len(scope["missions"]), scope["runs_per"])
    out = simple_replace(out, "5 models · 15 missions · 3 runs each", subt)
    out = simple_replace(out, "five frontier models are evaluated on",
                         "the contestant models are evaluated on")
    out = simple_replace(out, "Figures below are illustrative sample data.",
                         "Figures are computed from the run data.")
    note = "Complete" if scope["complete"] else "Preliminary &mdash; subject to change"
    out = simple_replace(out, "Template &mdash; sample data", note)
    out = simple_replace(out, "Template — sample data", note)

    # Report identity — h1 + <title> + summary lead reflect the ACTUAL playbook, not the P1 template.
    out = out.replace("P1 — Broad Coverage", esc(REPORT_TITLE))
    out = simple_replace(out, "{{REPORT_LEAD}}", build_report_lead(scope))

    return out


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_data = os.path.normpath(
        os.path.join(here, "..", "results", "xor-pb-001-broad_coverage", "data"))
    default_out = os.path.join(here, "p1-report.html")
    default_tmpl = os.path.normpath(
        os.path.join(here, "..", "templates", "eval-card-template.html"))

    ap = argparse.ArgumentParser(description="Generate the XORCISE P1 eval-card report.")
    ap.add_argument("--data", default=default_data, help="run data/ directory")
    ap.add_argument("--out", default=default_out, help="output HTML path")
    ap.add_argument("--template", default=default_tmpl, help="template HTML path")
    ap.add_argument("--date", default=None,
                    help="timestamp for the report (default: today, UTC)")
    ap.add_argument("--base", default=os.environ.get("XORCISE_BASE", "http://127.0.0.1:3001"),
                    help="XORCISE base URL to fetch live mission metadata (specialty) from; "
                         "falls back to the run's manifest.json if unreachable")
    args = ap.parse_args()

    date_str = args.date
    if not date_str:
        # stdlib datetime is available here; use today's date (UTC).
        date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    with open(args.template, "r", encoding="utf-8") as fh:
        template = fh.read()

    runs, source = load_runs(args.data)
    derive_grid(runs, args.data, base=args.base)  # scope the grid; fetch metadata from XORCISE
    if not REPORT_UUID:   # no manifest — mint a report-time evidence id so the report still carries one
        globals()["REPORT_UUID"] = str(uuid.uuid4())
    out_html = render(template, runs, source, date_str)

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(out_html)

    # --- console summary ---
    leftover = re.findall(r"\{\{.*?\}\}", out_html)
    n_valid = sum(1 for r in runs if r["valid"])
    n_excl = sum(1 for r in runs if r["excluded_zero_tools"])
    print("source       : {}".format(source))
    print("data dir     : {}".format(args.data))
    print("output       : {}".format(args.out))
    print("date         : {}".format(date_str))
    print("runs ingested: {} (valid {}, tool_calls=0 excluded {})".format(
        len(runs), n_valid, n_excl))
    print("per-model aggregates:")
    for a in [model_aggregate(runs, m) for m in MODEL_NAMES]:
        print("  {:<11} n={:>2} valid={:>2} excl={} mean_overall={} std={} "
              "pass@1={} det={} judge={} tokens={} avg_elapsed={}".format(
                  a["model"], a["n_total"], a["n_valid"], a["n_excluded"],
                  fmt_pct1(a["mean_overall"]), fmt_pct1(a["std_overall"]),
                  fmt_frac2(a["pass1"]), fmt_pct1(a["mean_det"]),
                  fmt_pct1(a["mean_judge"]), fmt_tokens(a["total_tokens"]),
                  fmt_dur_short(a["mean_elapsed"])))
    print("leftover {{ }} markers: {}".format(len(leftover)))
    if leftover:
        print("  ", leftover)


if __name__ == "__main__":
    main()
