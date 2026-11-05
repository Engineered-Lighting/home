#!/usr/bin/env python3
"""Probe: recompute N1's freeze hashes, split chain and re-mark agreement by hand; attack the freezes.

What it checks (synthetic takes only, temp dir)
  Part 1, a full N1 run on 3 staged takes (hard-a #1, hard-b #1, hard-a #2):
    - the split by rule (per-script alternation) and no session in both parts;
    - split.json's hash chain recomputed independently;
    - fixture.json / labels.json recomputed as canonical JSON and hashed by hand,
      against the freeze lines and the EvalRecord fields label.py prints;
    - every file sha256 in both manifests;
    - an EMPTY re-mark (end to end, no intervals) is accepted by `freeze labels`;
    - tamper A: one byte of a frozen held-out label file -> verify must fail;
    - tamper B: a held-out onset moved and the manifest re-hashed consistently,
      checked with and without the labels line in FREEZES.md;
    - tamper C: one byte of a frozen boxes CSV -> verify must fail.
  Part 2, n1.agreement() on a hand-made pair of passes against values worked
  out by hand (matches, band/case agreement, onset/offset median and p90 with
  linear-interpolation percentiles, carried and unsure counts).

How to run
  python3 probe_freeze-recompute-and-tamper-attack.py <den-label checkout>
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path


def canon(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def h(data):
    return hashlib.sha256(data).hexdigest()


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path[:0] = [str(root / "tools/den-label"), str(root / "tools/den-take")]
    import label
    import n1
    import test_den_label as T
    ok_all, fails = True, []

    def expect(cond, what):
        nonlocal ok_all
        print(("PASS " if cond else "FAIL ") + what)
        if not cond:
            ok_all = False
            fails.append(what)

    tmp = Path(tempfile.mkdtemp(prefix="n1-attack-freeze-")).resolve()
    takes = tmp / "takes"
    takes.mkdir()
    T.staged_take(takes, "a1", "hard-a", T.BASE_UNIX)
    T.staged_take(takes, "b1", "hard-b", T.BASE_UNIX + 3600)
    T.staged_take(takes, "a2", "hard-a", T.BASE_UNIX + T.DAY)
    ws = n1.Workspace(takes, takes / "n1")
    ws.scan()
    splits = {e["session"]: e["split"] for e in ws.entries()}
    print("RESULT splits", splits)
    expect(splits == {"a1": "development", "b1": "development", "a2": "held_out"}, "per-script alternation")
    expect(len(splits) == len(ws.entries()), "no session registered twice")
    raw_split = json.loads((takes / "n1/split.json").read_text())
    prev = None
    for e in raw_split["entries"]:
        expect(e["prev"] == prev, f"split chain link for {e['session']}")
        prev = h(canon(e))
    expect(raw_split["head"] == prev, "split head = sha256(canonical(last entry))")
    base = T.Base("run")
    base.takes, base.n1root = takes, takes / "n1"
    base.surfaces_done(ws, "a1")
    for take in ("a1", "b1", "a2"):
        base.boxes_done(ws, take)
        base.labels_done(ws, take)
    real_now = n1.now
    n1.now = lambda: real_now() + 4 * T.DAY
    remark_take = ws.remark_take()
    base.labels_done(ws, remark_take, intervals=[], spans=[], mode="remark")  # an empty re-mark
    fixture_line, _ = ws.freeze_fixture("")
    labels_line, record, _ = ws.freeze_labels("")
    print("RESULT", fixture_line)
    print("RESULT", labels_line)
    fx = takes / "n1/fixture-v1"
    lb = takes / "n1/labels-v1"
    fraw, lraw = (fx / "fixture.json").read_bytes(), (lb / "labels.json").read_bytes()
    expect(canon(json.loads(fraw)) == fraw, "fixture.json is canonical JSON (recomputed)")
    expect(canon(json.loads(lraw)) == lraw, "labels.json is canonical JSON (recomputed)")
    expect(f"sha256={h(fraw)}" in fixture_line, f"fixture hash by hand {h(fraw)[:16]} = freeze line")
    expect(f"sha256={h(lraw)}" in labels_line, f"labels hash by hand {h(lraw)[:16]} = freeze line")
    expect(record["gold_set"]["n1"]["fixture"]["hash"] == h(fraw) and record["hash"] == h(lraw),
           "EvalRecord gold_set.n1 hashes = hand hashes")
    fm, lm = json.loads(fraw), json.loads(lraw)
    files = {fm["surface_map"]["file"]: fm["surface_map"]["sha256"],
             **{s["boxes"]["file"]: s["boxes"]["sha256"] for s in fm["sessions"]}}
    expect(all(h((fx / k).read_bytes()) == v for k, v in files.items()), "every fixture file sha256 by hand")
    files = {lm["split"]["file"]: lm["split"]["sha256"], lm["remark"]["file"]: lm["remark"]["sha256"],
             **{s["file"]: s["sha256"] for s in lm["sessions"]}}
    expect(all(h((lb / k).read_bytes()) == v for k, v in files.items()), "every labels file sha256 by hand")
    expect("fixture" not in json.dumps(sorted(lm)) and "fixture" not in labels_line,
           "INFO labels.json and the labels line name no fixture version or hash (pairing only in the printout)")
    remark = json.loads((lb / "remark.json").read_text())
    print("RESULT re-mark of", remark["take"], "agreement", json.dumps(remark["agreement"], sort_keys=True))
    if remark["agreement"]["intervals"]["remark"] == 0:
        print("FAIL an empty re-mark (no intervals, marked end to end) was accepted by freeze labels "
              f"({remark['agreement']['intervals']['label']} first-pass intervals, 0 matched)")
        fails.append("empty re-mark accepted")
        ok_all = False
    ok, lines = ws.verify("")
    expect(ok, "verify OK on the untouched freeze")
    # tamper A: one byte of the held-out label file
    target = lb / "sessions/a2.json"
    target.chmod(0o644)
    original = target.read_bytes()
    target.write_bytes(original.replace(b'"by":"owner"', b'"by":"ownes"', 1))
    ok, lines = ws.verify("")
    expect(not ok and any("sessions/a2.json changed" in l for l in lines), "tamper A detected: " + lines[-1])
    # tamper B: move a held-out onset and re-hash the manifest consistently
    session = json.loads(original)
    first = session["intervals"][0]
    t_of = {f["n"]: f["t"] for f in n1.read_frames(takes / "a2")}
    first["onset_n"] += 1
    first["onset_t_s"] = t_of[first["onset_n"]]
    new = canon(session)
    target.write_bytes(new)
    manifest = json.loads(lraw)
    for s in manifest["sessions"]:
        if s["take"] == "a2":
            s["sha256"] = h(new)
    (lb / "labels.json").chmod(0o644)
    (lb / "labels.json").write_bytes(canon(manifest))
    ok_no_line, lines_no_line = ws.verify("")
    ok_line, lines_line = ws.verify("## Log\n" + labels_line + "\n" + fixture_line + "\n")
    print("RESULT tamper B, FREEZES.md without the line:", ok_no_line, lines_no_line)
    print("RESULT tamper B, FREEZES.md with the line:   ", ok_line, lines_line)
    expect(not ok_line, "tamper B detected once the labels line is in FREEZES.md")
    if ok_no_line:
        print("INFO tamper B is invisible to verify until the freeze line is committed (by design)")
    # tamper C: one byte of a frozen boxes CSV
    target.write_bytes(original)
    (lb / "labels.json").write_bytes(lraw)
    csv_path = fx / "boxes/a2.csv"
    csv_path.chmod(0o644)
    data = csv_path.read_bytes()
    rows = data.split(b"\n")
    rows[5] = rows[5][:-1] + (b"0" if rows[5][-1:] != b"0" else b"1")
    csv_path.write_bytes(b"\n".join(rows))
    ok, lines = ws.verify("")
    expect(not ok and any("boxes/a2.csv changed" in l for l in lines), "tamper C detected")
    n1.now = real_now
    # Part 2: agreement by hand
    def iv(a, b, band, case, ok_=("touch", "release"), sure=(True, True)):
        return {"onset_t_s": a, "offset_t_s": b, "onset_kind": ok_[0], "offset_kind": ok_[1], "band": band,
                "case": case, "sure": {"onset": sure[0], "offset": sure[1], "band": True}}
    first_pass = [iv(10, 20, "fine", "sorting_mail"), iv(30, 40, "casual", "wiping"),
                  iv(50, 60, "fine", "still_reader"), iv(70, 80, "casual", "phone_at_counter", ("carried", "release")),
                  iv(90, 100, "fine", "sorting_mail")]
    second_pass = [iv(11, 19, "fine", "sorting_mail"), iv(31, 42, "fine", "wiping"),
                   iv(56, 70, "fine", "still_reader"), iv(71, 83, "casual", "phone_at_counter"),
                   iv(90.5, 101.5, "fine", "sorting_mail", sure=(False, True))]
    got = n1.agreement(first_pass, second_pass)
    # by hand: (10-20,11-19) ov 8 >= 5,4; (30-40,31-42) ov 9 >= 5,5.5; (50-60,56-70) ov 4 < 5 -> no;
    # (70-80,71-83) ov 9 >= 5,6; (90-100,90.5-101.5) ov 9.5 >= 5,5.5. matched 4 of 5/5.
    # band: fine=fine, casual!=fine, casual=casual, fine=fine -> 3/4; case 4/4.
    # onset diffs: 1, 1 (pair 4 carried, pair 5 unsure) -> median 1, p90 1; carried 1, unsure 1.
    # offset diffs: 1, 2, 3, 1.5 -> sorted 1,1.5,2,3: median (1.5+2)/2 = 1.75; p90 pos 2.7 -> 2+0.7*1 = 2.7.
    hand = {"intervals": {"label": 5, "remark": 5, "matched": 4}, "band_agreement": 0.75, "case_agreement": 1.0,
            "onset": {"n": 2, "median_s": 1.0, "p90_s": 1.0, "carried": 1, "unsure": 1},
            "offset": {"n": 4, "median_s": 1.75, "p90_s": 2.7, "carried": 0, "unsure": 0}}
    print("RESULT agreement tool", json.dumps(got, sort_keys=True))
    print("RESULT agreement hand", json.dumps(hand, sort_keys=True))
    expect(got == hand, "re-mark agreement matches the hand computation")
    print(f"RESULT {'all checks passed' if ok_all else 'failures: ' + '; '.join(fails)}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
