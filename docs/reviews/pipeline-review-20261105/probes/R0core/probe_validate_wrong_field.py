"""probe_validate_wrong_field: validate.py on run logs in which one record carries a wrong field name.

A synthetic evening (need, incumbent, decide, with A's touch) is written to a temp folder; then one line at a time is
edited (a field renamed), kept canonical, and validate.py LOG is run as the owner would run it. Each case reports
validate's exit code and whether it printed its per-check lines and the overall line, or died with a traceback.

Cases
- input light_observed: data key `context` renamed `ctx`          (an input kind with required keys)
- decide decision: `requested_scene` renamed `requested`           (a derived kind: events.validate checks no keys)
- decide decision: skipped[].channel renamed skipped[].entity_id   (a derived kind)
- incumbent mode: holds[].since renamed holds[].opened             (a derived kind)
Expected of a check that "prints OK, FAIL or n/a per check and exits 0 or 1": every case prints the check lines and
an overall FAIL line.

How to run (synthetic data only; temp folders only; no network):
    python3 -B probe_validate_wrong_field.py /path/to/den-core-checkout
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-core"
sys.path.insert(0, str(TOOL))
import events  # noqa: E402
import runner  # noqa: E402
import synthetic  # noqa: E402

items, _, _ = synthetic.evening(touch=True)
header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", "decide"]),
                         synthetic=True)


def first(pred):
    return next(i for i, e in enumerate(log) if pred(e))


def rename(d, old, new):
    d[new] = d.pop(old)


cases = []
i = first(lambda e: e["kind"] == "light_observed" and e["source"] == "synthetic")
cases.append(("input light_observed context->ctx", i, lambda d: rename(d, "context", "ctx")))
i = first(lambda e: e["kind"] == "decision" and e["source"] == "decide")
cases.append(("decide decision requested_scene->requested", i, lambda d: rename(d, "requested_scene", "requested")))
i = first(lambda e: e["kind"] == "decision" and e["source"] == "decide" and e["data"]["skipped"])
cases.append(("decide decision skipped[].channel->entity_id", i,
              lambda d: [rename(s, "channel", "entity_id") for s in d["skipped"]]))
i = first(lambda e: e["kind"] == "mode" and e["data"]["holds"])
cases.append(("incumbent mode holds[].since->opened", i, lambda d: [rename(h, "since", "opened") for h in d["holds"]]))

bad = 0
with tempfile.TemporaryDirectory() as tmp:
    for name, index, edit in cases:
        changed = json.loads(json.dumps(log))
        edit(changed[index]["data"])
        path = Path(tmp) / ("%d.jsonl" % index)
        path.write_bytes("".join(events.dumps(v) + "\n" for v in [header, *changed]).encode("ascii"))
        p = subprocess.run([sys.executable, "-B", str(TOOL / "validate.py"), str(path)], cwd=str(ROOT),
                           capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        lines = p.stdout.strip().splitlines()
        overall = next((line for line in lines if line.startswith("overall:")), None)
        trace = p.stderr.strip().splitlines()[-1] if "Traceback" in p.stderr else None
        print("RESULT: %-46s exit=%d checks_printed=%d overall=%r traceback=%r" % (
            name, p.returncode, sum(line.split(":")[0] in ("replay", "need parity", "only the look", "holds", "quiet")
                                    for line in lines), overall and overall[:40], trace))
        if overall is None or trace:
            bad += 1
            print("FAIL: validate.py died on a log with a wrong field instead of reporting FAIL per check")
        else:
            print("PASS: validate.py reported the bad log check by check")
print("RESULT: %s" % ("PASS" if not bad else "FAIL (%d)" % bad))
sys.exit(1 if bad else 0)
