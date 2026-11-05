#!/usr/bin/env python3
"""Probe: replay determinism and per-decision latency for rung 9 (decide) on synthetic replays.

    python3 -B probe_v_replay_determinism_latency.py <den-core-live checkout root>

1. Runs `run.py --synthetic evening --touch --game --policies need incumbent decide --compensation P1` twice into
   two new temp folders (outside every git checkout) and compares the two log.jsonl files byte for byte; runs
   check.py on one (exit 0 expected).
2. Runs holds.report() twice and compares the JSON bytes; checks that in every case under every option each held
   bulb is requested and applied ('held') at its pinned read-back value.
3. Times every decide.handle() call that makes a decision during score.replay_timed() on the P1 and none-v0
   evening logs, with wall time (as score.py does) and this process's CPU time, against the 100 ms bound.
Synthetic data only; temp folders only, removed at the end; no host contacted. Prints PASS/FAIL per part.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time


def main():
    root = os.path.abspath(sys.argv[1])
    core = os.path.join(root, "tools", "den-core")
    sys.dont_write_bytecode = True
    sys.path.insert(0, core)
    import decide
    import holds
    import score

    bad = 0
    tmp = tempfile.mkdtemp(prefix="r9v-det-")
    try:
        logs = {}
        for option in ("P1", "none-v0"):
            paths = []
            for n in (1, 2):
                out = os.path.join(tmp, "%s-%d" % (option, n))
                proc = subprocess.run([sys.executable, "-B", os.path.join(core, "run.py"), "--out", out, "--synthetic",
                                       "evening", "--touch", "--game", "--policies", "need", "incumbent", "decide",
                                       "--compensation", option], capture_output=True, text=True, cwd=tmp)
                if proc.returncode:
                    print("FAIL run.py %s run %d: %s" % (option, n, proc.stderr.strip()[-300:]))
                    return 1
                paths.append(os.path.join(out, "log.jsonl"))
            a, b = (open(p, "rb").read() for p in paths)
            same = a == b
            bad += not same
            n_dec = sum(1 for line in a.decode("ascii").splitlines()[1:]
                        if '"kind":"decision"' in line and '"source":"decide"' in line)
            print("%s determinism %s: two runs byte-identical=%s (%d bytes, %d decide decisions)"
                  % ("PASS" if same else "FAIL", option, same, len(a), n_dec))
            chk = subprocess.run([sys.executable, "-B", os.path.join(core, "check.py"), paths[0]], capture_output=True,
                                 text=True, cwd=tmp)
            print("%s check.py %s: %s" % ("PASS" if chk.returncode == 0 else "FAIL", option, chk.stdout.strip()))
            bad += chk.returncode != 0
            logs[option] = paths[0]

        r1 = json.dumps(holds.report(), sort_keys=True)
        r2 = json.dumps(holds.report(), sort_keys=True)
        print("%s holds.report() twice byte-identical=%s" % ("PASS" if r1 == r2 else "FAIL", r1 == r2))
        bad += r1 != r2
        pin_bad = 0
        for case in holds.CASES:
            for option in holds.OPTIONS:
                fed, out = holds.run_case(case, option)
                mode = None
                for e in sorted(fed + out, key=lambda e: e["seq"]):
                    if e["kind"] == "mode":
                        mode = e["data"]
                    if e["source"] != "decide":
                        continue
                    d = e["data"]
                    for h in mode["holds"]:
                        b = h["entity_id"]
                        want = {k: h["pinned"][k] for k in ("on", "brightness", "kelvin")}
                        if d["requested_scene"].get(b) != want or d["applied_scene"][b]["status"] != "held" \
                                or d["applied_scene"][b]["readback"] != want:
                            pin_bad += 1
                            print("FAIL pinned %s %s %s at %s" % (case, option, b, e["t"]))
        print("%s held bulbs requested and applied at their pinned read-back in every case and option"
              % ("PASS" if not pin_bad else "FAIL"))
        bad += pin_bad

        for option, path in logs.items():
            cpu = []
            orig = decide.make_policy

            def make(config, _orig=orig):
                policy = _orig(config)
                handle = policy.handle

                def timed(event):
                    c0 = time.process_time()
                    out = handle(event)
                    if out:
                        cpu.append(time.process_time() - c0)
                    return out
                policy.handle = timed
                return policy
            result, spans, slowest = score.replay_timed(path)
            decide.make_policy = make
            try:
                result2, _, _ = score.replay_timed(path)
            finally:
                decide.make_policy = orig
            ok = result["ok"] and spans and max(spans) < 0.1
            print("%s latency %s: replay ok=%s, %d decisions, slowest wall %.2f ms (median %.3f ms), slowest cpu %.2f ms"
                  % ("PASS" if ok else "FAIL", option, result["ok"], len(spans), 1000 * max(spans),
                     1000 * sorted(spans)[len(spans) // 2], 1000 * max(cpu) if cpu else float("nan")))
            bad += not ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("RESULT %s" % ("all PASS" if not bad else "%d FAIL" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
