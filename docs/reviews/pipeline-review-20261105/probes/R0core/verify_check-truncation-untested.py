"""verify_check-truncation-untested (REPRODUCE lens, skeptical verifier).

What it checks: the reported surviving mutant of tools/den-core/check.py (compare range(min(len(want), len(got)))
instead of range(max(...))) on concrete inputs. It writes a synthetic-evening log (need, incumbent, decide) to a temp
folder, then makes (a) a copy cut at a line boundary (first 1000 lines) and (b) a copy with one fabricated decide
decision appended (valid den-event-1, next seq). It runs the real check.check() and the mutated check (built in
memory from the pinned check.py text; no file in the checkout is touched) on all three logs.

Expected if the finding holds: the real check refuses (a) and (b); the mutant says OK for both. Whether any test
catches the mutant is shown separately by verify_mutants_R0core.py.

How to run (synthetic data only; temp folders only; no network):
    python3 -B verify_check-truncation-untested.py /path/to/den-core-checkout
"""
import json
import sys
import tempfile
import types
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-core"
sys.path.insert(0, str(TOOL))
import check  # noqa: E402
import events  # noqa: E402
import runner  # noqa: E402
import synthetic  # noqa: E402

src = (TOOL / "check.py").read_text("utf-8")
old = "for index in range(max(len(want), len(got))):"
assert src.count(old) == 1, "pattern not found once"
mutant = types.ModuleType("check_min")
mutant.__file__ = str(TOOL / "check.py")
exec(compile(src.replace(old, old.replace("max(", "min(")), "check_min", "exec"), mutant.__dict__)

items, _, _ = synthetic.evening(touch=True, game=True)
header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", "decide"]),
                         synthetic=True)
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    full = tmp / "full.jsonl"
    events.write(full, header, log)
    lines = full.read_text("ascii").split("\n")[:-1]
    cut = tmp / "cut.jsonl"
    cut.write_text("\n".join(lines[:1000]) + "\n", "ascii")
    fake = {"schema": events.SCHEMA, "t": log[-1]["t"], "seq": len(log), "kind": "decision", "source": "decide",
            "data": {"decision_id": "decide-fabricated-1", "requested_scene": {events.LIGHTS[0]: {"on": False,
                     "brightness": None, "kelvin": None}}, "status": "requested", "why": "fabricated"}}
    events.validate(fake)
    added = tmp / "added.jsonl"
    added.write_text("\n".join(lines + [events.dumps(fake)]) + "\n", "ascii")
    print("RESULT: full log %d lines; cut %d lines; added %d lines" % (len(lines), 1000, len(lines) + 1))
    bad = 0
    for name, path in (("full", full), ("cut", cut), ("added", added)):
        real, mut = check.check(path), mutant.check(path)
        print("RESULT %-5s real: ok=%s %s | mutant: ok=%s %s" % (name, real["ok"], real["reason"], mut["ok"],
                                                                  mut["reason"]))
        if name != "full" and not real["ok"] and mut["ok"]:
            bad += 1
    print("RESULT: %s" % ("mutant passes %d tampered log(s) the real check refuses" % bad if bad else
                          "mutant behaves like the real check"))
