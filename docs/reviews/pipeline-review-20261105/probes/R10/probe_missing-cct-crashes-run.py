#!/usr/bin/env python3
"""probe_missing-cct-crashes-run: one channel with no cct_k aborts the whole run.

What it checks (synthetic only, temp dirs, no network). schemas.json's own
example Decision (d-190330-f1f2) lists F1.radial_4+5 as on at level 0.35 with
no cct_k in inputs.actuator_state and a readback of {level: 0.35}. den-verify's
synthetic V11 model gives that channel a single captured temperature, so the
case passes in its tests. Rung 8 captures three temperatures per channel; the
probe gives F1.radial_4+5 two captures (2700 K and 4000 K) and runs the command
line on the walkthrough records with every slot closed. A per-decision problem
should leave that decision unverified with a reason; the probe reports whether
the run survives and whether any output is written.

Run:
    python3 probe_missing-cct-crashes-run.py <root of the rung-10-verify checkout>
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
import verify as cli  # noqa: E402
from records import write_jsonl  # noqa: E402

model = syn.v11_model()
model["basis"]["F1.radial_4+5"]["4000"] = {"counter-1": 110.0, "armchair-book": 85.0}
d, f, c = syn.v11_evening()
text = method.closed_copy(method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md"))
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    write_jsonl(tmp / "d.jsonl", d)
    write_jsonl(tmp / "l.jsonl", f)
    write_jsonl(tmp / "c.jsonl", c)
    (tmp / "m.json").write_text(json.dumps(model), encoding="utf-8")
    (tmp / "VERIFY-V1.md").write_text(text, encoding="utf-8")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--method", str(tmp / "VERIFY-V1.md"), "run", "--decisions", str(tmp / "d.jsonl"),
                             "--light-states", str(tmp / "l.jsonl"), "--corrections", str(tmp / "c.jsonl"),
                             "--model", str(tmp / "m.json"), "--utc-offset-h", "-7", "--out", str(tmp / "out")])
        print(f"RESULT run finished, exit {code}: {buf.getvalue().strip()}")
        print("PASS the run survives a channel with no colour temperature")
    except Exception as e:  # noqa: BLE001
        written = sorted(p.name for p in (tmp / "out").iterdir()) if (tmp / "out").exists() else []
        print(f"RESULT the command line raised {type(e).__name__}: {e}; outputs written: {written}")
        print("FAIL the schema's own example decision aborts the whole run with a traceback once its "
              "on channel has more than one captured temperature")
