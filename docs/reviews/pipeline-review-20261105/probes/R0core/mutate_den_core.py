"""mutate_den_core (VALIDATE lens): targeted mutants of den-core's replay gate, runner, incumbent wrapper, need dwell,
decide and validate checks. Each mutant is applied to a temp copy of the checkout (never to the checkout itself), the
whole den-core suite is run with failfast, and a caught/survived table is printed.

How to run (synthetic data only; temp folders only; no network):
    python3 -B mutate_den_core_validate.py /path/to/den-core-checkout [MUTANT_ID ...]
The copy gets a `.git` marker file at its root, so events.write's refusal test behaves as in a real checkout.
Observed on claude/den-core @022aa9d5: 30 mutants, 24 caught, 6 survived
(C_trunc, E_match_b, R_roundtrip, V_inforce, V_holdvac, RD_both).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
ONLY = set(sys.argv[2:])
D = "tools/den-core/"

MUTANTS = [
    # id, file, old, new, what it breaks
    ("C_trunc", D + "check.py", "for index in range(max(len(want), len(got))):",
     "for index in range(min(len(want), len(got))):", "check: a truncated log or one with appended lines passes"),
    ("C_code", D + "check.py", 'if events.sha256_file(module.__file__) != entry["code_sha256"]:', "if False:",
     "check: no 'code changed' refusal"),
    ("E_req", D + "events.py", "    missing = [key for key in REQUIRED.get(kind, ()) if key not in data]",
     "    missing = []", "events.validate: required data keys not checked"),
    ("E_match_b", D + "events.py", 'abs(b - actual["brightness"]) <= 1)', 'abs(b - actual["brightness"]) <= 3)',
     "events.scene_matches: brightness tolerance 1 -> 3"),
    ("E_match_none", D + "events.py", "        return target is None and actual is None\n    if target[\"on\"]",
     "        return True\n    if target[\"on\"]", "events.scene_matches: a missing scene matches anything"),
    ("R_ref", D + "runner.py", "        if kind not in events.REFERENCE_KINDS:\n            deliver(event, 0)",
     "        deliver(event, 0)", "runner: live_command handed to the policies"),
    ("R_order", D + "runner.py", 'ORDER = ("need", "incumbent", "decide")', 'ORDER = ("need", "decide", "incumbent")',
     "runner: decide runs before the incumbent"),
    ("R_roundtrip", D + "runner.py", "        event = json.loads(events.dumps(event))\n", "",
     "runner: no canonical round trip before policies see an event"),
    ("I_cfg", D + "incumbent.py", "    if changed:\n        raise ValueError", "    if False:\n        raise ValueError",
     "incumbent: a logged config that differs from den_lighting's is accepted"),
    ("N_entry", D + "need.py", "if not self.engaged and self.playing >= self.entry_samples:",
     "if not self.engaged and self.playing > self.entry_samples:", "need: engage on the 3rd playing sample"),
    ("N_start", D + "need.py", 'return "starting" if self.playing >= 1 else "none"',
     'return "starting" if self.playing >= 2 else "none"', "need: first non-none output delayed to the 2nd sample"),
    ("N_onset", D + "need.py", "                self.onset_ts = captured", "                self.onset_ts = now",
     "need: onset_ts is the arrival time, not the capture time"),
    ("N_exit", D + "need.py", "if self.engaged and captured - self.idle_since >= self.exit_idle_s:",
     "if self.engaged and captured - self.idle_since > self.exit_idle_s:", "need: exit dwell boundary >= -> >"),
    ("D_vac", D + "decide.py", 'if since is None or now - since < config["vacancy_s"]:',
     'if since is None or now - since <= config["vacancy_s"]:', "decide: vacancy OFF boundary < -> <="),
    ("D_pin", D + "decide.py", '    requested.update({b: _scene(h["pinned"]) for b, h in held.items()})',
     '    requested.update({b: _scene(normal.get(b) or h["pinned"]) for b, h in held.items()})',
     "decide: a held bulb requests its normal scene instead of its pinned scene"),
    ("D_game", D + "decide.py", '    if mode["gaming"]:\n        return None', '    if False:\n        return None',
     "decide: decides during games"),
    ("D_read", D + "decide.py", '            and support.get("support_status") == "observed")', "            )",
     "decide: reading look on expired support"),
    ("D_chain", D + "decide.py", "                return []\n            trigger = \"hold change\"",
     "                pass\n            trigger = \"hold change\"",
     "decide: plans on the mode of an occupancy change before seeing the occupancy"),
    ("D_unknown", D + "decide.py", '    if occupancy not in ("on", "off"):\n        return None',
     '    if occupancy not in ("on", "off", "unknown"):\n        return None',
     "decide: does not fail closed on unknown occupancy"),
    ("V_skip", D + "validate.py", '            if bulb not in {s["channel"] for s in d["data"]["skipped"]}:',
     "            if False:", "validate check 4: skipped list not checked"),
    ("V_quiet_game", D + "validate.py", "            if gaming:\n                return fail(",
     "            if False:\n                return fail(", "validate check 5: decisions in games pass"),
    ("V_parity", D + "validate.py", "        if a == b:\n            continue",
     "        if (a and a[1]) == (b and b[1]):\n            continue", "validate check 2: move times not compared"),
    ("V_inforce", D + "validate.py",
     'in_force = [d for d in mine if d["t"] <= start][-1:] + [d for d in mine if start < d["t"] < end]',
     'in_force = [d for d in mine if d["t"] <= start][-1:]',
     "validate check 3: decisions made during a diff interval not checked"),
    ("V_pass", D + "validate.py", 'passed = counts["FAIL"] == 0', 'passed = counts["FAIL"] <= 1',
     "validate: overall OK with one failed check"),
    ("V_replay", D + "validate.py", '    if result["ok"]:\n        return ok(result["reason"])',
     '    if True:\n        return ok(result["reason"])', "validate check 1: replay always OK"),
    ("V_holdvac", D + "validate.py",
     'or occupancy == "off" and (vacant_since is None or t - vacant_since < vacancy_s)):',
     'or occupancy == "off"):', "validate check 4: holds never checked during vacancy"),
    ("V_look_scene", D + "validate.py", "if look is None or not events.scene_matches(look, wanted):",
     "if look is None:", "validate check 3: the look's scene on the bulb not checked"),
    ("RD_both", D + "run.py", "    return events.scene_matches(a, b) and events.scene_matches(b, a)",
     "    return events.scene_matches(a, b)", "run.diff: one-way scene comparison"),
    ("RD_gaming", D + "run.py",
     '(bulb, not state["gaming"] and not _both(wanted[bulb], den.wants(bulb, state["t"])))',
     '(bulb, not _both(wanted[bulb], den.wants(bulb, state["t"])))', "run.diff: game time not left out"),
    ("LD_found", D + "run.py",
     "                live[bulb] = target\n        if kind == \"live_command\":",
     "                pass\n        if kind == \"live_command\":",
     "run.live_diff: no 'found already there' inference for the live side"),
]


def copy_tree(dst):
    ignore = shutil.ignore_patterns(".git", "node_modules", "__pycache__")
    shutil.copytree(ROOT, dst, ignore=ignore, symlinks=True)
    (dst / ".git").write_text("gitdir: /nonexistent\n")


def suite(root):
    t = time.time()
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/den-core", "-p",
                        "test_*.py"], cwd=str(root), capture_output=True, text=True, timeout=900,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    tail = p.stderr.strip().splitlines()
    first = next((line for line in tail if line.startswith(("FAIL:", "ERROR:"))), "")
    return p.returncode, first, round(time.time() - t, 1), tail[-1] if tail else ""


with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp) / "copy"
    copy_tree(work)
    code, first, secs, last = suite(work)
    print("baseline: exit=%d %s (%ss)" % (code, last, secs))
    if code != 0:
        print("FAIL: baseline suite does not pass in the copy; stopping")
        sys.exit(2)
    rows = []
    for mid, rel, old, new, what in MUTANTS:
        if ONLY and mid not in ONLY:
            continue
        path = work / rel
        text = path.read_text("utf-8")
        if text.count(old) != 1:
            rows.append((mid, rel, "not-run", "pattern found %d times" % text.count(old), what))
            continue
        path.write_text(text.replace(old, new), "utf-8")
        try:
            code, first, secs, last = suite(work)
        finally:
            path.write_text(text, "utf-8")
        rows.append((mid, rel, "caught" if code != 0 else "survived", first or last, what))
        print("%-13s %-8s %-60s %s" % (mid, rows[-1][2], what[:60], (first or last)[:110]), flush=True)
    print()
    print("RESULT table (id | file | result | caught by | mutation)")
    for mid, rel, result, by, what in rows:
        print("RESULT %s | %s | %s | %s | %s" % (mid, rel, result, by, what))
    survived = [r for r in rows if r[2] == "survived"]
    print("RESULT: %d mutants, %d caught, %d survived, %d not run" % (
        len(rows), sum(r[2] == "caught" for r in rows), len(survived), sum(r[2] == "not-run" for r in rows)))
