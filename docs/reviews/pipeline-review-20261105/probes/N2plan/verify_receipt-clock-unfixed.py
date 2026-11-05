#!/usr/bin/env python3
"""verify_receipt-clock-unfixed: the N2 plan fixes the facts-event clock but not the receipt's.

What it checks, at the pinned plan commit (claude/plan-n2):
  1. PLAN.md fixes the facts-event time (t = the take's started_unix in whole seconds + r + S)
     but names no clock for the receipt's observed_at / generated_at / available_at /
     valid_until, which decide "fresh" and "stale on arrival".
  2. With rung R's own take.py and its test FakeCamera (bound to 127.0.0.1, temp dirs only),
     R's only per-frame wall clock, recv_pc_unix, differs from floor(started_unix) + t_s by
     a nonzero offset delta. One run uses the real clock; two shift time.time() so that
     started_unix ends in about .05 and about .90 (delta tracks the fraction the floor drops).
  3. With qwen_scene.build_observation and first_receipt (main's perception-lab, imported from
     a temp copy), one slot's valid_until on the two candidate bases differs by delta. A reply
     that is "stale on arrival" on the event clock's base reads fresh on recv_pc_unix, and a
     held level stays fresh delta seconds longer. Also checks that raw t_s cannot be a
     la-fact-log-1 observed_at, so a builder has to choose an epoch base.

Run:
  python3 verify_receipt-clock-unfixed.py <plan-n2 checkout> [--rung-r DIR] [--lab DIR]
  Defaults: <checkout>/../rung-r and <checkout>/../main/stack/services/perception-lab.
Synthetic data only; nothing is written in any checkout (modules are copied to a temp dir,
bytecode writing is off). Prints PASS/FAIL/RESULT lines.
"""
import argparse
import contextlib
import csv
import io
import json
import math
import re
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
P = 4.0          # the plan's request period on t_s
MAX_AGE = 10     # qwen_scene.MAX_AGE_SECONDS


def plan_text_check(root):
    path = root / "docs" / "plans" / "n2-need-test" / "PLAN.md"
    lines = path.read_text(encoding="utf-8").split("\n")
    flat = re.sub(r"\s+", " ", " ".join(lines))
    rule = "at t = the take's `started_unix` in whole seconds + r + S" in flat
    print(("PASS" if rule else "FAIL") + ": PLAN.md fixes the facts-event clock "
          "(t = floor(started_unix) + r + S)")
    for word in ("observed_at", "generated_at", "available_at"):
        n = flat.count(word)
        print(f"RESULT: PLAN.md mentions `{word}` {n} time(s)")
    hits = [(i + 1, l.strip()) for i, l in enumerate(lines)
            if re.search(r"valid_until|observed time|first_receipt|recv_pc_unix", l)]
    for n, l in hits:
        print(f"  PLAN.md:{n}: {l}")
    defined = re.search(r"(observed_at|observed time)[^.]{0,120}(started_unix|t_s\b|recv_pc_unix)", flat) or \
        re.search(r"(started_unix|t_s\b|recv_pc_unix)[^.]{0,120}(observed_at|observed time|valid_until)", flat)
    print(("FAIL" if defined else "PASS") + ": no sentence in PLAN.md ties the receipt's observed time / "
          "valid_until to started_unix, t_s or recv_pc_unix" + (f" (found: {defined.group(0)!r})" if defined else ""))
    return rule and not defined


def run_take(den_take, work, shift=None):
    """One short commission take with R's take.run against R's FakeCamera on 127.0.0.1."""
    import take
    import test_den_take as t
    real_time = time.time
    patches = [mock.patch.object(take.http.client, "HTTPConnection", t.LoopbackOnly),
               mock.patch.object(take, "free_bytes", return_value=40 * t.GIB)]
    if shift is not None:
        target = shift  # make started_unix end in about `target`
        offset = (target - (real_time() % 1.0)) % 1.0
        patches.append(mock.patch.object(take.time, "time", lambda: real_time() + offset))
    camera = t.FakeCamera(period=0.05)
    work.mkdir(parents=True)
    token = work / "private" / "camera-token.txt"
    token.parent.mkdir()
    token.write_text(t.TOKEN + "\n", encoding="ascii", newline="\n")
    args = types.SimpleNamespace(script="commission", out=str(work / "takes" / "t1"), camera=camera.url,
                                 token_file=str(token))
    settings = take.Settings(slate_s=1.0, poll_s=0.05, hold_lead_s=0.2, say_gap_s=0.1, away_s=0.2,
                             commission_activity_s=6.0, health_retry_s=0.1)
    assert camera.url.startswith("http://127.0.0.1:")
    try:
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            code = take.run(args, say=lambda s: None, peek=t.readable_peek, settings=settings)
    finally:
        camera.close()
    folder = Path(args.out)
    record = json.loads((folder / "take.json").read_text(encoding="utf-8"))
    with open(folder / "frames.csv", newline="", encoding="utf-8") as f:
        rows = [{k: float(r[k]) for k in ("t_s", "recv_pc_unix")} for r in csv.DictReader(f)]
    return code, record, rows


def pick(rows, x):
    """Index of the frame nearest x (ties to the earlier)."""
    return min(range(len(rows)), key=lambda i: (abs(rows[i]["t_s"] - x), i))


def slot_frames(rows, r):
    """The plan's request frames: the latest at or before r, and those nearest r - 1.5 and r - 3."""
    latest = max(i for i, row in enumerate(rows) if row["t_s"] <= r)
    picks = sorted({pick(rows, r - 3.0), pick(rows, r - 1.5), latest})
    return [rows[i] for i in picks]


def receipt(scene, frames, base_of, S):
    reply = {"people_visible": "one", "subject_posture": "sitting", "held_object": "book",
             "book_state": "open", "gaze_target": "book", "typing_visible": "no",
             "screen_facing_subject": "not_visible", "activity_hypothesis": "reading",
             "visible_basis": ["person_visible", "book_in_hands"], "change_vs_prior": "no_prior"}
    fr = [{"media_id": f"m{i}", "frame_id": f"f{i}", "observed_at": base_of(f)} for i, f in enumerate(frames)]
    generated = fr[-1]["observed_at"] + S
    draft = scene.build_observation(
        site_id="site", camera_id="cam", session_id="0" * 32, request_id="a" * 64, frames=fr,
        generated_at=generated, prompt_version=scene.PROMPT_VERSION, context={"x": 1}, body_sha256="0" * 64,
        engine_epoch="e1", artifact_sha256="0" * 64, content=json.dumps(reply), finish_reason="stop",
        prior_supplied=False)
    return scene.first_receipt(draft, generated)


def analyse(label, record, rows, scene):
    started = record["started_unix"]
    base = math.floor(started)
    deltas = [row["recv_pc_unix"] - (base + row["t_s"]) for row in rows]
    deltas_unfloored = [row["recv_pc_unix"] - (started + row["t_s"]) for row in rows]
    print(f"RESULT[{label}]: complete={record['complete']} frames={len(rows)} t_s span={rows[-1]['t_s']:.2f} s "
          f"started_unix frac={started % 1:.3f}")
    print(f"RESULT[{label}]: recv_pc_unix - (floor(started_unix) + t_s): min {min(deltas):.3f} "
          f"median {sorted(deltas)[len(deltas) // 2]:.3f} max {max(deltas):.3f} s")
    print(f"RESULT[{label}]: recv_pc_unix - (started_unix + t_s): min {min(deltas_unfloored):.3f} "
          f"max {max(deltas_unfloored):.3f} s (first-frame delay part)")
    ok = min(abs(d) for d in deltas) > 0.0
    print(("PASS" if ok else "FAIL") + f"[{label}]: the two per-frame epoch bases differ for every frame")
    # one slot at r = P on the take's own t_s (segment start 0)
    r = P
    frames = slot_frames(rows, r)
    latest = frames[-1]
    d_latest = latest["recv_pc_unix"] - (base + latest["t_s"])
    event_base = lambda f: base + f["t_s"]
    recv_base = lambda f: f["recv_pc_unix"]
    flips, checked = [], 0
    invariant_ok = True
    for i in range(0, 501):
        S = 8.0 + i * 0.01
        t_event = base + r + S
        a = receipt(scene, frames, event_base, S)
        b = receipt(scene, frames, recv_base, S)
        want = MAX_AGE - (r - latest["t_s"]) - S
        invariant_ok &= abs((a["valid_until"] - t_event) - want) < 1e-6
        stale_a, stale_b = a["valid_until"] <= t_event, b["valid_until"] <= t_event
        checked += 1
        if stale_a != stale_b:
            flips.append(S)
    print(("PASS" if invariant_ok else "FAIL") + f"[{label}]: on the event base, valid_until - t == "
          "10 - (r - t_latest) - S for every S")
    if flips:
        print(f"RESULT[{label}]: slot r={r:.1f} t_latest={latest['t_s']:.3f}: S in [{flips[0]:.2f}, {flips[-1]:.2f}] "
              f"({len(flips)} of {checked} S values) is stale on arrival on the event base but fresh on recv_pc_unix; "
              f"delta at the latest frame {d_latest:.3f} s")
    else:
        print(f"RESULT[{label}]: no S value flips (delta at latest frame {d_latest:.3f} s)")
    # a held level: scored seconds k + 0.5 that read fresh on recv_pc_unix but stale on the event base
    a = receipt(scene, frames, event_base, 1.5)
    b = receipt(scene, frames, recv_base, 1.5)
    extra = [k + 0.5 for k in range(0, 40) if a["valid_until"] <= base + k + 0.5 < b["valid_until"]]
    print(f"RESULT[{label}]: held level from this reply: fresh until t_s {a['valid_until'] - base:.3f} on the event "
          f"base vs {b['valid_until'] - base:.3f} on recv_pc_unix; scored sample points falsely fresh: {extra}")
    return bool(flips), d_latest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan_root")
    ap.add_argument("--rung-r")
    ap.add_argument("--lab")
    a = ap.parse_args()
    root = Path(a.plan_root).resolve()
    rung_r = Path(a.rung_r).resolve() if a.rung_r else root.parent / "rung-r"
    lab = Path(a.lab).resolve() if a.lab else root.parent / "main" / "stack" / "services" / "perception-lab"
    plan_ok = plan_text_check(root)
    with tempfile.TemporaryDirectory(prefix="verify-receipt-clock-") as tmp:
        tmp = Path(tmp)
        shutil.copytree(rung_r / "tools" / "den-take", tmp / "den-take",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (tmp / "lab").mkdir()
        for name in ("qwen_scene.py", "la_fact_log.py"):
            shutil.copy2(lab / name, tmp / "lab" / name)
        sys.path[:0] = [str(tmp / "den-take"), str(tmp / "lab")]
        import qwen_scene as scene
        import la_fact_log
        # raw t_s cannot be a la-fact-log-1 time: the first frame's t_s is 0
        try:
            la_fact_log.milliseconds(0.0)
            print("FAIL: la_fact_log accepts t_s = 0 as an epoch")
        except ValueError as exc:
            print(f"PASS: la_fact_log.milliseconds(0.0) refuses raw t_s ({exc}); a builder must pick an epoch base")
        results = []
        for label, shift in (("real-clock", None), ("frac-0.05", 0.05), ("frac-0.90", 0.90)):
            code, record, rows = run_take(tmp / "den-take", tmp / f"w-{label}", shift)
            if code != 0 or not record["complete"] or rows[-1]["t_s"] < P:
                print(f"FAIL[{label}]: the fake take did not complete with t_s >= {P} (code {code})")
                continue
            results.append((label,) + analyse(label, record, rows, scene))
    worst = [flip for label, flip, _ in results if label == "frac-0.90"]
    reproduced = bool(plan_ok and worst and worst[0])
    print("RESULT: " + ("REPRODUCED" if reproduced else "NOT REPRODUCED") +
          ": the plan leaves the receipt clock open; delta between the candidate bases per run: "
          + ", ".join(f"{label} {d:.3f} s" for label, _, d in results))


if __name__ == "__main__":
    main()
