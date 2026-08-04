#!/usr/bin/env python3
"""
Live status for a XORCISE playbook run — progress, per-model means, flags, and (optionally)
core health, in one glance. Safe to run against an in-flight benchmark.

ISOLATION: this reads ONLY the given run's own files (its `progress.jsonl`, written by
run_playbook.py, or a stdout log you point it at). It never enumerates host containers or
all `/api/runs`, so it can't confuse — or interfere with — another playbook or a normal
xorcise run sharing the host.

Usage:
    status.py <outdir>            # a run's results dir (contains progress.jsonl + session.txt)
    status.py <path/to.jsonl>     # a progress.jsonl directly
    status.py --log <run.log>     # parse a runner stdout log instead (the `done ...` lines)
    status.py <outdir> --total 180   # show X/Y when you know the expected run count
    status.py <outdir> --health   # also probe core health at $XORCISE_BASE/api/health
"""
import argparse, json, os, re, statistics, sys, urllib.request
from collections import defaultdict

DONE_RE = re.compile(r"done (\S+?)/(\S+?)#(\d+): overall=([0-9.]+|None) flag=(\w+)")


def _succeeded(overall, tool_calls):
    if overall is None:
        return False
    if tool_calls is not None and tool_calls <= 0:
        return False
    return True


def rows_from_jsonl(path):
    rows = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("model") and r.get("mission") and r.get("run_idx") is not None:
            rows.append({
                "model": r["model"], "mission": r["mission"], "run_idx": r["run_idx"],
                "overall": r.get("overall"), "flag": r.get("flag_pass"),
                "tool_calls": r.get("tool_calls"),
            })
    return rows


def rows_from_log(path):
    rows = []
    for line in open(path, errors="ignore"):
        m = DONE_RE.search(line)
        if not m:
            continue
        mod, ch, idx, ov, fl = m.groups()
        rows.append({
            "model": mod, "mission": ch, "run_idx": int(idx),
            "overall": None if ov == "None" else float(ov),
            "flag": True if fl == "True" else (False if fl == "False" else None),
            "tool_calls": None,
        })
    return rows


def dedupe_best(rows):
    """One row per (model, mission, run_idx) — prefer a graded attempt — so retries in a
    resumed run don't double-count replicates."""
    best = {}
    for r in rows:
        k = (r["model"], r["mission"], r["run_idx"])
        cur = best.get(k)
        if cur is None or (r["overall"] is not None and cur["overall"] is None):
            best[k] = r
    return list(best.values())


def probe_health():
    base = os.environ.get("XORCISE_BASE", "http://127.0.0.1:3001")
    try:
        req = urllib.request.Request(base.rstrip("/") + "/api/health")
        with urllib.request.urlopen(req, timeout=5) as r:
            return str(r.status)
    except Exception as e:
        code = getattr(e, "code", None)
        return str(code) if code else "000"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="run outdir, a progress.jsonl, or (with --log) a stdout log")
    ap.add_argument("--log", help="parse a runner stdout log (the `done <model>/<mission>#<n>` lines)")
    ap.add_argument("--total", type=int, help="expected total run count, to show X/Y")
    ap.add_argument("--health", action="store_true", help="also probe core health at $XORCISE_BASE/api/health")
    args = ap.parse_args()

    session = None
    if args.log:
        rows = rows_from_log(args.log)
    else:
        p = args.path
        if not p:
            sys.exit("give a run outdir / progress.jsonl, or use --log <file>")
        if os.path.isdir(p):
            st = os.path.join(p, "session.txt")
            session = open(st).read().strip() if os.path.exists(st) else None
            jp = os.path.join(p, "progress.jsonl")
            if not os.path.exists(jp):
                sys.exit(f"no progress.jsonl in {p} (has the run started? or point --log at its stdout log)")
            rows = rows_from_jsonl(jp)
        else:
            rows = rows_from_jsonl(p)

    rows = dedupe_best(rows)
    done = len(rows)
    flags = sum(1 for r in rows if r["flag"] is True)
    label = session or "run"
    header = f"{label} | {done}"
    if args.total:
        header += f"/{args.total}"
    header += f" done, {flags} flags"
    if args.health:
        header += f" | core={probe_health()}"
    print(header)

    sc, fl, ch, dead = defaultdict(list), defaultdict(int), defaultdict(set), defaultdict(int)
    for r in rows:
        m = r["model"]; ch[m].add(r["mission"])
        if _succeeded(r["overall"], r["tool_calls"]):
            sc[m].append(r["overall"])
        else:
            dead[m] += 1
        if r["flag"] is True:
            fl[m] += 1
    for m in sorted(ch):
        v = sc[m]
        mean = f"{statistics.mean(v):.2f}" if v else "n/a"
        tail = f" dead/ungraded={dead[m]}" if dead[m] else ""
        print(f"  {m:14} runs={len(v)+dead[m]:3} missions={len(ch[m]):2} mean={mean} flags={fl[m]}{tail}")


if __name__ == "__main__":
    main()
