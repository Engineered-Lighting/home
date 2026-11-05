#!/usr/bin/env python3
"""verify_mutant_semantics: are mutants M3, M12 (verify_folder) and M4 (load_split) real behaviour changes?

For findings verify-extra-file-canonical-untested and split-rule-recheck-untested. For the original
n1.py and for each mutant (applied to a temp copy of tools/den-label + tools/den-take), runs in a
subprocess:
  A. a hand-built frozen fixture folder (write_frozen) plus one extra file -> verify_folder problems
  B. the same folder with its manifest rewritten non-canonically, same content -> verify_folder problems
  C. split.json from n1.register; one held_out entry flipped to development with every prev and the
     head recomputed (the chain has no secret) -> does load_split refuse?
Synthetic data only, temp dirs only, no network.
Run:
    python3 verify_mutant_semantics.py /path/to/den-label-checkout /path/to/mut/work/dir
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()

MUTANTS = {
    "original": [],
    "M3": [('    for rel in sorted(present - set(expected)):', '    for rel in sorted(set()):')],
    "M12": [('    if canonical(manifest) != raw:', '    if False:')],
    "M4": [('                or entry["order"] != counts[entry["script"]] or entry["split"] != split_for(entry["order"])):',
            '                or entry["order"] != counts[entry["script"]]):')],
}

SCENARIO = r'''
import json, os, stat, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import n1
tmp = Path(tempfile.mkdtemp(prefix="sem-", dir=sys.argv[2]))
smap = n1.canonical({"schema": "den-n1-surface-map-1", "surfaces": []})
box = b"n,t_s,x0,y0,x1,y1\n1,0.0,1,2,3,4\n"
manifest = {"schema": "den-n1-fixture-1", "version": 1,
            "surface_map": {"file": "surface-map.json", "sha256": n1.sha256_bytes(smap)},
            "sessions": [{"take": "a1", "boxes": {"file": "boxes/a1.csv", "sha256": n1.sha256_bytes(box)}}]}
files = {"surface-map.json": smap, "boxes/a1.csv": box, "fixture.json": n1.canonical(manifest)}
a = tmp / "A" / "fixture-v1"; a.parent.mkdir()
n1.write_frozen(a, files)
(a / "sessions").mkdir(); (a / "boxes" / "x.csv").write_bytes(b"stray\n")
print("A", n1.verify_folder(a)[1])
b = tmp / "B" / "fixture-v1"; b.parent.mkdir()
n1.write_frozen(b, files)
os.chmod(b / "fixture.json", stat.S_IRUSR | stat.S_IWUSR)
(b / "fixture.json").write_text(json.dumps(manifest, indent=1), encoding="ascii")
print("B", n1.verify_folder(b)[1])
path = tmp / "split.json"
n1.register(path, [("a1", "hard-a", 1.0), ("a2", "hard-a", 2.0), ("b1", "hard-b", 3.0)])
data = json.loads(path.read_text())
prev = None
for e in data["entries"]:
    if e["session"] == "a2":
        e["split"] = "development"
    e["prev"] = prev
    prev = n1.entry_hash(e)
data["head"] = prev
path.write_text(json.dumps(data))
try:
    print("C accepted", [(e["session"], e["split"]) for e in n1.load_split(path)])
except n1.Refused as exc:
    print("C refused", exc)
for p in tmp.rglob("*"):
    os.chmod(p, stat.S_IRWXU)
'''


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, repls in MUTANTS.items():
        work = Path(tempfile.mkdtemp(prefix=f"sem-{name}-", dir=WORK))
        for sub in ("den-label", "den-take"):
            shutil.copytree(ROOT / "tools" / sub, work / "tools" / sub, ignore=shutil.ignore_patterns("__pycache__"))
        target = work / "tools" / "den-label" / "n1.py"
        text = target.read_text(encoding="utf-8")
        for old, new in repls:
            assert text.count(old) == 1, (name, old)
            text = text.replace(old, new)
        target.write_text(text, encoding="utf-8", newline="\n")
        script = work / "scenario.py"
        script.write_text(SCENARIO, encoding="utf-8")
        proc = subprocess.run([sys.executable, "-B", str(script), str(work / "tools" / "den-label"), str(work)],
                              capture_output=True, text=True, timeout=300)
        results[name] = proc.stdout.strip().splitlines() + ([proc.stderr.strip()[-300:]] if proc.returncode else [])
        for line in results[name]:
            print(f"RESULT {name}: {line}")
        shutil.rmtree(work, ignore_errors=True)
    o = results["original"]
    print(("PASS " if "extra file" in o[0] else "FAIL ") + "original verify_folder reports the extra file")
    print(("PASS " if "not canonical" in o[1] else "FAIL ") + "original verify_folder reports the non-canonical manifest")
    print(("PASS " if o[2].startswith("C refused") else "FAIL ") + "original load_split refuses a recomputed-chain split flip")
    print(("CHANGED " if "extra file" not in results["M3"][0] else "SAME ") + "M3 no longer reports the extra file")
    print(("CHANGED " if "not canonical" not in results["M12"][1] else "SAME ") + "M12 no longer reports the non-canonical manifest")
    print(("CHANGED " if results["M4"][2].startswith("C accepted") else "SAME ") + "M4 accepts the recomputed-chain split flip")


if __name__ == "__main__":
    main()
