# Per-run agent sandbox for XORCISE benchmarking.
# Runs `openhands --headless` inside a throwaway container: no host filesystem, no
# host creds (model calls go to the host litellm proxy). The container joins the
# run's tailnet itself in KERNEL/TUN mode (via join-and-run.sh) so the agent reaches
# challenge IPs directly — "as if host-networked" — before the agent starts.
# Requires: --cap-add NET_ADMIN --device /dev/net/tun at run time.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates iproute2 iptables git tar jq \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv
# boto3: litellm needs it to call AWS Bedrock DIRECTLY from inside the sandbox
# (agents are OpenHands-direct now — no proxy in the model path).
RUN uv tool install 'openhands==1.16.0' --with boto3 --python 3.12
ENV PATH=/root/.local/bin:$PATH
ENV OPENHANDS_SUPPRESS_BANNER=1

# Required OTEL fix: OpenHands 1.16.0 calls lmnr.observe(rollout_entrypoint=…) which
# the latest published lmnr (0.7.56) rejects — enabling OTEL crashes the agent without it.
RUN f="$(find /root/.local/share/uv/tools/openhands -path '*/observability/laminar.py' | head -1)" \
    && sed -i '/rollout_entrypoint=rollout_entrypoint,/d' "$f" \
    && echo "patched $f"

COPY join-and-run.sh /usr/local/bin/join-and-run.sh
RUN chmod +x /usr/local/bin/join-and-run.sh

# Task via OH_TASK; tailnet creds via RC_BASE + RUN_BEARER; model/params via LLM_* env.
ENTRYPOINT ["/usr/local/bin/join-and-run.sh"]
