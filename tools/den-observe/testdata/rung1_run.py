"""A real rung 1 run on synthetic data, through den-register's own code and test harness.

    python -B tools/den-observe/testdata/rung1_run.py <den-register folder> <out folder>

test_observe.py runs this when tools/den-register is present (or DEN_REGISTER_DIR
names it). Its <out>/room-model.jsonl is what rung1-room-model.jsonl beside this
file was copied from, so that test also runs on a branch without den-register.

Writes <out>/room-model.jsonl with five revisions: a first fit, a second fit from
a later take, the revision a real check-mount appends when it confirms, the
invalidated one it appends after a 5 degree turn, and a fit from a take after the
turn. Prints the transform ids.
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

tool, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(tool))
import register  # noqa: E402
import synthetic  # noqa: E402
import test_den_register as harness  # noqa: E402


class Holder:
    pass


holder = Holder()
holder.tmp = tempfile.TemporaryDirectory(prefix="rung1-run-")
out.mkdir(parents=True, exist_ok=True)

first = harness.Scene(holder, ended=1_800_000_100.0, background=synthetic.room_depth())
first.click_all()
code, report = first.fit(now=1_800_000_500.0)
assert code == 0, first.out
print(report["transform_id"])

second = harness.Scene(holder, name="take2", ended=1_800_000_900.0, background=synthetic.room_depth())
second.click_all(shift={"p1": (0.4, 0)})  # the same room, a slightly different click
code, report = second.fit(now=1_800_001_000.0)
assert code == 0, second.out
print(report["transform_id"])

held = harness.Scene(holder, name="held", ended=1_800_001_500.0, background=synthetic.room_depth())
held.click_all()
text = io.StringIO()
code, report = register.run_check_mount(first.work, held.take, m=first.m, now=lambda: 1_800_001_600.0, out=text)
assert report["result"] == "confirmed", text.getvalue()

turned = synthetic.moved(yaw_deg=5.0)
later = harness.Scene(holder, name="later", ended=1_800_002_000.0, pose=turned, background=synthetic.room_depth(turned))
later.click_all()
text = io.StringIO()
code, report = register.run_check_mount(first.work, later.take, m=first.m, now=lambda: 1_800_002_100.0, out=text)
assert report["result"] == "changed", text.getvalue()

again = harness.Scene(holder, name="again", ended=1_800_003_000.0, pose=turned, background=synthetic.room_depth(turned))
again.click_all()
code, report = again.fit(now=1_800_003_100.0)
assert code == 0, again.out
print(report["transform_id"])
shutil.copy(first.work / "room-model.jsonl", out / "room-model.jsonl")
holder.tmp.cleanup()
