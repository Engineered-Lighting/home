"""verify_reaction-touch-in-ack-window: independent check, built with the checkout's own test fixture (test_capture.Den),
that a physical dimmer press shortly after one of den_lighting's commands is set aside as den_lighting's own report.

den_lighting 1.0.4 (ha-config/custom_components/den_lighting/__init__.py state_changed and _verify_readback): an id-less
report inside the command's phase (transition + 3 s, or while read-back is pending) opens a provisional hold; read-back
then keeps it as a manual hold when the bulb is not at the commanded target. After the phase, an id-less report opens an
ordinary manual hold. Cases:
  A  dimmer at +3 s, provisional, read-back finds DIM not WARM and keeps the hold
  B  dimmer at +6.5 s after a 2 s transition: phase over, ordinary hold at DIM
  C  the HA app at +3 s (a user id): scope check
  D  the same dimmer press at +30 s: control
Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 -B verify_reaction-touch-in-ack-window.py <root of the claude/correction-capture checkout>
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
import test_capture as tc  # noqa: E402  the checkout's own synthetic export writer

L0, T0, WARM, DIM = tc.L0, tc.T0, tc.WARM, tc.DIM


def run(build):
    den = tc.Den()
    build(den)
    den.row(T0 + 3600)
    tmp = Path(tempfile.mkdtemp(prefix="verify-ack-"))
    try:
        rec, _ = capture.capture(*den.write(tmp))
    finally:
        shutil.rmtree(tmp, True)
    counted = [(c["source"], c["changes"][0]["hold"], c["changes"][0]["after"]["brightness"]) for c in rec["corrections"]]
    skipped = [(c["reason"], c["source"], c["hold"], c["after"]["brightness"]) for c in rec["not_counted"]]
    return counted, skipped


def a(den):
    den.command(T0 + 100, L0, WARM)
    den.bulb(T0 + 103, L0, True)                     # A's dimmer: id-less, still on (no flip)
    den.hold(T0 + 103.01, {L0: DIM}, provisional=[L0])
    den.settle(T0 + 107, L0)                         # read-back: DIM is not WARM, the hold stays


def b(den):
    den.command(T0 + 100, L0, WARM)
    den.bulb(T0 + 101.5, L0, True)                   # its exact final report: den_lighting's own, no hold
    den.bulb(T0 + 106.5, L0, True)                   # A's dimmer after the phase and read-back
    den.hold(T0 + 106.51, {L0: DIM})


def c(den):
    den.command(T0 + 100, L0, WARM)
    den.hold(T0 + 103, {L0: DIM})                    # service call from the app opens the hold
    den.bulb(T0 + 103.3, L0, True, user=True)


def d(den):
    den.command(T0 + 100, L0, WARM)
    den.bulb(T0 + 101.5, L0, True)
    den.bulb(T0 + 130, L0, True)
    den.hold(T0 + 130.01, {L0: DIM})


dropped = 0
for name, build, person in (("A dimmer +3 s (confirmed)", a, True), ("B dimmer +6.5 s (ordinary)", b, True),
                            ("C app +3 s", c, True), ("D dimmer +30 s", d, True)):
    counted, skipped = run(build)
    print("RESULT %-28s counted=%s not_counted=%s" % (name, counted, skipped))
    if person and not counted:
        dropped += 1
        print("FAIL %s: a person's change to brightness 40 after a command for brightness 100 is not counted" % name)
print("RESULT %d of 4 person touches dropped (command target brightness 100, hold target 40 in each)" % dropped)
