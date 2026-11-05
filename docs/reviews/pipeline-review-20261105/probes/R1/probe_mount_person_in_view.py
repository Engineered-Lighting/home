#!/usr/bin/env python3
"""check-mount: a person in front of the camera gives MOUNT CHANGED, not UNKNOWN.

REGISTER-V1.md says `unknown` is "too few comparable cells, for example a person
in front of the camera" and the README says MOUNT UNKNOWN means "too much of the
view blocked". A person has valid depth, so the blocked cells are comparable
and count as moved: once they are more than half of the compared cells the
check says `changed` and invalidates the registration. This probe registers on
a synthetic scene, then runs check-mount on a later take of the same unmoved
camera in which a near "person" (1.2 m depth) covers about 60% of the frame.

Synthetic data only; temp dirs only; contacts nothing.

    python3 -B probe_mount_person_in_view.py <root of the rung-1-register checkout>
"""
import io
import json
import re
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-register"
sys.path.insert(0, str(TOOL))
import method  # noqa: E402
import records  # noqa: E402
import register  # noqa: E402
import synthetic  # noqa: E402
import worksheet as worksheet_mod  # noqa: E402


def closed_method():
    text = (TOOL / "REGISTER-V1.md").read_text(encoding="utf-8")
    text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}  # closed for a probe",
                  text, flags=re.M)
    return method.parse(text)


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    depth, left, seen = synthetic.render()
    take = synthetic.write_take(tmp / "reg", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = tmp / "work"
    work.mkdir()
    (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet()), encoding="utf-8")
    m = closed_method()
    sheet = worksheet_mod.load(work / "worksheet.json", need_positions=False)
    session = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, m)
    for pid, (u, v, _) in seen.items():
        session.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    session.done()
    code, report = register.run_fit(work, take, m=m, now=lambda: 1_800_000_500.0, out=io.StringIO())
    print("RESULT registration:", report["result"], report["transform_id"])

    # Same camera, same room; a person close to the camera fills the left 60% of the frame at 1.2 m.
    values = list(struct.unpack(f"<{640 * 400}H", depth))
    for y in range(400):
        for x in range(0, 384):
            values[y * 640 + x] = 1200
    later = synthetic.write_take(tmp / "later", [synthetic.bundle(synthetic.metadata(), struct.pack(
        f"<{640 * 400}H", *values), left)] * 3)
    data = json.loads((later / "take.json").read_text(encoding="utf-8"))
    data["ended_unix"] = 1_800_001_000.0
    (later / "take.json").write_text(json.dumps(data), encoding="utf-8")
    out = io.StringIO()
    code, mount = register.run_check_mount(work, later, m=m, now=lambda: 1_800_002_000.0, out=out)
    print(out.getvalue().rstrip())
    found = records.resolve(records.read_jsonl(work / "room-model.jsonl", "x"), report["transform_id"])
    print(f"RESULT check-mount result {mount.get('result')}; {report['transform_id']} invalidated: "
          f"{bool(found['invalidated'])}")
    bad = mount.get("result") == "changed"
    print(("FAIL" if bad else "PASS") + " a person in front of an unmoved camera gives MOUNT UNKNOWN (as the "
          "method and README say), not MOUNT CHANGED with an invalidation")
    sys.exit(1 if bad else 0)
