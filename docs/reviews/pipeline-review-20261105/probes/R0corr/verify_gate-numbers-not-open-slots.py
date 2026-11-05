"""verify_gate-numbers-not-open-slots: independent re-check of the finding that cph-budget-v0's min_occupied_h (20) and
max_hours_per_arm (300) are builder-chosen, not marked open, and still drive a printed verdict.

Checks: (1) the rule file has no slot marked open (no key or value "open", no null constant); (2) the contracts
(ladder.json, owner-choices.md, decisions.json, experiments.json, schemas.json, notes.json) never name an occupied-hour
minimum or a per-arm hour budget, and the ladder's only "300" is the hours needed at an illustrative phi = 2;
(3) the CLI, run on a synthetic export into a temp folder, exits 0 and prints a verdict instead of refusing.
Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 -B verify_gate-numbers-not-open-slots.py <root of the claude/correction-capture checkout> <live-v1 spec dir>
"""
import io
import json
import re
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
SPEC = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
import events  # noqa: E402

rule = json.loads((ROOT / "tools/correction-capture/cph-budget-v0.json").read_text("utf-8"))


def walk(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from walk(v, path + "/" + k)
            yield path + "/" + k, k
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from walk(v, "%s[%d]" % (path, i))
    else:
        yield path, x


open_marks = [(p, v) for p, v in walk(rule) if v is None or (isinstance(v, str) and v.strip().lower() == "open")]
print("RESULT rule constants: %s" % rule["constants"])
print("RESULT slots marked open or empty in cph-budget-v0.json: %s" % (open_marks or "none"))

hits = {}
for name in ("ladder.json", "owner-choices.md", "decisions.json", "experiments.json", "schemas.json", "notes.json"):
    text = (SPEC / name).read_text("utf-8")
    for pat in (r"min_occupied|max_hours_per_arm|minimum (of )?\d+ occupied|\b20 occupied|hours per arm budget",
                r"[^\d.]300 [^\d]"):
        for m in re.finditer(pat, text):
            hits.setdefault(name, []).append(text[max(0, m.start() - 80):m.end() + 60].replace("\n", " "))
for name, found in hits.items():
    for h in found:
        print("RESULT contract text %s: ...%s..." % (name, h))
if not any("min_occupied" in h or "budget" in h for v in hits.values() for h in v):
    print("RESULT no contract file sets an occupied-hour minimum or a per-arm hour budget")

# (3) the CLI on a synthetic one-evening export
STATUS = "sensor.den_lighting_control"
T0 = datetime(2026, 10, 30, 2, 0, tzinfo=timezone.utc).timestamp()
status = []
for i, t in enumerate((T0, T0 + 3600)):
    status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": {
        "automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
        "occupancy": "on", "revision": 100 + i, "error": None, "gaming_entity": "sensor.pc_gaming", "holds": {},
        "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
        "friendly_name": "Den lighting control"}})
with tempfile.TemporaryDirectory() as tmp:
    s, b, c = (Path(tmp) / n for n in ("s.jsonl", "b.jsonl", "c.jsonl"))
    s.write_text("".join(json.dumps(r) + "\n" for r in status), "ascii")
    b.write_text("", "ascii")
    c.write_text("", "ascii")
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            capture.main(["--status", str(s), "--lights", str(b), "--commands", str(c), "--out", str(Path(tmp) / "o")])
        refused = False
    except (SystemExit, ValueError) as error:
        refused = True
        print("RESULT CLI refused: %r" % error)
    verdicts = re.findall(r"verdict: (\w+)", out.getvalue())
    print("RESULT CLI refused=%s printed verdicts=%s" % (refused, verdicts))
if not open_marks and verdicts and not refused:
    print("FAIL builder-chosen gate numbers carry no open slot and the tool prints a verdict from them")
else:
    print("PASS")
