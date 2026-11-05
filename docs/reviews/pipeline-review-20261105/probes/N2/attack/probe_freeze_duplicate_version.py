"""Probe: does score.py accept labels changed after their freeze when FREEZES.md logs the same version twice?

N1's own `label.py verify` (n1.Workspace.verify) fails "one version logged twice with different hashes".
score.py's frozen_manifest() accepts a manifest whose hash matches ANY freeze line of that version.

Steps (synthetic data, temp folder, the test module's World builder):
  1. build a frozen labels-v1 and score held-out: note the verdict;
  2. edit one held-out session's labels after the freeze (every fine interval relabelled casual), rewrite
     labels.json canonically with the new file hash, and append a SECOND `den-n1 labels-v1 sha256=...` line;
  3. run n1's verify over the same folder and FREEZES text: does it flag the double line?
  4. run score.py --split held_out again: refused, or scored on the changed labels?

Run:  python3 -B probe_freeze_duplicate_version.py <root of the den-n2 checkout>
"""
import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402
import score  # noqa: E402
n1 = T.n1


def main():
    ok = True
    with tempfile.TemporaryDirectory(prefix="n2-probe-freeze-") as tmp:
        world = T.World(Path(tmp), T.FOUR_DAYS)
        before, _ = world.run("held_out")
        print(f"RESULT 1 frozen labels: verdict={before['verdict']} units.fine="
              f"{[g['n'] for g in before['sample_gates'] if g['name'] == 'units.fine']}")
        labels_dir = world.n1 / "labels-v1"
        take = world.held_out()[0]
        rel = f"sessions/{take}.json"
        record = json.loads((labels_dir / rel).read_bytes())
        for x in record["intervals"]:
            if x["band"] == "fine":
                x["band"] = "casual"
        (labels_dir / rel).write_bytes(n1.canonical(record))
        manifest = json.loads((labels_dir / "labels.json").read_bytes())
        for s in manifest["sessions"]:
            if s["file"] == rel:
                s["sha256"] = n1.sha256_bytes(n1.canonical(record))
        (labels_dir / "labels.json").write_bytes(n1.canonical(manifest))
        new_hash = n1.sha256_bytes(n1.canonical(manifest))
        text = world.freezes.read_text(encoding="utf-8")
        text += f"den-n1 labels-v1 sha256={new_hash} protocol=x frozen=x\n"
        world.freezes.write_text(text, encoding="utf-8")
        print("RESULT FREEZES.md labels lines:", [l[:40] for l in text.splitlines() if "labels-v1" in l])
        verified, lines = n1.Workspace.verify(types.SimpleNamespace(root=world.n1), text)
        print(f"RESULT 3 n1 verify: ok={verified} lines={lines}")
        if verified:
            print("FAIL setup: expected n1 verify to flag the double line")
            ok = False
        try:
            after, _ = world.run("held_out")
            fine = [g["n"] for g in after["sample_gates"] if g["name"] == "units.fine"]
            print(f"RESULT 4 score.py after the post-freeze edit: verdict={after['verdict']} units.fine={fine} "
                  f"labels hash={after['gold_set']['hash'][:12]}")
            print("FAIL score.py scored labels changed after their freeze (a version logged twice with different "
                  "hashes), which N1's own verify rejects")
            ok = False
        except score.Refused as exc:
            print(f"PASS refused: {exc}")
    print("RESULT overall:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
