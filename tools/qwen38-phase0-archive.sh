#!/usr/bin/env bash
# qwen38-phase0-archive.sh — Phase 0 of docs/QWEN38-MIGRATION.md.
#
# READ-ONLY on-host verification. Archives the live truth of the AI box into
# /srv/data/eval/migration/phase0/ so the migration has evidence instead of
# assumptions. The archive IS the never-deploy list: every service captured
# here has a live source that diverges from the repo copy, and redeploying
# the repo copy destroys live behaviour.
#
# This script NEVER stops, restarts, or reconfigures anything. It only reads.
# It is safe to run at any time, but the migration doc requires it to run
# after a green 5-step maintenance admission check
# (docs/UBUNTU-AI-HOST-STABILITY-REVIEW-2026-08-13.md).
#
# Secrets: .env and any token-bearing file is archived REDACTED. The archive
# lives on the host only; nothing here is meant to be committed.
#
# Usage:  tools/qwen38-phase0-archive.sh [OUT_DIR]
set -uo pipefail

OUT="${1:-/srv/data/eval/migration/phase0}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=/opt/home-ai-voice/docker-compose.yml
ENVFILE=/opt/home-ai-voice/.env
HA_SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -p 22222 root@homeassistant.local"

mkdir -p "$OUT" || { echo "FATAL: cannot create $OUT" >&2; exit 1; }
exec > >(tee "$OUT/collect.log") 2>&1

echo "=== qwen38 Phase 0 archive ==="
echo "host:      $(hostname)"
echo "collected: $(date -Is)  (host TZ $(date +%Z))"
echo "out:       $OUT"
echo

# Redact credentials while keeping the key names visible, so the inventory
# stays reviewable. Deliberately NOT a blunt /TOKEN/ match: settings like
# VISION_MAX_TOKENS and VLLM_MAX_TOKENS are latency evidence, not secrets,
# and an over-eager redactor silently destroys the timeout inventory.
redact() {
  python3 -c '
import re, sys
SECRET = re.compile(r"^(?P<k>[A-Za-z0-9_.\-]*?(?:API_?KEY|_KEY|^KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|SALT|SIGNING))(?P<sep>\s*[:=]\s*)(?P<v>.*)$")
# Counters and budgets that merely contain a sensitive-looking word.
SAFE = re.compile(r"(TOKENS|_MAX_|MAX_TOKEN|KEEP_ALIVE|KEY_PREFIX|TOKENIZER)", re.I)
INLINE = [
    (re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/\-]{8,}=*"), r"\1 <REDACTED>"),
    (re.compile(r"\beyJ[A-Za-z0-9._-]{20,}"), "<REDACTED-JWT>"),
    (re.compile(r"\bhf_[A-Za-z0-9]{10,}"), "<REDACTED-HF>"),
    (re.compile(r"\b(tk_|sk-|ghp_)[A-Za-z0-9_-]{10,}"), "<REDACTED-KEY>"),
]
for line in sys.stdin:
    line = line.rstrip("\n")
    m = SECRET.match(line)
    if m and not SAFE.search(m.group("k")):
        v = m.group("v").strip()
        line = m.group("k") + m.group("sep") + ("<REDACTED>" if v else "(empty)")
    else:
        for pat, rep in INLINE:
            line = pat.sub(rep, line)
    print(line)
'
}

section() { echo; echo "--- $1 ---"; }

# ── 1. Engine: live compose, env, image digest ───────────────────────────
section "1. live compose + env + image digest"
cp -a "$COMPOSE" "$OUT/docker-compose.live.yml" 2>/dev/null \
  && echo "compose      -> docker-compose.live.yml"
redact < "$ENVFILE" > "$OUT/env.live.redacted" 2>/dev/null \
  && echo "env          -> env.live.redacted (secrets stripped)"

{
  echo "# vLLM service — live compose block (authoritative)"
  grep -n "^  vllm:" -A 90 "$COMPOSE"
} > "$OUT/vllm-compose-block.txt" 2>/dev/null

{
  echo "container_image_ref: $(docker inspect hav-vllm --format '{{.Config.Image}}' 2>/dev/null)"
  echo "resolved_image_id:   $(docker inspect hav-vllm --format '{{.Image}}' 2>/dev/null)"
  echo "repo_digests:        $(docker image inspect "$(docker inspect hav-vllm --format '{{.Image}}' 2>/dev/null)" --format '{{json .RepoDigests}}' 2>/dev/null)"
  echo "started_at:          $(docker inspect hav-vllm --format '{{.State.StartedAt}}' 2>/dev/null)"
  echo
  echo "# CUTOVER GATE: the digest below must equal the one in the compose"
  echo "# 'image:' line at cutover time. ':latest' must never smuggle an"
  echo "# engine change — qualification is valid only on this digest."
} > "$OUT/vllm-image-digest.txt"
echo "image digest -> vllm-image-digest.txt"

# ── 2. Engine runtime facts (KV geometry, backend, versions) ─────────────
section "2. engine runtime facts"
docker logs hav-vllm 2>&1 | grep -iE \
  "GPU KV cache size|Maximum concurrency|attention backend|Using .*backend|Graph capturing|torch.compile|model weights take|Memory profiling|available KV cache" \
  > "$OUT/vllm-startup-assertions.txt"
echo "startup      -> vllm-startup-assertions.txt"
grep -E "GPU KV cache size|Maximum concurrency|FLASH|FLASHINFER" "$OUT/vllm-startup-assertions.txt" | tail -6

docker exec hav-vllm python3 -c \
  "import transformers,vllm,torch;print('transformers',transformers.__version__);print('vllm',vllm.__version__);print('torch',torch.__version__);print('cuda',torch.version.cuda)" \
  > "$OUT/engine-versions.txt" 2>&1
echo "versions     -> engine-versions.txt"; cat "$OUT/engine-versions.txt"

docker exec hav-vllm env 2>/dev/null | grep -iE "^(VLLM_|CUDA_|TORCH_|NV_|HF_)" | sort | redact \
  > "$OUT/vllm-container-env.txt"
echo "container env-> vllm-container-env.txt"

# ── 3. VRAM tenant map + weight sets + disk ──────────────────────────────
section "3. VRAM tenant map / weights / disk"
{
  echo "# pid -> container -> VRAM. The compose comment claiming a ~24 GB"
  echo "# Moshi listener is STALE: the s2s profile is not running."
  nvidia-smi --query-gpu=name,memory.used,memory.total,temperature.gpu --format=csv
  echo
  nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader | while read -r line; do
    pid=$(echo "$line" | cut -d, -f1 | tr -d ' ')
    mem=$(echo "$line" | cut -d, -f2)
    cname=""
    for c in $(docker ps -q); do
      if docker top "$c" 2>/dev/null | grep -qw "$pid"; then
        cname=$(docker inspect -f '{{.Name}}' "$c"); break
      fi
    done
    echo "pid=$pid mem=$mem container=${cname:-host/unknown}"
  done
} > "$OUT/vram-tenant-map.txt" 2>&1
echo "vram         -> vram-tenant-map.txt"; cat "$OUT/vram-tenant-map.txt"

docker exec hav-vllm sh -c 'du -sh /root/.cache/huggingface/hub/* 2>/dev/null' \
  > "$OUT/weight-sets.txt" 2>&1
{
  echo
  echo "# CUTOVER PRECONDITION: BOTH weight sets present (rollback is a"
  echo "# compose restore + recreate, ~3-5 min, no re-download). No cache"
  echo "# pruning until soak exit."
} >> "$OUT/weight-sets.txt"
echo "weights      -> weight-sets.txt"; grep -E "Qwen3-VL-30B-A3B-Instruct-FP8|Qwen3.8-27B-FP8" "$OUT/weight-sets.txt"

df -h / /srv > "$OUT/disk-headroom.txt" 2>&1
echo "disk         -> disk-headroom.txt"

# ── 4. Candidate checkpoint geometry (settles R1 offline) ────────────────
section "4. candidate KV geometry (R1)"
CFG=$(docker exec hav-vllm sh -c \
  'find /root/.cache/huggingface/hub/models--Qwen--Qwen3.8-27B-FP8/snapshots -name config.json 2>/dev/null | head -1' 2>/dev/null)
if [ -n "$CFG" ]; then
  docker exec hav-vllm sh -c "cat '$CFG'" > "$OUT/candidate-config.json" 2>/dev/null
  python3 - "$OUT/candidate-config.json" > "$OUT/candidate-kv-math.txt" 2>&1 <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
t = cfg.get("text_config", cfg)
types = t["layer_types"]
full = sum(1 for x in types if x == "full_attention")
lin = sum(1 for x in types if x == "linear_attention")
kvh, hd = t["num_key_value_heads"], t["head_dim"]
per_tok_bf16 = full * 2 * kvh * hd * 2           # K+V, 2 bytes
print(f"num_hidden_layers      : {t['num_hidden_layers']}")
print(f"full_attention layers  : {full}   (interval {t.get('full_attention_interval')})")
print(f"linear_attention layers: {lin}")
print(f"num_key_value_heads    : {kvh}")
print(f"head_dim               : {hd}")
print(f"max_position_embeddings: {t['max_position_embeddings']}")
print(f"mtp_num_hidden_layers  : {t.get('mtp_num_hidden_layers')}  (MTP head present)")
print(f"mamba_ssm_dtype        : {t.get('mamba_ssm_dtype')}")
print()
print(f"KV per token bf16      : {per_tok_bf16} B = {per_tok_bf16/1024:.0f} KiB/token")
print(f"KV per token fp8       : {per_tok_bf16//2} B = {per_tok_bf16/2048:.0f} KiB/token")
print(f"KV for 131072 ctx bf16 : {per_tok_bf16*131072/2**30:.1f} GiB")
print(f"KV for 262144 ctx bf16 : {per_tok_bf16*262144/2**30:.1f} GiB")
# GDN recurrent state, per sequence, contiguous
vh, vd = t["linear_num_value_heads"], t["linear_value_head_dim"]
kh, kd = t["linear_num_key_heads"], t["linear_key_head_dim"]
ssm_b = 4 if t.get("mamba_ssm_dtype") == "float32" else 2
rec = lin * vh * kd * vd * ssm_b
print(f"GDN recurrent state    : {rec/2**20:.0f} MiB per sequence "
      f"({lin} layers x {vh} vheads x {kd} x {vd} x {ssm_b}B)")
print()
print("R1 CHECK: a startup 'GPU KV cache size' near ~170k tokens at bf16 would")
print("mean the geometry assumption is wrong -> STOP the cutover (runbook 6).")
PY
  echo "kv math      -> candidate-kv-math.txt"; cat "$OUT/candidate-kv-math.txt"
else
  echo "WARN: candidate config.json not found in the live hf cache"
fi

# ── 5. Live sources for the four diverging services (never-deploy list) ──
section "5. never-deploy-list live sources"
mkdir -p "$OUT/live-sources"
for svc in hav-metrics-sidecar hav-vision-sidecar hav-intelligence hav-video-labeler; do
  short=${svc#hav-}
  if docker inspect "$svc" >/dev/null 2>&1; then
    # /app is the convention for every python service in this stack.
    docker exec "$svc" sh -c 'ls -la /app 2>/dev/null' > "$OUT/live-sources/$short.listing.txt" 2>&1
    docker cp "$svc:/app" "$OUT/live-sources/$short" >/dev/null 2>&1 \
      && echo "$short -> live-sources/$short/" \
      || echo "$short: docker cp failed (inspect listing only)"
    # Strip caches and any secret material that rode along.
    rm -rf "$OUT/live-sources/$short/__pycache__" \
           "$OUT/live-sources/$short"/*/__pycache__ 2>/dev/null
    docker inspect "$svc" --format '{{json .Config.Env}}' 2>/dev/null \
      | tr ',' '\n' | redact > "$OUT/live-sources/$short.env.redacted" 2>&1
  else
    echo "$short: container not present"
  fi
done

# The EOC custom component is the fifth diverging surface and the only one
# that does not live in a container: it runs inside HA Core. The repo copy
# under ha-config/ is the zero-tool containment refactor
# (MODEL_TOOL_CATALOG_ENABLED=False, pinned by test_action_containment.py);
# the live copy has no such constant and runs the full tool catalog.
# Deploying the repo copy would zero out every tool. Archive live instead.
mkdir -p "$OUT/live-sources/eoc-component"
if $HA_SSH 'tar cf - -C /config/custom_components extended_openai_conversation --exclude=__pycache__' 2>/dev/null \
     | tar xf - -C "$OUT/live-sources/eoc-component" 2>/dev/null; then
  n=$(find "$OUT/live-sources/eoc-component" -name '*.py' | wc -l)
  echo "eoc-component -> live-sources/eoc-component/ ($n python files)"
  {
    echo "## containment-flag divergence (repo vs live)"
    echo "repo (ha-config/, NEVER DEPLOY):"
    grep -rn "MODEL_TOOL_CATALOG_ENABLED" "$REPO_ROOT/ha-config/extended_openai_conversation/const.py" 2>/dev/null
    echo "live:"
    grep -rn "MODEL_TOOL_CATALOG_ENABLED" "$OUT/live-sources/eoc-component" 2>/dev/null \
      || echo "  (absent — the live component predates the containment refactor and runs the full catalog)"
  } > "$OUT/live-sources/eoc-containment-divergence.txt" 2>&1
else
  echo "eoc-component: ssh/tar failed — component NOT archived"
fi

# ── 6. Consumer-timeout inventory vs D1 ──────────────────────────────────
section "6. consumer-timeout inventory (vs D1 budget)"
TIMEOUT_RE='(TIMEOUT|_TTL|TTL_SEC|within_ms|WITHIN_MS|processingGuard|GUARD_MS|MAX_FRAMES|context_threshold|KEEP_ALIVE|MAX_TOKENS|POLL_|RETRY|BACKOFF)'
{
  echo "# Every timeout a slower model can trip. D1: ambient p95 <= 1.5s,"
  echo "# voice e2e p95 <= 4s (TTFT <= 1.2s), 4-frame clip <= 6s."
  echo "# The room-binding TTL is the dangerous one: expiry under a slower"
  echo "# turn is a WRONG-ROOM ACTUATION path, not just a slow answer."
  echo
  echo "## container environment (live)"
  for svc in hav-metrics-sidecar hav-vision-sidecar hav-intelligence hav-video-labeler; do
    echo "### $svc"
    docker inspect "$svc" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
      | grep -iE "$TIMEOUT_RE" | redact | sort
  done
  echo
  echo "## constants in the ARCHIVED LIVE SOURCES (repo copies diverge)"
  grep -rnE "$TIMEOUT_RE[A-Za-z_]*\s*[:=]\s*[0-9]+" "$OUT/live-sources" \
    --include=*.py --include=*.yaml --include=*.yml 2>/dev/null \
    | sed "s#$OUT/live-sources/##" | sort | head -60
  echo
  echo "## app-side guards (repo app/ is the deployed surface for these)"
  grep -rnE "processingGuardMs|_TTL_SEC|PERCEPTION_AUTO_TIMEOUT_S|REFRESH_PERCEPTION_TIMEOUT_S|within_ms" \
    "$REPO_ROOT/app/src" "$REPO_ROOT/stack" 2>/dev/null | head -30
} > "$OUT/consumer-timeouts.txt" 2>&1
echo "timeouts     -> consumer-timeouts.txt"
grep -cE "=[0-9]+|: *[0-9]+" "$OUT/consumer-timeouts.txt" | sed 's/^/  numeric settings captured: /'

# ── 7. Prompt-surface freeze ─────────────────────────────────────────────
section "7. prompt-surface freeze"
{
  echo "# Frozen at Phase 0. NO prompt-surface edits during the soak."
  echo "# Phase 2 translates these; each edit needs a golden-transcript replay."
  echo
  for f in "$OUT/live-sources"/*/app.py "$OUT/live-sources"/*/main.py \
           "$OUT/live-sources"/*/*/prompt*.py; do
    [ -f "$f" ] || continue
    echo "### $f"
    grep -nE "SYSTEM|PROMPT|TMPL|TEMPLATE|_MSG" "$f" | head -40
    echo
  done
} > "$OUT/prompt-surfaces.txt" 2>&1
sha256sum "$OUT/live-sources"/*/app.py "$OUT/live-sources"/*/main.py 2>/dev/null \
  > "$OUT/prompt-surface-hashes.txt"
echo "prompts      -> prompt-surfaces.txt + prompt-surface-hashes.txt"

# ── 8. Frigate record state + face-rec overrides (roadmap gate V1) ───────
section "8. Frigate record state (roadmap V1) + face recognition"
{
  echo "# V1 asks: is Frigate recording on? The repo example says"
  echo "# record.enabled:false, but ~36 GB in /media/frigate suggested"
  echo "# otherwise. Disk usage alone does NOT answer it — resolve it by"
  echo "# asking which subdirectory holds the bytes AND what file type they"
  echo "# are. recordings/ + .mp4 = continuous recording; clips/ + .jpg ="
  echo "# event snapshots, which are NOT clips and carry no video."
  echo "#"
  echo "# This distinction gates roadmap A1: POST /describe_event validates"
  echo "# has_clip and fetches clip.mp4. Frigate builds event clips FROM"
  echo "# recording segments, so with record disabled there is no clip.mp4"
  echo "# to fetch and has_clip is false for every event."
  echo
  $HA_SSH '
    echo "== du /media/frigate =="
    du -sh /media/frigate/recordings /media/frigate/clips 2>/dev/null
    echo
    echo "== file types under clips/ (the decisive evidence) =="
    printf "mp4 files: "; find /media/frigate/clips -type f -name "*.mp4" 2>/dev/null | wc -l
    printf "jpg files: "; find /media/frigate/clips -type f -name "*.jpg" 2>/dev/null | wc -l
    printf "webp files: "; find /media/frigate/clips -type f -name "*.webp" 2>/dev/null | wc -l
    echo
    echo "== cameras =="
    sed -n "/^cameras:/,/^[a-z]/p" /addon_configs/ccab4aaf_frigate/config.yaml | grep -E "^  [a-z_]+:"
    echo
    echo "== per-camera record/snapshots state =="
    grep -nE "^\s*(record|snapshots|detect):" -A 3 /addon_configs/ccab4aaf_frigate/config.yaml 2>/dev/null
    echo
    echo "== global record: block =="
    grep -nE "^record:" -A 12 /addon_configs/ccab4aaf_frigate/config.yaml 2>/dev/null || echo "(none — record is set per camera)"
    echo
    echo "== face_recognition (roadmap D3 / example-config inversion) =="
    n=$(grep -c "face" /addon_configs/ccab4aaf_frigate/config.yaml 2>/dev/null)
    echo "lines mentioning face: $n"
    grep -n "face" /addon_configs/ccab4aaf_frigate/config.yaml 2>/dev/null || echo "(face recognition is NOT configured live at all)"
    echo
    echo "== disk headroom for a D8 recording decision =="
    df -h /media 2>/dev/null
  ' 2>&1
} > "$OUT/frigate-record-state.txt" 2>&1
echo "frigate      -> frigate-record-state.txt"
grep -E "mp4 files|jpg files|enabled: false|lines mentioning face" "$OUT/frigate-record-state.txt" | head -8

# ── 8b. EOC live subentry + tool-spec audit ──────────────────────────────
section "8b. EOC subentry (storage is truth) + tool-spec audit"
mkdir -p "$OUT/eoc"
TMP_ENTRIES="$OUT/eoc/.core.config_entries.json"
if $HA_SSH 'cat /config/.storage/core.config_entries' > "$TMP_ENTRIES" 2>/dev/null \
   && [ -s "$TMP_ENTRIES" ]; then
  python3 - "$OUT" "$TMP_ENTRIES" <<'PY'
import hashlib, json, os, sys
out, src = sys.argv[1], sys.argv[2]
entries = json.load(open(src))["data"]["entries"]
eoc = next((e for e in entries if e["domain"] == "extended_openai_conversation"), None)
if not eoc:
    print("WARN: no extended_openai_conversation entry found"); raise SystemExit(0)
conv = next(s for s in eoc["subentries"] if s["subentry_type"] == "conversation")
d = conv["data"]
prompt, funcs = d.get("prompt", ""), d.get("functions", "")
open(f"{out}/eoc/prompt.live.txt", "w").write(prompt)
open(f"{out}/eoc/functions.live.yaml", "w").write(
    funcs if isinstance(funcs, str) else json.dumps(funcs, indent=2))
L = [
    "# EOC live subentry — HA storage is truth; there is no file copy, and the",
    "# repo component is containment-refactored (never deploy it).",
    f"entry title      : {eoc.get('title')}",
    f"base_url         : {eoc['data'].get('base_url')}",
    f"chat_model       : {d.get('chat_model')}   (served-name freeze)",
    f"context_threshold: {d.get('context_threshold')}   <-- repo says 40000; LIVE IS TRUTH",
    f"max_tokens       : {d.get('max_tokens')}",
    f"max_function_calls_per_conversation: {d.get('max_function_calls_per_conversation')}",
    f"context_truncate_strategy: {d.get('context_truncate_strategy')}",
    "",
    f"prompt chars     : {len(prompt)}",
    f"prompt sha256    : {hashlib.sha256(prompt.encode()).hexdigest()}",
    f"functions chars  : {len(str(funcs))}",
    f"functions sha256 : {hashlib.sha256(str(funcs).encode()).hexdigest()}",
    "",
    "# PROMPT-SURFACE FREEZE: these hashes pin the surface for the soak.",
    "# No prompt-surface edits during the soak (migration doc, Soak section).",
]
open(f"{out}/eoc/subentry-summary.txt", "w").write("\n".join(L) + "\n")
print("\n".join(L))
PY
  rm -f "$TMP_ENTRIES"   # holds the API key; the extracted files do not
  if [ -s "$OUT/eoc/functions.live.yaml" ]; then
    python3 "$REPO_ROOT/tools/qwen38-toolspec-audit.py" \
      "$OUT/eoc/functions.live.yaml" > "$OUT/eoc/toolspec-audit.txt" 2>&1
    python3 "$REPO_ROOT/tools/qwen38-toolspec-audit.py" --json \
      "$OUT/eoc/functions.live.yaml" > "$OUT/eoc/toolspec-audit.json" 2>&1
    echo "toolspec     -> eoc/toolspec-audit.txt"
    grep -E "^tools audited|^hazards|^advisories|ZERO-ARG" "$OUT/eoc/toolspec-audit.txt"
  fi
else
  echo "WARN: could not read HA storage over ssh — EOC dump skipped"
  rm -f "$TMP_ENTRIES"
fi

# ── 9. ntfy topology (gates the soak alarm + roadmap E0/E1 payloads) ─────
section "9. ntfy topology"
{
  echo "# The soak alarm rule and roadmap E0/E1 both say: images and names"
  echo "# are allowed ONLY if ntfy is self-hosted / LAN-only."
  echo
  echo "## endpoints referenced on this host"
  grep -rhoiE "https?://[a-z0-9.:_/-]*ntfy[a-z0-9.:_/-]*" /usr/local/sbin/ 2>/dev/null | sort -u
  echo
  echo "## local ntfy container?"
  docker ps -a --format '{{.Names}} {{.Image}}' | grep -i ntfy || echo "NONE — no self-hosted ntfy on this host"
  echo
  echo "## alert senders"
  grep -rln "ntfy.sh" /usr/local/sbin/ 2>/dev/null
  echo
  echo "## payload shape (does any sender attach an image?)"
  grep -rn "Attach\|Filename\|-T \|--upload-file" /usr/local/sbin/ntfy-alert /usr/local/sbin/ntfy-alert-reset /usr/local/sbin/smartd-ntfy 2>/dev/null \
    || echo "no image/attachment headers in any current sender (text only)"
} > "$OUT/ntfy-topology.txt" 2>&1
echo "ntfy         -> ntfy-topology.txt"
grep -E "NONE|ntfy.sh" "$OUT/ntfy-topology.txt" | head -4

# ── 10. Maintenance-flag semantics + admission evidence ──────────────────
section "10. /run/ha-maintenance semantics + admission evidence"
{
  echo "## flag present right now?"
  ls -la /run/ha-maintenance 2>&1 || echo "absent (no maintenance in progress)"
  echo
  echo "## who honours it"
  grep -rn "ha-maintenance" /usr/local/sbin/ 2>/dev/null
  echo
  echo "## semantics (from ha-reachable)"
  grep -n "ha-maintenance" -B 3 -A 2 /usr/local/sbin/ha-reachable 2>/dev/null
} > "$OUT/maintenance-flag-semantics.txt" 2>&1
echo "flag         -> maintenance-flag-semantics.txt"

{
  echo "## 5-step maintenance admission check"
  echo "1. systemctl is-system-running: $(systemctl is-system-running 2>&1)"
  echo
  echo "2. containers with health checks:"
  docker ps --format '{{.Names}}\t{{.Status}}' | sort
  echo
  echo "3. headroom + temperatures:"
  df -h / | tail -1
  nvidia-smi --query-gpu=temperature.gpu,memory.used,memory.total --format=csv,noheader
  sensors 2>/dev/null | grep -iE "composite|tctl" | head -4
  echo
  echo "4. journal hardware-error scan (current boot):"
  journalctl -b --no-pager 2>/dev/null | grep -icE \
    "machine check|hardware error|soft lockup|hard lockup|kernel panic|oom-kill|segfault" \
    | sed 's/^/   matches: /'
  echo
  echo "5. failed units:"
  systemctl --failed --no-pager 2>&1 | head -5
} > "$OUT/admission-check.txt" 2>&1
echo "admission    -> admission-check.txt"

# ── 11. Qualification + baseline artefact freeze ─────────────────────────
section "11. qualification + baseline freeze"
mkdir -p "$OUT/qualification" "$OUT/baselines"
cp -a /srv/data/eval/qualification/. "$OUT/qualification/" 2>/dev/null \
  && echo "qualification-> qualification/"
cp -a /srv/data/eval/baselines/. "$OUT/baselines/" 2>/dev/null \
  && echo "baselines    -> baselines/"
sha256sum "$OUT/qualification"/* "$OUT/baselines"/* 2>/dev/null \
  > "$OUT/artifact-hashes.txt"

# ── 12. Clock-skew trap ──────────────────────────────────────────────────
section "12. clock/timezone reconciliation"
{
  echo "# TRAP: container logs and the host journal do NOT share a timezone."
  echo "host date:            $(date -Is)  ($(date +%Z))"
  echo "vllm container date:  $(docker exec hav-vllm date -Is 2>/dev/null)"
  echo "journal sample:       $(journalctl -n 1 --no-pager -o short 2>/dev/null | tail -1 | cut -c1-15)"
  echo
  echo "Any latency evidence correlating vLLM logs with the host journal or"
  echo "the baseline CSV must convert. Every latency number in this migration"
  echo "states its measurement point AND cache state."
} > "$OUT/clock-skew.txt" 2>&1
echo "clock        -> clock-skew.txt"; cat "$OUT/clock-skew.txt"

# ── 13. Manifest ─────────────────────────────────────────────────────────
section "13. manifest"
( cd "$OUT" && find . -type f -printf '%10s  %p\n' | sort -k2 ) > "$OUT/MANIFEST.txt"
echo "manifest     -> MANIFEST.txt  ($(wc -l < "$OUT/MANIFEST.txt") files)"
echo
echo "=== Phase 0 archive complete: $OUT ==="
