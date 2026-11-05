#!/usr/bin/env python3
"""probe_split_rule_guard_scope: what load_split's `split == split_for(order)` check stops, and what it does not.

Verifier probe for finding split-rule-recheck-untested (INTENT lens). Builds a synthetic split.json
with n1.register (3 sessions: a1, a2 hard-a; b1 hard-b), then edits it three ways and recomputes
every `prev` and the `head` hash (the chain has no secret):
  1. flip: a2's split held_out -> development (the finding's case);
  2. swap: a1 and a2 trade places (session, started_unix), so a2 becomes development, a1 held_out;
  3. naive flip without recomputing the chain (what SplitTests.test_refuses_an_edited_split_json does).
Each is loaded by the checkout's n1.load_split and by a copy with the rule check removed (mutant M4),
made in the given work dir. Prints accepted/refused for each.
Synthetic data only; temp dirs only; no network.
Run:
    python3 probe_split_rule_guard_scope.py /path/to/den-label-checkout /path/to/scratch/mut/N1-intent
"""
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()
RULE = ('                or entry["order"] != counts[entry["script"]] or entry["split"] != split_for(entry["order"])):')
NO_RULE = '                or entry["order"] != counts[entry["script"]]):'


def load_module(name, folder):
    sys.path.insert(0, str(folder.parent / "den-take"))
    spec = importlib.util.spec_from_file_location(name, folder / "n1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rechain(n1, data):
    prev = None
    for entry in data["entries"]:
        entry["prev"] = prev
        prev = n1.entry_hash(entry)
    data["head"] = prev
    return data


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="split-guard-", dir=WORK))
    try:
        mut = tmp / "tools"
        for sub in ("den-label", "den-take"):
            shutil.copytree(ROOT / "tools" / sub, mut / sub, ignore=shutil.ignore_patterns("__pycache__"))
        target = mut / "den-label" / "n1.py"
        text = target.read_text(encoding="utf-8")
        assert text.count(RULE) == 1, "rule line not found exactly once"
        target.write_text(text.replace(RULE, NO_RULE), encoding="utf-8", newline="\n")
        orig = load_module("n1_orig", ROOT / "tools" / "den-label")
        m4 = load_module("n1_m4", mut / "den-label")
        path = tmp / "split.json"
        orig.register(path, [("a1", "hard-a", 1.0), ("a2", "hard-a", 2.0), ("b1", "hard-b", 3.0)])
        base = json.loads(path.read_text(encoding="ascii"))
        print("RESULT registered:", [(e["session"], e["order"], e["split"]) for e in base["entries"]])

        def flip():
            d = json.loads(json.dumps(base))
            d["entries"][1]["split"] = "development"
            return rechain(orig, d)

        def swap():
            d = json.loads(json.dumps(base))
            e0, e1 = d["entries"][0], d["entries"][1]
            (e0["session"], e0["started_unix"]), (e1["session"], e1["started_unix"]) = \
                (e1["session"], e1["started_unix"]), (e0["session"], e0["started_unix"])
            return rechain(orig, d)

        def naive():
            d = json.loads(json.dumps(base))
            d["entries"][1]["split"] = "development"
            return d

        for name, make in (("flip+rechain", flip), ("swap+rechain", swap), ("naive flip (no rechain)", naive)):
            data = make()
            path.write_text(json.dumps(data), encoding="ascii")
            for label, module in (("checkout", orig), ("M4 (rule check removed)", m4)):
                try:
                    got = module.load_split(path)
                    print(f"RESULT {name:24} {label:24} ACCEPTED splits {[(e['session'], e['split']) for e in got]}")
                except module.Refused as exc:
                    print(f"RESULT {name:24} {label:24} refused: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
