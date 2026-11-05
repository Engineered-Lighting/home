"""R0corr verifier probe for finding code-fingerprint-misses-den-core-parsers.

What it checks: exposure.json's tool.code_sha256 hashes capture.py alone (capture.py:523), but capture.py's counts
depend on den-core's sources.py (ulid, recorder_commands, ...). The probe copies the tool, den-core and den_lighting
twice into a temp dir, writes one synthetic export with the unedited copy, edits only den-core/sources.py in the
second copy (ulid() returns lower-case text: a change to
how context ids are parsed), runs the same synthetic export through both, and compares tool.code_sha256, the input
sha256s and the people count. Scenario: den_lighting's own bulb row carries its command context, but its
den_lighting_command event fires 3 s later (a slow blocking light call), outside the 2 s timing fallback, so only the
context match says the hold den_lighting opened on its own report is not a person.

How to run (stdlib only, synthetic data, temp dirs only, no host contacted):
    python3 -B verify_code-fingerprint-misses-den-core-parsers.py /path/to/branch/checkout
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCENARIO = r'''
import json, sys, tempfile
import capture, sources
from test_capture import Den, T0, L0, WARM
den = Den()
ctx = den.bulb(T0 + 100, L0, True)                     # den_lighting's own row, its command context
den.commands.append({"time_fired_ts": T0 + 103, "event_data": {"entity_id": L0, "revision": den.revision,
                     "target": WARM, "context_id": sources.ulid(ctx)}})   # the event, 3 s later
den.hold(T0 + 100.05, {L0: WARM})                      # den_lighting took its own report for a touch
den.row(T0 + 3600)
folder = sys.argv[1]
if sys.argv[2] == "make":
    den.write(folder)
    raise SystemExit(0)
rec, _ = capture.capture(*[[folder + "/" + n + ".jsonl"] for n in ("status", "lights", "commands")])
print(json.dumps({"code_sha256": rec["tool"]["code_sha256"], "inputs": [i["sha256"] for i in rec["inputs"]],
                  "people": rec["planning"]["strata"]["no_game"]["people"]["count"],
                  "not_counted": [c["reason"] for c in rec["not_counted"]]}))
'''

OLD = 'return "".join(CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))'
NEW = 'return "".join(CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26))).lower()'


def run(src, dst, patch, data):
    for rel in ("tools/correction-capture", "tools/den-core", "ha-config/custom_components/den_lighting"):
        shutil.copytree(src / rel, dst / rel, ignore=shutil.ignore_patterns("__pycache__"))
    if patch:
        path = dst / "tools/den-core/sources.py"
        text = path.read_text("utf-8")
        assert text.count(OLD) == 1
        path.write_text(text.replace(OLD, NEW), "utf-8")
    if not data.exists():  # the export is written once, by the unedited copy, and read by both
        data.mkdir()
        subprocess.run([sys.executable, "-B", "-c", SCENARIO, str(data), "make"], cwd=dst / "tools/correction-capture",
                       check=True, timeout=120)
    proc = subprocess.run([sys.executable, "-B", "-c", SCENARIO, str(data), "count"],
                          cwd=dst / "tools/correction-capture", capture_output=True, text=True, timeout=120)
    if proc.returncode:
        raise SystemExit("FAIL: scenario error: %s" % proc.stderr.strip().splitlines()[-1])
    return json.loads(proc.stdout)


def main():
    src = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="r0corr-verify-") as tmp:
        data = Path(tmp) / "data"
        a = run(src, Path(tmp) / "orig", False, data)
        b = run(src, Path(tmp) / "den-core-edited", True, data)
    print("RESULT original:        code_sha256 %s people %d not_counted %s" % (a["code_sha256"][:12], a["people"],
                                                                                a["not_counted"]))
    print("RESULT den-core edited: code_sha256 %s people %d not_counted %s" % (b["code_sha256"][:12], b["people"],
                                                                                b["not_counted"]))
    same_id = a["code_sha256"] == b["code_sha256"] and a["inputs"] == b["inputs"]
    print("%s: same code_sha256 and input sha256s, %s people count" % (
        "FAIL" if same_id and a["people"] != b["people"] else "PASS",
        "different" if a["people"] != b["people"] else "same"))


if __name__ == "__main__":
    main()
