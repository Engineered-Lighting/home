#!/usr/bin/env python3
"""probe_freeze_hash: recompute N1's freeze hashes independently and check tamper detection.

What it checks (synthetic takes only, temp dirs only, no network beyond what R's helpers guard):
  1. Builds 4 staged synthetic takes + 1 commissioning take with the branch's own test helpers,
     runs boxes, labels and the re-mark, then `freeze fixture` and `freeze labels`.
  2. Recomputes, with its OWN canonical-JSON and sha256 code (not n1.py's):
       - sha256(fixture.json) and sha256(labels.json) against the printed freeze lines;
       - every file a manifest lists against its sha256, and that no unlisted file exists;
       - that each manifest is canonical (sorted keys, no spaces, ASCII, one final newline);
       - protocol=, split= and remark= hashes in the labels line.
  3. Tampers with copies of the N1 folder and runs label.py's verify (ws.verify) on each:
       a. one byte of a frozen label file           -> must FAIL
       b. a label edit plus a consistently re-hashed manifest, line logged -> must FAIL
       c. same as b, but no FREEZES.md line yet (documented design: not detectable) -> reported
       d. one fixture box row                        -> must FAIL
       e. an extra file in a frozen folder           -> must FAIL
       f. the manifest file itself deleted           -> must FAIL cleanly (no traceback)
       g. the logged line indented as a list item ("- den-n1 ...") with a tampered manifest -> reported
Run:
    python3 probe_freeze_hash.py /path/to/den-label-checkout
Prints PASS/FAIL/RESULT lines.
"""
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import traceback
from pathlib import Path
from unittest import mock

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import test_den_label as T  # noqa: E402  the branch's synthetic helpers
import n1  # noqa: E402
import label  # noqa: E402

FAILS = []


def say(ok, what):
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)


def my_canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unlock(tree):
    for p in Path(tree).rglob("*"):
        try:
            os.chmod(p, stat.S_IRWXU)
        except OSError:
            pass


class Builder(T.Base):
    def runTest(self):
        pass


def build(tmp):
    b = Builder()
    b.tmp = tmp
    b.takes = tmp / "takes"
    b.takes.mkdir()
    b.n1root = b.takes / "n1"
    names = (("a1", "hard-a"), ("b1", "hard-b"), ("a2", "hard-a"), ("b2", "hard-b"))
    for i, (name, script) in enumerate(names):
        T.staged_take(b.takes, name, script, T.BASE_UNIX + (i // 2) * T.DAY + (i % 2) * 3600)
    T.commissioning_take(b.takes)
    ws = n1.Workspace(b.takes, b.n1root)
    ws.scan()
    b.surfaces_done(ws, "c1")
    with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 5 * T.DAY):
        for name, _ in names:
            b.boxes_done(ws, name)
            b.labels_done(ws, name)
    with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 9 * T.DAY):
        p = label.Pass(ws, "remark", out=io.StringIO())
        p.save({"intervals": [b.interval(ws, "b1", 40.5, 50.2)], "spans": [], "end_to_end": True})
    with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 10 * T.DAY):
        fline, _ = ws.freeze_fixture()
        lline, record, _ = ws.freeze_labels()
    return b, ws, fline, lline, record


def listed(manifest):
    if manifest["schema"] == "den-n1-fixture-1":
        out = {manifest["surface_map"]["file"]: manifest["surface_map"]["sha256"]}
        for s in manifest["sessions"]:
            out[s["boxes"]["file"]] = s["boxes"]["sha256"]
        return out
    out = {manifest["split"]["file"]: manifest["split"]["sha256"], manifest["remark"]["file"]: manifest["remark"]["sha256"]}
    for s in manifest["sessions"]:
        out[s["file"]] = s["sha256"]
    return out


def independent(folder, manifest_name, line):
    raw = (folder / manifest_name).read_bytes()
    manifest = json.loads(raw)
    say(my_canonical(manifest) == raw, f"{folder.name}: manifest is canonical by an independent canonicaliser")
    logged = re.search(r"sha256=([0-9a-f]{64})", line)[1]
    say(sha(raw) == logged, f"{folder.name}: sha256({manifest_name}) recomputed = {sha(raw)[:12]} equals the line's {logged[:12]}")
    files = listed(manifest)
    present = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()} - {manifest_name}
    say(present == set(files), f"{folder.name}: files on disk == files listed ({len(files)})")
    for rel, h in sorted(files.items()):
        say(sha((folder / rel).read_bytes()) == h, f"{folder.name}: {rel} sha256 recomputed matches the manifest")
    return manifest


def verify_copy(src_root, ws, mutate, freezes_text):
    tmp = Path(tempfile.mkdtemp(prefix="n1-probe-tamper-"))
    try:
        takes = tmp / "takes"
        shutil.copytree(src_root, takes)
        unlock(takes)
        mutate(takes / "n1")
        w = n1.Workspace(takes, takes / "n1")
        try:
            ok, lines = w.verify(freezes_text)
            return ok, lines, None
        except Exception as exc:  # noqa: BLE001
            return None, [], f"{type(exc).__name__}: {exc}"
    finally:
        unlock(tmp)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    T.setUpModule()
    tmp = Path(tempfile.mkdtemp(prefix="n1-probe-freeze-")).resolve()
    try:
        b, ws, fline, lline, record = build(tmp)
        print("RESULT fixture line:", fline)
        print("RESULT labels line:", lline)
        fdir, ldir = ws.root / "fixture-v1", ws.root / "labels-v1"
        independent(fdir, "fixture.json", fline)
        lman = independent(ldir, "labels.json", lline)
        proto = sha((ROOT / "tools" / "den-label" / "PROTOCOL-V1.md").read_bytes())
        say(f"protocol={proto}" in lline and f"protocol={proto}" in fline, "protocol= in both lines is sha256(PROTOCOL-V1.md)")
        say(f"split={sha((ldir / 'split.json').read_bytes())}" in lline, "split= is sha256 of the frozen split.json")
        say(f"remark={sha((ldir / 'remark.json').read_bytes())}" in lline, "remark= is sha256 of the frozen remark.json")
        say(record["gold_set"]["n1"]["fixture"]["hash"] == sha((fdir / "fixture.json").read_bytes()),
            "EvalRecord gold_set.n1.fixture.hash == sha256(fixture.json)")
        say(record["n_intervals"] == sum(len(json.loads((ldir / s["file"]).read_bytes())["intervals"]) for s in lman["sessions"]),
            "EvalRecord n_intervals == intervals counted in the frozen session files")
        good = f"{fline}\n{lline}\n"
        ok, lines, err = verify_copy(ws.takes, ws, lambda r: None, good)
        say(ok is True, f"untouched copy verifies OK: {lines}")

        def flip_byte(r):
            p = r / "labels-v1" / "sessions" / "a1.json"
            d = p.read_bytes().replace(b'"band":"fine"', b'"band":"casual"', 1)
            p.write_bytes(d)
        ok, lines, err = verify_copy(ws.takes, ws, flip_byte, good)
        say(ok is False, f"a. edited frozen label detected: {lines}")

        def consistent(r):
            p = r / "labels-v1" / "sessions" / "a1.json"
            rec = json.loads(p.read_bytes())
            rec["intervals"][0]["band"] = "casual"
            p.write_bytes(my_canonical(rec))
            mp = r / "labels-v1" / "labels.json"
            man = json.loads(mp.read_bytes())
            for s in man["sessions"]:
                if s["take"] == "a1":
                    s["sha256"] = sha(p.read_bytes())
            mp.write_bytes(my_canonical(man))
        ok, lines, err = verify_copy(ws.takes, ws, consistent, good)
        say(ok is False and any("differs from FREEZES.md" in l for l in lines),
            f"b. label edit with a re-hashed manifest detected against the logged line: {lines}")
        ok, lines, err = verify_copy(ws.takes, ws, consistent, "")
        print(f"RESULT c. same edit, no FREEZES.md line yet: ok={ok} lines={lines} (by design undetectable until the line is logged)")

        def box(r):
            p = r / "fixture-v1" / "boxes" / "a1.csv"
            rows = p.read_text().splitlines()
            parts = rows[5].split(",")
            parts[2] = str(int(parts[2]) + 1)
            rows[5] = ",".join(parts)
            p.write_text("\n".join(rows) + "\n")
        ok, lines, err = verify_copy(ws.takes, ws, box, good)
        say(ok is False and any("changed" in l for l in lines), f"d. edited fixture box row detected: {lines}")

        ok, lines, err = verify_copy(ws.takes, ws, lambda r: (r / "labels-v1" / "extra.txt").write_text("x"), good)
        say(ok is False and any("extra file" in l for l in lines), f"e. extra file detected: {lines}")

        ok, lines, err = verify_copy(ws.takes, ws, lambda r: (r / "fixture-v1" / "fixture.json").unlink(), good)
        say(ok is False and err is None, f"f. deleted manifest reported as a problem (ok={ok}, lines={lines}, crash={err})")

        indented = f"- {fline}\n- {lline}\n"
        ok, lines, err = verify_copy(ws.takes, ws, consistent, indented)
        print(f"RESULT g. tampered + manifest re-hashed, lines logged as list items: ok={ok} lines={lines}")
    finally:
        unlock(tmp)
        shutil.rmtree(tmp, ignore_errors=True)
        T.tearDownModule()
    print("RESULT", "FAIL" if FAILS else "PASS", f"({len(FAILS)} failing checks)")
    for f in FAILS:
        print("  failing:", f)


if __name__ == "__main__":
    main()
