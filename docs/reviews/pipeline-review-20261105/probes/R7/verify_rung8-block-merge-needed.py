#!/usr/bin/env python3
"""verify_rung8-block-merge-needed: after rung 8's open marks are closed on rung 8's branch and a Stage 0
sweep passes under the closed block, does rung 7's --new-day (on this branch, rung 7's own marks closed)
refuse until rung 8's exact closing commit is merged here, and does the refusal say so?

    python3 -B verify_rung8-block-merge-needed.py <branch-root>

Works on copies in a temp dir only (synthetic Stage 0 response, no camera, no network). Cases:
  A  rung 7 marks closed here, rung 8's METHOD here still open (as at the pinned commit)
  B  as A, plus rung 8's closed METHOD copied in byte for byte (the merge)
  C  as A, but rung 8's marks closed here by a different edit (comments dropped instead)
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()


def close_block(text, info, mode):
    head, body, tail = re.split(r"(?ms)(^```" + re.escape(info) + r"\n.*?^```)", text, maxsplit=1)
    lines = []
    for line in body.split("\n"):
        if mode == "drop":
            line = re.sub(r"\s*#\s*open.*$", "", line)
        else:
            line = re.sub(r"#(\s*)open", r"#\1owner-confirmed", line)
        lines.append(line)
    body = "\n".join(lines)
    body = (body.replace("k_L_scope =      ", "k_L_scope = single", 1)
                .replace("meter_tolerance_lx =     ", "meter_tolerance_lx = 10 ", 1)
                .replace("meter_class =    ", "meter_class = class-B", 1))
    return head + body + tail


MAKE_S0 = r'''
import json, sys
from pathlib import Path
import card, meter, synthetic as syn  # card puts tools/den-light on the path
closed = meter.load_method(Path(sys.argv[1]))
assert not closed.open, closed.open
Path(sys.argv[2]).write_text(json.dumps(syn.stage0_response(closed)), encoding="utf-8")
print("stage0 block", closed.block_sha256[:12])
'''


def run(tool, args):
    p = subprocess.run([sys.executable, "-B", str(tool / "photometry.py")] + args, capture_output=True, text=True,
                       timeout=600)
    return p.returncode, (p.stdout + p.stderr).strip()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="r7-merge-"))
    r7 = tmp / "r7"
    for rel in ("tools/den-photometry", "tools/den-light", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / rel, r7 / rel, ignore=shutil.ignore_patterns("__pycache__"))
    tool, den = r7 / "tools/den-photometry", r7 / "tools/den-light"
    m7 = tool / "METHOD-V1.md"
    m7.write_text(close_block(m7.read_text(encoding="utf-8"), "method-v1 photometry", "rename"), encoding="utf-8")
    open8 = (den / "METHOD-V1.md").read_text(encoding="utf-8")
    r8 = tmp / "r8-METHOD-V1.md"  # rung 8's branch after its closing commit
    r8.write_text(close_block(open8, "method-v1 stage0", "rename"), encoding="utf-8")
    s0 = tmp / "den-light" / "stage0-20261108" / "sweep-1"
    s0.mkdir(parents=True)
    p = subprocess.run([sys.executable, "-B", "-c", MAKE_S0, str(r8), str(s0 / "response.json")], cwd=tool,
                       capture_output=True, text=True)
    print("SETUP " + (p.stdout.strip() or p.stderr.strip()[-400:]))
    token = tmp / "private" / "camera-token.txt"
    token.parent.mkdir()
    token.write_text("t" * 32)
    code, out = run(tool, ["--self-check"])
    print(f"SELF-CHECK exit {code}: {out}")
    results = {}
    for case in ("A", "B", "C"):
        if case == "B":
            shutil.copyfile(r8, den / "METHOD-V1.md")
        elif case == "C":
            (den / "METHOD-V1.md").write_text(close_block(open8, "method-v1 stage0", "drop"), encoding="utf-8")
        root = tmp / f"photometry-{case}"
        root.mkdir()
        code, out = run(tool, ["--new-day", str(root), "--stage0-sweep", str(s0), "--token-file", str(token),
                               "--date", "20261110"])
        results[case] = (code, out)
        print(f"CASE {case} exit {code}: {out}")
    a, b, c = results["A"], results["B"], results["C"]
    print("RESULT A refuses on rung 8's open lines: " + ("YES" if a[0] == 2 and "rung 8's METHOD-V1 still has open lines" in a[1] else "NO"))
    print("RESULT A's refusal names a merge or rung 8's branch: " + ("YES" if re.search(r"merge|rung-8-stage0|pull", a[1]) else "NO"))
    print("RESULT B (exact merge) makes the day: " + ("YES" if b[0] == 0 and "Made" in b[1] else "NO"))
    print("RESULT C (closed here by another edit) refuses on the block: " + ("YES" if c[0] == 2 and "Stage 0 block" in c[1] else "NO"))
    shutil.rmtree(tmp)


main()
