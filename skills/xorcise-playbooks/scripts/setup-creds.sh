#!/usr/bin/env bash
# Scaffold a per-run credentials .env with the REQUIRED variable names, then OPEN it for
# the user to fill in the values.
#
# The value is typed into the FILE (in the user's editor) — never on a command line, never
# echoed, never in the transcript. The runner then reads this file via `--creds`; the agent
# never cat/print/echoes it. The file is chmod 600 and git-ignored.
#
# Usage:
#   setup-creds.sh <creds.env> VAR1 [VAR2 ...]         # explicit variable names, or
#   setup-creds.sh <creds.env> --models <models.yaml>  # derive the vars each contestant needs
set -u

DEST="${1:-}"; shift || true
if [ -z "$DEST" ]; then
  echo "usage: setup-creds.sh <creds.env> VAR1 [VAR2 ...] | --models <models.yaml>" >&2
  exit 2
fi

VARS=()
if [ "${1:-}" = "--models" ]; then
  MODELS="${2:-}"
  if [ -z "$MODELS" ] || [ ! -f "$MODELS" ]; then
    echo "--models needs a readable model-map yaml path" >&2; exit 2
  fi
  # Derive the provider env vars each contestant's litellm model string requires.
  while IFS= read -r v; do [ -n "$v" ] && VARS+=("$v"); done < <(python3 - "$MODELS" <<'PY'
import sys, yaml
PFX = [
  ("bedrock/",     ["AWS_BEARER_TOKEN_BEDROCK", "AWS_REGION"]),
  ("openai/",      ["OPENAI_API_KEY"]),
  ("anthropic/",   ["ANTHROPIC_API_KEY"]),
  ("gemini/",      ["GEMINI_API_KEY"]),
  ("openrouter/",  ["OPENROUTER_API_KEY"]),
  ("azure/",       ["AZURE_API_KEY", "AZURE_API_BASE"]),
  ("mistral/",     ["MISTRAL_API_KEY"]),
  ("groq/",        ["GROQ_API_KEY"]),
  ("together_ai/", ["TOGETHER_API_KEY"]),
  ("deepseek/",    ["DEEPSEEK_API_KEY"]),
  ("cohere/",      ["COHERE_API_KEY"]),
]
try:
    cfg = yaml.safe_load(open(sys.argv[1])) or {}
except Exception:
    cfg = {}
out = []
for m in (cfg.get("model_list") or []):
    s = str((m.get("litellm_params") or {}).get("model", ""))
    for pfx, vs in PFX:
        if s.startswith(pfx):                      # bedrock/converse/... also starts with bedrock/
            for v in vs:
                if v not in out:
                    out.append(v)
            break
for v in out:
    print(v)
PY
)
  if [ "${#VARS[@]}" -lt 1 ]; then
    echo "couldn't derive any provider vars from $MODELS — pass explicit VAR names instead" >&2
    exit 4
  fi
else
  if [ "$#" -lt 1 ]; then
    echo "give at least one env var name, or use --models <models.yaml>" >&2; exit 2
  fi
  VARS=("$@")
fi

if [ -e "$DEST" ]; then
  echo "refusing to overwrite existing file: $DEST" >&2
  echo "(delete it first, or point at a new path)" >&2
  exit 3
fi

mkdir -p "$(dirname "$DEST")" 2>/dev/null || true
umask 077
{
  echo "# xorcise-playbooks — provider credentials for THIS run."
  echo "# Fill in each value after the '=', then save and close this file."
  echo "# Do NOT commit or share it. It is chmod 600 and git-ignored."
  for v in "${VARS[@]}"; do echo "${v}="; done
} > "$DEST"
chmod 600 "$DEST"

# best-effort .gitignore next to it so it can never be committed
d="$(cd "$(dirname "$DEST")" && pwd)"; b="$(basename "$DEST")"
if ! grep -qxF "$b" "$d/.gitignore" 2>/dev/null; then
  printf '%s\n' "$b" >> "$d/.gitignore" 2>/dev/null || true
fi
ABS="$d/$b"

echo "Scaffolded this run's credentials file (chmod 600, git-ignored) with the keys your"
echo "contestants need — fill in the value after each '=':"
echo
printf '    %s\n' "${VARS[@]/%/=…}"
echo
echo "File: $ABS"
# Open the file in the user's editor (best-effort, non-blocking). An agent's shell often
# does NOT inherit $DISPLAY, so when it's missing we fall back to the active desktop
# session's display instead of giving up. Always print the path as the manual fallback.
_disp="${DISPLAY:-}"
if [ -z "$_disp" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
  _disp="$(ls /tmp/.X11-unix/ 2>/dev/null | sed -n 's/^X\([0-9]\+\)$/:\1/p' | sort | head -1)"
  [ -z "$_disp" ] && _disp=":0"
fi
_opened=""
for _o in xdg-open gio gnome-text-editor code kate gedit; do
  command -v "$_o" >/dev/null 2>&1 || continue
  case "$_o" in
    gio)  _cmd=(gio open "$ABS") ;;
    code) _cmd=("$_o" -- "$ABS") ;;
    *)    _cmd=("$_o" "$ABS") ;;
  esac
  ( DISPLAY="${DISPLAY:-$_disp}" WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}" nohup "${_cmd[@]}" >/dev/null 2>&1 & ) 2>/dev/null
  _opened="$_o"; break
done
if [ -n "$_opened" ]; then
  echo "Opened it in your editor ($_opened) — fill in each value, save, and close."
  echo "(If nothing appeared, just open the path above.)"
else
  echo "Open that path in your editor, fill in the values, save, and close."
fi
echo
echo "The value is only ever typed into the file — never on a command line."
