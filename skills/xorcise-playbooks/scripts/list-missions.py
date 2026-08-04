#!/usr/bin/env python3
"""
Browse the missions your XORCISE platform offers — and filter them by category so you
can build a custom playbook that targets exactly the cyber skills you care about.

Every mission carries metadata you can filter on. The available VALUES are discovered live
from your platform (they vary by library) — run --list-specialties to see yours. Matching is
case-insensitive:
  * specialty     — the cyber category (e.g. Penetration, Engineering, Detection,
                    Investigation, Intelligence, Protection)
  * proficiency   — difficulty (e.g. Novice, Competent, Proficient, Expert)
  * type          — lab (live networked env) or static

Examples:
  # See everything, grouped by specialty
  python3 list-missions.py

  # List the filterable values live (specialties, proficiencies, types) with counts
  python3 list-missions.py --list-specialties

  # Only Penetration + Engineering, Expert only
  python3 list-missions.py --specialty Penetration,Engineering --proficiency Expert

  # Emit a ready-to-paste playbook `missions:` block for all Investigation missions
  python3 list-missions.py --specialty Investigation --as-playbook

Reads the platform at $XORCISE_BASE (default http://127.0.0.1:3001). Read-only.
"""
import argparse, json, os, sys, urllib.request
from collections import defaultdict

# The core names the mission catalog "mission" on the wire; these two constants are the
# only place that wire word appears — everything the user sees says "mission".
_CATALOG = "/api/missions"
_CATALOG_ID = "mission_id"

def fetch(base):
    url = base.rstrip("/") + _CATALOG
    last = None
    for _ in range(4):                     # a busy XORCISE can briefly blip — retry
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                d = json.load(r)
            return d if isinstance(d, list) else d.get("items", [])
        except Exception as e:
            last = e
            import time; time.sleep(2)
    raise last

def main():
    ap = argparse.ArgumentParser(description="Browse/filter XORCISE missions by category.")
    ap.add_argument("--base", default=os.environ.get("XORCISE_BASE", "http://127.0.0.1:3001"))
    ap.add_argument("--specialty", help="comma-separated specialties (case-insensitive; run --list-specialties to see yours), e.g. Penetration,Engineering")
    ap.add_argument("--proficiency", help="comma-separated difficulties (case-insensitive), e.g. Novice,Competent,Proficient,Expert")
    ap.add_argument("--type", dest="ctype", help="comma-separated types (lab,static)")
    ap.add_argument("--installed-only", action="store_true", help="only missions already installed")
    ap.add_argument("--list-specialties", action="store_true", help="print the filterable values live: specialties, proficiencies, types (+ counts)")
    ap.add_argument("--as-playbook", action="store_true", help="print a ready-to-paste `missions:` YAML block")
    a = ap.parse_args()

    try:
        items = fetch(a.base)
    except Exception as e:
        sys.exit(f"could not reach XORCISE at {a.base}: {e}\n"
                 f"Start your platform, or set XORCISE_BASE to its URL.")

    def csv(x): return {s.strip().lower() for s in x.split(",")} if x else None
    fs, fp, ft = csv(a.specialty), csv(a.proficiency), csv(a.ctype)
    def keep(c):
        if fs and str(c.get("specialty", "")).lower() not in fs: return False
        if fp and str(c.get("proficiency", "")).lower() not in fp: return False
        if ft and str(c.get("type", "")).lower() not in ft: return False
        if a.installed_only and not c.get("installed"): return False
        return True
    sel = [c for c in items if keep(c)]

    if a.list_specialties:
        # All filterable values, discovered LIVE from this platform (so docs never go stale).
        for label, field in (("Specialties", "specialty"),
                             ("Proficiencies", "proficiency"),
                             ("Types", "type")):
            counts = defaultdict(int)
            for c in items:
                if keep(c): counts[c.get(field, "?")] += 1
            print(f"{label} available ({len(counts)}):")
            for v in sorted(counts):
                print(f"  {v:24} {counts[v]} mission(s)")
            print()
        print("Matching is case-insensitive; pass these to --specialty / --proficiency / --type.")
        return

    if a.as_playbook:
        if not sel:
            sys.exit("no missions match those filters.")
        print("missions:")
        for c in sorted(sel, key=lambda c: (c.get("specialty", ""), c.get(_CATALOG_ID, ""))):
            cid = c.get(_CATALOG_ID) or c.get("name")
            print(f"  - {cid:26} # {c.get('specialty','?')} · {c.get('proficiency','?')}")
        return

    # default: human-readable, grouped by specialty
    by = defaultdict(list)
    for c in sel: by[c.get("specialty", "?")].append(c)
    total = sum(len(v) for v in by.values())
    filt = " · ".join(f for f in [
        f"specialty={a.specialty}" if a.specialty else "",
        f"proficiency={a.proficiency}" if a.proficiency else "",
        f"type={a.ctype}" if a.ctype else "",
        "installed-only" if a.installed_only else "",
    ] if f) or "no filter"
    print(f"{total} mission(s)  [{filt}]\n")
    for spec in sorted(by):
        print(f"  {spec}")
        for c in sorted(by[spec], key=lambda c: c.get(_CATALOG_ID, "")):
            cid = c.get(_CATALOG_ID) or c.get("name")
            tag = "installed" if c.get("installed") else "not installed"
            print(f"    - {cid:26} {c.get('proficiency','?'):12} {c.get('type','?'):7} ({tag})")
        print()
    print("Tip: add --as-playbook to emit a missions: block you can drop into a playbook YAML.")

if __name__ == "__main__":
    main()
