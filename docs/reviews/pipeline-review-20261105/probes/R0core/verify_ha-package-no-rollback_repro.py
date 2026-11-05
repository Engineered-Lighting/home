"""verify_ha-package-no-rollback_repro: (1) does tools/den-core/README.md give any undo step for the HA changes it
asks for, and (2) can a den-core replay represent the den_lighting restart that the HA restart causes?

(1) greps the README for rollback/undo/remove/uninstall/delete/revert wording.
(2) uses den_lighting's own policy.py (loaded unchanged, as incumbent.py loads it) twice over the same evening:
    continuous (what a den-core replay does: one Controller, no restart input exists in events.INPUT_KINDS), and
    restarted at +500 s while the den is vacant, as __init__.py does on an HA restart: Controller.restore(dump()),
    re-observe the bulbs, then set occupancy 'unknown' because Frigate's HA entity reads off after a restart
    (start(): 'on' if the entity is on, else 'unknown'). Prints which bulbs each turns ON when the person returns.
Synthetic data only, no host.

    python3 -B verify_ha-package-no-rollback_repro.py /path/to/den-core-checkout
"""
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-core"))
import events  # noqa: E402
import incumbent  # noqa: E402

text = (root / "tools" / "den-core" / "README.md").read_text("utf-8")
hits = [line for line in text.splitlines() if re.search(r"rollback|roll back|undo|uninstall|revert|remove the|delete the", line, re.I)]
print("RESULT: README undo/rollback lines: %s" % hits)
print("RESULT: den-core input kinds: %s (no restart kind)" % (events.INPUT_KINDS,))

den = incumbent.load_policy(incumbent.find_policy())
T0 = 1793764800.0
LIGHTS = den.LIGHTS


def evening(restart):
    c = den.Controller()
    c.set_enabled(True, T0 - 20)
    for e in LIGHTS:  # three on at a level, the last-listed rear left off (its normal is off)
        c.observe(e, den.Scene(True, 100, 3000) if e != "light.den_rear_left" else den.Scene(False), T0 - 20)
    c.set_occupancy("on", T0 - 10); c.reconcile(T0 - 10)
    c.set_occupancy("off", T0 + 100)
    for cmd in c.reconcile(T0 + 220):  # vacancy OFF; the bulbs report off
        c.observe(cmd.entity_id, cmd.target, T0 + 222)
    if restart:  # HA restart at +500 s: __init__.py restores the stored state, then start()
        c = den.Controller.restore(c.dump())
        for e in LIGHTS:
            c.observe(e, den.Scene(False), T0 + 500)
        c.set_occupancy("unknown", T0 + 500)
        c.reconcile(T0 + 500)
    c.set_occupancy("on", T0 + 1000)
    return sorted(cmd.entity_id.split(".")[1] for cmd in c.reconcile(T0 + 1000) if cmd.target.on)


a, b = evening(False), evening(True)
print("RESULT: continuous replay turns ON at return: %s" % a)
print("RESULT: den_lighting restarted while vacant turns ON at return: %s" % b)
print("FAIL: no undo step, and a replay across the restart diverges from the restarted den_lighting"
      if not hits and a != b else "PASS")
