#!/usr/bin/env bash
# Pinned, resumable download of Qwen/Qwen3.6-35B-A3B-FP8 into the hf_cache
# volume hav-vllm already mounts (docs/VLLM-QWEN36-SWAP.md). Production keeps
# serving; this uses network and disk only, in a capped throwaway container on
# the pinned vLLM image (no new image is pulled).
#
#   Get-Content stack\vllm\qwen36-download.sh -Raw | ssh hav-ubuntu "tr -d '\r' | bash -s -- start"
#   ... -- status     progress and log tail
#   ... -- verify     sha256 of every LFS blob against its content address
#   ... -- cleanup    remove the finished download container
# Re-running "start" after an interruption resumes: hf skips complete files.
# Rollback: delete models--Qwen--Qwen3.6-35B-A3B-FP8 from the volume (see doc).
set -eu
REPO=Qwen/Qwen3.6-35B-A3B-FP8
REV=95a723d08a9490559dae23d0cff1d9466213d989
NAME=qwen36-download
EXPECTED_FILES=56
NEED_GB=60
IMAGE=$(docker inspect -f '{{.Config.Image}}' hav-vllm)
VOL=$(docker inspect hav-vllm -f '{{range .Mounts}}{{if eq .Destination "/root/.cache/huggingface"}}{{.Name}}{{end}}{{end}}')
[ -n "$VOL" ] || { echo "hav-vllm has no hf_cache volume mount"; exit 1; }
DIR=hub/models--${REPO/\//--}
echo "image=$IMAGE volume=$VOL"

case "${1:-status}" in
start)
  root=$(docker info -f '{{.DockerRootDir}}')
  avail=$(df -BG --output=avail "$root" | tail -n 1 | tr -dc 0-9)
  echo "free on $root: ${avail}G (need ${NEED_GB}G)"
  [ "$avail" -ge "$NEED_GB" ] || { echo "not enough disk; stopping"; exit 1; }
  docker rm "$NAME" >/dev/null 2>&1 || true
  # hf's default of 8 parallel files outgrew a 4g cap and was OOM-killed
  # (exit 137) on 2026-10-20; 2 workers under 8g stays bounded.
  docker run -d --name "$NAME" --cpus 2 --memory 8g --pids-limit 256 \
    --security-opt no-new-privileges:true \
    -v "$VOL":/root/.cache/huggingface -e HF_HUB_DISABLE_TELEMETRY=1 \
    --entrypoint hf "$IMAGE" download "$REPO" --revision "$REV" --max-workers 2
  echo "started; check with: status"
  ;;
status)
  docker ps -a --filter "name=^${NAME}$" --format '{{.Names}} {{.Status}}'
  docker inspect -f 'oom_killed={{.State.OOMKilled}} exit={{.State.ExitCode}}' "$NAME" 2>/dev/null || true
  docker logs --tail 5 "$NAME" 2>&1 | tr '\r' '\n' | tail -n 5 || true
  docker run --rm --network none -v "$VOL":/hf:ro --entrypoint sh "$IMAGE" -c \
    "du -sh /hf/$DIR 2>/dev/null; ls /hf/$DIR/snapshots/$REV 2>/dev/null | wc -l | sed 's/^/snapshot files: /'; ls /hf/$DIR/blobs 2>/dev/null | grep -c '\.incomplete$' | sed 's/^/incomplete: /'"
  echo "expected snapshot files: $EXPECTED_FILES"
  ;;
verify)
  docker run --rm --network none --cpus 1 --memory 1g -v "$VOL":/hf:ro --entrypoint sh "$IMAGE" -c "
    set -e; cd /hf/$DIR
    n=\$(ls snapshots/$REV | wc -l); echo \"snapshot files: \$n (expected $EXPECTED_FILES)\"
    [ \"\$n\" -eq $EXPECTED_FILES ] || { echo VERIFY FAILED: file count; exit 1; }
    ! ls blobs | grep -q '\.incomplete\$' || { echo VERIFY FAILED: incomplete blobs; exit 1; }
    cd blobs
    for f in *; do [ \${#f} -eq 64 ] || continue; echo \"\$f  \$f\"; checked=\$((checked+1)); done > /tmp/sums
    echo \"LFS blobs: \$(wc -l < /tmp/sums)\"
    nice -n 19 sha256sum -c --quiet /tmp/sums && echo VERIFY OK"
  ;;
cleanup)
  docker rm "$NAME"
  ;;
*) echo "usage: start|status|verify|cleanup"; exit 2 ;;
esac
