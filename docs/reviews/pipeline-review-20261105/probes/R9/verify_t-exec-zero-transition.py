#!/usr/bin/env python3
"""Verify finding t-exec-zero-transition (rung 9, tools/den-core/decide.py record()/t_exec()).

    python3 -B verify_t-exec-zero-transition.py <den-core-live checkout root>

In a temp copy of the checkout's tools (the checkout is never touched; synthetic data only; no host), it runs the
synthetic evening (touch + game) through need, incumbent and decide, then for every decide decision:
 - counts the changed (non-held, non-kept) channels whose logged inputs.t_exec equals the decision's ts, and the
   decision's 'transitions' field;
 - lists the transitions den_lighting (the incumbent, policy.py unchanged) logged on its own commands that evening;
 - prints the exclusions.ages decide logged for one decision, and what they would be with den_lighting's 2 s fade.
RESULT CONFIRMED when every changed channel has t_exec == ts with transitions == {} while den_lighting's commands
carry nonzero transitions.
"""
import os
import shutil
import subprocess
import sys
import tempfile

COPY = ["tools/den-core", "ha-config/custom_components/den_lighting", "stack/services/perception-lab",
        "tools/la-latency"]

DRIVER = r'''
import json, sys
sys.path.insert(0, ".")
import decide, runner, synthetic
items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True, game=True)
header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(("need", "incumbent", "decide"), None),
                         synthetic=True, sources=[{"name": "synthetic:evening", "params": {}}])
changed = equal = 0
transitions_seen = set()
example = None
for e in log:
    if e["kind"] != "decision":
        continue
    d = e["data"]
    if e["source"] == "incumbent":
        transitions_seen.update((d.get("transitions") or {}).values())
        continue
    if e["source"] != "decide":
        continue
    assert d["transitions"] == {}, d["transitions"]
    for b, t in d["inputs"]["t_exec"].items():
        if b.endswith("_status") or t is None:
            continue
        changed += 1
        equal += t == d["ts"]
    if example is None and d["inputs"]["exclusions"]["ages"]:
        example = d
print("DECIDE changed channels with a t_exec: %d ; of them t_exec == ts: %d ; decide 'transitions' always {}" % (changed, equal))
print("INCUMBENT transitions on den_lighting's own commands this evening: %s" % sorted(transitions_seen))
if example:
    inp = example["inputs"]
    print("EXAMPLE decision %s ts=%.3f t_exec=%s" % (example["decision_id"], example["ts"],
          {k: v for k, v in inp["t_exec"].items()}))
    print("EXAMPLE logged ages=%s" % json.dumps(inp["exclusions"]["ages"]))
    shifted = dict(inp, t_exec={k: (v + 2 if isinstance(v, (int, float)) and not k.endswith("_status") else v)
                                for k, v in inp["t_exec"].items()})
    print("EXAMPLE ages with a 2 s fade=%s" % json.dumps(decide.replay_exclusions(shifted)["ages"]))
    print("EXAMPLE excluded (logged) = %s" % inp["exclusions"]["excluded"])
if changed and equal == changed and any(t > 0 for t in transitions_seen):
    print("RESULT CONFIRMED: every changed channel logs t_exec == ts (0 s transition) while den_lighting fades over %s s"
          % sorted(t for t in transitions_seen if t))
else:
    print("RESULT NOT REPRODUCED")
'''


def main():
    root = os.path.abspath(sys.argv[1])
    work = tempfile.mkdtemp(prefix="verify-r9-texec-")
    try:
        for rel in COPY:
            src = os.path.join(root, rel)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(work, rel), ignore=shutil.ignore_patterns("__pycache__"))
        core = os.path.join(work, "tools", "den-core")
        p = subprocess.run([sys.executable, "-B", "-c", DRIVER], cwd=core, capture_output=True, text=True, timeout=900)
        print(p.stdout.strip())
        if p.returncode:
            print("FAIL driver error:\n" + p.stderr.strip()[-3000:])
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
