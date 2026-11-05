"""verify_photometry-settings-counted-as-people: independent check with the checkout's own fixture (test_capture.Den).

A photometry evening as tools/den-photometry/README.md (rung-7 branch) step 5 has it: den_lighting keeps running and
the owner, present, sets the light being measured from the HA app before each capture. Variant 1 counts those settings;
variant 2 is the ladder's rung 7 ('den_lighting paused'): the same settings with den_lighting's automatic switch off.
It also lists capture.py's command-line options.
Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 -B verify_photometry-settings-counted-as-people.py <root of the claude/correction-capture checkout>
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

L0, L1, T0 = tc.L0, tc.L1, tc.T0
LEVELS = [{"on": True, "brightness": b, "kelvin": 2700} for b in (254, 200, 150, 100, 60, 30)]


def photometry(paused):
    den = tc.Den()
    if paused:
        den.row(T0 + 10, automatic=False)
    t = T0 + 60
    for i, level in enumerate(LEVELS):          # one app setting per capture, owner present
        e = (L0, L1)[i % 2]
        den.hold(t, {e: level})
        den.bulb(t + 0.3, e, True, user=True)
        t += 300
    if paused:
        den.row(t, automatic=True, holds={})    # resume_automatic clears the holds
    den.row(T0 + 3600)
    tmp = Path(tempfile.mkdtemp(prefix="verify-photo-"))
    try:
        rec, _ = capture.capture(*den.write(tmp))
    finally:
        shutil.rmtree(tmp, True)
    return rec


for name, paused in (("den_lighting running (rung-7 README)", False), ("den_lighting paused (ladder rung 7)", True)):
    rec = photometry(paused)
    p = rec["planning"]["strata"]["no_game"]
    print("RESULT %-38s people=%s occupied_h=%s not_counted=%s" % (
        name, p["people"]["count"], p["occupied_h"], rec["planning"]["not_counted_by_reason"]))
    if not paused and p["people"]["count"] == len(LEVELS):
        print("FAIL all %d photometry settings count as people's corrections" % len(LEVELS))
help_text = io.StringIO()
with contextlib.redirect_stdout(help_text), contextlib.suppress(SystemExit):
    capture.main(["--help"])
opts = sorted({w.strip(",[]") for w in help_text.getvalue().split() if w.startswith("--")})
print("RESULT capture.py options: %s" % opts)
print(("FAIL " if not any("exclude" in o or "window" in o or "probe" in o for o in opts) else "PASS ")
      + "no option or input to exclude a probe log or time window")
