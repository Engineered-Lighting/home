#!/usr/bin/env bash
# Snapshot the working HomeAIVoice stack into this repo's stack/.
#
# Run from the repo root before tagging a release. Keeps stack/ aligned
# with the user's local working copy at C:\Claude\HomeAIVoice (the
# operator workstation's dev source of truth).
#
# Caveats:
#   - Personal LAN IPs, HA tokens, and Tailscale hostnames are scrubbed
#     to placeholders before commit. Re-check by diffing if anything
#     environment-specific slips through.
#   - .env is NEVER copied. Only .env.example lives in the repo.
set -euo pipefail

SRC="${HOME_AI_VOICE_DIR:-/c/Claude/HomeAIVoice}"
DST="$(cd "$(dirname "$0")/.." && pwd)/stack"

if [ ! -d "$SRC" ]; then
  echo "no source dir at $SRC" >&2
  echo "set HOME_AI_VOICE_DIR if your working copy is elsewhere" >&2
  exit 1
fi

echo "==> Syncing $SRC → $DST"

cp "$SRC/docker-compose.yml"   "$DST/docker-compose.yml"
cp "$SRC/scripts/stack.sh"     "$DST/scripts/stack.sh"
cp "$SRC/stack.ps1"            "$DST/scripts/stack.ps1"

for s in router stt tts vision metrics-sidecar; do
  if [ -d "$SRC/services/$s" ]; then
    rm -rf "$DST/services/$s"
    cp -r "$SRC/services/$s" "$DST/services/"
  fi
done

# Scrub identifying details.
echo "==> Scrubbing private IPs / hostnames / tokens"
sed -i 's|192\.168\.0\.[0-9]\+|<ai-box-ip>|g' "$DST/docker-compose.yml" 2>/dev/null || true
sed -i 's|hav-ubuntu|<ai-box-host>|g'         "$DST/scripts/stack.ps1"
sed -i 's|HA_TOKEN: ".*"|HA_TOKEN: ""|g'      "$DST/docker-compose.yml" 2>/dev/null || true

echo "==> Done. Review:  git diff stack/"
echo "    Then commit + tag for release."
