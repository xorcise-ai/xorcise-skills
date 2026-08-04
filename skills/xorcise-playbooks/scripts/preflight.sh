#!/usr/bin/env bash
# xorcise-playbooks preflight — checks the machine is ready to run a benchmark.
# Friendly, read-only: it inspects, it never installs, starts, or stops anything.
#
# Checks:
#   1. XORCISE reachable + healthy (REST health at $XORCISE_BASE/api/system)
#   2. Docker daemon running
#   3. Agent-sandbox image present (build it from sandbox/ if missing — we tell you how)
#   4. /dev/net/tun present (the sandbox joins the mission network in TUN mode)
#   5. Python packages available — PyYAML (runner) + litellm (connectivity check)
#   6. Judge model configured on XORCISE (grading needs it; presence check, spends nothing)
# (Provider keys are handled later in Step 2 — a per-run creds .env — not checked here.)
#
# It NEVER reads or prints any API key value: the judge check reads XORCISE's masked
# /api/config (the secret key is write-only there), and the package check only imports.
#
# Exit status: 0 if every REQUIRED check passed, 1 otherwise. WARN never fails.
#
# Environment overrides:
#   XORCISE_BASE          default http://127.0.0.1:3001
#   XORCISE_PLAYBOOKS_IMAGE   default xorcise-agent-sandbox:latest

set -u

BASE="${XORCISE_BASE:-http://127.0.0.1:3001}"
IMAGE="${XORCISE_PLAYBOOKS_IMAGE:-xorcise-agent-sandbox:latest}"

# Colours only when stdout is a terminal.
if [ -t 1 ]; then
  G="$(printf '\033[32m')"; R="$(printf '\033[31m')"; Y="$(printf '\033[33m')"
  B="$(printf '\033[1m')"; N="$(printf '\033[0m')"
else
  G=""; R=""; Y=""; B=""; N=""
fi

fails=0
pass() { printf "  ${G}PASS${N}  %s\n" "$1"; }
warn() { printf "  ${Y}WARN${N}  %s\n" "$1"; }
fail() { printf "  ${R}FAIL${N}  %s\n" "$1"; fails=$((fails + 1)); }
tip() { printf "        %s\n" "$1"; }

printf "${B}xorcise-playbooks preflight${N}\n"
printf "  base image  %s\n" "$IMAGE"
printf "  xorcise url %s\n\n" "$BASE"

# --- 1. XORCISE reachable + healthy -----------------------------------------
if ! command -v curl >/dev/null 2>&1; then
  fail "curl not found — needed to reach XORCISE."
  tip "Install curl, then re-run this preflight."
else
  # Retry a few times — a busy XORCISE can momentarily blip under load, and a single
  # failed probe shouldn't look like "platform down".
  code=000
  for _ in 1 2 3 4; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/api/system" 2>/dev/null)"
    [ "$code" = "200" ] && break
    sleep 2
  done
  if [ "$code" = "200" ]; then
    pass "XORCISE is up and answering at $BASE"
  else
    fail "XORCISE is not reachable at $BASE (HTTP '$code' after retries)."
    tip "Start your XORCISE platform, or set XORCISE_BASE to its URL. Do NOT install it from here."
  fi
fi

# --- 2. Docker daemon -------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  fail "docker command not found."
  tip "Install Docker and make sure your user can run it."
elif ! docker info >/dev/null 2>&1; then
  fail "Docker is installed but the daemon is not responding."
  tip "Start Docker (e.g. 'systemctl start docker' or open Docker Desktop), then re-run."
else
  pass "Docker daemon is running"

  # --- 3. Agent-sandbox image ----------------------------------------------
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    pass "Agent-sandbox image present: $IMAGE"
  else
    warn "Agent-sandbox image '$IMAGE' not found — the skill will build it for you."
    here="$(cd "$(dirname "$0")/.." && pwd)"
    tip "Build it with:  docker build -t $IMAGE \"$here/sandbox\""
  fi
fi

# --- 4. /dev/net/tun --------------------------------------------------------
if [ -e /dev/net/tun ]; then
  pass "/dev/net/tun present (sandbox networking available)"
else
  warn "/dev/net/tun not found — sandboxes join the mission network in TUN mode."
  tip "On most hosts: 'sudo modprobe tun'. Required before a real (non --dry-run) run."
fi

# --- 5. Python packages (PyYAML for the runner; litellm for the check) ------
if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 not found — the runner needs it."
else
  if python3 -c "import yaml" >/dev/null 2>&1; then
    pass "python3 + PyYAML available (the runner needs them)"
  else
    fail "python3 is present but PyYAML is missing."
    tip "Install it:  python3 -m pip install pyyaml   (or use a venv that has it)."
  fi
  if python3 -c "import litellm" >/dev/null 2>&1; then
    pass "litellm available (the Step 3 connectivity check needs it)"
  else
    warn "litellm not installed — the Step 3 connectivity check can't run without it."
    tip "Install it:  python3 -m pip install litellm   (or use a venv that has it)."
  fi
fi

# --- 6. Judge model configured on XORCISE -----------------------------------
# Grading is 50% deterministic + 50% LLM judge; with no judge configured on the platform,
# runs finish but can't be graded. Presence check only (spends no tokens, reads no key —
# /api/config masks the secret). Step 3's connectivity check live-tests that it answers.
if command -v curl >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
  jc="$(curl -s --max-time 10 "$BASE/api/config" 2>/dev/null | python3 -c 'import sys,json
try: print("yes" if json.load(sys.stdin).get("judge",{}).get("configured") else "no")
except Exception: print("err")' 2>/dev/null)"
  if [ "$jc" = "yes" ]; then
    pass "Judge model is configured on XORCISE"
  elif [ "$jc" = "no" ]; then
    fail "No judge model configured on XORCISE — runs would finish ungraded."
    tip "Configure the judge on your XORCISE platform (its grading/model settings) — that's done on XORCISE."
  else
    warn "Couldn't read the judge config from $BASE/api/config."
    tip "Make sure XORCISE is reachable; the judge is configured on the platform, not here."
  fi
fi
# (Provider keys are NOT checked here — they don't exist yet at preflight time. In Step 2
# the skill scaffolds a per-run creds .env (scripts/setup-creds.sh) that the user fills in
# and the runner reads via --creds; Step 3 then live-tests each key.)

# --- summary ----------------------------------------------------------------
printf "\n"
if [ "$fails" -eq 0 ]; then
  printf "${G}${B}All required checks passed.${N} Review any WARN notes above, then continue.\n"
  exit 0
else
  printf "${R}${B}%d required check(s) failed.${N} Fix the FAIL items above, then re-run this preflight.\n" "$fails"
  exit 1
fi
