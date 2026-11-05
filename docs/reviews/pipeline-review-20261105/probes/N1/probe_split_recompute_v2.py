#!/usr/bin/env python3
"""Independent recompute of N1's whole-session split, and the no-session-in-both-parts property.

What it checks (pure functions on a temp split.json; synthetic names only):
  1. n1.register over random registration batches against this file's own model
     of the rule: within each script name, in the order sessions are appended
     (each batch sorted by started_unix, then name), development, held-out,
     development, ...; the first of each script is development; a known session
     is never re-assigned.
  2. No session ever appears twice in split.json, so none is in both parts.
  3. A split.json whose split field was flipped AND whose hash chain was
     recomputed (so only the split-rule check can catch it) is refused.
  4. RESULT: how far the appended order departs from recording order when a
     take is registered late (the protocol says "in recording order").

Run:  python3 -B probe_split_recompute_v2.py <den-label branch root>
"""
import hashlib
import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import n1  # noqa: E402

fails = []


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok:
        fails.append(what)


def my_hash(entry):
    return hashlib.sha256((json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
                          .encode("ascii")).hexdigest()


def main():
    rng = random.Random(7)
    bad_model = bad_dupe = 0
    for trial in range(400):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "split.json"
            model, counts = {}, {}
            pool = [(f"t{i}", rng.choice(["hard-a", "hard-b", "hard-c"]), round(rng.uniform(0, 1e6), 3))
                    for i in range(rng.randint(1, 14))]
            for _ in range(rng.randint(1, 4)):
                batch = rng.sample(pool, rng.randint(1, len(pool)))
                added = n1.register(path, batch)
                expect = []
                for take_id, script, started in sorted(batch, key=lambda s: (s[2], s[0])):
                    if take_id in model:
                        continue
                    counts[script] = counts.get(script, 0) + 1
                    model[take_id] = "development" if counts[script] % 2 == 1 else "held_out"
                    expect.append((take_id, model[take_id]))
                if [(e["session"], e["split"]) for e in added] != expect:
                    bad_model += 1
            entries = n1.load_split(path)
            names = [e["session"] for e in entries]
            if len(names) != len(set(names)) or {e["session"]: e["split"] for e in entries} != model:
                bad_dupe += 1
    check(bad_model == 0, f"400 random registration histories: register equals my rule ({bad_model} mismatches)")
    check(bad_dupe == 0, f"no session twice in split.json, and every split equals my model ({bad_dupe} bad)")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "split.json"
        n1.register(path, [("a1", "hard-a", 1.0), ("a2", "hard-a", 2.0), ("b1", "hard-b", 3.0)])
        data = json.loads(path.read_text(encoding="ascii"))
        data["entries"][1]["split"] = "development"  # a2 flipped to development
        prev = None
        for e in data["entries"]:
            e["prev"] = prev
            prev = my_hash(e)
        data["head"] = prev
        path.write_text(json.dumps(data), encoding="ascii")
        try:
            n1.load_split(path)
            refused = False
        except n1.Refused:
            refused = True
        check(refused, "a flipped split with a recomputed hash chain is refused by the split-rule check")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "split.json"
        n1.register(path, [("a2", "hard-a", 200.0), ("b1", "hard-b", 150.0)])  # a1 still blocked by its check
        n1.register(path, [("a1", "hard-a", 100.0)])  # a1 accepted later
        got = {e["session"]: e["split"] for e in n1.load_split(path)}
        print(f"RESULT late registration: recording order a1(100) a2(200) -> splits {got}; by recording order "
              f"a1 would be development and a2 held_out")
    print("RESULT", "FAIL" if fails else "PASS", f"({len(fails)} failed checks)")


if __name__ == "__main__":
    main()
