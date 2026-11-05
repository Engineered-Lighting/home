#!/usr/bin/env python3
"""verify_SEAM-05: need's PersonState.focused_engagement carries non-canon kind/onset_ts. What breaks?

    python3 -B verify_SEAM-05.py <wt-root containing den-core-live/ and live-records/>

1. Canon: schemas.json PersonState.focused_engagement type.
2. den-core's synthetic evening (need incumbent decide): the focused_engagement keys need writes, and whether
   canon since_s already carries the onset (since_s == emitted_ts - onset_ts on every record).
3. decide (rung 9's consumer) fed a PersonState built to schemas.json ({state, since_s, confidence}, no kind)
   vs the same record with need's extra kind='reading': which one gets the reading look?
Temp dirs only; no network or hardware."""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

wt = Path(sys.argv[1]).resolve()
core = wt / "den-core-live" / "tools" / "den-core"
S = json.loads((wt / "live-records/docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
ps_rec = next(r for r in S["records"] if r["id"] == "person_state")
print("RESULT canon focused_engagement:", next(f["type"] for f in ps_rec["fields"] if f["name"] == "focused_engagement"))
tmp = Path(tempfile.mkdtemp(prefix="v05-"))
p = subprocess.run([sys.executable, "-B", str(core / "run.py"), "--out", str(tmp / "core"), "--synthetic", "evening",
                    "--policies", "need", "incumbent", "decide"], capture_output=True, text=True, cwd=str(tmp))
log = [json.loads(l) for l in open(tmp / "core" / "log.jsonl", encoding="utf-8") if l.strip()]
pss = [e["data"] for e in log if e.get("kind") == "person_state"]
keys = sorted({k for d in pss for k in d["focused_engagement"]})
with_onset = [d for d in pss if d["focused_engagement"]["onset_ts"] is not None]
agree = all(abs(d["emitted_ts"] - d["focused_engagement"]["since_s"] - d["focused_engagement"]["onset_ts"]) < 0.002
            for d in with_onset)
print("RESULT need writes focused_engagement keys:", keys, "over", len(pss), "records")
print("RESULT onset_ts == emitted_ts - since_s on all %d records with an onset: %s" % (len(with_onset), agree))
sys.path.insert(0, str(core))
import decide  # noqa: E402
T0 = 1793764800.0


def ev(t, kind, data):
    return {"schema": "den-event-1", "t": float(t), "seq": 0, "kind": kind, "source": "x", "data": data}


def arrival():
    out = [ev(T0, "light_observed", {"entity_id": b, "on": False, "brightness": None, "kelvin": None,
                                     "context": "unknown"}) for b in decide.LIGHTS]
    out.append(ev(T0, "mode", {"policy": "den_lighting@1.0.4", "revision": 1, "gaming": False, "occupancy": "on",
                               "holds": [], "normal": {b: {"on": True, "brightness": 180, "kelvin": 2700}
                                                       for b in decide.LIGHTS}}))
    out.append(ev(T0 + 1, "occupancy", {"value": "on", "via": "synthetic"}))
    return out


canon = {"schema_version": "person_state/v1", "state_id": "A@%.1f" % (T0 + 10), "emitted_ts": T0 + 10,
         "track_id": "A", "identity": "unbound",
         "focused_engagement": {"state": "engaged", "since_s": 8.0, "confidence": 0.9},
         "posture": "sitting",
         "field_support": {"focused_engagement": {"last_support_capture_ts": T0 + 9, "support_status": "observed"}}}
with_kind = copy.deepcopy(canon)
with_kind["focused_engagement"]["kind"] = "reading"
res = {}
for label, rec in (("canon PersonState (schemas.json fields only)", canon), ("same + need's kind='reading'", with_kind)):
    pol = decide.make_policy()
    for e in arrival():
        pol.handle(e)
    outs = pol.handle(ev(T0 + 10, "person_state", rec))
    whys = [o["data"]["why"].split(":", 1)[0] for o in outs if o["kind"] == "decision"]
    res[label] = whys
    print("RESULT decide.reading()=%s; decisions on it: %s  <- %s" % (decide.reading(rec), whys or "none", label))
ok = "reading_look" not in res["canon PersonState (schemas.json fields only)"] and \
    "reading_look" in res["same + need's kind='reading'"]
print("PASS (defect reproduced, consumer side): decide needs the non-canon kind to choose the reading look"
      if ok else "FAIL (consumer side not reproduced)")
print("NOTE onset timing is %s from canon since_s" % ("recoverable" if agree else "NOT recoverable"))
print("tmp:", tmp)
