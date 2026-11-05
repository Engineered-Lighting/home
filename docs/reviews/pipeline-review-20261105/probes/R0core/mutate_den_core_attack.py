"""mutate_den_core: mutation runner for tools/den-core's replay-gate and decision logic.

Copies the parts of the checkout the den-core tests need into a temp folder (with an empty .git marker, so
events.inside_checkout behaves as in a checkout), applies one mutant at a time, runs the narrowest test
module or class with failfast, and if the mutant survives, runs the whole den-core suite to confirm. Prints a
caught/survived table. Synthetic data only; nothing in the checkout is modified.

    python3 mutate_den_core_attack.py /path/to/den-core-checkout
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
PARTS = tuple(str(p.relative_to(root)) for p in [root / "tools" / "den-core", *root.glob("tools/*-latency"),
                                                 root / "stack" / "services" / "perception-lab",
                                                 *root.glob("stack/services/lighting-brain-bridges/*-ha/tests/fixtures"),
                                                 root / "ha-config" / "custom_components" / "den_lighting"])
D = "tools/den-core/"
MUTANTS = [
    ("M1", D + "check.py", "for index in range(max(len(want), len(got))):",
     "for index in range(1, max(len(want), len(got))):", "check skips the header line", "test_spine.CheckTest"),
    ("M2", D + "check.py", 'if events.sha256_file(module.__file__) != entry["code_sha256"]:', "if False:",
     "check ignores a changed policy module", "test_spine.CheckTest"),
    ("M3", D + "incumbent.py", "    if changed:\n", "    if False:\n",
     "incumbent accepts a config that differs from den_lighting's", "test_spine.CheckTest"),
    ("M4", D + "decide.py", 'if since is None or now - since < config["vacancy_s"]:',
     'if since is None or now - since <= config["vacancy_s"]:', "decide vacancy boundary < -> <=", "test_decide"),
    ("M5", D + "decide.py", '    requested.update({b: _scene(h["pinned"]) for b, h in held.items()})\n', "    pass\n",
     "decide stops pinning held bulbs", "test_decide"),
    ("M6", D + "decide.py", '    if mode["gaming"]:\n        return None', '    if False:\n        return None',
     "decide decides during games", "test_decide"),
    ("M7", D + "need.py", "                self.onset_ts = captured", '                self.onset_ts = s["available_at"]',
     "need onset from availability instead of capture", "test_need"),
    ("M8", D + "need.py", "captured - self.idle_since >= self.exit_idle_s", "captured - self.idle_since > self.exit_idle_s",
     "need exit boundary >= -> >", "test_need"),
    ("M9", D + "validate.py", "            if gaming:\n                return fail(", "            if False:\n                return fail(",
     "validate check 5 ignores decisions in games", "test_validate"),
    ("M10", D + "run.py", '        if kind == "live_command":\n            live[data["entity_id"]] = data["target"]\n',
     '        if False:\n            pass\n', "live_diff ignores live commands", "test_spine.RealFilesRunTest"),
    ("M11", D + "validate.py", "            if not events.scene_matches(pinned, wanted):", "            if False:",
     "validate check 4 ignores a moved held bulb", "test_validate"),
    ("M12", D + "decide.py", '            and support.get("support_status") == "observed")', ")",
     "decide treats expired support as reading", "test_decide"),
]


def tests(cwd, target, failfast=True):
    if target == "ALL":
        args = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tools/den-core", "-p", "test_*.py"]
    else:
        args = [sys.executable, "-B", "-m", "unittest"] + (["-f"] if failfast else []) + [target]
    env_cwd = cwd if target == "ALL" else cwd / "tools" / "den-core"
    r = subprocess.run(args, cwd=str(env_cwd), capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stderr.strip().splitlines() or ["?"])[-1]


rows = []
for mid, rel, old, new, what, target in MUTANTS:
    tmp = Path(tempfile.mkdtemp(prefix="mut-dencore-"))
    for part in PARTS:
        shutil.copytree(root / part, tmp / part)
    (tmp / ".git").mkdir()
    path = tmp / rel
    text = path.read_text("utf-8")
    if text.count(old) != 1:
        rows.append((mid, rel, what, "not-run", "pattern found %d times" % text.count(old)))
        continue
    path.write_text(text.replace(old, new), "utf-8")
    code, last = tests(tmp, target)
    if code:
        rows.append((mid, rel, what, "caught", "%s (%s)" % (target, last)))
    else:
        code, last = tests(tmp, "ALL")
        rows.append((mid, rel, what, "caught" if code else "survived",
                     "full suite: %s" % last if code else "%s and full suite pass (%s)" % (target, last)))
    shutil.rmtree(tmp, ignore_errors=True)
print("%-4s %-26s %-50s %-9s %s" % ("id", "file", "mutation", "result", "by"))
for row in rows:
    print("%-4s %-26s %-50s %-9s %s" % row)
