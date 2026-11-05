#!/usr/bin/env python3
"""End-to-end seam harness for the live-v1 den pipeline. Synthetic data only.

    python3 run_seam.py --wt-root <dir with one checkout per branch> [--out <tmpdir>]

One synthetic evening (two people, A at the counter, B in the armchair, one
crossing) is pushed through the REAL tools, each tool's real output feeding the
next. Raw inputs only are made with the tools' own fakes: den-take's loopback
fake camera (serving den-register's synthetic depth scene so a registration is
possible) and den-observe's fake detector. The one stand-in written here is the
FAKE INTERPRETER (clearly labelled), which turns person_bundle records into the
la-fact-log-1 lines den-core's need reads. Every record file is run through
schema_validator.py. No network beyond 127.0.0.1; temp dirs only.
"""
import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
VALIDATOR = HERE / "schema_validator.py"
REGISTER2 = []
REAL_BUNDLES = []
SCHEMAS = []
ROWS = []  # (link, producer@sha, consumer@sha, status, detail)
LOG = []


def log(msg):
    LOG.append(msg)
    print(msg, flush=True)


def sha(wt, name):
    p = wt / name
    if not p.exists():
        return "absent"
    try:
        return subprocess.check_output(["git", "-C", str(p), "rev-parse", "--short=8", "HEAD"], text=True).strip()
    except Exception:
        return "?"


def take_wt(wt):
    """den-take's checkout: claude/den-take-script (on top of claude/rung-r) when present, else rung-r."""
    return "den-take-script" if (wt / "den-take-script" / "tools" / "den-take" / "take.py").exists() else "rung-r"


def row(link, producer, consumer, status, detail):
    ROWS.append((link, producer, consumer, status, detail))
    log(f"[{status}] {link}: {detail}")


def run(cmd, cwd=None, timeout=600, env=None):
    log("$ " + " ".join(str(c) for c in cmd))
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    out = (p.stdout + p.stderr).strip()
    log(f"  -> exit {p.returncode}\n  " + out[-3000:].replace("\n", "\n  "))
    return p.returncode, out


def validate(schemas, rid, path):
    code, out = run([sys.executable, "-B", VALIDATOR, schemas, rid, path])
    return code, out


def push_path(*dirs):
    for d in dirs:
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))


def fresh_import(names):
    for n in names:
        sys.modules.pop(n, None)


# ---------------------------------------------------------------- link 1: den-take
def make_takes(wt, out, evening_s):
    """A real take.py run against a loopback fake camera (den-take's own test FakeCamera),
    whose bundles carry den-register's synthetic depth scene and the CHECK-V1 slate."""
    take_dir = wt / take_wt(wt) / "tools" / "den-take"
    reg_dir = wt / "rung-1-register" / "tools" / "den-register"
    fresh_import(["check", "take", "test_den_take", "synthetic"])
    push_path(reg_dir, take_dir)
    import check  # noqa
    import take  # noqa
    import test_den_take as T  # den-take's own fakes (FakeCamera, nv12, screen_value, bundle, metadata)
    sys.path.remove(str(reg_dir))
    sys.path.insert(0, str(reg_dir))
    import synthetic as RS  # den-register's synthetic depth scene
    depth, left, seen = RS.render()
    k = RS.K

    class SceneCamera(T.FakeCamera):
        """SlateCamera-like: camera time runs `speed` x real time; 4 fps of camera time."""

        def __init__(self, c, speed=5.0):
            super().__init__(period=0.25 / speed)
            self.c, self.showing = c, False

        def response(self, path):
            if path != "/frame.npz" or self.mode != "ok":
                return super().response(path)
            index, showing = int((time.monotonic() - self.start) / self.period), self.showing
            key = (self.session, index, showing)
            if key not in self.cache:
                self.cache.clear()
                t_host = 7000.0123456 + index * 0.25
                meta = T.metadata(self.session, 500 + index, t_host, t_host - 3000.0)
                for plane in ("depth", "left", "right"):
                    meta["frames"][plane]["transformation_valid"] = True
                    meta["frames"][plane]["intrinsics"] = k
                screen = T.screen_value(t_host - self.c) if showing else None
                self.cache[key] = T.bundle(meta, T.nv12(screen, index),
                                           members={"depth_mm.npy": T.npy("<u2", (400, 640), depth),
                                                    "left.npy": T.npy("|u1", (400, 640), left),
                                                    "right.npy": T.npy("|u1", (400, 640), left)})
            return 200, "application/octet-stream", self.cache[key]

    loopback = mock.patch.object(take.http.client, "HTTPConnection", T.LoopbackOnly)
    free = mock.patch.object(take, "free_bytes", return_value=40 * T.GIB)
    loopback.start()
    free.start()
    token_file = out / "private" / "camera-token.txt"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(T.TOKEN + "\n", encoding="ascii", newline="\n")
    takes = {}
    try:
        cam = SceneCamera(c=2.3456)

        def say(text):
            if text.startswith("Hold the phone"):
                cam.showing = True
            elif text.startswith(("Slate OK", "End slate")):
                cam.showing = False
        settings = take.Settings(slate_s=T.M["slate_length_s"] / 5, poll_s=0.05, hold_lead_s=0.2, say_gap_s=0.1,
                                 away_s=0.4, commission_activity_s=evening_s / 5.0, health_retry_s=0.1)
        args = types.SimpleNamespace(script="commission", out=str(out / "takes" / "evening"), camera=cam.url,
                                     token_file=str(token_file))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = take.run(args, say=say, peek=take.peek, settings=settings, enter=lambda: False)
        log("take.run exit %s; %s" % (code, buf.getvalue().strip()[-600:]))
        cam.close()
        folder = Path(args.out)
        rec = json.loads((folder / "take.json").read_text(encoding="utf-8"))
        n = sum(1 for _ in open(folder / "frames.csv")) - 1
        takes["evening"] = folder
        status = "OK" if code == 0 and rec.get("complete") else "REFUSED"
        row("fake camera -> take", "den-take fake camera (test_den_take.FakeCamera + den-register scene)",
            "den-take take.py@" + sha(wt, take_wt(wt)), status,
            f"take.run exit {code}, complete={rec.get('complete')}, {n} frames, script={rec.get('script')}")
        # check.py, the interactive prompt answered 'none' as its own tests do
        cbuf = io.StringIO()
        got = check.run_check(folder, ask=lambda: "none", show=lambda p: None, out=cbuf,
                              console=T.Console())
        ccode, report = got[0], (got[1] if isinstance(got[1], dict) else next((g for g in got if isinstance(g, dict)), {}))
        row("take -> check", "take.py@" + sha(wt, take_wt(wt)), "check.py@" + sha(wt, take_wt(wt)),
            "OK" if ccode == 0 else "REFUSED",
            f"check exit {ccode}, verdict {report.get('verdict') if report else None}, "
            f"failures {report.get('failures') if report else None}")
        return takes, seen
    finally:
        free.stop()
        loopback.stop()
        for name in ("check", "take", "test_den_take", "synthetic"):
            sys.modules.pop(name, None)
        for d in (take_dir, reg_dir):
            while str(d) in sys.path:
                sys.path.remove(str(d))


# ---------------------------------------------------------------- link 2: den-register
def register(wt, out, take_folder, seen, schemas):
    reg_dir = wt / "rung-1-register" / "tools" / "den-register"
    work = out / "register"
    work.mkdir(parents=True, exist_ok=True)
    fresh_import(["check", "take", "synthetic", "register", "worksheet", "records", "method", "scene", "rigid",
                  "serve", "test_den_register"])
    push_path(reg_dir.parent / "den-take", reg_dir)
    try:
        import synthetic as RS
        import register as R
        import worksheet as W
        import test_den_register as TR
        # The owner's raw inputs: a worksheet of taped points and one click per point (den-register's synthetic room).
        (work / "worksheet.json").write_text(json.dumps(RS.worksheet()), encoding="utf-8")
        # 1. the CLI as the owner runs it, with the committed REGISTER-V1 (slots open)
        code, text = run([sys.executable, "-B", reg_dir / "register.py", "fit", work, "--take", take_folder])
        sheet = W.load(work / "worksheet.json", need_positions=False)
        n = R.frame_number(take_folder, None, work)
        session = R.ClickSession(work, take_folder, n, sheet, TR.closed_method())
        for pid, (u, v, _) in seen.items():
            session.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
        session.done()
        code, text = run([sys.executable, "-B", reg_dir / "register.py", "fit", work, "--take", take_folder])
        cli_reason = "slots_open" if "slots_open" in text else text[-200:]
        # 2. the same fit with the proposed values closed in memory, as den-register's own tests do
        buf = io.StringIO()
        code, report = R.run_fit(work, take_folder, m=TR.closed_method(), now=time.time, out=buf)
        log("run_fit (proposed values closed in memory): exit %s\n%s" % (code, buf.getvalue()[-1500:]))
        rm, cr = work / "room-model.jsonl", work / "calibration-records.jsonl"
        ok = code == 0 and rm.exists()
        row("take -> register", "take.py@" + sha(wt, take_wt(wt)), "den-register@" + sha(wt, "rung-1-register"),
            "OK" if ok else "REFUSED",
            f"CLI fit with committed REGISTER-V1: {cli_reason} (expected: open slots); "
            f"fit with proposed values: exit {code}, " + (buf.getvalue().splitlines() or [""])[0][:160])
        for path, rid in ((rm, "room_model"), (cr, "calibration_record")):
            if path.exists():
                vcode, vout = validate(schemas, rid, path)
                row(f"register output {path.name}", "den-register@" + sha(wt, "rung-1-register"), "schemas.json",
                    "OK" if vcode == 0 else "SCHEMA", vout.replace("\n", " | ")[:600])
        if ok:
            # A second registration (another frame of the same take, as `fit --frame N` does): the file then holds
            # two RoomModel revisions, one per line, which is what den-observe's --room-model will be given.
            import shutil as _sh
            work2 = out / "register-2"
            _sh.copytree(work, work2)
            n2 = R.frame_number(take_folder, 40, work2)
            s2 = R.ClickSession(work2, take_folder, n2, sheet, TR.closed_method())
            for pid, (u, v, _) in seen.items():
                s2.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
            s2.done()
            buf2 = io.StringIO()
            code2, _ = R.run_fit(work2, take_folder, frame=40, m=TR.closed_method(), now=time.time, out=buf2)
            lines = (work2 / "room-model.jsonl").read_text(encoding="utf-8").splitlines()
            log(f"second fit (frame 40): exit {code2}; room-model.jsonl now has {len(lines)} lines; "
                + (buf2.getvalue().splitlines() or [''])[0])
            REGISTER2.append(work2 / "room-model.jsonl")
        return rm if ok else None
    finally:
        for d in (reg_dir.parent / "den-take", reg_dir):
            while str(d) in sys.path:
                sys.path.remove(str(d))
        fresh_import(["check", "take", "synthetic", "register", "worksheet", "records", "method", "scene", "rigid",
                      "serve", "test_den_register"])


# ---------------------------------------------------------------- link 3: detector rows (raw input)
def detector_rows(wt, out, take_folder, evening_s):
    """den-observe's fake detector, two figures per frame: A at the counter (left of frame),
    B in the armchair (right, lower); A crosses to B's side and back once mid-evening."""
    obs_dir = wt / "rung-2-observe" / "tools" / "den-observe"
    fresh_import(["detections", "fake_detector", "check", "takes"])
    push_path(obs_dir.parent / "den-take", obs_dir)
    try:
        import fake_detector as F
        import detections as D
        import takes as TK
        tk = TK.Take(take_folder)
        rows = []
        t0 = tk.rows[0]["t_host"]
        t1 = tk.rows[-1]["t_host"]
        mid = (t0 + t1) / 2
        for r in tk.rows:
            t = r["t_host"]
            # A: counter at 0.62 of the frame width; walks to 0.86 (past B) and back over 20 s around mid-evening.
            # B: armchair at 0.88. Both right of centre, so the room positions stay at x > 0 for a camera at x = 0.
            u = abs(t - mid)
            ax = 0.62 + (0.24 * max(0.0, 1 - u / 10.0))
            a = F.row(t, rgb_seq=r["rgb_seq"], centre=(ax, 0.45))
            b = F.row(t, rgb_seq=r["rgb_seq"], centre=(0.88, 0.55))
            dets = a["detections"] + [dict(b["detections"][0], id=f"{r['rgb_seq']}:1")]
            merged = dict(a, detections=dets)
            rows.append(D.validate(merged))
        path = out / "detections.jsonl"
        D.write_rows(path, rows)
        row("take -> detector rows", "take.py@" + sha(wt, take_wt(wt)),
            "den-observe fake_detector@" + sha(wt, "rung-2-observe"), "OK",
            f"{len(rows)} den-detections-1 rows (2 persons each) over {t1 - t0:.1f} s of camera time")
        return path
    finally:
        for d in (obs_dir.parent / "den-take", obs_dir):
            while str(d) in sys.path:
                sys.path.remove(str(d))
        fresh_import(["detections", "fake_detector", "check", "takes"])


# ---------------------------------------------------------------- link 4: den-observe
def observe(wt, out, take_folder, room_model, det_rows, schemas):
    obs_dir = wt / "rung-2-observe" / "tools" / "den-observe"
    tag = "den-observe@" + sha(wt, "rung-2-observe")
    result = None
    if room_model is not None:
        target = out / "observe-from-register"
        code, text = run([sys.executable, "-B", obs_dir / "replay.py", "--take", take_folder, "--room-model",
                          room_model, "--detections", det_rows, "--out", target])
        status = "OK" if code == 0 else "REFUSED"
        row("register -> observe (room_model camera entry)", "den-register@" + sha(wt, "rung-1-register"), tag,
            status, text.splitlines()[-1][:300] if text else "")
        if code == 0:
            result = target / "observations.jsonl"
    if REGISTER2:
        target2 = out / "observe-from-register-2rev"
        code2, text2 = run([sys.executable, "-B", obs_dir / "replay.py", "--take", take_folder, "--room-model",
                            REGISTER2[0], "--detections", det_rows, "--out", target2])
        row("register (2 revisions in room-model.jsonl) -> observe", "den-register@" + sha(wt, "rung-1-register"),
            tag, "OK" if code2 == 0 else "REFUSED", text2.splitlines()[-1][:300] if text2 else "")
    if result is None:
        # FALLBACK to keep going: den-observe's own synthetic RoomModel (its synth.room_model()), not a hand-written
        # fixture; the take, detector rows and replay are still the real ones.
        fresh_import(["synth", "camgeom", "check", "take"])
        push_path(obs_dir.parent / "den-take", obs_dir)
        import synth as OS
        rm = out / "room-model-den-observe-synth.json"
        rm.write_text(json.dumps(OS.room_model(), ensure_ascii=False), encoding="utf-8")
        for d in (obs_dir.parent / "den-take", obs_dir):
            while str(d) in sys.path:
                sys.path.remove(str(d))
        fresh_import(["synth", "camgeom", "check", "take"])
        target = out / "observe-fallback"
        code, text = run([sys.executable, "-B", obs_dir / "replay.py", "--take", take_folder, "--room-model", rm,
                          "--detections", det_rows, "--out", target])
        row("take + detector rows -> observe (FALLBACK room model: den-observe synth)",
            "take.py@" + sha(wt, take_wt(wt)) + " + fake_detector", tag,
            "OK" if code == 0 else "REFUSED", " / ".join(text.splitlines()[:3])[:300])
        if code == 0:
            result = target / "observations.jsonl"
    if result is not None:
        vcode, vout = validate(schemas, "observation", result)
        row("observe output observations.jsonl", tag, "schemas.json", "OK" if vcode == 0 else "SCHEMA",
            vout.replace("\n", " | ")[:700])
    return result


def observation_checks(wt, out, obs_path, register_rm, take_folder):
    """Things the validator cannot see: clock domain, units, transform_id resolution, encoding."""
    tag = "den-observe@" + sha(wt, "rung-2-observe")
    raw = obs_path.read_bytes()
    obs = [json.loads(l) for l in raw.decode("utf-8").splitlines() if l.strip()]
    details = []
    crlf = b"\r\n" in raw
    details.append(f"LF only: {not crlf}; UTF-8: True; {len(obs)} records")
    ts = [o.get("capture_ts") for o in obs]
    details.append(f"capture_ts range {min(ts):.3f}..{max(ts):.3f}")
    take_rows = (take_folder / "frames.csv").read_text().splitlines()[1:]
    t_host = [float(l.split(",")[4]) for l in take_rows]
    details.append(f"take t_host range {t_host[0]:.3f}..{t_host[-1]:.3f}")
    unix_like = all(1.7e9 < t < 2.0e9 for t in ts)
    row("observe capture_ts clock domain", "take.py@" + sha(wt, take_wt(wt)), tag,
        "OK" if unix_like else "MISREAD", "; ".join(details))
    if register_rm is not None and register_rm.exists():
        lines = [json.loads(l) for l in register_rm.read_text(encoding="utf-8").splitlines() if l.strip()]
        cams = [c for m in lines for c in m.get("cameras", [])]
        latest = lines[-1].get("cameras", [])
        tids = sorted({o.get("transform_id") for o in obs})
        res = {t: sum(1 for c in latest if f"{c.get('camera_id')}@{c.get('version')}" == t) for t in tids}
        row("observation.transform_id -> den-register RoomModel.cameras", tag,
            "den-register@" + sha(wt, "rung-1-register"),
            "OK" if all(v == 1 for v in res.values()) else "MISREAD",
            f"transform_ids {tids} resolve in den-register's latest RoomModel: {res}; register entries: "
            + str([f"{c.get('camera_id')}@{c.get('version')}" for c in cams]))
    return obs


# ---------------------------------------------------------------- link 5: den-track
def track(wt, out, obs_path, schemas=None):
    tdir = wt / "rung-3-track" / "tools" / "den-track"
    tag = "den-track@" + sha(wt, "rung-3-track")
    files = sorted(p.name for p in tdir.glob("*.py"))
    if (tdir / "den_track.py").exists() and obs_path is not None:
        session = out / "track-session"
        take = session / "evening"
        take.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(obs_path, take / "observations.jsonl")  # a byte copy of den-observe's real output
        code, text = run([sys.executable, "-B", tdir / "den_track.py", "track", "--session", session])
        tracks = take / "tracks.jsonl"
        row("observe -> track", "den-observe@" + sha(wt, "rung-2-observe"), tag,
            "OK" if code == 0 and tracks.exists() else "REFUSED", " / ".join(text.splitlines()[-3:])[:300])
        if not tracks.exists():
            return None
        vcode, vout = validate(schemas, "track", tracks)
        row("track output tracks.jsonl", tag, "schemas.json", "OK" if vcode == 0 else "SCHEMA",
            vout.replace("\n", " | ")[:600])
        recs = [json.loads(l) for l in open(tracks, encoding="utf-8") if l.strip()]
        obs_ids = {json.loads(l)["obs_id"] for l in open(obs_path, encoding="utf-8") if l.strip()}
        lo = [o for t in recs for o in (t.get("last_obs_ids") or [])]
        ids = sorted({t.get("track_id") for t in recs})
        states = {}
        for t in recs:
            states[t.get("state")] = states.get(t.get("state"), 0) + 1
        amb = sum(1 for t in recs if (t.get("association") or {}).get("status") == "ambiguous")
        empty = sum(1 for t in recs if not t.get("last_obs_ids"))
        timekeys = sorted({k for t in recs for k in t if k.endswith("_ts") or k == "ts"})
        row("track.last_obs_ids -> observation.obs_id", tag, "den-observe@" + sha(wt, "rung-2-observe"),
            "OK" if all(o in obs_ids for o in lo) else "MISREAD",
            f"{len(recs)} track lines, ids {ids}, states {states}, ambiguous lines {amb}; {len(lo)} last_obs_ids, "
            f"{sum(1 for o in lo if o not in obs_ids)} unresolved; {empty} lines with last_obs_ids [] (undatable "
            f"by den-bundle, which dates a line only from its observations); time keys on track lines: {timekeys}")
        return tracks
    code, text = run([sys.executable, "-B", "-c", "import method; print(method.read_block())"], cwd=tdir)
    cli = [f for f in files if f in ("track.py", "den_track.py", "tracker.py", "run.py", "replay.py")]
    row("observe -> track", "den-observe@" + sha(wt, "rung-2-observe"), tag, "MISSING-TOOL",
        f"no CLI/reader/writer (files: {', '.join(files)}); TrackerCore.step takes plain (id, xy, cov) inputs and "
        f"returns Python objects, no Track records; method.read_block(): exit {code}: {text.splitlines()[-1][:160]}")
    return None


# ---------------------------------------------------------------- link 6: den-bundle
def bundle(wt, out, obs_path, tracks_path, schemas):
    bdir = wt / "rung-5-bundle" / "tools" / "den-bundle"
    tag = "den-bundle@" + sha(wt, "rung-5-bundle")
    cli = bdir / "den_bundle.py"
    # (a) the real chain: real observations, and no track records (den-track writes none)
    empty = out / "tracks-none.jsonl"
    empty.write_text("", encoding="utf-8")
    if obs_path is not None and tracks_path is not None:
        obs_all = [json.loads(l) for l in open(obs_path, encoding="utf-8") if l.strip()]
        span = max(o["capture_ts"] for o in obs_all) - min(o["capture_ts"] for o in obs_all)
        rq, bu = out / "requests-real.jsonl", out / "bundles-real.jsonl"
        code, text = run([sys.executable, "-B", cli, "route", "--observations", obs_path, "--tracks", tracks_path,
                          "--from", "2.5", "--to", f"{span:.1f}", "--requests", rq, "--bundles", bu])
        nb = sum(1 for l in open(bu) if l.strip()) if bu.exists() else 0
        row("track -> bundle (real observations + real tracks)", "den-track@" + sha(wt, "rung-3-track"), tag,
            "OK" if code == 0 and nb else ("REFUSED" if code else "MISREAD"),
            f"route exit {code}, {nb} bundles: " + " / ".join(text.splitlines()[-4:])[:300])
        gcode, gtext = run([sys.executable, "-B", cli, "gate", "--observations", obs_path, "--tracks", tracks_path,
                            "--bundles", bu, "--requests", rq])
        row("bundle gate on the real chain", tag, tag, "OK" if gcode == 0 else "REFUSED",
            f"gate exit {gcode}: " + " / ".join(gtext.splitlines()[-4:])[:300])
        if nb:
            vcode, vout = validate(schemas, "person_bundle", bu)
            row("bundle output bundles-real.jsonl", tag, "schemas.json", "OK" if vcode == 0 else "SCHEMA",
                vout.replace("\n", " | ")[:600])
            ids = {o["obs_id"] for o in obs_all}
            bl = [json.loads(l) for l in open(bu, encoding="utf-8") if l.strip()]
            ks = [k.get("obs_id") for b in bl for k in (b.get("keypoint_series") or [])]
            row("bundles-real keypoint_series -> observation.obs_id", tag, "den-observe@" + sha(wt, "rung-2-observe"),
                "OK" if all(k in ids for k in ks) else "MISREAD",
                f"{len(ks)} keypoint entries, {sum(1 for k in ks if k not in ids)} unresolved; tracks bundled "
                f"{sorted({b['track_id'] for b in bl})}; association_safe false in "
                f"{sum(1 for b in bl if not b.get('association_safe'))} of {len(bl)}")
            REAL_BUNDLES.append(bu)
    elif obs_path is not None:
        code, text = run([sys.executable, "-B", cli, "route", "--observations", obs_path, "--tracks", empty,
                          "--from", "2.5", "--to", "60", "--requests", out / "requests-real.jsonl",
                          "--bundles", out / "bundles-real.jsonl"])
        row("track -> bundle (real observations, no Track records)", "den-track@" + sha(wt, "rung-3-track"), tag,
            "MISSING-TOOL", f"route exit {code}: {text.splitlines()[-1][:200] if text else ''}")
    # (b) to keep going: den-bundle's own CLI on its own 'crossing' fixture (the closest real bundle output)
    fx = out / "bundle-fixtures"
    run([sys.executable, "-B", cli, "fixtures", "--out", fx])
    req, bun = out / "requests.jsonl", out / "bundles.jsonl"
    code, text = run([sys.executable, "-B", cli, "route", "--observations", fx / "crossing" / "observations.jsonl",
                      "--tracks", fx / "crossing" / "tracks.jsonl", "--from", "2.5", "--to", "13",
                      "--requests", req, "--bundles", bun])
    row("bundle fixture -> route (FIXTURE-FED, upstream missing)", tag + " fixtures", tag,
        "OK" if code == 0 else "REFUSED", (text.splitlines() or [""])[-1][:200])
    gcode, gtext = run([sys.executable, "-B", cli, "gate", "--observations", fx / "crossing" / "observations.jsonl",
                        "--tracks", fx / "crossing" / "tracks.jsonl", "--bundles", bun, "--requests", req,
                        "--crossings", fx / "crossing" / "crossings.jsonl"])
    if bun.exists():
        vcode, vout = validate(schemas, "person_bundle", bun)
        row("bundle output bundles.jsonl", tag, "schemas.json", "OK" if vcode == 0 else "SCHEMA",
            vout.replace("\n", " | ")[:600])
        # obs_id references in keypoint_series and track.last_obs_ids resolve to real observations
        obs_ids = {json.loads(l)["obs_id"] for l in open(fx / "crossing" / "observations.jsonl") if l.strip()}
        bundles = [json.loads(l) for l in open(bun, encoding="utf-8") if l.strip()]
        ks = [k.get("obs_id") for b in bundles for k in (b.get("keypoint_series") or [])]
        bad = [k for k in ks if k not in obs_ids]
        trk = [json.loads(l) for l in open(fx / "crossing" / "tracks.jsonl") if l.strip()]
        lo = [o for t in trk for o in (t.get("last_obs_ids") or [])]
        badlo = [o for o in lo if o not in obs_ids]
        row("bundle.keypoint_series / track.last_obs_ids -> observation.obs_id", tag, tag,
            "OK" if not bad and not badlo else "MISREAD",
            f"{len(ks)} keypoint obs_ids, {len(bad)} unresolved; {len(lo)} last_obs_ids, {len(badlo)} unresolved")
    if req.exists():
        vcode, vout = validate(schemas, "auto", req)
        log("router_request (rung 5's own record, not in schemas.json): " + vout[:300])
    if REAL_BUNDLES:
        return REAL_BUNDLES[0]
    return bun if bun.exists() else None


# ---------------------------------------------------------------- link 7: FAKE INTERPRETER (stand-in, this harness)
def fake_interpreter(wt, out, bundles_path):
    """FAKE INTERPRETER. Reads person_bundle records and emits, per bundle, one la-fact-log-1 line
    (the only interpreter output den-core's need v0 reads), built with den-core's own fact builder
    (synthetic.fact_line, which uses the perceiver's qwen_scene/la_fact_log). Trivial rule: track B, or with
    den-track ids the bundle whose mean x is past 1.2 m (the armchair figure),
    (armchair) is 'reading', track A (counter) is 'computer_work'; people_visible is 'one'."""
    core = wt / "den-core-live" / "tools" / "den-core"
    fresh_import(["synthetic", "events", "sources", "la_fact_log", "qwen_scene"])
    push_path(core)
    try:
        import synthetic as CS
        bundles = [json.loads(l) for l in open(bundles_path, encoding="utf-8") if l.strip()]
        lines = []
        def armchair(b):
            if b["track_id"] in ("A", "B"):
                return b["track_id"] == "B"
            xs = [p["x"] for p in b.get("positions") or [] if isinstance(p.get("x"), (int, float))]
            return bool(xs) and sum(xs) / len(xs) > 1.2  # B's armchair is the far-right figure (x about 1.7 m)
        for i, b in enumerate(sorted(bundles, key=lambda b: b["interval"][1])):
            template = CS.TEMPLATES["reading" if armchair(b) else "computer_work"]
            t1 = float(b["interval"][1])
            lines.append(CS.fact_line(900000 + i, t1, t1 + 1.2, facts=dict(template)))
        path = out / "interp" / "la-fact-log-1-seam.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(l + "\n" for l in lines), encoding="ascii", newline="\n")
        row("bundle -> FAKE INTERPRETER -> facts", "den-bundle@" + sha(wt, "rung-5-bundle"),
            "FAKE INTERPRETER (run_seam.py)", "OK",
            f"{len(bundles)} bundles -> {len(lines)} la-fact-log-1 lines; per-track facts collapse into one "
            "room-level line format (no track_id field exists in la-fact-log-1)")
        return path, bundles
    finally:
        while str(core) in sys.path:
            sys.path.remove(str(core))
        fresh_import(["synthetic", "events", "sources", "la_fact_log", "qwen_scene"])


# ---------------------------------------------------------------- link 8: den-core need -> decide
def core(wt, out, facts_path, bundles, schemas, branch="den-core-live"):
    cdir = wt / branch / "tools" / "den-core"
    tag = f"{branch} need/decide@" + sha(wt, branch)
    target = out / f"core-{branch}"
    code, text = run([sys.executable, "-B", cdir / "run.py", "--out", target, "--facts", facts_path,
                      "--assume-occupied", "--policies", "need", "incumbent", "decide"])
    status = "OK" if code == 0 else "REFUSED"
    row(f"FAKE INTERPRETER facts -> need ({branch})", "FAKE INTERPRETER", tag, status,
        " / ".join(text.splitlines()[-3:])[:300])
    logp = target / "log.jsonl"
    if not logp.exists():
        return None
    events = [json.loads(l) for l in open(logp, encoding="utf-8") if l.strip()]
    kinds = {}
    for e in events:
        kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + 1
    ps = [e["data"] for e in events if e.get("kind") == "person_state"]
    dec = [e["data"] for e in events if e.get("kind") == "decision"]
    (target / "person_state.jsonl").write_text("".join(json.dumps(p) + "\n" for p in ps), encoding="utf-8")
    (target / "decision.jsonl").write_text("".join(json.dumps(d) + "\n" for d in dec), encoding="utf-8")
    log(f"log.jsonl kinds: {kinds}")
    for name, rid in (("person_state.jsonl", "person_state"), ("decision.jsonl", "decision")):
        vcode, vout = validate(schemas, rid, target / name)
        row(f"{branch} {rid} records (from log.jsonl)", tag, "schemas.json", "OK" if vcode == 0 else "SCHEMA",
            vout.replace("\n", " | ")[:600])
    tracks = sorted({p.get("track_id") for p in ps})
    states = [(p.get("emitted_ts"), p["focused_engagement"]["state"]) for p in ps]
    btracks = sorted({b["track_id"] for b in bundles})
    row(f"person_bundle tracks -> person_state.track_id ({branch})", "den-bundle + FAKE INTERPRETER", tag,
        "MISREAD" if tracks != btracks else "OK",
        f"bundles for tracks {btracks} -> person_state track_ids {tracks}; engagement states {states[:6]}")
    return target


def light_and_verify(wt, out=None, core_dir=None):
    l8 = wt / "rung-8-stage1" / "tools" / "den-light"
    vdir = wt / "rung-10-verify" / "tools" / "den-verify"
    pdir = wt / "rung-7-photometry" / "tools" / "den-photometry"
    ccli = wt / "den-core-live" / "tools" / "den-core" / "run.py"
    if (l8 / "model.py").exists() and out is not None and core_dir is not None and (core_dir / "log.jsonl").exists():
        probe = out / "verify-probe"
        code, text = run([sys.executable, "-B", HERE / "probe_verify.py", wt, probe, core_dir])
        lm = probe / "light-model.json"
        row("Stage 1 fit -> light-model.json + predict()", "den-light stage1 model.py@" + sha(wt, "rung-8-stage1"),
            "den-light predict.py@" + sha(wt, "rung-8-stage1"), "OK" if lm.exists() else "REFUSED",
            next((l for l in text.splitlines() if l.startswith("stage1 predict")), text[-200:])[:300])
        if lm.exists():
            vcode, vout = validate(Path(SCHEMAS[0]), "light_model", lm)
            row("light-model.json", "den-light@" + sha(wt, "rung-8-stage1"), "schemas.json",
                "OK" if vcode == 0 else "SCHEMA", vout.replace("\n", " | ")[:500])
            c2, t2 = run([sys.executable, "-B", ccli, "--out", out / "core-with-stage1-model", "--facts",
                          out / "interp" / "la-fact-log-1-seam.jsonl", "--assume-occupied", "--policies", "need",
                          "incumbent", "decide", "--light-model", lm])
            row("light-model.json -> den-core-live decide (--light-model)", "den-light@" + sha(wt, "rung-8-stage1"),
                "den-core-live@" + sha(wt, "den-core-live"), "OK" if c2 == 0 else "REFUSED",
                f"exit {c2}: " + (t2.splitlines() or [""])[-1][:240])
            c3, t3 = run([sys.executable, "-B", HERE / "probe_verify_model.py", wt, lm])
            bad = [l for l in t3.splitlines() if l.startswith("den-verify LightModel")]
            row("light-model.json -> den-verify LightModel adapter", "den-light@" + sha(wt, "rung-8-stage1"),
                "den-verify@" + sha(wt, "rung-10-verify"), "REFUSED" if any("Error" in l for l in bad) else "OK",
                " / ".join(bad)[:400])
        for line in text.splitlines():
            if line.startswith("verify.py run --decisions"):
                okv = ": exit 0:" in line
                row("den-core decisions -> den-verify (" + ("log.jsonl" if "log.jsonl" in line else
                    "decision.jsonl unwrapped by the harness") + ")", "den-core-live@" + sha(wt, "den-core-live"),
                    "den-verify@" + sha(wt, "rung-10-verify"), "OK" if okv else "REFUSED", line[:300])
    else:
        has_predict = any("def predict" in p.read_text(encoding="utf-8", errors="replace")
                          for base in (wt / "rung-8-stage0" / "tools" / "den-light", l8) if base.exists()
                          for p in base.glob("*.py"))
        row("need -> decision through light model predict()", "den-light@" + sha(wt, "rung-8-stage0"),
            "den-core-live coordinator@" + sha(wt, "den-core-live"), "MISSING-TOOL",
            f"rung-8-stage1 checkout: {'present' if l8.exists() else 'absent (branch not pushed)'}; any predict() in "
            f"den-light: {has_predict}; den-core-live decide.py reads no light_model ('no lux or light model')")
        files = sorted(p.name for p in vdir.glob("*")) if vdir.exists() else []
        row("decision -> verify", "den-core-live@" + sha(wt, "den-core-live"), "den-verify@" + sha(wt, "rung-10-verify"),
            "MISSING-TOOL", f"tools/den-verify holds only {files}")
    files = sorted(p.name for p in pdir.glob("*")) if pdir.exists() else []
    if (pdir / "photometry.py").exists():
        row("photometry -> light_state/k_L", "den-photometry@" + sha(wt, "rung-7-photometry"), "den-verify",
            "MISSING-TOOL", "photometry.py present but not exercised here: --capture needs a camera day folder and a "
                            "Stage 0 sweep; no light_state.jsonl, so verify ran with an empty light-states file")
    else:
        row("photometry -> light_state/k_L", "den-photometry@" + sha(wt, "rung-7-photometry"), "den-light / den-verify",
            "MISSING-TOOL", f"tools/den-photometry holds only {files}")


def labels(wt, out, take_folder):
    ldir = wt / "den-label" / "tools" / "den-label"
    tag = "den-label@" + sha(wt, "den-label")
    takes_root = take_folder.parent
    code, text = run([sys.executable, "-B", ldir / "label.py", "sessions", "--takes", takes_root, "--n1",
                      out / "n1", "--no-browser"])
    row("take -> den-label sessions", "take.py@" + sha(wt, take_wt(wt)), tag,
        "OK" if code == 0 else "REFUSED", " / ".join(text.splitlines()[-3:])[:300])
    code2, text2 = run([sys.executable, "-B", ldir / "label.py", "status", "--takes", takes_root, "--n1", out / "n1",
                        "--no-browser"])
    row("den-label status", tag, tag, "OK" if code2 == 0 else "REFUSED", " / ".join(text2.splitlines()[-3:])[:300])
    n2 = wt / "den-n2" / "tools" / "den-n2" / "score.py"
    if n2.exists():
        c, t = run([sys.executable, "-B", n2, "--split", "development", "--takes", takes_root, "--n1", out / "n1",
                    "--n2", out / "n2"])
        row("labels -> N2 scorer", tag, "den-n2@" + sha(wt, "den-n2"), "OK" if c == 0 else "REFUSED",
            f"exit {c}: " + (t.splitlines() or [""])[-1][:240] + " (no staged take, so N1 froze nothing)")
    else:
        row("labels -> N2 scorer", tag, "den-n2@" + sha(wt, "den-n2"), "MISSING-TOOL",
            "claude/den-n2 does not exist on origin")


# ---------------------------------------------------------------- encoding probes
def ps51(src, dst):
    """What PowerShell 5.1 `Get-Content a | ... > b` or `cmd > b` writes: UTF-16LE with a BOM, CRLF."""
    text = Path(src).read_text(encoding="utf-8")
    Path(dst).write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-16"))
    return Path(dst)


def encoding_probes(wt, out, take_folder, det_rows, obs_rm, fx, facts):
    enc = out / "enc"
    enc.mkdir(exist_ok=True)
    obs_dir = wt / "rung-2-observe" / "tools" / "den-observe"
    bdir = wt / "rung-5-bundle" / "tools" / "den-bundle"
    cdir = wt / "den-core-live" / "tools" / "den-core"
    cases = []
    if det_rows and obs_rm:
        d16 = ps51(det_rows, enc / "detections-utf16.jsonl")
        code, text = run([sys.executable, "-B", obs_dir / "replay.py", "--take", take_folder, "--room-model", obs_rm,
                          "--detections", d16, "--out", enc / "obs-d16"])
        cases.append(("detector rows", "den-observe@" + sha(wt, "rung-2-observe"), code, text))
        r16 = ps51(obs_rm, enc / "room-model-utf16.json")
        code, text = run([sys.executable, "-B", obs_dir / "replay.py", "--take", take_folder, "--room-model", r16,
                          "--detections", det_rows, "--out", enc / "obs-r16"])
        cases.append(("room model", "den-observe@" + sha(wt, "rung-2-observe"), code, text))
    if fx is not None:
        o16 = ps51(fx / "crossing" / "observations.jsonl", enc / "obs-utf16.jsonl")
        t16 = ps51(fx / "crossing" / "tracks.jsonl", enc / "tracks-utf16.jsonl")
        code, text = run([sys.executable, "-B", bdir / "den_bundle.py", "route", "--observations", o16, "--tracks",
                          t16, "--from", "2.5", "--to", "13", "--requests", enc / "rq.jsonl", "--bundles",
                          enc / "bu.jsonl"])
        cases.append(("observations + tracks", "den-bundle@" + sha(wt, "rung-5-bundle"), code, text))
    if facts is not None:
        f16 = ps51(facts, enc / "la-fact-log-1-utf16.jsonl")
        code, text = run([sys.executable, "-B", cdir / "run.py", "--out", enc / "core16", "--facts", f16,
                          "--assume-occupied", "--policies", "need", "incumbent", "decide"])
        cases.append(("la-fact-log-1 facts", "den-core-live@" + sha(wt, "den-core-live"), code, text))
    for what, consumer, code, text in cases:
        last = (text.splitlines() or [""])[-1][:240]
        row(f"PowerShell 5.1 '>' (UTF-16LE+BOM, CRLF) {what}", "owner redirection", consumer,
            "OK" if code == 0 else "REFUSED", f"exit {code}: {last}")


# ---------------------------------------------------------------- main
def table():
    w = [max(len(str(r[i])) for r in ROWS + [("link", "producer", "consumer", "status", "")]) for i in range(4)]
    w = [min(x, 60) for x in w]
    lines = ["| link | producer@sha -> consumer@sha | status | detail |", "|---|---|---|---|"]
    for link, p, c, s, d in ROWS:
        lines.append(f"| {link} | {p} -> {c} | {s} | {str(d).replace('|', '/')[:400]} |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--wt-root", required=True)
    ap.add_argument("--out")
    ap.add_argument("--evening-s", type=float, default=150.0, help="camera seconds of activity (default 150)")
    ap.add_argument("--schemas", help="schemas.json (default: <wt-root>/live-records/docs/architecture/live-v1/)")
    a = ap.parse_args(argv)
    wt = Path(a.wt_root).resolve()
    out = Path(a.out or tempfile.mkdtemp(prefix="seam-")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    schemas = Path(a.schemas) if a.schemas else wt / "live-records/docs/architecture/live-v1/schemas.json"
    if not schemas.exists():
        cands = sorted(wt.glob("*/docs/architecture/live-v1/schemas.json"))
        schemas = cands[0]
    log(f"wt-root {wt}; out {out}; schemas {schemas}")
    SCHEMAS.append(str(schemas))
    # The validator must pass on the spec's own examples (schemas.json records[].example) before it judges a tool.
    exdir = out / "spec-examples"
    exdir.mkdir(exist_ok=True)
    spec = json.loads(Path(schemas).read_text(encoding="utf-8"))
    results = []
    for r in spec["records"]:
        ex = r.get("example")
        ex = ex if isinstance(ex, list) else [ex]
        path = exdir / f"{r['id']}.jsonl"
        path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in ex if isinstance(e, dict)),
                        encoding="utf-8")
        p = subprocess.run([sys.executable, "-B", str(VALIDATOR), str(schemas), r["id"], str(path)],
                           capture_output=True, text=True)
        results.append((r["id"], p.returncode))
    bad = [rid for rid, c in results if c]
    row("schemas.json examples -> schema_validator.py", "live-records@" + sha(wt, "live-records"), "validator",
        "OK" if not bad else "SCHEMA", f"{len(results) - len(bad)} of {len(results)} examples pass; failing: {bad}")
    takes, seen = make_takes(wt, out, a.evening_s)
    rm = register(wt, out, takes["evening"], seen, schemas)
    det = detector_rows(wt, out, takes["evening"], a.evening_s)
    obs = observe(wt, out, takes["evening"], rm, det, schemas)
    if obs is not None:
        observation_checks(wt, out, obs, rm, takes["evening"])
    trk = track(wt, out, obs, schemas)
    bun = bundle(wt, out, obs, trk, schemas)
    core_dir = None
    if bun is not None:
        facts, bundles = fake_interpreter(wt, out, bun)
        core_dir = core(wt, out, facts, bundles, schemas, "den-core-live")
        core(wt, out, facts, bundles, schemas, "den-core")
    obs_rm = out / "room-model-den-observe-synth.json"
    encoding_probes(wt, out, takes["evening"], det, obs_rm if obs_rm.exists() else None,
                    out / "bundle-fixtures" if (out / "bundle-fixtures").exists() else None,
                    out / "interp" / "la-fact-log-1-seam.jsonl" if (out / "interp").exists() else None)
    light_and_verify(wt, out, core_dir)
    labels(wt, out, takes["evening"])
    print()
    print(table())
    (out / "seam-table.md").write_text(table() + "\n", encoding="utf-8")
    (out / "seam-log.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    print(f"\nout: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
