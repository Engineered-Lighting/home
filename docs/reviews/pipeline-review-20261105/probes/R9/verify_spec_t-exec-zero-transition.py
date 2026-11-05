#!/usr/bin/env python3
"""Spec-lens verification of finding t-exec-zero-transition (rung 9, tools/den-core/decide.py record()/t_exec()).

    python3 -B verify_spec_t-exec-zero-transition.py <den-core-live checkout root>

schemas.json Decision inputs.t_exec: "t_exec = max(t_settle, t_chk + T_chk + T_react) ... T_chk and T_react are
{{dec:freshness}}, so a kept channel's t_exec is null with a status". decide.t_exec() logs ts + transition for a
changed channel and argues t_settle dominates on a Hue bulb, but record() passes transitions = {}.
This probe runs one synthetic evening (need, incumbent, decide; the runner's synthetic generator, in memory, no
host, no file outside a temp dir) and prints, for every decide decision, the changed channels' t_exec - ts, and the
transitions den_lighting's own commands carried in the same log. Prints RESULT lines.
"""
import os
import shutil
import sys
import tempfile

COPY = ["tools/den-core", "ha-config/custom_components/den_lighting", "stack/services/perception-lab",
        "tools/la-latency"]


def main():
    root = os.path.abspath(sys.argv[1])
    work = tempfile.mkdtemp(prefix="verify-spec-r9-texec-")
    try:
        for rel in COPY:
            src = os.path.join(root, rel)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(work, rel), ignore=shutil.ignore_patterns("__pycache__"))
        core = os.path.join(work, "tools", "den-core")
        sys.path.insert(0, core)
        import runner  # noqa: E402
        import synthetic  # noqa: E402
        items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)
        header, log = runner.run(runner.tagged(items, "synthetic"),
                                 runner.load_policies(("need", "incumbent", "decide"), None),
                                 synthetic=True, sources=[{"name": "synthetic:evening", "params": {"touch": True}}])
        offsets, transitions_logged, incumbent_transitions = set(), [], {}
        changed = 0
        for e in log:
            if e["kind"] != "decision":
                continue
            d = e["data"]
            if e["source"] == "incumbent":
                for b, s in (d.get("transitions") or {}).items():
                    incumbent_transitions[s] = incumbent_transitions.get(s, 0) + 1
                continue
            if e["source"] != "decide":
                continue
            transitions_logged.append(d["transitions"])
            for b, t in d["inputs"]["t_exec"].items():
                if b.endswith("_status") or t is None:
                    continue
                changed += 1
                offsets.add(round(t - d["ts"], 6))
        print("RESULT decide decisions=%d changed-channel t_exec entries=%d distinct (t_exec - ts)=%s"
              % (len(transitions_logged), changed, sorted(offsets)))
        print("RESULT decide transitions fields seen: %s" % sorted({repr(t) for t in transitions_logged}))
        print("RESULT den_lighting command transitions in the same log (seconds: count): %s" % incumbent_transitions)
        print("RESULT %s: a changed channel's logged t_exec equals ts, i.e. t_settle = ts and the pending "
              "T_chk + T_react treated as 0" % ("REPRODUCED" if offsets == {0.0} and changed else "NOT REPRODUCED"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
