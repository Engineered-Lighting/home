"""verify_attribution-windows-unpinned_reproduce (REPRODUCE lens): independent re-check of the finding that the
correction-capture suite does not pin ACK_AFTER_S (den_lighting's acknowledgement window, 8 s) or WINDOW_S (the
attribution window, 5 s), nor the printed interval level or hours_per_arm_at_phi_hat.

Part 1: copies tools/correction-capture, tools/den-core and ha-config/custom_components/den_lighting into a temp dir
(with a .git marker so the inside-checkout guard still sees a checkout), applies each mutant, and runs the WHOLE suite
(no failfast) so the full pass/fail count is visible.
Part 2: shows the two window mutants are not equivalent: on a synthetic den (a dimmer touch 10 s after den_lighting's
own command; an app hold whose bulb row lands 3 s after the hold) the people count changes when the constant changes.
The constants are module globals read at call time, so part 2 patches them on an imported copy of capture.py.

Synthetic data only, temp dirs only, never contacts a host. The pinned checkout is never modified.
Run:  python3 -B verify_attribution-windows-unpinned_reproduce.py <root of the claude/correction-capture checkout>
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
CAP = "tools/correction-capture/capture.py"
MUTANTS = [
    ("ACK_AFTER_S 8 -> 30", "ACK_BEFORE_S, ACK_AFTER_S = 2.0, 8.0", "ACK_BEFORE_S, ACK_AFTER_S = 2.0, 30.0"),
    ("WINDOW_S 5 -> 1", "WINDOW_S = 5.0", "WINDOW_S = 1.0"),
    ("_rate interval at 50%", 'lo, hi = garwood(count, constants["interval_level"])', "lo, hi = garwood(count, 0.5)"),
    ("hours_per_arm_at_phi_hat * -> /", 'people_rate["hours_per_arm"] * phi)', 'people_rate["hours_per_arm"] / phi)'),
]


def tree(dest):
    for rel in ("tools/correction-capture", "tools/den-core", "ha-config/custom_components/den_lighting"):
        shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / ".git").mkdir()


def suite(dest):
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tools/correction-capture",
                           "-p", "test_*.py"], cwd=dest, capture_output=True, text=True, timeout=600)
    ran = re.search(r"Ran (\d+) test", proc.stderr)
    tail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ""
    return proc.returncode, (ran.group(1) if ran else "?"), tail


work = Path(tempfile.mkdtemp(prefix="verify-windows-"))
try:
    base = work / "base"
    tree(base)
    code, ran, tail = suite(base)
    print("RESULT baseline copy: exit %d, ran %s, %s" % (code, ran, tail))
    for i, (name, old, new) in enumerate(MUTANTS):
        dest = work / ("m%d" % i)
        tree(dest)
        path = dest / CAP
        text = path.read_text("utf-8")
        assert text.count(old) == 1, name
        path.write_text(text.replace(old, new), "utf-8")
        code, ran, tail = suite(dest)
        print("%s mutant %-34s exit %d, ran %s, %s" % ("SURVIVED" if code == 0 else "CAUGHT", name, code, ran, tail))
finally:
    shutil.rmtree(work, True)

# ---- part 2: non-equivalence on synthetic dens
spec = importlib.util.spec_from_file_location("capture_copy", ROOT / CAP)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
events = sys.modules["events"]
L0, L1 = events.LIGHTS[0], events.LIGHTS[1]
STATUS = "sensor.den_lighting_control"
T0 = datetime(2026, 10, 30, 2, 0, tzinfo=timezone.utc).timestamp()
WARM = {"on": True, "brightness": 100, "kelvin": 2700}
DIM = {"on": True, "brightness": 40, "kelvin": 2700}


def srow(rows, t, rev, holds):
    attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
             "occupancy": "on", "revision": rev, "error": None, "gaming_entity": "sensor.pc_gaming",
             "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in holds.items()},
             "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
             "friendly_name": "Den lighting control"}
    rows.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})


def bulb(rows, t, e, on, ctx, user=""):
    rows.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off", "context_id": ctx,
                 "context_user_id": user, "context_parent_id": ""})


def run(status, bulbs, commands):
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, rows in (("s", status), ("b", bulbs), ("c", commands)):
            p = Path(tmp) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            paths.append([p])
        record, _ = capture.capture(*paths)
    return record["planning"]["strata"]["no_game"]["people"]["count"]


def dimmer_after_command():
    # den_lighting commands L0 on (its own row during the call); 10 s later a hand dims L0 at a dimmer (no ids,
    # stays on, so no flip); den_lighting records a hold.
    status, bulbs, commands = [], [], []
    srow(status, T0, 100, {})
    own = "%032X" % 0x01900000000000000000000000000001
    bulb(bulbs, T0 + 99.7, L0, True, own)
    commands.append({"time_fired_ts": T0 + 100, "event_data": {"entity_id": L0, "revision": 100, "target": WARM,
                                                               "context_id": own}})
    bulb(bulbs, T0 + 110, L0, True, "%032X" % 0x01900000000000000000000000000002)
    srow(status, T0 + 110.01, 101, {L0: DIM})
    srow(status, T0 + 3600, 102, {})
    return status, bulbs, commands


def app_row_late():
    # The HA app sets L1: the hold opens at the call; the bulb's row (with the user id) lands 3 s later.
    status, bulbs = [], []
    srow(status, T0, 100, {})
    srow(status, T0 + 200, 101, {L1: DIM})
    bulb(bulbs, T0 + 203, L1, True, "%032X" % 0x01900000000000000000000000000003, user="0F" * 16)
    srow(status, T0 + 3600, 102, {})
    return status, bulbs, []


for name, attr, mutant, case in (("ACK_AFTER_S", "ACK_AFTER_S", 30.0, dimmer_after_command),
                                 ("WINDOW_S", "WINDOW_S", 1.0, app_row_late)):
    original = getattr(capture, attr)
    a = run(*case())
    setattr(capture, attr, mutant)
    b = run(*case())
    setattr(capture, attr, original)
    print("RESULT %s=%s people=%d ; %s=%s people=%d -> %s" % (
        name, original, a, name, mutant, b, "NOT EQUIVALENT (mutant changes the count)" if a != b else "equivalent"))
