"""probe_replay_determinism: run den-core's run.py twice on the same synthetic evening and compare the log bytes;
then run check.py on each log, and an independent byte comparison of the replayed lines.

What it checks
- run.py --synthetic evening --touch --game --policies need incumbent decide writes byte-identical log.jsonl twice
  (same seed), and different bytes for a different seed (sanity: the comparison can fail).
- check.py LOG says OK on both logs and exits 0.
- An independent replay (runner.run on the inputs read back from the log, re-serialised here) gives the same bytes.

How to run (synthetic data only; temp folders only; no network):
    python3 -B probe_replay_determinism.py /path/to/den-core-checkout
Prints PASS/FAIL lines and a RESULT line.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-core"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
failures = []


def say(ok, text):
    print(("PASS: " if ok else "FAIL: ") + text)
    if not ok:
        failures.append(text)


def run(out, *extra):
    cmd = [sys.executable, "-B", str(TOOL / "run.py"), "--synthetic", "evening", "--touch", "--game",
           "--policies", "need", "incumbent", "decide", "--out", str(out), *extra]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=ENV)
    if p.returncode != 0:
        print(p.stdout[-2000:], p.stderr[-2000:])
    return p


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    a, b, c = tmp / "a", tmp / "b", tmp / "c"
    pa, pb, pc = run(a), run(b), run(c, "--seed", "7")
    say(pa.returncode == 0 and pb.returncode == 0 and pc.returncode == 0, "three run.py invocations exit 0")
    log_a, log_b, log_c = (x / "log.jsonl" for x in (a, b, c))
    ha, hb, hc = (hashlib.sha256(x.read_bytes()).hexdigest() for x in (log_a, log_b, log_c))
    print("RESULT: sha256 run1=%s run2=%s seed7=%s bytes=%d lines=%d" % (
        ha[:16], hb[:16], hc[:16], log_a.stat().st_size, log_a.read_bytes().count(b"\n")))
    say(ha == hb, "same seed twice gives byte-identical log.jsonl")
    say(ha != hc, "a different seed gives different bytes (the comparison can fail)")
    strip = lambda out: [l for l in out.splitlines() if not l.startswith("log: ")]  # the out path differs
    say(strip(pa.stdout) == strip(pb.stdout), "the printed summaries are identical too (bar the log path)")
    for log in (log_a, log_b):
        p = subprocess.run([sys.executable, "-B", str(TOOL / "check.py"), str(log)], cwd=str(ROOT),
                           capture_output=True, text=True, env=ENV)
        say(p.returncode == 0 and p.stdout.startswith("OK"), "check.py %s: %s" % (log.parent.name, p.stdout.strip()))
    # Independent byte comparison: re-run the runner on the logged inputs and serialise it ourselves.
    sys.path.insert(0, str(TOOL))
    import events  # noqa: E402
    import runner  # noqa: E402
    header, logged = events.read(log_a)
    inputs = [(e["t"], e["kind"], e["data"], e["source"]) for e in logged if e["source"] in events.ADAPTER_SOURCES]
    policies = runner.load_policies([p["module"] for p in header["policies"]],
                                    {p["module"]: p["config"] for p in header["policies"]})
    params = {k: header[k] for k in ("tick_s", "tail_s", "plant", "shown", "synthetic", "assume_occupied")}
    h2, again = runner.run(inputs, policies, sources=header["sources"], **params)
    text = "".join(json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"
                   for v in [h2, *again])
    say(text.encode("ascii") == log_a.read_bytes(), "independent re-run of the logged inputs reproduces every byte")
    kinds = {}
    for e in logged:
        kinds[(e["kind"], e["source"])] = kinds.get((e["kind"], e["source"]), 0) + 1
    print("RESULT: decisions incumbent=%d decide=%d person_state=%d mode=%d" % (
        kinds.get(("decision", "incumbent"), 0), kinds.get(("decision", "decide"), 0),
        kinds.get(("person_state", "need"), 0), kinds.get(("mode", "incumbent"), 0)))
    has_applied = any("applied_scene" in e["data"] for e in logged if e["kind"] == "decision")
    print("RESULT: any decision carries applied_scene/step log: %s" % has_applied)

print("RESULT: %s" % ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(0 if not failures else 1)
