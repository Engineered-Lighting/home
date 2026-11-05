"""probe_atk_score_gate: attacks on score.py's rung 9 gate, on a synthetic evening scored as the suite's own
ScoreTest does (a non-synthetic-flagged log, decide's slots filled at their proposed values, fake() model with
fake=False; this is the only way to reach a scored verdict today).

What it checks
  A. hard constraints: the scored verdict is "pass" and the 'record' check text says hard_conflicts "recompute",
     while EVERY decide decision logged its glare check as unchecked. Does score.py ever say so?
  B. floors "fixed before the run": --plumbing on the same real-flagged log (floors open) prints the candidate's
     anticipation and stability rates; a gate whose floors are then set to exactly those rates scores "pass".
     Nothing binds the gate to a time before the log (the record only carries the gate's own sha256).
  C. recipients of the APPLIED scene (ladder rung 9 gate): count decide decisions whose recipients list a bulb
     that the decision's own applied_scene shows OFF (or unknown), i.e. a bulb named as serving a person that
     is not lit; score.py's record check compares recipients with the REQUESTED scene only.
  D. determinism: the run twice gives byte-identical logs (and check.check OK); score.py --out twice on the same
     log gives byte-identical score records?
  E. hand recomputation of the candidate's anticipation/stability on this log from the logged decisions.

How to run (synthetic data only; temp folders only; no host contacted):
  python3 -B probe_atk_score_gate.py /path/to/den-core-live-checkout
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / "tools" / "den-core"))
    import check
    import decide
    import events
    import light_model
    import runner
    import score
    import synthetic

    tmp = Path(tempfile.mkdtemp(prefix="probe-r9-score-"))
    try:
        slots = {n: {**e, "value": e["proposed"], "status": "set"} for n, e in decide.DEFAULT_CONFIG["slots"].items()}
        model = light_model.fake()
        model.update(version="probe-lm-1", fake=False)
        configs = {"decide": {"slots": slots, "light_model": model}}

        def make_log(name):
            items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True, game=True)
            header, log = runner.run(runner.tagged(items, "synthetic"),
                                     runner.load_policies(["need", "incumbent", "decide"], configs),
                                     synthetic=False, sources=[{"name": "synthetic:evening"}])
            events.write(tmp / name, header, log)
            return tmp / name, header, log

        path, header, log = make_log("a.jsonl")
        path2, _, _ = make_log("b.jsonl")
        same = path.read_bytes() == path2.read_bytes()
        print("%s D1 two runs of the same inputs give byte-identical logs (%d bytes)" % ("PASS" if same else "FAIL",
                                                                                         len(path.read_bytes())))
        ok = check.check(path)
        print("%s D2 check.check: %s" % ("PASS" if ok["ok"] else "FAIL", ok["reason"]))

        gate = json.loads(score.GATE.read_text("utf-8"))
        for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
            entry.update(value=entry["proposed"], status="set")
        gate["parameters"]["compensation"]["value"] = "none-v0"
        gate_path = tmp / "gate.json"
        gate_path.write_text(json.dumps(gate), "ascii")

        record, reasons = score.score(path, gate_path)
        decided = [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]
        statuses = sorted({d["inputs"]["hard_constraints"]["glare"]["status"] for d in decided})
        print("INFO A scored verdict: %s; refusals: %s" % (record["verdict"], reasons))
        print("INFO A record check: ok=%s text=%r" % (record["checks"]["record"]["ok"], record["checks"]["record"]["text"]))
        print("INFO A glare status logged by the %d decide decisions: %s" % (len(decided), statuses))
        said = any("unchecked" in json.dumps(c) for c in record["checks"].values())
        all_unchecked = all(s.startswith("unchecked") for s in statuses)
        print("%s A the verdict says the hard-constraint check was never made" % ("PASS" if said or not all_unchecked else "FAIL"))
        if all_unchecked and record["verdict"] == "pass" and not said:
            print("RESULT A DEFECT REPRODUCED: 'pass' with the hard-constraint item vacuous (every check unchecked), "
                  "reported as OK")

        # B: plumbing on the real-flagged log with the shipped (open) gate reveals the rates.
        rec_p, reasons_p = score.score(path, score.GATE, plumbing=True)
        cand = rec_p["surrogates"]["arms"]["candidate"]
        print("INFO B --plumbing (floors open) prints candidate anticipation %s, stability %s; refused-without-plumbing: %s"
              % (cand["anticipation"]["rate"], cand["stability"]["rate"], reasons_p))
        post = json.loads(json.dumps(gate))
        post["surrogates"]["anticipation"]["floor"]["value"] = cand["anticipation"]["rate"]
        post["surrogates"]["stability"]["floor"]["value"] = cand["stability"]["rate"]
        post_path = tmp / "gate-after.json"
        post_path.write_text(json.dumps(post), "ascii")
        rec_b, reasons_b = score.score(path, post_path)
        print("INFO B floors set after seeing the rates: verdict %s, refusals %s, gate id %s"
              % (rec_b["verdict"], reasons_b, rec_b["gate"]["id"]))
        if rec_b["verdict"] == "pass" and not reasons_b:
            print("RESULT B DEFECT REPRODUCED: floors chosen from the same log's plumbing output give a scored pass "
                  "under the same gate id; nothing records that the floors were fixed before the run")

        # C: recipients vs the applied scene.
        bad = []
        for d in decided:
            for b in d["recipients"]:
                applied = d["applied_scene"][b]["readback"]
                if applied is None or not applied["on"]:
                    bad.append((d["decision_id"], b, d["requested_scene"][b], applied))
        print("INFO C %d of %d decide decisions; %d (decision, bulb) recipients rows name a bulb whose applied readback "
              "is off or unknown" % (len(decided), len(decided), len(bad)))
        for row in bad[:3]:
            print("INFO C   %s %s requested %s applied readback %s" % row)
        if bad and record["checks"]["record"]["ok"]:
            print("RESULT C DEFECT REPRODUCED: recipients follow the requested scene; score.py's record check passes "
                  "decisions whose recipients name bulbs the applied scene shows off/unknown")

        # D3: score twice, --out bytes.
        outs = []
        for n in range(2):
            out = tmp / ("score-%d.json" % n)
            with contextlib.redirect_stdout(io.StringIO()):
                score.main([str(path), "--gate", str(gate_path), "--out", str(out)])
            outs.append(out.read_bytes())
        print("%s D3 score.py --out twice on one log gives byte-identical records" % ("PASS" if outs[0] == outs[1] else "FAIL"))
        if outs[0] != outs[1]:
            a, b = json.loads(outs[0]), json.loads(outs[1])
            print("INFO D3   timing text run 1: %s" % a["checks"]["timing"]["text"])
            print("INFO D3   timing text run 2: %s" % b["checks"]["timing"]["text"])

        # E: hand recomputation of the candidate's rates.
        found = score.episodes(log)
        print("INFO E episodes (track, onset, end) relative to T0: %s"
              % [(tr, round(a - synthetic.T0, 1), round(b - synthetic.T0, 1)) for tr, a, b in found])
        lm = light_model.LightModel(model)
        pts = decide.task_points({"track_id": "room"}, {"slots": slots}, lm)
        need_changes = []
        last_rule = None
        for e in log:
            if e["kind"] == "decision" and e["source"] == "decide":
                rule = e["data"]["why"].split(":", 1)[0]
                if (rule == "reading_look") != (last_rule == "reading_look"):
                    need_changes.append((e["t"], rule))
                last_rule = rule
        print("INFO E need-driven decide decisions (t-T0, rule): %s"
              % [(round(t - synthetic.T0, 1), r) for t, r in need_changes])
        hits = []
        for tr, t0, t1 in found:
            within = [t for t, r in need_changes if t0 <= t <= t0 + 10 and r == "reading_look"]
            hits.append(bool(within))
        hand = round(sum(hits) / len(hits), 4) if hits else None
        print("INFO E hand anticipation (a reading_look turn-on within 10 s of each onset; rise checked by the tool): %s,"
              " tool: %s" % (hand, record["surrogates"]["arms"]["candidate"]["anticipation"]["rate"]))
        print("INFO E task points used for track 'room': %s" % pts)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
