"""probe_decide_look_sticks: in a facts-only replay decide's reading look stays requested after reading ends.

The README's path for reading-shadow folders and copied fact logs without a journal is
`--facts FOLDER --assume-occupied`. The incumbent then never observes a bulb, mode.normal stays empty, and
decide's base rule has nothing to request, so decide emits no decision when reading ends: its last Decision
(the placeholder look on all four bulbs) stays the requested scene through the phone block between the two
reading blocks and to the end of the evening. validate.py's check 3 still says OK.

The probe writes the synthetic evening's fact-log day files with run.py --synthetic, replays only those files
with --facts ... --assume-occupied, and reports how long decide's latest request is the look while need's
latest person state is not reading. Offline, temp dirs only.

    python3 probe_decide_look_sticks.py /path/to/den-core-checkout
"""
import json
import subprocess
import sys
sys.dont_write_bytecode = True  # never leave __pycache__ in the checkout under test
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-core"
sys.path.insert(0, str(tool))
import decide  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="probe-look-"))
syn = tmp / "syn"
r = subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--synthetic", "evening", "--clean",
                    "--policies", "incumbent", "--out", str(syn)], cwd=str(root), capture_output=True, text=True)
assert r.returncode == 0, r.stderr
facts = tmp / "facts"
facts.mkdir()
for p in syn.glob("*-fact-log-1-*.jsonl"):
    (facts / p.name).write_bytes(p.read_bytes())
out = tmp / "facts-only"
r = subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--facts", str(facts), "--assume-occupied",
                    "--policies", "need", "incumbent", "decide", "--out", str(out)], cwd=str(root),
                   capture_output=True, text=True)
assert r.returncode == 0, r.stderr
log = [json.loads(line) for line in (out / "log.jsonl").read_text("ascii").splitlines()[1:]]
first, end = log[0]["t"], log[-1]["t"]
look = None
reading = False
stuck = 0.0
prev_t = first
for e in log:
    t = e["t"]
    if look and not reading:
        stuck += t - prev_t
    prev_t = t
    if e["kind"] == "person_state" and e["source"] == "need":
        reading = decide.reading(e["data"])
    elif e["kind"] == "decision" and e["source"] == "decide":
        look = e["data"]["inputs"].get("look") and "reading_look" in e["data"]["why"]
dec = [e for e in log if e["kind"] == "decision" and e["source"] == "decide"]
print("RESULT: decide decisions: %s" % ["%+.0f s %s" % (e["t"] - first, e["data"]["why"].split(":")[0]) for e in dec])
print("RESULT: last decide decision at %+.0f s is '%s'; log ends at %+.0f s" % (
    dec[-1]["t"] - first, dec[-1]["data"]["why"].split(":")[0], end - first))
print("RESULT: seconds with the look requested while need is not reading: %.0f" % stuck)
v = subprocess.run([sys.executable, "-B", str(tool / "validate.py"), str(out / "log.jsonl")], cwd=str(root),
                   capture_output=True, text=True)
print("RESULT: validate.py exit %d\n  %s" % (v.returncode, v.stdout.strip().replace("\n", "\n  ")))
if stuck > 60 and v.returncode == 0:
    print("FAIL: decide keeps the placeholder look requested for %.0f s after reading has ended and "
          "validate.py's 'only the look' check passes" % stuck)
else:
    print("PASS")
