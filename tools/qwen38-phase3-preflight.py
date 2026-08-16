#!/usr/bin/env python3
"""qwen38-phase3-preflight.py — go/no-go for a Phase-3 candidate session.

Checks every precondition in `docs/QWEN38-PHASE3-SESSION-1-RUNBOOK.md`
mechanically, so the decision to start is evidence and not recollection.
Read-only: it inspects, it never changes anything.

The stability review's rule is **abort without retrying** on any failed
admission check, so this exits non-zero and says which check failed rather
than printing warnings that are easy to scroll past at midnight.

Exit: 0 all clear · 1 a BLOCKING check failed · 2 warnings only.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import urllib.request

PHASE0 = pathlib.Path("/srv/data/eval/migration/phase0")
COMPOSE = pathlib.Path("/opt/home-ai-voice/docker-compose.yml")
INCUMBENT = "Qwen/Qwen3-VL-30B-A3B-Instruct-FP8"
CANDIDATE = "Qwen/Qwen3.8-27B-FP8"

BLOCKING: list[str] = []
WARNINGS: list[str] = []


def sh(cmd: str, timeout: int = 30) -> str:
    try:
        return subprocess.run(["bash", "-lc", cmd], capture_output=True,
                              timeout=timeout).stdout.decode(errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def check(name: str, ok: bool, detail: str = "", blocking: bool = True) -> bool:
    if ok:
        print(f"  \033[32mPASS\033[0m  {name}" + (f"  — {detail}" if detail else ""))
    else:
        tag = "\033[31mBLOCK\033[0m" if blocking else "\033[33mWARN\033[0m"
        print(f"  {tag}  {name}" + (f"  — {detail}" if detail else ""))
        (BLOCKING if blocking else WARNINGS).append(name)
    return ok


print("=== Phase-3 session preflight ===\n")

print("1. host admission check")
state = sh("systemctl is-system-running").strip()
check("systemctl is-system-running", state == "running", state or "unknown")

ps = sh("docker ps --filter 'name=hav-' --format '{{.Names}}\t{{.Status}}'").strip()
rows = [r for r in ps.splitlines() if r.strip()]
unhealthy = [r for r in rows if not r.split("\t")[1].startswith("Up")]
check(f"all hav-* containers up ({len(rows)} found)", rows and not unhealthy,
      "; ".join(unhealthy) if unhealthy else f"{len(rows)} up")

failed = sh("systemctl --failed --no-pager --plain --no-legend").strip()
check("no failed systemd units", not failed, failed[:120] or "none")

df = sh("df --output=avail -BG / | tail -1").strip().rstrip("G")
try:
    avail = int(df)
except ValueError:
    avail = 0
check("root filesystem headroom", avail >= 100, f"{avail} GiB free")

# KERNEL ring only (-k), and drop audit lines that merely quote a command.
# Scanning the full journal matches sudo's own audit record of an earlier
# `journalctl --grep='...|segfault'`, i.e. the search terms match the log
# of the search. That produced 7 phantom "hardware errors" on a healthy
# host the first time this ran. A preflight that cries wolf gets ignored,
# and then it is not there on the night it matters.
errs = sh("journalctl -k -b --no-pager 2>/dev/null "
          "| grep -vE 'COMMAND=|--grep=' "
          "| grep -icE 'machine check|hardware error|soft lockup|hard lockup"
          "|kernel panic|oom-kill|segfault|Uncorrected'").strip()
check("no kernel hardware/OOM/lockup errors this boot", errs in ("0", ""),
      f"{errs} kernel-ring matches")

gpu = sh("nvidia-smi --query-gpu=memory.used,memory.total,temperature.gpu "
         "--format=csv,noheader").strip()
try:
    used, total = (int(x.split()[0]) for x in gpu.split(",")[:2])
    temp = int(gpu.split(",")[2])
except Exception:  # noqa: BLE001
    used = total = temp = 0
check("GPU temperature sane", 0 < temp < 80, f"{temp} C")

print("\n2. engine pin and weights")
compose = COMPOSE.read_text(errors="replace") if COMPOSE.exists() else ""
check("live compose readable", bool(compose), str(COMPOSE))

digest_file = PHASE0 / "vllm-image-digest.txt"
archived = digest_file.read_text(errors="replace") if digest_file.exists() else ""
running_img = sh("docker inspect hav-vllm --format '{{.Config.Image}}'").strip()
check("engine image is digest-pinned", "@sha256:" in running_img,
      running_img[:70] or "unknown")
if archived and running_img:
    tail = running_img.split("@")[-1][:24]
    check("running image matches the Phase-0 archived digest",
          tail and tail in archived, tail)
else:
    check("Phase-0 digest archive present", False,
          "run tools/qwen38-phase0-archive.sh", blocking=False)

weights = sh("docker exec hav-vllm sh -c "
             "'du -sh /root/.cache/huggingface/hub/* 2>/dev/null'")
check("INCUMBENT weights cached (rollback needs them)",
      "Qwen3-VL-30B-A3B-Instruct-FP8" in weights, "hf cache")
check("CANDIDATE weights cached (no download mid-session)",
      "Qwen3.8-27B-FP8" in weights, "hf cache")

cfg = PHASE0 / "candidate-kv-math.txt"
if cfg.exists():
    txt = cfg.read_text(errors="replace")
    check("candidate KV geometry archived (64 KiB/token)",
          "64 KiB/token" in txt, "expect >=300k token pool at startup")
else:
    check("candidate KV geometry archived", False,
          "run the Phase-0 collector", blocking=False)

served = ""
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/v1/models", timeout=10) as r:
        served = (json.load(r).get("data") or [{}])[0].get("id", "")
except Exception:  # noqa: BLE001
    pass
check("served model name is the frozen 'qwen3-vl-30b'",
      served == "qwen3-vl-30b", served or "unreachable")
check("engine currently serving the INCUMBENT",
      INCUMBENT in compose, "compose --model line")

print("\n3. VRAM headroom")
other = max(0, used - int(0.70 * total)) if total else 0
check("non-vLLM GPU tenants leave room for a reload",
      total and (total - used) > 8000,
      f"{used}/{total} MiB used, {total - used} MiB free")

print("\n4. quiet-house checks")
desc = sh("docker logs --since 5m hav-vision-sidecar 2>&1 | "
          "grep -c 'POST /describe'").strip() or "0"
check("ambient /describe traffic is quiet", int(desc or 0) <= 5,
      f"{desc} in the last 5 min — stop hav-personaplex-bridge and "
      "the HA-side driver", blocking=False)

flag = pathlib.Path("/run/ha-maintenance")
check("maintenance flag not already set", not flag.exists(),
      "a stale flag means paging is suppressed and nobody knows",
      blocking=False)

print("\n5. harnesses and baselines")
here = pathlib.Path(__file__).resolve().parent
for tool in ("qwen38-matrix-driver.py", "qwen38_capture.py", "qwen38_gates.py",
             "gate-g4-negatives.py", "gate-g5-leak-grep.py"):
    check(f"tool present: {tool}", (here / tool).exists())

base = pathlib.Path("/srv/data/eval/migration/matrix-incumbent-baseline/session.json")
if base.exists():
    b = json.loads(base.read_text())
    valid = (b.get("drift") or {}).get("valid")
    check("incumbent baseline exists and is a valid session", bool(valid),
          f"drift {(b.get('drift') or {}).get('drift', 0) * 100:.1f}%")
else:
    check("incumbent baseline exists", False,
          "the paired G6-c gate cannot be scored without it")

g4 = pathlib.Path("/srv/data/eval/migration/g4-corpus/manifest.json")
if g4.exists():
    m = json.loads(g4.read_text())
    ok = sum(1 for f in m["frames"] if f.get("verified") is True)
    check("G4 corpus verified by a human", ok >= 25,
          f"{ok}/{len(m['frames'])} confirmed", blocking=False)
else:
    check("G4 corpus built", False, "gate-g4-negatives.py build", blocking=False)

print("\n" + "=" * 60)
if BLOCKING:
    print(f"\033[31mNO-GO\033[0m — {len(BLOCKING)} blocking check(s) failed:")
    for b in BLOCKING:
        print(f"  - {b}")
    print("\nThe stability review's rule is abort-without-retry. Reschedule "
          "the session; do not fix-and-continue.")
    sys.exit(1)
if WARNINGS:
    print(f"\033[33mGO WITH CARE\033[0m — {len(WARNINGS)} warning(s):")
    for w in WARNINGS:
        print(f"  - {w}")
    print("\nNone are blocking, but each is something you should have "
          "decided about deliberately rather than discovered mid-session.")
    sys.exit(2)
print("\033[32mGO\033[0m — every precondition met. Proceed to runbook step 3.")
sys.exit(0)
