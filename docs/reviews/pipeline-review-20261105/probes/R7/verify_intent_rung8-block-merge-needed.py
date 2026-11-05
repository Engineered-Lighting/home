#!/usr/bin/env python3
"""verify_intent_rung8-block-merge-needed (intent-lens verifier, finding rung8-block-merge-needed).

What it checks: rung 7's branch carries its own copy of rung 8's tools/den-light/METHOD-V1.md, with the
Stage 0 block's `open` marks still on.  Rung 8's README has the agent take those marks off "in one commit"
on claude/rung-8-stage0, and the owner's passing Stage 0 sweep then records that closed block's sha256.
This probe builds exactly that situation in memory and in temp folders, and runs rung 7's --new-day
(photometry.new_day) the way README step 4 would:

  case A  rung 7's own marks closed, rung 8's copy on this branch unchanged (no merge)   -> expect a refusal
  case B  as A, but someone strips only the open tuple (block text unchanged)           -> expect a refusal
  case C  the rung-8 closing text present on this branch (as after a merge)             -> expect a day folder

    python3 -B verify_intent_rung8-block-merge-needed.py <branch-root>

Synthetic Stage 0 response (the tests' own helper), temp folders only, no camera and no other host.
The branch checkout is only imported from, never written (bytecode writing is off).
"""
import re
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools/den-photometry"))

import card  # noqa: E402  (puts tools/den-light on the path)
import meter  # noqa: E402
import photometry  # noqa: E402
import synthetic as syn  # noqa: E402


def closed_rung8_text(text):
    """Rung 8's METHOD-V1 as the closing commit would leave it: each `# open...` comment in the
    stage0 block becomes `# confirmed...` (values unchanged)."""
    head, sep, rest = text.partition("```method-v1 stage0\n")
    block, fence, tail = rest.partition("```")
    block = re.sub(r"#\s*open:?", "# confirmed:", block)
    return head + sep + block + fence + tail


def attempt(label, method, den, stage0_den, tmp):
    root = tmp / label / "den-photometry"
    root.mkdir(parents=True)
    token = tmp / label / "private" / "camera-token.txt"
    token.parent.mkdir()
    token.write_text(syn.TOKEN)
    stage0 = tmp / label / "den-light" / "stage0-20261108" / "sweep-1"
    stage0.mkdir(parents=True)
    import json
    (stage0 / "response.json").write_text(json.dumps(syn.stage0_response(stage0_den)), encoding="utf-8")
    try:
        folder = photometry.new_day(root, stage0, token, method, den, "20261110")
        return f"MADE {folder.name}"
    except photometry.Refused as exc:
        return f"REFUSED {exc}"


def main():
    branch_den = meter.load_method()  # rung 8's METHOD-V1 as this branch carries it
    print(f"INFO this branch's rung 8 METHOD-V1 open lines: {len(branch_den.open)} ({', '.join(branch_den.open[:4])}, ...)")
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        closed_file = tmp / "rung8-closed" / "METHOD-V1.md"
        closed_file.parent.mkdir()
        closed_file.write_text(closed_rung8_text((ROOT / "tools/den-light/METHOD-V1.md").read_text(encoding="utf-8")),
                               encoding="utf-8")
        rung8_closed = meter.load_method(closed_file)  # what claude/rung-8-stage0 holds after its closing commit
        print(f"INFO rung-8 closed copy open lines: {len(rung8_closed.open)}; block sha differs from this branch's: "
              f"{rung8_closed.block_sha256 != branch_den.block_sha256}")
        method = syn.photometry_method()  # rung 7's own marks closed (test values)
        a = attempt("A", method, branch_den, rung8_closed, tmp)
        b = attempt("B", method, meter.Method(branch_den.values, (), branch_den.file_sha256, branch_den.block_sha256),
                    rung8_closed, tmp)
        c = attempt("C", method, rung8_closed, rung8_closed, tmp)
    print("RESULT A (no merge):            " + a)
    print("RESULT B (open tuple stripped): " + b)
    print("RESULT C (closing text merged): " + c)
    ok = a.startswith("REFUSED rung 8's METHOD-V1 still has open lines") and b.startswith("REFUSED") and c.startswith("MADE")
    print("RESULT " + ("REPRODUCED: step 4 refuses until rung 8's closing commit is on this branch" if ok
                       else "NOT REPRODUCED"))
    print("INFO refusal text mentions a merge or the rung-8 branch: "
          + str(any(w in a.lower() for w in ("merge", "rung-8-stage0", "git pull"))))


if __name__ == "__main__":
    main()
