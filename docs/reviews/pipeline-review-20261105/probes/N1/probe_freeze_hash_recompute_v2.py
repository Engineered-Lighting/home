#!/usr/bin/env python3
"""Independent recompute of N1's freeze hashes, and tamper detection by `verify`.

What it checks (synthetic takes only, temp dirs only, no network):
  1. Builds four synthetic staged takes (two hard-a, two hard-b) and a
     commissioning take with R's own test helpers, runs den-label's real flow
     (sessions, surfaces, boxes, labels, re-mark, freeze fixture, freeze labels).
  2. Recomputes, with this file's own code (hashlib + its own canonical-JSON
     encoder, not n1.py), the fixture hash, the labels hash, every per-file
     sha256 the manifests list, the canonical form of each manifest, and the
     padded boxes rows; compares them with the freeze lines and the printed
     EvalRecord fields.
  3. Tampers with copies of the frozen folders and runs `Workspace.verify`
     (and `label.py verify`) to see which edits are detected:
       T1 one label edited in sessions/<take>.json
       T2 the same edit plus the manifest re-hashed (with and without FREEZES lines)
       T3 a frozen file deleted
       T4 an extra file added
       T5 the manifest rewritten non-canonically (same content)
       T6 one box row edited
       T7 an interrupted freeze (folder made, manifest never written)
Prints PASS/FAIL/RESULT lines.

Run:  python3 -B probe_freeze_hash_recompute_v2.py <den-label branch root>
"""
import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import label  # noqa: E402
import n1  # noqa: E402
import take  # noqa: E402
import test_den_take as R  # noqa: E402

BASE = 1_793_016_000.0
DAY = 86400.0
fails = []


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok:
        fails.append(what)


def my_canonical(value):
    # independent re-implementation: sorted keys, (",", ":"), ASCII escapes, final LF
    def enc(v):
        if v is None:
            return "null"
        if v is True:
            return "true"
        if v is False:
            return "false"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, float):
            return repr(v)
        if isinstance(v, str):
            out = ['"']
            for ch in v:
                o = ord(ch)
                if ch == '"':
                    out.append('\\"')
                elif ch == "\\":
                    out.append("\\\\")
                elif ch == "\n":
                    out.append("\\n")
                elif ch == "\r":
                    out.append("\\r")
                elif ch == "\t":
                    out.append("\\t")
                elif ch == "\b":
                    out.append("\\b")
                elif ch == "\f":
                    out.append("\\f")
                elif o < 0x20 or o > 0x7e:
                    if o > 0xFFFF:
                        o -= 0x10000
                        out.append("\\u%04x\\u%04x" % (0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)))
                    else:
                        out.append("\\u%04x" % o)
                else:
                    out.append(ch)
            out.append('"')
            return "".join(out)
        if isinstance(v, list):
            return "[" + ",".join(enc(x) for x in v) + "]"
        if isinstance(v, dict):
            return "{" + ",".join(enc(k) + ":" + enc(v[k]) for k in sorted(v)) + "}"
        raise TypeError(type(v))
    return (enc(value) + "\n").encode("ascii")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def frames_of(ws, take_id):
    state = ws.state(take_id, allow_commissioning=True)
    return n1.usable_frames(state, n1.read_frames(state.folder))


def at(ws, take_id, t):
    return next(f for f in frames_of(ws, take_id) if f["t"] >= t)


def interval(ws, take_id, t0, t1, band="fine", case="sorting_mail"):
    return {"onset_n": at(ws, take_id, t0)["n"], "onset_kind": "touch", "offset_n": at(ws, take_id, t1)["n"],
            "offset_kind": "release", "band": band, "case": case,
            "sure": {"onset": True, "offset": True, "band": True}, "by": "owner"}


def build(tmp):
    takes = tmp / "takes"
    takes.mkdir()
    template = R.make_take(tempfile.mkdtemp(prefix="n1v2-tpl-", dir=tmp), activity=60.0, commissioning=False)
    ctemplate = R.make_take(tempfile.mkdtemp(prefix="n1v2-ctpl-", dir=tmp), activity=30.0, commissioning=True)
    names = [("a1", "hard-a"), ("b1", "hard-b"), ("a2", "hard-a"), ("b2", "hard-b")]
    for i, (name, script) in enumerate(names):
        folder = takes / name
        shutil.copytree(template, folder)
        record = json.loads((folder / "take.json").read_text(encoding="utf-8"))
        record["script"], record["started_unix"] = script, BASE + (i // 2) * DAY + (i % 2) * 3600
        take.write_json(folder / "take.json", record)
        R.run_check(folder, typed="none")
    shutil.copytree(ctemplate, takes / "c1")
    R.run_check(takes / "c1", typed="none")
    ws = n1.Workspace(takes, takes / "n1")
    ws.scan()
    p = label.Pass(ws, "surfaces", "c1", out=io.StringIO())
    p.save({"reference_n": p.usable[0]["n"], "surfaces": [
        {"id": "desk", "type": "desk", "height_cm": 74, "polygon": [[10, 200], [200, 200], [200, 300]]},
        {"id": "handheld", "type": "handheld", "polygon": None, "height_cm": {"seated": 85, "standing": 110}}]})
    with mock.patch.object(n1, "now", return_value=BASE + 5 * DAY):
        for name, _ in names:
            p = label.Pass(ws, "boxes", name, out=io.StringIO())
            fr = p.usable
            p.save({"keyframes": [[fr[0]["n"], [100, 50, 300, 340]]], "audited": [[fr[0]["n"], fr[-1]["n"]]]})
            p = label.Pass(ws, "labels", name, out=io.StringIO())
            p.save({"intervals": [interval(ws, name, 40, 50), interval(ws, name, 60, 66, "casual", "wiping")],
                    "spans": [{"kind": "quiet", "case": "idle_seated", "start_n": at(ws, name, 51)["n"],
                               "end_n": at(ws, name, 58)["n"], "by": "owner"}], "end_to_end": True})
    with mock.patch.object(n1, "now", return_value=BASE + 9 * DAY):
        p = label.Pass(ws, "remark", out=io.StringIO())
        p.save({"intervals": [interval(ws, p.take, 40.5, 50.2)], "spans": [], "end_to_end": True})
    with mock.patch.object(n1, "now", return_value=BASE + 10 * DAY):
        fixture_line, _ = ws.freeze_fixture()
        labels_line, record, _ = ws.freeze_labels()
    return takes, ws, fixture_line, labels_line, record


def line_sha(line):
    return line.split(" sha256=", 1)[1].split()[0]


def recompute(ws, fixture_line, labels_line, record):
    fdir, ldir = ws.root / "fixture-v1", ws.root / "labels-v1"
    for d, mname, line, key in ((fdir, "fixture.json", fixture_line, "fixture"), (ldir, "labels.json", labels_line, "labels")):
        raw = (d / mname).read_bytes()
        manifest = json.loads(raw)
        mine = sha(raw)
        check(mine == line_sha(line), f"{key}: my sha256 of {mname} {mine[:12]} equals the freeze line's")
        check(mine == record["gold_set"]["n1"][key]["hash"], f"{key}: equals the printed gold_set.n1.{key}.hash")
        check(my_canonical(manifest) == raw, f"{key}: {mname} is canonical by my own encoder")
        listed = {}
        if key == "fixture":
            listed[manifest["surface_map"]["file"]] = manifest["surface_map"]["sha256"]
            for s in manifest["sessions"]:
                listed[s["boxes"]["file"]] = s["boxes"]["sha256"]
        else:
            listed[manifest["split"]["file"]] = manifest["split"]["sha256"]
            listed[manifest["remark"]["file"]] = manifest["remark"]["sha256"]
            for s in manifest["sessions"]:
                listed[s["file"]] = s["sha256"]
        on_disk = {p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()} - {mname}
        check(set(listed) == on_disk, f"{key}: the manifest lists exactly the folder's other files ({len(on_disk)})")
        bad = [rel for rel, h in listed.items() if sha((d / rel).read_bytes()) != h]
        check(not bad, f"{key}: every listed file's sha256 recomputes ({len(listed)} files)")
    # padded boxes, by hand: tight [100,50,300,340], pad 0.2 / 16 px -> [60, 0, 340, 360]
    rows = (fdir / "boxes" / "a1.csv").read_text(encoding="ascii").splitlines()[1:]
    boxes = {tuple(r.split(",")[2:]) for r in rows}
    check(boxes == {("60", "0", "340", "360")}, f"fixture: every a1 row holds the hand-padded box 60,0,340,360 ({boxes})")


def verify(ws, text):
    return ws.verify(text)


def tamper_case(base_takes, name, edit, freezes_text):
    tmp = Path(tempfile.mkdtemp(prefix=f"n1v2-{name}-"))
    takes = tmp / "takes"
    shutil.copytree(base_takes, takes)
    for p in takes.rglob("*"):
        with contextlib.suppress(OSError):
            os.chmod(p, stat.S_IRWXU)
    ws = n1.Workspace(takes, takes / "n1")
    edit(ws)
    try:
        ok, lines = ws.verify(freezes_text)
        outcome = ("ok" if ok else "flagged", lines)
    except Exception as exc:  # noqa: BLE001
        outcome = ("crash", [f"{type(exc).__name__}: {exc}"])
    # and through label.py's own command, for the exit code
    buf = io.StringIO()
    freezes = tmp / "FREEZES.md"
    freezes.write_text(freezes_text, encoding="utf-8")
    try:
        with mock.patch.object(label, "FREEZES", freezes):
            code = label.main(["verify", "--takes", str(takes)], out=buf)
        cli = (code, buf.getvalue().strip().replace("\n", " | ")[:300])
    except Exception as exc:  # noqa: BLE001
        cli = ("uncaught " + type(exc).__name__, str(exc)[:200])
    shutil.rmtree(tmp, ignore_errors=True)
    return outcome, cli


def main():
    tmp = Path(tempfile.mkdtemp(prefix="n1v2-freeze-"))
    guard = mock.patch.object(take.http.client, "HTTPConnection", R.LoopbackOnly)
    guard.start()
    try:
        takes, ws, fixture_line, labels_line, record = build(tmp)
        print("RESULT freeze lines:\n  " + fixture_line + "\n  " + labels_line)
        print("RESULT printed EvalRecord fields: " + json.dumps(record, sort_keys=True))
        recompute(ws, fixture_line, labels_line, record)
        logged = f"{fixture_line}\n{labels_line}\n"

        def edit_label(ws_, rehash=False):
            d = ws_.root / "labels-v1"
            path = d / "sessions" / "a2.json"
            rec = json.loads(path.read_bytes())
            rec["intervals"][0]["band"] = "casual" if rec["intervals"][0]["band"] == "fine" else "fine"
            path.write_bytes(n1.canonical(rec))
            if rehash:
                man = json.loads((d / "labels.json").read_bytes())
                for s in man["sessions"]:
                    if s["take"] == "a2":
                        s["sha256"] = sha(path.read_bytes())
                (d / "labels.json").write_bytes(n1.canonical(man))

        cases = [
            ("T1-label-edited", lambda w: edit_label(w), logged, True),
            ("T2a-label-edited-manifest-rehashed-logged", lambda w: edit_label(w, True), logged, True),
            ("T2b-label-edited-manifest-rehashed-not-yet-logged", lambda w: edit_label(w, True), "", False),
            ("T3-remark-json-deleted", lambda w: os.remove(w.root / "labels-v1" / "remark.json"), logged, True),
            ("T4-extra-file", lambda w: (w.root / "labels-v1" / "sessions" / "zz.json").write_bytes(b"{}\n"), logged, True),
            ("T5-manifest-non-canonical-not-yet-logged",
             lambda w: (w.root / "labels-v1" / "labels.json").write_text(
                 json.dumps(json.loads((w.root / "labels-v1" / "labels.json").read_bytes()), indent=1, sort_keys=True),
                 encoding="ascii"), "", True),
            ("T6-box-row-edited", lambda w: (w.root / "fixture-v1" / "boxes" / "b2.csv").write_bytes(
                (w.root / "fixture-v1" / "boxes" / "b2.csv").read_bytes().replace(b",60,0,340,360\n", b",61,0,340,360\n", 1)),
             logged, True),
            ("T7-interrupted-fixture-freeze", lambda w: (w.root / "fixture-v2" / "boxes").mkdir(parents=True), logged, True),
            ("T8-fixture-json-deleted", lambda w: os.remove(w.root / "fixture-v1" / "fixture.json"), logged, True),
        ]
        for name, edit, text, must_flag in cases:
            (kind, lines), cli = tamper_case(takes, name, edit, text)
            print(f"RESULT {name}: verify -> {kind}: {' | '.join(lines)[:400]}")
            print(f"RESULT {name}: label.py verify exit/out -> {cli[0]} :: {cli[1]}")
            if must_flag:
                check(kind == "flagged" and cli[0] == 1,
                      f"{name}: verify reports the problem (no crash) and label.py verify exits 1")
    finally:
        guard.stop()
        for p in tmp.rglob("*"):
            with contextlib.suppress(OSError):
                os.chmod(p, stat.S_IRWXU)
        shutil.rmtree(tmp, ignore_errors=True)
    print("RESULT", "FAIL" if fails else "PASS", f"({len(fails)} failed checks)")
    for f in fails:
        print("  failed:", f)


if __name__ == "__main__":
    main()
