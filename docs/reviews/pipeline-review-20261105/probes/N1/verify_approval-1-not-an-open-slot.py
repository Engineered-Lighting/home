#!/usr/bin/env python3
"""Verify probe for finding approval-1-not-an-open-slot (den-label, N1).

What it checks (static plus in-process, no takes needed)
  1. PROTOCOL-V1.md's settings block parses with load_protocol() and carries
     no 'open' marker or okay field (the finding's premise).
  2. load_protocol() rejects any extra key (so an 'okayed = null' slot could
     not be added without a code change) and rejects a null value.
  3. n1.py / label.py hold no okay / approval / open-slot check (grep).
  4. What IS recorded about the phone band: the labels manifest writes
     phone_at_counter_band and the protocol sha256 (n1.py source), and
     status() prints the band on its first line (run on an empty workspace).

How to run
  python3 -B verify_approval-1-not-an-open-slot.py <den-label checkout>
Temp dirs only. No network.
"""
import re
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path[:0] = [str(root / "tools/den-label"), str(root / "tools/den-take")]
    import n1
    proto_path = root / "tools/den-label/PROTOCOL-V1.md"
    text = proto_path.read_text(encoding="utf-8")
    p = n1.load_protocol(proto_path)
    print("RESULT protocol settings:", p.settings)
    block = re.findall(r"^```n1-protocol-v1\n(.*?)^```", text, re.M | re.S)[0]
    has_open = bool(re.search(r"\bopen\b|okay", block.split("\n", 1)[1] if block else "", re.I))
    print("RESULT settings block lines mention open/okay (outside the phone comment):",
          [l for l in block.splitlines() if re.search(r"open|okay", l, re.I)])
    tmp = Path(tempfile.mkdtemp(prefix="n1-verify-okay-"))
    extra = tmp / "PROTOCOL-V1.md"
    extra.write_text(text.replace('pad_min_px = 16', 'pad_min_px = 16\nokayed = null'), encoding="utf-8")
    try:
        n1.load_protocol(extra)
        print("RESULT protocol with 'okayed = null' accepted")
    except ValueError as exc:
        print(f"RESULT protocol with 'okayed = null' refused: {exc}")
    src = {name: (root / "tools/den-label" / name).read_text(encoding="utf-8") for name in ("n1.py", "label.py")}
    hits = {name: [i + 1 for i, l in enumerate(s.splitlines()) if re.search(r"okay|approv|\bslot\b", l, re.I)]
            for name, s in src.items()}
    print("RESULT okay/approv/slot lines in code:", hits)
    rec = [i + 1 for i, l in enumerate(src["n1.py"].splitlines()) if '"phone_at_counter_band": self.protocol' in l]
    print("RESULT labels manifest records phone_at_counter_band at n1.py lines:", rec)
    takes = tmp / "takes"
    takes.mkdir()
    ws = n1.Workspace(takes, takes / "n1")
    first = ws.status()[0]
    print("RESULT status first line:", first)
    no_enforcement = not any(hits.values())
    recorded = bool(rec) and "phone band casual" in first
    print(f"RESULT no okay enforcement in code: {no_enforcement}; band in effect is recorded: {recorded}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
