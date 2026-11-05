"""verify_staged-end-slate-page-not-restored: do the staged-take owner steps say to bring the slate page back
before the end slate, after the script has had A use the phone?

What it checks (file reads only, no camera, no network)
  * counts the phone blocks in tools/den-take/scripts/*.json (case phone_at_counter);
  * looks in tools/den-take/README.md (the owner's steps) and take.py's spoken end-slate cues for any
    instruction to reopen / bring back / switch to the slate page before the end slate;
  * prints the README's Step 8 slate sentence and the Step 3 fallback (server stopped after loading).

How to run
  python3 -B verify_staged-end-slate-page-not-restored.py <den-take-script checkout root>
"""
import json, re, sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tools = root / "tools" / "den-take"
for p in sorted((tools / "scripts").glob("*.json")):
    blocks = json.loads(p.read_text(encoding="utf-8"))["blocks"]
    phone = [b["at_s"] for b in blocks if b["case"] == "phone_at_counter"]
    print(f"RESULT {p.name}: {len(phone)} phone_at_counter blocks at {phone} s; end slate at "
          f"{json.loads(p.read_text(encoding='utf-8'))['end_s']} s")
readme = (tools / "README.md").read_text(encoding="utf-8")
take = (tools / "take.py").read_text(encoding="utf-8")
pat = re.compile(r"(re-?open|open (the )?slate|slate page again|back to the slate|switch to the slate|"
                 r"bring (up|back) the slate)", re.I)
print(f"RESULT README lines asking to reopen the slate page: {[l for l in readme.splitlines() if pat.search(l)]}")
print(f"RESULT take.py lines asking to reopen the slate page: {[l.strip() for l in take.splitlines() if pat.search(l)]}")
print("RESULT spoken end-slate cue:", re.findall(r'cue\(f"hold_phone_\{which\}", "([^"]+)"\)', take))
for i, line in enumerate(readme.splitlines(), 1):
    if "slates are as in Step 6" in line or "then stop the server" in line or "pocket between" in line:
        print(f"RESULT README.md:{i}: {line.strip()}")
missing = not any(pat.search(l) for l in readme.splitlines() + take.splitlines())
print("FAIL: no owner step or cue says to bring the slate page back for the end slate" if missing else "PASS")
