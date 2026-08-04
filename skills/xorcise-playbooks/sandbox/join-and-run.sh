#!/bin/sh
# Sandbox entrypoint: provision → connect → run.
#   1. pull the run's tailnet credentials from run-control (needs RC_BASE + RUN_BEARER)
#   2. bring up tailscale in KERNEL/TUN mode so the agent reaches mission IPs directly
#      ("as if host-networked" — requires --cap-add NET_ADMIN --device /dev/net/tun)
#   3. exec `openhands --headless` with the task (OH reads model/params from env)
set -eu
: "${RC_BASE:?RC_BASE required}" "${RUN_BEARER:?RUN_BEARER required}"
AUTH="Authorization: Bearer ${RUN_BEARER}"
log() { echo "[sandbox] $*"; }

log "fetching tailnet connect info from ${RC_BASE}/connect"
CONN="$(curl -fsS -H "$AUTH" "${RC_BASE}/connect")"
LOGIN="$(printf '%s' "$CONN" | jq -r '.login_server')"
AUTHKEY="$(printf '%s' "$CONN" | jq -r '.join_key // .authkey')"
printf '%s' "$CONN" | jq -r '.ca_cert // empty' > /usr/local/share/ca-certificates/headscale.crt || true
if [ -s /usr/local/share/ca-certificates/headscale.crt ]; then
  update-ca-certificates >/dev/null 2>&1 || true
  log "installed Headscale CA"
fi

log "fetching pinned tailscale client from run-control"
curl -fsS -H "$AUTH" "${RC_BASE}/tailscale.tgz" -o /tmp/ts.tgz
mkdir -p /tmp/ts && tar xzf /tmp/ts.tgz -C /tmp/ts
TSD="$(find /tmp/ts -name tailscaled -type f | head -1)"
TS="$(find /tmp/ts -name tailscale -type f | head -1)"
[ -n "$TSD" ] && [ -n "$TS" ] || { log "tailscale binaries not found in tarball"; exit 1; }

mkdir -p /var/run/tailscale /var/lib/tailscale
SOCK=/var/run/tailscale/tailscaled.sock
log "starting tailscaled (kernel/TUN mode)"
"$TSD" --state=/var/lib/tailscale/tailscaled.state --socket="$SOCK" --tun=tailscale0 >/tmp/tailscaled.log 2>&1 &
n=0; until "$TS" --socket="$SOCK" status >/dev/null 2>&1 || [ "$n" -gt 20 ]; do n=$((n+1)); sleep 0.5; done

# A live "lab" mission has network terrain to join; a "static" mission (e.g. a
# reverse-engineering binary) has NONE, and run-control may hand back no usable tailnet
# creds for it. Only attempt the join when we actually have them, ALWAYS with a timeout
# (so a join that can't register can't fall back to interactive auth and hang forever),
# and NEVER let a join problem be fatal — a static mission runs fine with no tailnet.
if [ -n "$LOGIN" ] && [ "$LOGIN" != "null" ] && [ -n "$AUTHKEY" ] && [ "$AUTHKEY" != "null" ]; then
  log "joining tailnet at ${LOGIN}"
  if "$TS" --socket="$SOCK" up --login-server="$LOGIN" --authkey="$AUTHKEY" \
        --hostname="xorcise-agent-${RUN_ID:-run}" --accept-routes --accept-dns=false \
        --timeout=90s; then
    IP="$("$TS" --socket="$SOCK" ip -4 2>/dev/null | head -1 || true)"
    log "tailnet up — agent IP ${IP:-unknown}; mission targets reachable directly by IP"
  else
    log "WARNING: tailnet join did not complete — continuing without it (expected for static missions)"
  fi
else
  log "no tailnet creds for this run (static mission / no network terrain) — skipping join"
fi

printf '%s' "${OH_TASK:-}" > /tmp/task.txt
log "starting openhands agent"
exec openhands --headless --override-with-envs --always-approve -f /tmp/task.txt
