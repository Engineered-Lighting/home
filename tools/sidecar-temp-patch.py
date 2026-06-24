#!/usr/bin/env python3
"""One-shot patch: add gpu_temp_c to metrics-sidecar /metrics response.

Idempotent — re-running on an already-patched file prints ALREADY_PATCHED
and exits 0. Insertion points are anchored on existing lines, so we fail
loudly if the file shape changed from what we expect.
"""
import re
import sys

PATH = "/opt/home-ai-voice/services/metrics-sidecar/main.py"

with open(PATH) as f:
    src = f.read()

if "gpu_temp_c" in src:
    print("ALREADY_PATCHED")
    sys.exit(0)

# Anchor 1: insert temp read after `vm = psutil.virtual_memory()`
m1 = re.search(r"(\n)(\s+)vm = psutil\.virtual_memory\(\)", src)
if not m1:
    print("ERROR: anchor 'vm = psutil.virtual_memory()' not found")
    sys.exit(1)
indent = m1.group(2)
temp_block = (
    "\n" + indent + "try:"
    + "\n" + indent + "    gpu_temp_c = int(pynvml.nvmlDeviceGetTemperature(_gpu, pynvml.NVML_TEMPERATURE_GPU))"
    + "\n" + indent + "except Exception:"
    + "\n" + indent + "    gpu_temp_c = None"
)
src2 = src[: m1.end()] + temp_block + src[m1.end():]

# Anchor 2: insert payload key after gpu_util_pct line
m2 = re.search(
    r'(\s+)"gpu_util_pct": int\(util\.gpu\),  # NVML already returns an int %',
    src2,
)
if not m2:
    print("ERROR: anchor 'gpu_util_pct' not found")
    sys.exit(1)
src3 = (
    src2[: m2.end()]
    + m2.group(1)
    + '"gpu_temp_c": gpu_temp_c,  # int °C from NVML, None if read fails'
    + src2[m2.end():]
)

with open(PATH, "w") as f:
    f.write(src3)

print("PATCHED_OK")
