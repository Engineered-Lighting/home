#!/usr/bin/env python3
"""verify_SEAM-04: what does need v0 do with the input its real producer gives it for "A works at the counter
while B reads in the armchair", and is the room-level merge a departure or the documented stage?

    python3 -B verify_SEAM-04.py <wt-root containing den-core-live/ and live-records/>

The real la-fact-log-1 producer is Qwen on WHOLE-ROOM stills (notes [[R3]]), so with A and B in view a
valid answer says people_visible 'two'. Feeds den-core's need + decide (via run.py --facts, --assume-occupied)
(a) 60 s of whole-room facts with people_visible 'two', activity 'reading'  (A+B in view)
(b) 60 s of whole-room facts with people_visible 'one', activity 'reading'  (B alone)
and prints the spec lines that set the stage (need.py docstring, schemas.json PersonState.status_note,
ladder rungs 5/6/9). Temp dirs only; no network or hardware."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

wt = Path(sys.argv[1]).resolve()
core = wt / "den-core-live" / "tools" / "den-core"
spec = wt / "live-records/docs/architecture/live-v1"
sys.path.insert(0, str(core))
import synthetic as CS  # noqa: E402
import need  # noqa: E402
tmp = Path(tempfile.mkdtemp(prefix="v04-"))
T = 1793066400.0
res = {}
for label, people in (("whole-room facts, A+B in view (people_visible two)", "two"),
                      ("whole-room facts, B alone (people_visible one)", "one")):
    facts = dict(CS.TEMPLATES["reading"], people_visible=people)
    lines = [CS.fact_line(800000 + i, T + 4 * i, T + 4 * i + 1.2, facts=dict(facts)) for i in range(15)]
    day = json.loads(lines[0])["available_at"][:10]
    folder = tmp / ("facts-" + people)
    folder.mkdir()
    (folder / ("la-fact-log-1-%s.jsonl" % day)).write_text("".join(l + "\n" for l in lines), encoding="ascii")
    out = tmp / ("core-" + people)
    p = subprocess.run([sys.executable, "-B", str(core / "run.py"), "--out", str(out), "--facts", str(folder),
                        "--assume-occupied", "--policies", "need", "incumbent", "decide"],
                       capture_output=True, text=True, cwd=str(tmp))
    log = [json.loads(l) for l in open(out / "log.jsonl", encoding="utf-8") if l.strip()]
    eng = [e["data"]["focused_engagement"]["state"] for e in log if e.get("kind") == "person_state"]
    whys = [e["data"]["why"].split(":", 1)[0] for e in log if e.get("kind") == "decision"
            and e["data"]["policy"].startswith("decide")]
    res[people] = whys
    print("RESULT %s: run.py exit %d; engagement %s; decide rules %s" % (label, p.returncode, eng, whys))
print("SPEC need.py:", " ".join(need.__doc__.split("\n\n")[1].split())[:240])
print("SPEC need.py:", [l.strip() for l in need.__doc__.splitlines() if "two people" in l][0])
S = json.loads((spec / "schemas.json").read_text(encoding="utf-8"))
print("SPEC schemas.json PersonState.status_note:", next(r for r in S["records"] if r["id"] == "person_state")["status_note"])
L = json.loads((spec / "ladder.json").read_text(encoding="utf-8"))
R = {str(r["n"]): r for r in L["rungs"]}
print("SPEC ladder rung 5 replaces:", R["5"]["replaces"][:120])
print("SPEC ladder rung 6 replaces:", R["6"]["replaces"][R["6"]["replaces"].find("A fixed adapter"):][:200])
print("SPEC ladder rung 9 unblocks:", R["9"]["unblocks"])
ok = "reading_look" not in res["two"] and "reading_look" in res["one"]
print("RESULT with the real producer's input, two people in view never get the reading look, by the documented "
      "rule:", ok)
print("tmp:", tmp)
