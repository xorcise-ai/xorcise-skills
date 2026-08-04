#!/bin/sh
# XORCISE core health watchdog — for long / unattended runs.
#
# Polls the core's health endpoint and, after N *consecutive* failures, runs a restart
# command you supply. A single slow probe (the core can briefly exceed the timeout during
# a grading+provisioning burst) will NOT trip it — only a sustained outage does. This is
# the durable auto-recovery we use for overnight runs so a rare core wedge self-heals
# instead of stalling the whole matrix.
#
# Config (all via env):
#   XORCISE_BASE          core base URL            (default http://127.0.0.1:3001)
#   WATCHDOG_INTERVAL     seconds between probes    (default 30)
#   WATCHDOG_TIMEOUT      per-probe curl timeout, s (default 5)
#   WATCHDOG_FAILS        consecutive fails to trip (default 4  => ~2 min at defaults)
#   WATCHDOG_COOLDOWN     seconds to wait after a restart before probing again (default 120)
#   WATCHDOG_RESTART_CMD  shell command to restart the core when tripped. If unset, the
#                         watchdog only logs a loud warning (still useful as an alerter).
#
# Example (systemd, or just `nohup ... &`):
#   XORCISE_RESTART_CMD is deployment-specific — e.g. "xorcise restart", a
#   "docker restart <core>", or "systemctl restart xorcise-core".
#     WATCHDOG_RESTART_CMD='xorcise restart' scripts/watchdog.sh
set -u

BASE="${XORCISE_BASE:-http://127.0.0.1:3001}"
HEALTH="${BASE%/}/api/health"
INTERVAL="${WATCHDOG_INTERVAL:-30}"
TIMEOUT="${WATCHDOG_TIMEOUT:-5}"
FAILS="${WATCHDOG_FAILS:-4}"
COOLDOWN="${WATCHDOG_COOLDOWN:-120}"
RESTART_CMD="${WATCHDOG_RESTART_CMD:-}"

command -v curl >/dev/null 2>&1 || { echo "watchdog: curl not found" >&2; exit 1; }
log() { echo "[watchdog $(date '+%Y-%m-%dT%H:%M:%S')] $*"; }

log "watching $HEALTH — probe ${INTERVAL}s, trip after ${FAILS} consecutive fails, restart=$([ -n "$RESTART_CMD" ] && echo "\"$RESTART_CMD\"" || echo '(none — alert only)')"

streak=0
while :; do
  # curl already prints 000 on connection failure/timeout; guard only the empty case
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" "$HEALTH" 2>/dev/null)"
  [ -n "$code" ] || code=000
  if [ "$code" = "200" ]; then
    if [ "$streak" -ne 0 ]; then log "core healthy again (200) after ${streak} miss(es)"; fi
    streak=0
  else
    streak=$((streak + 1))
    log "health probe -> ${code} (consecutive miss ${streak}/${FAILS})"
    if [ "$streak" -ge "$FAILS" ]; then
      if [ -n "$RESTART_CMD" ]; then
        log "TRIPPED — restarting core: $RESTART_CMD"
        sh -c "$RESTART_CMD" || log "restart command exited non-zero"
        log "restarted; cooling down ${COOLDOWN}s"
        sleep "$COOLDOWN"
      else
        log "TRIPPED — core down for ${streak} probes and no WATCHDOG_RESTART_CMD set (alert only)"
        sleep "$COOLDOWN"
      fi
      streak=0
    fi
  fi
  sleep "$INTERVAL"
done
