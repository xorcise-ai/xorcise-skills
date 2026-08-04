#!/usr/bin/env python3
"""
XORCISE playbook runner — orchestrates a benchmark playbook (missions x runs)
against a matrix of models via the OpenHands headless harness, then aggregates.

Run with a python that has pyyaml, e.g.:
    ~/xorcise-venv/bin/python run_playbook.py xor-pb-002-offensive_core.yaml --dry-run

Design notes
------------
* One XORCISE run == 1 agent : 1 mission : 1 run (the platform primitive).
  A playbook of C missions x R runs x M models => C*R*M runs, created here.
* Model provenance: XORCISE records run.model as null (audit F19), so we register
  one agent PER MODEL (kind=openhands, --model=<model>) and label every result row
  by the model from OUR config — never trust the platform for provenance.
* Grading validity: a judge that produced no score ("unavailable" = call failed,
  or "model-not-configured" = no judge set) folds in as 0.0 at 50% (audit F18),
  which silently halves solved runs. We record judge_status on every row so you can
  filter/renormalize; configure the judge, or raise its transcript_max_tokens / the
  run budget, if you see many. ("partial" scored some criteria -> a conservative
  lower bound, not a zero.)
* Sandbox topology (per Christo): OpenHands gives each session its own runtime.
  For a clean, parallel-safe environment the agent should join the run's tailnet
  from INSIDE its sandbox (run join.sh there) and reach run-control at the HOST
  rather than 127.0.0.1 — at host.docker.internal on every platform (Docker Desktop
  provides it; on native Linux Docker the sandbox adds --add-host ...:host-gateway so
  it resolves to the bridge gateway too, replacing the old Linux-only 172.17.0.1).
  Override with --run-control-host. The exact sandbox/runtime wiring for the v1 CLI is
  the one piece to validate live (see spawn_openhands()).
"""
import argparse, json, os, re, subprocess, sys, time, csv, statistics, shlex, threading, urllib.request, urllib.error
import datetime, uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required — run with ~/xorcise-venv/bin/python (has pyyaml) or `pip install pyyaml`")

BASE = os.environ.get("XORCISE_BASE", "http://127.0.0.1:3001")
OPENHANDS = os.environ.get("OPENHANDS_BIN", os.path.expanduser("~/.local/bin/openhands"))

# The sandbox reaches run-control on the SAME port XORCISE serves on — derive it from
# XORCISE_BASE so a non-default port (e.g. XORCISE_BASE=http://127.0.0.1:3005) works
# end-to-end, not just for the runner's own API calls. OTLP is a separate port,
# overridable via XORCISE_OTLP_PORT (default 4318).
from urllib.parse import urlparse as _urlparse
RC_PORT = _urlparse(BASE).port or 3001
OTLP_PORT = int(os.environ.get("XORCISE_OTLP_PORT", "4318"))


# The sandbox reaches the host (run-control + OTEL) at host.docker.internal on EVERY
# platform: Docker Desktop (macOS/Windows/Linux) provides it natively, and on native Linux
# Docker the sandbox is launched with `--add-host host.docker.internal:host-gateway` (see
# spawn_openhands) so it resolves to the bridge gateway there too. This replaces the old
# 172.17.0.1 default, which is Linux-bridge-only and dead on Docker Desktop.
DEFAULT_RC_HOST = "host.docker.internal"


# Provider credentials come from the ENVIRONMENT — exported directly, or loaded from a
# --creds .env by load_env_file() — and are passed to sandboxes BY NAME (`docker -e NAME`
# inherits the value), so this skill never prints a key or puts one on a command line, and
# nothing persists between runs. We select the provider-relevant env vars by a name pattern,
# so it stays provider-agnostic (Bedrock, OpenAI, Anthropic, Gemini, … all work without branching).
_PROVIDER_ENV_RE = re.compile(
    r"(_API_KEY$|_API_BASE$|_BASE_URL$|^AWS_|^OPENAI_|^ANTHROPIC_|^GEMINI_|^GOOGLE_|"
    r"^AZURE_|^OPENROUTER_|^VERTEX_|^COHERE_|^MISTRAL_|^GROQ_|^TOGETHER_|^DEEPSEEK_|^LLM_)")


def provider_env_keys():
    """Names (NOT values) of provider-relevant env vars available for this run. Only the
    name is handed to `docker -e`, which inherits the value from our environment."""
    return [k for k in os.environ if _PROVIDER_ENV_RE.search(k)]


# OpenHands authenticates via a SINGLE `LLM_API_KEY`. For api-key providers we forward the
# provider's own key (loaded from the run's --creds / environment) AS LLM_API_KEY, so
# litellm inside the sandbox gets a real key instead of the placeholder default — otherwise
# the placeholder reaches litellm and every run fails auth with 0 model calls. Bedrock /
# Vertex use cloud auth and ignore LLM_API_KEY, so they keep the (harmless) placeholder.
_LLM_KEY_FOR_PREFIX = {
    "openai/": "OPENAI_API_KEY", "anthropic/": "ANTHROPIC_API_KEY",
    "gemini/": "GEMINI_API_KEY", "openrouter/": "OPENROUTER_API_KEY",
    "mistral/": "MISTRAL_API_KEY", "groq/": "GROQ_API_KEY",
    "deepseek/": "DEEPSEEK_API_KEY", "together_ai/": "TOGETHER_API_KEY",
    "cohere/": "COHERE_API_KEY", "azure/": "AZURE_API_KEY",
}


def llm_api_key_for(full_model):
    """The value LLM_API_KEY should carry for this model, read from the environment (never
    printed). None for cloud-auth providers (bedrock/vertex) that ignore LLM_API_KEY."""
    for pfx, var in _LLM_KEY_FOR_PREFIX.items():
        if full_model.startswith(pfx):
            return os.environ.get(var)
    return None


def load_env_file(path):
    """Load KEY=VALUE lines from a --creds .env into THIS process's environment, so the
    per-sandbox name-passthrough (provider_env_keys → `docker -e NAME`) forwards them
    without the value ever touching a command line. The runner reads the file mechanically
    and never prints it. Tolerates `export ` prefixes, surrounding quotes, blanks, #comments."""
    try:
        f = open(os.path.expanduser(path))
    except FileNotFoundError:
        sys.exit(f"--creds file not found: {path}")
    with f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
            if not m:
                continue
            k, v = m.group(1), m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            if v:
                os.environ[k] = v


# ---------- REST helpers ----------
def _req(method, path, body=None, token=None, timeout=30):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


def get(p, token=None):    return _req("GET", p, token=token)
def post(p, b=None, token=None): return _req("POST", p, body=b, token=token)
def delete(p):             return _req("DELETE", p)


# ---------- REST wire vocabulary ----------
# The mission-catalog and run-create wire fields, kept together so the REST API boundary
# is easy to see in one place. The intel-disclosure field is "intel_policy" on the wire.
_CATALOG = "/api/missions"       # the mission catalog
_CATALOG_ID = "mission_id"       # a catalog entry's id field
_RUN_TARGET = "mission"          # run-create body: which mission to run
_RUN_SUBRESOURCE = "mission"     # per-run mission sub-resource under /api/runs/<id>/

# judge_status values where the judge produced NO score, so its half folds in as 0.0
# (audit F18) and silently halves solved runs. The core splits this into "unavailable"
# (judge configured but the call failed) and "model-not-configured" (no judge set at
# all) — both mean a zero judge half, so both must be counted. "partial" is NOT here:
# it scored some criteria, giving a conservative lower bound rather than a zero.
_JUDGE_ZERO_STATUSES = ("unavailable", "model-not-configured")


def run_entry(rid):
    """The run's row from the LIST endpoint (which carries state + the terminal signals);
    the single-run GET omits them. Lets us detect completion the moment it lands rather
    than waiting out the full budget+grace deadline."""
    _, lst = get("/api/runs")
    if isinstance(lst, list):
        for r in lst:
            if r.get("run_id") == rid:
                return r
    return None


def is_terminal(entry):
    """True once a run has finished (ready to grade) or its environment failed. The core
    signals completion via terminal_trigger / completed_at; its run-state enum is
    none/starting/ready/failed/released. Either way we then stop waiting and poll /result."""
    if not isinstance(entry, dict):
        return False
    if entry.get("completed_at") or entry.get("terminal_trigger"):
        return True
    return entry.get("state") in ("released", "failed")


# ---------- preflight ----------
def preflight(missions):
    code, sysinfo = get("/api/system")
    if code != 200:
        sys.exit(f"XORCISE not reachable at {BASE} ({code}). Run `xorcise up`.")
    planes = {p["name"]: p["ok"] for p in sysinfo.get("planes", [])}
    if not all(planes.get(x) for x in ("rest", "otlp", "docker")):
        sys.exit(f"XORCISE planes not healthy: {planes}")
    print(f"  xorcise ok — planes {planes}")

    code, cat = get(_CATALOG)
    installed = {c[_CATALOG_ID] for c in cat if c.get("installed")}
    for ch in missions:
        if ch in installed:
            print(f"  mission {ch}: installed")
            continue
        print(f"  mission {ch}: pulling...")
        code, _ = _req("POST", f"{_CATALOG}/{ch}/pull", timeout=600)  # pull is synchronous (~2min image download)
        if code not in (200, 201, 202):
            sys.exit(f"failed to pull {ch}: {code}")
        # poll until installed (pull can take ~2 min)
        for _ in range(120):
            time.sleep(3)
            _, cat2 = get(_CATALOG)
            if any(c[_CATALOG_ID] == ch and c.get("installed") for c in cat2):
                print(f"  mission {ch}: installed")
                break
        else:
            sys.exit(f"timed out pulling {ch}")

    if not os.path.exists(OPENHANDS):
        print(f"  WARN: openhands not found at {OPENHANDS} (ok for --dry-run)")


# Provider credentials are taken from the environment BY NAME (see provider_env_keys()
# near the top) and inherited by the sandbox via `docker -e NAME`. This skill deliberately
# opens NO credential file and never reads a key's value — nothing is stored between runs.


def load_model_map(path):
    """Map short model name → full litellm model string (e.g. bedrock/au.anthropic...).
    Sourced from the litellm config (same file the judge-only proxy uses), so the
    playbook stays clean (short names) and there's one provider-mapping source."""
    try:
        cfg = yaml.safe_load(open(path)) or {}
        return {m["model_name"]: m["litellm_params"]["model"] for m in cfg.get("model_list", [])}
    except Exception:
        return {}


def _provider_label(name, litellm):
    """Human provider label for a model, from its litellm string (or name as a fallback)."""
    hay = ((litellm or "") + " " + (name or "")).lower()
    for key, val in (("anthropic", "Anthropic"), ("claude", "Anthropic"),
                     ("zai", "Z.ai"), ("glm", "Z.ai"), ("moonshot", "Moonshot AI"),
                     ("kimi", "Moonshot AI"), ("openai", "OpenAI"), ("gpt", "OpenAI"),
                     ("gemini", "Google"), ("google", "Google"), ("vertex", "Google"),
                     ("meta", "Meta"), ("llama", "Meta"), ("mistral", "Mistral"),
                     ("deepseek", "DeepSeek"), ("grok", "xAI"), ("qwen", "Alibaba"),
                     ("cohere", "Cohere")):
        if key in hay:
            return val
    return ""


def _routing_auth(litellms):
    """Methodology routing + auth lines inferred from the set of litellm model prefixes."""
    prefixes = {(x or "").split("/", 1)[0].lower() for x in litellms if x}
    via = 'OpenHands-direct via <span class="mono">litellm</span> &rarr; '
    table = {
        frozenset({"bedrock"}): (via + "AWS Bedrock", "AWS credentials (Bedrock)"),
        frozenset({"openai"}): (via + "OpenAI API", '<span class="mono">OPENAI_API_KEY</span>'),
        frozenset({"anthropic"}): (via + "Anthropic API", '<span class="mono">ANTHROPIC_API_KEY</span>'),
        frozenset({"gemini"}): (via + "Google API", '<span class="mono">GEMINI_API_KEY</span>'),
    }
    return table.get(frozenset(prefixes),
                     (via + "provider APIs (see provider map)", "per-provider API keys"))


def write_manifest(outdir, models, model_map, missions, runs, budget, concurrency, intel_policy,
                   report_uuid=None, started_at=None, pb_name=None, pb_desc=None):
    """Write manifest.json so the report renders run-accurate labels (model providers,
    mission specialties) and the real run config in the methodology table — instead of
    falling back to the built-in P1 grid or neutral markers on a custom playbook.

    Best-effort: reads the catalog (specialty) and config (judge MODEL NAME only — never
    the key) if the core is reachable; a missing field just degrades to a blank/derived
    label, never an error."""
    spec = {}
    try:
        _, cat = get(_CATALOG)
        for c in (cat or []):
            if c.get(_CATALOG_ID):
                spec[c[_CATALOG_ID]] = c.get("specialty") or ""
    except Exception:
        pass
    judge = ""
    try:
        _, cfg = get("/api/config")
        jm = ((cfg or {}).get("judge") or {}).get("model_name")
        if jm:
            judge = "{} &mdash; held outside the contestant pool to avoid self-grading bias".format(jm)
    except Exception:
        pass

    mnames = [resolve_model(m)[0] for m in models]
    litellms = [model_map.get(n, "") for n in mnames]
    routing, auth = _routing_auth(litellms)
    intel_line = ("none &mdash; no authored intel disclosed to the agent (clean capability run)"
                  if (intel_policy or "none") == "none" else str(intel_policy))

    manifest = {
        # Stable identity + timing so the report + its files are referenceable evidence.
        "report_uuid": report_uuid or str(uuid.uuid4()),
        "started_at": started_at,          # ISO-8601 UTC; completed_at stamped at end of run
        "completed_at": None,
        "playbook": os.path.basename(outdir),
        "playbook_name": pb_name or "",   # full playbook name -> report title (e.g. xor-pb-002-offensive_core)
        "description": pb_desc or "",     # playbook one-liner -> report summary lead
        "runs_per_cell": runs,
        "models": [{"name": n, "provider": _provider_label(n, lm), "litellm": lm}
                   for n, lm in zip(mnames, litellms)],
        "missions": [{"name": ch, "specialty": spec.get(ch, "")} for ch in missions],
        "methodology": {
            "routing": routing,
            "auth": auth,
            "judge": judge or "configured on XORCISE",
            "budget": "{}s / run &middot; concurrency {}".format(budget, concurrency),
            "intel_policy": intel_line,
        },
    }
    try:
        with open(os.path.join(outdir, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)
        print("  manifest.json written ({} models x {} missions)".format(
            len(manifest["models"]), len(manifest["missions"])))
    except Exception as e:
        print("  WARN manifest.json not written: {}".format(e))


def resolve_model(entry):
    """A playbook `models:` entry is either a bare model name (str) or a per-agent
    OpenHands config: {model: <name>, env: {LLM_TEMPERATURE: "0.2", ...}}. Returns
    (model_name, env_overrides) — env is passed verbatim into the sandbox for OH."""
    if isinstance(entry, str):
        return entry, {}
    return entry["model"], {str(k): str(v) for k, v in (entry.get("env") or {}).items()}


def agent_name(model):
    """Standardized-harness agent name derived from the model (harness is always openhands)."""
    return "oh-" + model


def ensure_agent(entry):
    """Idempotently register the openhands agent for a model (agent = model + the fixed harness)."""
    model, _ = resolve_model(entry)
    name = agent_name(model)
    code, _ = post("/api/agents", {"name": name, "kind": "openhands", "model": model})
    if code in (200, 201):
        print(f"  agent {name} registered (model={model})")
    elif code in (409, 422):
        print(f"  agent {name} already present")
    else:
        print(f"  WARN agent {name} register -> {code}")


# Every sandbox this invocation launches is namespaced by a per-run SESSION tag, so
# the skill only ever acts on ITS OWN containers — never another playbook's or another
# user's benchmark sandboxes sharing the host. Set once in main() from the playbook name;
# cleanup (Step 7) and `docker ps` filter on `xorcise-run-<SESSION>-` instead of the bare
# `xorcise-run-` prefix that would sweep up everyone's runs.
SESSION_TAG = os.environ.get("XORCISE_PLAYBOOKS_SESSION", "")


def container_name(rid):
    return f"xorcise-run-{SESSION_TAG or 'run'}-{rid[:12]}"


# ---------- the OpenHands harness spawn (VALIDATE LIVE) ----------
def extract_task(cmd_str):
    """Pull just the task text out of the launch 'command' (openhands --headless -t '<task>')."""
    try:
        toks = shlex.split(cmd_str)
        if "-t" in toks:
            return toks[toks.index("-t") + 1]
    except Exception:
        pass
    return cmd_str


def spawn_openhands(task_text, otel_env, model, run_control_host, workdir, log_path,
                    use_otel, sandbox, run_id, proxy_base_url, image, oh_env, model_map):
    """Launch one agent run and return the subprocess.

    sandbox=True (default): the container (join-and-run.sh entrypoint) auto-joins the
    run's tailnet in KERNEL/TUN mode — the agent reaches mission IPs directly ("as
    if host-networked") before it starts. No host filesystem, no cloud creds (model
    calls go to the host litellm proxy). Requires NET_ADMIN + /dev/net/tun.
    Per-agent OpenHands params come from `oh_env` (verbatim env, e.g. LLM_TEMPERATURE).

    sandbox=False (LEGACY, UNSAFE): CLI directly on the host, no isolation.
    """
    # Rewrite the localhost run-control URL in the task to the docker-bridge host:port
    # the sandbox can actually reach (matches XORCISE's real port, see RC_PORT).
    task_text = task_text.replace(f"127.0.0.1:{RC_PORT}", f"{run_control_host}:{RC_PORT}")
    lf = open(log_path, "w")

    if sandbox:
        # the entrypoint sets up networking; tell the agent not to re-do the join
        note = ("NOTE: your tailnet is ALREADY connected and mission target IPs are directly "
                "reachable — do NOT run any join script (curl .../join.sh | sh); reach targets by IP.\n\n")
        m = re.search(r"Bearer ([0-9a-f]+)", task_text)
        rc_base = f"http://{run_control_host}:{RC_PORT}/api/runs/{run_id}"
        full_model = model_map.get(model, model)   # short name → full litellm string (e.g. bedrock/...)
        args = ["docker", "run", "--rm", "--name", container_name(run_id),
                # Make host.docker.internal resolve on native Linux Docker too (Docker Desktop
                # provides it already); it maps to the host gateway so the sandbox reaches
                # run-control + OTEL on the host regardless of platform. Needs Docker 20.10+.
                "--add-host", "host.docker.internal:host-gateway",
                "--cap-add", "NET_ADMIN", "--device", "/dev/net/tun",    # kernel/TUN tailscale
                "-e", f"OH_TASK={note}{task_text}",
                "-e", f"RC_BASE={rc_base}",                              # entrypoint fetches /connect + /tailscale.tgz
                "-e", f"RUN_BEARER={m.group(1) if m else ''}",
                "-e", f"RUN_ID={run_id}",
                # OpenHands-DIRECT: litellm in the container calls the provider itself (no proxy)
                "-e", f"LLM_MODEL={full_model}",
                # LLM_API_KEY = the provider's real key (from --creds/env) for api-key
                # providers; cloud-auth providers (bedrock/vertex) keep the harmless dummy.
                "-e", f"LLM_API_KEY={llm_api_key_for(full_model) or 'set-in-credentials-file'}"]
        # Provider credentials come from the ENVIRONMENT (the user exports their keys for
        # this run) and are passed to the sandbox BY NAME: `docker -e NAME` makes docker
        # inherit the value from our environment, so this skill never reads, prints, or
        # persists a key. The in-sandbox litellm picks the right var for LLM_MODEL, so this
        # works for ANY provider without branching, and nothing is stored between runs.
        for k in provider_env_keys():
            args += ["-e", k]        # NAME only — value inherited from env, never read here
        for k, v in (oh_env or {}).items():                # per-agent OpenHands config (verbatim)
            args += ["-e", f"{k}={v}"]
        if use_otel:
            args += ["-e", f"OTEL_EXPORTER_OTLP_ENDPOINT=http://{run_control_host}:{OTLP_PORT}",
                     "-e", "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf",
                     "-e", "OTEL_EXPORTER_OTLP_TIMEOUT=8000",   # don't let a slow collector hang telemetry init
                     "-e", "OTEL_TRACES_EXPORTER=otlp"]
        args.append(image)
        return subprocess.Popen(args, stdout=lf, stderr=subprocess.STDOUT)

    # --- LEGACY host path (UNSANDBOXED) — also routes the model via the proxy ---
    taskfile = os.path.join(workdir, "task.txt")
    with open(taskfile, "w") as f:
        f.write(task_text)
    env = dict(os.environ)
    if use_otel:
        env.update(otel_env)
    else:
        for k in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_TRACES_EXPORTER"):
            env.pop(k, None)
    env["OPENHANDS_SUPPRESS_BANNER"] = "1"
    env["LLM_MODEL"] = f"openai/{model}"
    env["LLM_API_KEY"] = "dummy-no-real-creds"
    env["LLM_BASE_URL"] = proxy_base_url
    cmd = [OPENHANDS, "--headless", "--override-with-envs", "--always-approve", "-f", taskfile]
    return subprocess.Popen(cmd, env=env, cwd=workdir, stdout=lf, stderr=subprocess.STDOUT)


# ---------- one run ----------
def save_run_data(rid, outdir, tag, bearer):
    """Dump everything the platform exposes for a run — result, stats, traces,
    events, artifacts, mission — one JSON per endpoint (max data collection)."""
    d = os.path.join(outdir, "data"); os.makedirs(d, exist_ok=True)
    for ep, gated in (("result", False), ("stats", False), ("traces", False),
                      ("events", False), ("artifacts", True), (_RUN_SUBRESOURCE, True)):
        try:
            _, body = get(f"/api/runs/{rid}/{ep}", token=(bearer if gated else None))
            with open(os.path.join(d, f"{tag}.{ep}.json"), "w") as f:
                if isinstance(body, (dict, list)):
                    json.dump(body, f, indent=1)
                else:
                    f.write(str(body))
        except Exception:
            pass


def flag_pass_of(result):
    for c in (result or {}).get("grade", {}).get("check_breakdown", []) or []:
        if "flag" in ((c.get("id") or "") + str(c.get("check_id") or "")).lower():
            return bool(c.get("passed"))   # a flag counts ONLY if the check PASSED — a submitted value (even a wrong flag) is truthy and must never count
    return None


# OTEL is REQUIRED (XORCISE grades from the agent's telemetry). But when several
# sandboxes start at the same instant, their telemetry init collides and the agent can
# hang before its first model call (0 tool_calls, whole waves lost). Staggering sandbox
# starts ~8s apart avoids that collision without touching OTEL itself.
_SPAWN_STAGGER_S = 8
_spawn_gate = threading.Lock()
_last_spawn = [0.0]


def do_run(entry, mission, idx, budget, dry_run, run_control_host, outdir, use_otel, sandbox, proxy_base_url, image, intel_policy, model_map):
    model, oh_env = resolve_model(entry)
    code, run = post("/api/runs", {"agent": agent_name(model), _RUN_TARGET: mission,
                                   "budget_seconds": budget, "intel_policy": intel_policy})
    if code not in (200, 201) or not isinstance(run, dict):
        return {"model": model, "mission": mission, "run_idx": idx, "error": f"create {code}: {run}"}
    rid = run["run_id"]
    row = {"model": model, "mission": mission, "run_idx": idx, "run_id": rid}

    _, prof = get(f"/api/runs/{rid}/launch-profile")
    cmd_str = prof.get("command", "") if isinstance(prof, dict) else ""
    task = extract_task(cmd_str)               # just the -t task text, not the whole command
    otel = prof.get("env", {}) if isinstance(prof, dict) else {}

    if dry_run:
        # validate orchestration only: emit the launch cmd, then tear down
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, f"launch-{agent_name(model)}-{mission}-{idx}.txt"), "w") as f:
            f.write(cmd_str or "(no command)")
        post(f"/api/runs/{rid}/terminate"); delete(f"/api/runs/{rid}")
        row["dry_run"] = True
        return row

    workdir = os.path.join(outdir, "work", f"{agent_name(model)}-{mission}-{idx}")
    os.makedirs(workdir, exist_ok=True)
    logp = os.path.join(workdir, "openhands.log")
    with _spawn_gate:                              # stagger sandbox starts (see _SPAWN_STAGGER_S)
        _wait = _SPAWN_STAGGER_S - (time.time() - _last_spawn[0])
        if _wait > 0:
            time.sleep(_wait)
        _last_spawn[0] = time.time()
    proc = spawn_openhands(task, otel, model, run_control_host, workdir, logp, use_otel,
                           sandbox, rid, proxy_base_url, image, oh_env, model_map)

    deadline = time.time() + budget + 180  # budget + grace
    rentry = None
    while time.time() < deadline:
        time.sleep(10)
        rentry = run_entry(rid) or rentry
        if is_terminal(rentry):
            break
        if proc.poll() is not None and not is_terminal(rentry):
            time.sleep(15)  # container exited — let a final /complete land
            rentry = run_entry(rid) or rentry
            break
    if not is_terminal(rentry):
        post(f"/api/runs/{rid}/terminate")
    try:
        proc.terminate()
    except Exception:
        pass
    if sandbox:  # ensure the container is gone (proc is the docker client, not the container)
        subprocess.run(["docker", "kill", container_name(rid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Grading can land seconds-to-minutes AFTER the run goes terminal — poll /result
    # until a real grade appears (else the row records overall=None). Large/verbose
    # transcripts (e.g. kimi-2.5) grade slowly, so wait up to ~5 min, not 60s.
    grade = {}
    for _ in range(60):
        _, result = get(f"/api/runs/{rid}/result")
        grade = result.get("grade", {}) if isinstance(result, dict) else {}
        if grade.get("overall") is not None:
            break
        time.sleep(5)
    _, stats = get(f"/api/runs/{rid}/stats")
    counts = (stats or {}).get("counts", {}) if isinstance(stats, dict) else {}
    row.update({
        "state": (rentry or {}).get("state"),
        "overall": grade.get("overall"),
        "deterministic": (grade.get("breakdown") or {}).get("deterministic"),
        "judge": (grade.get("breakdown") or {}).get("judge"),
        "judge_status": grade.get("judge_status"),
        "judge_detail": grade.get("judge_detail"),
        "flag_pass": flag_pass_of(result),
        "hard_fails": len(grade.get("hard_fails", []) or []),
        "major_deductions": len(grade.get("major_deductions", []) or []),
        "tokens": ((stats or {}).get("tokens") if isinstance(stats, dict) else None),
        "model_calls": counts.get("model_calls"),
        "tool_calls": counts.get("tool_calls"),
        "errors": counts.get("errors"),
        "elapsed_seconds": ((stats or {}).get("timing", {}) or {}).get("elapsed_seconds") if isinstance(stats, dict) else None,
    })
    # dump the full per-run data (result/stats/traces/events/artifacts/mission)
    b = re.search(r"Bearer ([0-9a-f]+)", task)
    save_run_data(rid, outdir, f"{model}-{mission}-{idx}", b.group(1) if b else "")
    return row


# ---------- aggregate ----------
def aggregate(rows, outdir):
    from collections import defaultdict
    by = defaultdict(list)
    for r in rows:
        if "overall" in r:
            by[(r["model"], r["mission"])].append(r)
    summary = []
    for (model, ch), rs in sorted(by.items()):
        overalls = [x["overall"] for x in rs if isinstance(x.get("overall"), (int, float))]
        flags = [x.get("flag_pass") for x in rs if x.get("flag_pass") is not None]
        summary.append({
            "model": model, "mission": ch, "n": len(rs),
            "pass@1": round(sum(1 for f in flags if f) / len(flags), 3) if flags else None,
            "pass@k": (1 if any(flags) else 0) if flags else None,
            "mean_overall": round(statistics.mean(overalls), 3) if overalls else None,
            "std_overall": round(statistics.pstdev(overalls), 3) if len(overalls) > 1 else 0.0,
            "judge_unavailable": sum(1 for x in rs if x.get("judge_status") in _JUDGE_ZERO_STATUSES),
        })
    with open(os.path.join(outdir, "rows.json"), "w") as f:
        json.dump(rows, f, indent=1)
    if summary:
        with open(os.path.join(outdir, "summary.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(summary[0].keys())); w.writeheader(); w.writerows(summary)
    print(f"\n=== SUMMARY ({outdir}/summary.csv) ===")
    for s in summary:
        print(f"  {s['model']:20} {s['mission']:22} n={s['n']} pass@1={s['pass@1']} "
              f"mean={s['mean_overall']} std={s['std_overall']} judge_unavail={s['judge_unavailable']}")
    return summary


# ---------- resume / bounded re-run ----------
def run_succeeded(r):
    """A cell counts as DONE only if it produced a real, gradable result. A stalled or
    content-filtered run (tool_calls==0) or an ungraded one (overall is None) is NOT a
    success — it may be re-run, bounded by --max-attempts. Content filtering needs no
    special-casing here: such a run simply fails every attempt and, once attempts are
    exhausted, is left as a dead run (that's the xorcise/agents side's call, not ours)."""
    if r.get("error"):
        return False
    if r.get("dry_run"):
        return True
    if r.get("overall") is None:
        return False
    tc = r.get("tool_calls")
    if tc is not None and tc <= 0:
        return False
    return True


def _cell_key(r):
    k = (r.get("model"), r.get("mission"), r.get("run_idx"))
    return None if any(x is None for x in k) else k


def read_all_progress(outdir):
    """Every run row ever recorded for this outdir — one JSON object per line, appended
    the instant each run finishes, across ALL invocations. The durable, resume-safe
    source of truth: it survives a crash that never reaches the end-of-run aggregate()."""
    out = []
    try:
        for line in open(os.path.join(outdir, "progress.jsonl")):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    except FileNotFoundError:
        pass
    return out


def load_progress(outdir):
    """cell_key -> {'attempts': n, 'success': bool}, folded over all recorded rows."""
    prog = {}
    for r in read_all_progress(outdir):
        k = _cell_key(r)
        if not k:
            continue
        e = prog.setdefault(k, {"attempts": 0, "success": False})
        e["attempts"] += 1
        if run_succeeded(r):
            e["success"] = True
    return prog


def dedupe_best(rows):
    """One row per (model, mission, run_idx) — prefer a successful attempt, else the
    last seen — so aggregating a RESUMED run (which may hold several attempts per cell)
    counts each replicate once rather than inflating n."""
    best = {}
    for r in rows:
        k = _cell_key(r)
        if not k:
            continue
        cur = best.get(k)
        if cur is None or (run_succeeded(r) and not run_succeeded(cur)):
            best[k] = r
    return list(best.values())


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("playbook")
    ap.add_argument("--models", help="comma-separated model names to override the playbook's models: list")
    ap.add_argument("--runs", type=int, help="override runs_per_mission")
    ap.add_argument("--budget", type=int, help="override per-run budget_seconds")
    ap.add_argument("--concurrency", type=int, default=3,
                    help="how many runs execute in parallel; start at 3 and scale up only if the core stays healthy")
    ap.add_argument("--run-control-host", default=DEFAULT_RC_HOST,
                    help="host address the sandbox uses to reach run-control + OTEL "
                         "(default host.docker.internal — works on every platform; the "
                         "sandbox adds --add-host ...:host-gateway so it also resolves on "
                         "native Linux Docker). Override if your setup differs.")
    ap.add_argument("--dry-run", action="store_true", help="orchestrate + emit launch cmds, no agent spawn (no keys needed)")
    ap.add_argument("--otel", action="store_true", help="enable OTEL export — REQUIRED for scoring: XORCISE grades from the agent's telemetry, so always pass this (the runner staggers sandbox starts so telemetry init doesn't collide)")
    ap.add_argument("--no-sandbox", action="store_true", help="UNSAFE: run agents directly on the host (no isolation). Default is per-run sandbox containers.")
    ap.add_argument("--proxy-base-url", default=f"http://{DEFAULT_RC_HOST}:4000/v1", help="model proxy for the LEGACY --no-sandbox path ONLY; ignored in the default sandbox mode, where each sandbox's litellm calls providers directly")
    ap.add_argument("--image", default="xorcise-agent-sandbox:latest", help="agent sandbox docker image")
    ap.add_argument("--intel-policy", help="per-run intel disclosure: none (default) | all | i1,i3 — overrides the playbook")
    ap.add_argument("--llm-config", default=os.path.join(os.path.dirname(__file__), "sandbox", "agent-models.yaml"),
                    help="model-name → provider mapping (agents call the provider directly via OpenHands)")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--creds", help="path to this run's .env of provider KEY=VALUE lines "
                    "(see scripts/setup-creds.sh). The runner loads it into the environment "
                    "and forwards keys to sandboxes by name — the value is never printed.")
    ap.add_argument("--resume", action="store_true",
                    help="skip cells already completed successfully in this outdir (read from progress.jsonl) and run only what's left; also re-runs failed/ungraded cells when --max-attempts > 1. Safe to re-invoke after a crash — nothing already graded is repeated.")
    ap.add_argument("--rerun-failed", action="store_true",
                    help="re-run ONLY cells that were attempted but failed/stalled (overall=None or tool_calls=0); leaves successful and never-attempted cells alone. Implies --max-attempts >= 2.")
    ap.add_argument("--max-attempts", type=int, default=1,
                    help="cap total attempts per cell across resumed invocations (default 1). A permanently-failing cell — e.g. a content-filtered model — exhausts its attempts and is then left as a dead run.")
    args = ap.parse_args()
    if args.creds:
        load_env_file(args.creds)   # load provider keys into env (never printed) for the passthrough

    pb = yaml.safe_load(open(args.playbook))
    # Models come from the playbook (a plain `models:` list of names, each served by
    # the litellm proxy). The harness is fixed — the standardized sandboxed OH CLI.
    models = [m.strip() for m in args.models.split(",")] if args.models else (pb.get("models") or [])
    if not models:
        sys.exit("no models: add a `models:` list to the playbook or pass --models a,b,c")
    runs = args.runs or pb["runs_per_mission"]
    budget = args.budget or pb["budget_seconds"]
    intel_policy = args.intel_policy or pb.get("intel_policy") or "none"  # clean capability run by default
    model_map = load_model_map(args.llm_config)  # short name → full litellm string (direct provider calls)
    outdir = args.outdir or os.path.join(os.path.dirname(__file__), "results", pb["name"])
    os.makedirs(outdir, exist_ok=True)
    sandbox = not args.no_sandbox

    # Isolation: namespace every sandbox this invocation launches with a per-playbook
    # SESSION tag (stable across a --resume), so the skill only ever reads or removes
    # ITS OWN containers — a concurrent playbook or a normal xorcise run on the same
    # host is left untouched. Persisted to session.txt so Step-7 cleanup scopes to it.
    globals()["SESSION_TAG"] = (re.sub(r"[^A-Za-z0-9_.-]+", "-", pb["name"]).strip("-") or "run")
    with open(os.path.join(outdir, "session.txt"), "w") as _sf:
        _sf.write(SESSION_TAG + "\n")

    mode = "SANDBOXED (docker)" if sandbox else "HOST (UNSANDBOXED)"
    print(f"== playbook {pb['name']}: {len(pb['missions'])} missions x {runs} runs x {len(models)} models "
          f"= {len(pb['missions'])*runs*len(models)} runs (budget {budget}s, conc {args.concurrency}, {mode}, intel={intel_policy}) ==")
    print("models:", ", ".join(resolve_model(m)[0] for m in models))
    print(f"session: {SESSION_TAG}  (sandboxes: xorcise-run-{SESSION_TAG}-* — cleanup is scoped to these only)")
    print("preflight:")
    preflight(pb["missions"])
    for m in models:
        ensure_agent(m)

    # Batch identity + timing (referenceable evidence). On --resume/--rerun-failed, keep the
    # original UUID + start time from the existing manifest so the evidence id stays stable.
    _prev = {}
    try:
        _prev = json.load(open(os.path.join(outdir, "manifest.json")))
    except Exception:
        pass
    _resuming = args.resume or args.rerun_failed
    report_uuid = (_prev.get("report_uuid") if _resuming else None) or str(uuid.uuid4())
    started_at = ((_prev.get("started_at") if _resuming else None)
                  or datetime.datetime.now(datetime.timezone.utc).isoformat())

    # Record what this batch IS (models+providers, missions+specialties, run config) so
    # report/gen_report.py renders the real grid + methodology rather than a hardcoded one.
    write_manifest(outdir, models, model_map, pb["missions"], runs, budget,
                   args.concurrency, intel_policy, report_uuid=report_uuid, started_at=started_at,
                   pb_name=pb.get("name"), pb_desc=pb.get("description"))

    tasks = [(m, ch, i) for m in models for ch in pb["missions"] for i in range(1, runs + 1)]

    # ---- resume / bounded re-run: keep only the cells that still need running ----
    max_attempts = args.max_attempts
    if args.rerun_failed and max_attempts < 2:
        max_attempts = 2  # a failed cell already carries 1 attempt; allow at least one retry
    if args.resume or args.rerun_failed:
        prog = load_progress(outdir)
        kept, done_ok, at_max = [], 0, 0
        for (m, ch, i) in tasks:
            e = prog.get((resolve_model(m)[0], ch, i))
            if args.rerun_failed and (not e or e["success"]):
                continue                      # touch only previously-failed cells
            if e and e["success"]:
                done_ok += 1; continue          # already graded — never repeat
            if e and e["attempts"] >= max_attempts:
                at_max += 1; continue           # exhausted — leave as a dead run
            kept.append((m, ch, i))
        print(f"  resume: {len(kept)} cell(s) to run, {done_ok} already succeeded, "
              f"{at_max} at max-attempts ({max_attempts}) left as dead runs")
        tasks = kept

    # Durable per-run checkpoint (flushed after every run) so a crash never loses progress.
    # A run WITHOUT --resume is a NEW run: reset the checkpoint so its results never mix with
    # a prior run's in this outdir. --resume / --rerun-failed continue the existing checkpoint.
    prog_path = os.path.join(outdir, "progress.jsonl")
    fresh = not (args.resume or args.rerun_failed)
    if fresh and os.path.exists(prog_path) and os.path.getsize(prog_path) > 0:
        print(f"  fresh run — resetting the existing checkpoint at {prog_path} "
              f"(pass --resume to continue a prior run instead)")
    prog_f = open(prog_path, "w" if fresh else "a")
    rows = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(do_run, m, ch, i, budget, args.dry_run, args.run_control_host, outdir,
                          args.otel, sandbox, args.proxy_base_url, args.image, intel_policy, model_map): (m, ch, i)
                for (m, ch, i) in tasks}
        for fu in as_completed(futs):
            m, ch, i = futs[fu]
            try:
                r = fu.result()
            except Exception as e:  # one bad run must not abort the whole matrix
                r = {"model": resolve_model(m)[0], "mission": ch, "run_idx": i, "error": f"{type(e).__name__}: {e}"}
            rows.append(r)
            if not r.get("dry_run"):                              # dry-run is orchestration validation, not a
                prog_f.write(json.dumps(r) + "\n"); prog_f.flush()  # result — never let it satisfy a later --resume
            tag = f"{r.get('model')}/{r.get('mission')}#{r.get('run_idx')}"
            print(f"  done {tag}: " + (r.get("error") or (f"overall={r.get('overall')} flag={r.get('flag_pass')}"
                                                          if not r.get("dry_run") else "dry-run ok")), flush=True)
    prog_f.close()

    # Aggregate over the FULL recorded history (all invocations), de-duped to one row per
    # cell, so a resumed run yields a complete, correct summary — not just this pass.
    all_rows = read_all_progress(outdir) or rows
    aggregate(dedupe_best(all_rows), outdir)
    # Stamp completion time onto the manifest (report duration = completed_at - started_at).
    try:
        _mp = os.path.join(outdir, "manifest.json")
        _man = json.load(open(_mp))
        _man["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with open(_mp, "w") as _mf:
            json.dump(_man, _mf, indent=2)
    except Exception:
        pass
    print(f"\nresults in {outdir}/  (evidence UUID {report_uuid})")


if __name__ == "__main__":
    main()
