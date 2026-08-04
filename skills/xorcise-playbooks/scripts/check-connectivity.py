#!/usr/bin/env python3
"""
xorcise-playbooks connectivity check.

A cheap dress-rehearsal to run BEFORE a paid benchmark. It confirms the whole chain is
wired up and catches the failures that would otherwise waste a long, real-money run:
  1. every contestant model actually answers (credentials + model string are right,
     and the provider isn't content-filtering it),
  2. XORCISE's own judge model (configured ON the platform) answers — asked via
     XORCISE's model-test endpoint. Setting up the judge is done on XORCISE, not here.
  3. XORCISE's telemetry (OTLP) endpoint is reachable — XORCISE GRADES from the agent's
     telemetry, so if this is down, scores collapse to ~0.

Usage:
  python3 check-connectivity.py \
    --llm-config "$MODELS_YAML" \
    [--models a,b] [--base http://127.0.0.1:3001] [--otlp http://127.0.0.1:4318]

Provider keys are read from the ENVIRONMENT (litellm picks them up) — export them in your
shell before running. This check opens no credential file and never reads or prints a key.

Exit code 0 = all good, 1 = at least one check failed.
"""
import argparse, json, os, sys, urllib.request, urllib.error

G, R, D, X = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
def pass_(m): print(f"  {G}PASS{X}  {m}")
def fail_(m): print(f"  {R}FAIL{X}  {m}")

def load_env_file(path):
    """Load KEY=VALUE lines from a --creds .env into this process's environment (litellm
    then reads them). The file is read mechanically; no value is printed."""
    try:
        f = open(os.path.expanduser(path))
    except FileNotFoundError:
        sys.exit(f"--creds file not found: {path}")
    with f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            if k and v:
                os.environ[k] = v

def load_models(path):
    import yaml
    cfg = yaml.safe_load(open(os.path.expanduser(path))) or {}
    return {m["model_name"]: m["litellm_params"]["model"] for m in cfg.get("model_list", [])}

def main():
    ap = argparse.ArgumentParser(description="Pre-run connectivity check for xorcise-playbooks.")
    ap.add_argument("--llm-config", default="agent-models.yaml",
                    help="the run's throwaway model map (short name → litellm string)")
    ap.add_argument("--models", help="comma-separated subset to check (default: all in the config)")
    ap.add_argument("--creds", help="path to this run's .env of provider KEY=VALUE lines "
                    "(see scripts/setup-creds.sh); loaded into the environment, never printed")
    ap.add_argument("--base", default=os.environ.get("XORCISE_BASE", "http://127.0.0.1:3001"),
                    help="XORCISE base URL — used to ask the platform to live-test its configured judge model")
    ap.add_argument("--otlp", default=os.environ.get("XORCISE_OTLP", "http://127.0.0.1:4318"))
    a = ap.parse_args()

    # Provider keys come from the --creds .env (loaded here, never printed) or the ambient
    # environment; litellm reads them. The key value is never echoed.
    if a.creds:
        load_env_file(a.creds)
    fails = 0
    print("xorcise-playbooks · connectivity check\n")

    # 1. contestant models -----------------------------------------------------
    print("Contestant models (one tiny test call each):")
    try:
        import litellm
        litellm.drop_params = True
        litellm.suppress_debug_info = True
    except Exception as e:
        fail_(f"litellm not importable: {e}")
        sys.exit(2)
    try:
        mp = load_models(a.llm_config)
    except Exception as e:
        fail_(f"could not read model map {a.llm_config}: {e}")
        sys.exit(2)
    names = [s.strip() for s in a.models.split(",")] if a.models else list(mp)
    if not names:
        fail_("no models found — check --llm-config")
        fails += 1
    for name in names:
        full = mp.get(name, name)
        try:
            r = litellm.completion(model=full, messages=[{"role": "user", "content": "reply with: OK"}],
                                   max_tokens=5, timeout=45)
            if r.choices[0].finish_reason == "content_filter":
                fail_(f"{name} {D}({full}){X} — provider CONTENT-FILTERED this model (drop it or pick another)")
                fails += 1
            else:
                pass_(f"{name} {D}({full}){X}")
        except Exception as e:
            fail_(f"{name} {D}({full}){X} — {type(e).__name__}: {str(e)[:110]}")
            fails += 1

    # 2. judge model (configured ON XORCISE — the platform tests its own judge) -------
    print("\nJudge model (configured on your XORCISE platform):")
    try:
        req = urllib.request.Request(a.base.rstrip("/") + "/api/config/model/test",
                                     data=b"", method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            jr = json.load(resp)
        if jr.get("ok"):
            pass_("XORCISE's configured judge model answered")
        elif jr.get("status") == "not_configured":
            fail_("no judge model configured on XORCISE — set one up on your XORCISE platform.")
            print("        The judge is configured on XORCISE (its grading/model settings), not in this skill.")
            fails += 1
        else:
            fail_(f"XORCISE's judge model is configured but failing: {str(jr.get('message'))[:140]}")
            fails += 1
    except Exception as e:
        fail_(f"couldn't reach XORCISE to test its judge at {a.base} — {type(e).__name__}: {str(e)[:90]}")
        print("        Is XORCISE running and reachable? (The judge lives on XORCISE; setting it up is your responsibility.)")
        fails += 1

    # 3. telemetry (OTLP) endpoint --------------------------------------------
    print("\nTelemetry (OTLP) endpoint — XORCISE grades from agent telemetry, so it must be up:")
    try:
        urllib.request.urlopen(a.otlp, timeout=8)
        pass_(f"OTLP endpoint reachable at {a.otlp}")
    except urllib.error.HTTPError:
        pass_(f"OTLP endpoint reachable at {a.otlp}")   # any HTTP status = the port is listening
    except Exception as e:
        fail_(f"OTLP endpoint not reachable at {a.otlp} — {type(e).__name__}")
        print("        Agents send telemetry here and XORCISE grades from it. Make sure XORCISE's OTLP receiver is running.")
        fails += 1

    print()
    if fails:
        print(f"{R}{fails} check(s) failed.{X} Fix the FAIL items above before starting a paid run.")
        sys.exit(1)
    print(f"{G}All connectivity checks passed.{X} Models, XORCISE's judge, and telemetry are reachable.")
    print(f"{D}Next: run xor-pb-000-smoke to prove the full pipeline end-to-end (telemetry flows and runs come back graded with tool_calls > 0).{X}")

if __name__ == "__main__":
    main()
