"""verify_empty-exports-accepted: independent check with the checkout's own fixture (test_capture.evening(), the suite's
reference hour) that capture.py runs to a verdict when the command or bulb export is an empty file, and what the
owner's printed summary shows. Also prints the phi disclosure line used by the within-budget finding.
Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 -B verify_empty-exports-accepted.py <root of the claude/correction-capture checkout>
"""
import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
import test_capture as tc  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="verify-empty-"))
try:
    [status], [lights], [commands] = tc.evening().write(tmp)
    empty = tmp / "empty.jsonl"
    empty.write_text("", "ascii")
    for name, c, l in (("all three", commands, lights), ("empty commands", empty, lights),
                       ("empty bulbs", commands, empty)):
        out = tmp / ("out-" + name.replace(" ", "-"))
        printed = io.StringIO()
        try:
            with contextlib.redirect_stdout(printed):
                capture.main(["--status", str(status), "--lights", str(l), "--commands", str(c), "--out", str(out)])
            code = "ok"
        except (SystemExit, ValueError) as err:
            code = "refused: %s" % err
        text = printed.getvalue()
        lines = [x.strip() for x in text.splitlines() if x.strip().startswith(("people", "every_hold", "by source"))]
        print("RESULT %-15s %s | %s" % (name, code, " | ".join(lines[:3])))
        if name != "all three" and code == "ok":
            print("FAIL %s: a verdict is printed; summary mentions commands/bulb rows: %s"
                  % (name, any(w in x for x in text.splitlines() if not x.startswith("wrote") for w in ("commands", "bulb_rows", "bulb rows"))))
        if name == "all three":
            print("RESULT phi line in summary: %s" % [x for x in text.splitlines() if x.startswith("phi_hat")])
finally:
    shutil.rmtree(tmp, True)
