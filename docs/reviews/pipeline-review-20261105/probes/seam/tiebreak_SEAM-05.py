#!/usr/bin/env python3
"""tiebreak_SEAM-05: need's PersonState.focused_engagement carries the non-canon sub-keys kind and onset_ts.
Is onset timing lost to a reader built from schemas.json, and does any consumer depend on the extra keys?

    python3 -B tiebreak_SEAM-05.py <den-core-live checkout root> [extra person_state.jsonl ...]

Checks, synthetic data and temp dirs only (no network, no hardware):
 1. canon focused_engagement type in the checkout's docs/architecture/live-v1/schemas.json;
 2. den-core's own synthetic evening (run.py --synthetic evening --touch --policies need incumbent decide):
    the focused_engagement keys need writes, and whether onset_ts == emitted_ts - since_s on every record
    (i.e. the canon fields already carry the onset);
 3. owner choice 3 (contact-starting): onset is timed to the system's first output that leaves 'none'.
    That instant is the emitted_ts (= event t) of the first non-'none' record, a canon field; compare it with
    onset_ts (the capture time of the first playing sample) to show they are different quantities;
 4. any extra person_state.jsonl files given (e.g. the harness's round2b output) get check 2 as well;
 5. consumer side: decide.reading() with a schemas.json-shaped PersonState vs the same record plus kind.
Prints RESULT lines and a final VERDICT line.
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
core = root / "tools" / "den-core"
schemas = json.loads((root / "docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
ps = next(r for r in schemas["records"] if r["id"] == "person_state")
canon_type = next(f["type"] for f in ps["fields"] if f["name"] == "focused_engagement")
canon_keys = [k.strip() for k in canon_type.strip("{}").split(",")]
print("RESULT canon focused_engagement:", canon_type)

tmp = Path(tempfile.mkdtemp(prefix="tb05-"))
p = subprocess.run([sys.executable, "-B", str(core / "run.py"), "--out", str(tmp / "core"), "--synthetic", "evening",
                    "--touch", "--policies", "need", "incumbent", "decide"], capture_output=True, text=True,
                   cwd=str(tmp))
print("RESULT run.py synthetic evening exit", p.returncode)
log = [json.loads(l) for l in (tmp / "core" / "log.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
events = [e for e in log if e.get("kind") == "person_state"]


def recoverable(records, label):
    keys = sorted({k for d in records for k in d["focused_engagement"]})
    extra = sorted(set(keys) - set(canon_keys))
    onset = [d for d in records if d["focused_engagement"].get("onset_ts") is not None]
    worst = max((abs(d["emitted_ts"] - d["focused_engagement"]["since_s"] - d["focused_engagement"]["onset_ts"])
                 for d in onset), default=0.0)
    lost = [d["state_id"] for d in onset if d["focused_engagement"].get("since_s") is None]
    print("RESULT %s: %d records, keys %s, non-canon %s" % (label, len(records), keys, extra))
    print("RESULT %s: %d records with onset_ts; max |emitted_ts - since_s - onset_ts| = %.6f s; "
          "records with onset but no since_s: %d" % (label, len(onset), worst, len(lost)))
    return extra, worst < 0.001 and not lost


extra, ok_syn = recoverable([e["data"] for e in events], "synthetic evening")
ok_extra = True
for path in sys.argv[2:]:
    recs = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    recs = [r.get("data", r) if r.get("kind") == "person_state" else r for r in recs]
    _, ok = recoverable(recs, Path(path).parent.name + "/" + Path(path).name)
    ok_extra = ok_extra and ok

# 3. Owner choice 3's onset instant: the first output that leaves 'none', per episode.
prev, rows = "none", []
for e in events:
    d = e["data"]
    st = d["focused_engagement"]["state"]
    if prev == "none" and st != "none":
        rows.append((e["t"], d["emitted_ts"], d["focused_engagement"]["onset_ts"], d["focused_engagement"]["since_s"]))
    prev = st
for t, emitted, onset, since in rows:
    print("RESULT episode start: first non-none output t=%.3f emitted_ts=%.3f (canon) | onset_ts=%.3f "
          "(capture of first playing sample) | since_s=%.3f" % (t, emitted, onset, since))
same_t = all(abs(t - emitted) < 1e-9 for t, emitted, _, _ in rows)
print("RESULT first-output instant equals the canon emitted_ts on all %d episode starts: %s" % (len(rows), same_t))

# 5. Consumer side.
sys.dont_write_bytecode = True
sys.path.insert(0, str(core))
import decide  # noqa: E402

base = {"schema_version": "person_state/v1", "state_id": "A@1793764810.0", "emitted_ts": 1793764810.0,
        "track_id": "A", "identity": "unbound", "focused_engagement": {"state": "engaged", "since_s": 8.0,
                                                                       "confidence": 0.9},
        "field_support": {"focused_engagement": {"last_support_capture_ts": 1793764809.0,
                                                 "support_status": "observed"}}}
kinded = copy.deepcopy(base)
kinded["focused_engagement"]["kind"] = "reading"
print("RESULT decide.reading(schemas.json-shaped engaged record) = %s; with kind='reading' = %s"
      % (decide.reading(base), decide.reading(kinded)))

print("VERDICT onset timing %s from canon fields (emitted_ts - since_s); the owner's onset instant is the canon "
      "emitted_ts of the first non-none record; extra sub-keys %s; decide depends on kind: %s"
      % ("RECOVERABLE" if ok_syn and ok_extra else "LOST", extra,
         (not decide.reading(base)) and decide.reading(kinded)))
print("tmp:", tmp)
