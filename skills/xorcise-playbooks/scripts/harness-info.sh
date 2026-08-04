#!/usr/bin/env bash
# Print the OpenHands + litellm versions the sandbox image ACTUALLY ships, and warn about
# the model-cost implication: OpenHands routes every model call through litellm, so a model
# that this litellm build doesn't fully support (common for brand-new models) may not get
# prompt caching by default — which can multiply token cost.
#
# Usage:  harness-info.sh [image]   (default: xorcise-agent-sandbox:latest)
set -u

IMAGE="${1:-${XORCISE_PLAYBOOKS_IMAGE:-xorcise-agent-sandbox:latest}}"
# litellm lives in OpenHands' isolated uv-tool venv, not the image's system python.
VENV_PY="/root/.local/share/uv/tools/openhands/bin/python"

vers=""
if command -v docker >/dev/null 2>&1 && docker image inspect "$IMAGE" >/dev/null 2>&1; then
  vers="$(docker run --rm -i --entrypoint "$VENV_PY" "$IMAGE" - <<'PY' 2>/dev/null
import importlib.metadata as md
def v(p):
    try: return md.version(p)
    except Exception: return "unknown"
print(f"OpenHands {v('openhands')}  ·  litellm {v('litellm')}")
PY
)"
fi

echo "── OpenHands / litellm — model-cost note ─────────────────────────────"
if [ -n "$vers" ]; then
  echo "  sandbox image : $IMAGE"
  echo "  harness       : $vers"
else
  echo "  sandbox image '$IMAGE' isn't built yet — re-run this after the Step-5 build"
  echo "  to see the exact OpenHands / litellm versions."
fi
cat <<'EOF'

  Why this matters:
  OpenHands sends EVERY model call through litellm inside the sandbox. If this litellm
  build doesn't fully support your chosen model — common for very new models — it may
  NOT enable prompt caching by default, which can multiply token cost. (Seen in practice:
  a model that ran without caching used ~1.7x the tokens of a comparable one that cached.)

  >> If you're not certain your model is BOTH supported and prompt-cached by the litellm
     version above, confirm it yourself (litellm release notes / provider docs) before
     committing to a large, paid run.
EOF
