#!/usr/bin/env python3
"""verify_multiday-run-id-collision: independent re-check (skeptical verifier, R10).

What it checks (synthetic only, temp dir, no network): runs den-verify's CLI
(verify.py run, every slot closed, as a subprocess like the owner would) on two
evenings of LightStates without an `id` key (as den-photometry writes them),
one decision per evening. Reports the exit code, whether a traceback was
printed, and whether anything was written to --out. Then runs each evening
alone and prints the interval status.

Run:
    python3 verify_multiday-run-id-collision.py <root of rung-10-verify checkout>
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-verify"
sys.dont_write_bytecode = True
sys.path.insert(0, str(tool))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
from records import write_jsonl  # noqa: E402

B0 = syn.HUE[0]
DAY = 86400.0
model = syn.hue_model({"p": {B0: 100.0}}, kelvins=(2700,))


def evening(t0, tag):
    frames = [syn.light_state(t0 + 3 * i, {"p": 0.0 if i == 0 else 100.0}, anchored=False) for i in range(40)]
    frames[0]["id"] = f"ls-ref-{tag}"   # the read frame carries an id so the decision can name it
    dec = syn.hue_decision(f"d-{tag}", t0 + 0.5, {B0: 0.0},
                           {B0: {"level": 1.0, "steps": [{"kind": "send", "sent_at": t0 + 0.5}]}},
                           f"ls-ref-{tag}", targets={"A": {"surface_id": "p"}})
    return [dec], frames


def cli(tmp, decs, frames, out):
    write_jsonl(tmp / "d.jsonl", decs)
    write_jsonl(tmp / "l.jsonl", frames)
    p = subprocess.run([sys.executable, "-B", str(tool / "verify.py"), "--method", str(tmp / "V.md"), "run",
                        "--decisions", str(tmp / "d.jsonl"), "--light-states", str(tmp / "l.jsonl"),
                        "--model", str(tmp / "m.json"), "--utc-offset-h", "-7", "--out", str(tmp / out)],
                       capture_output=True, text=True, cwd=str(tmp))
    return p


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / "m.json").write_text(json.dumps(model), encoding="utf-8")
    (tmp / "V.md").write_text(method.closed_copy(method.lf_text(tool / "VERIFY-V1.md")), encoding="utf-8")
    d1, f1 = evening(syn.T0, "1")
    d2, f2 = evening(syn.T0 + DAY, "2")
    p = cli(tmp, d1 + d2, f1 + f2, "out2")
    wrote = sorted(x.name for x in (tmp / "out2").iterdir()) if (tmp / "out2").exists() else []
    print(f"RESULT two evenings: exit {p.returncode}; traceback {'Traceback' in p.stderr}; "
          f"last stderr line {p.stderr.strip().splitlines()[-1] if p.stderr.strip() else None!r}; wrote {wrote}")
    p1 = cli(tmp, d1, f1, "out1")
    rep = json.loads((tmp / "out1" / "report.json").read_text())
    print(f"RESULT one evening: {p1.stdout.strip().splitlines()[0]}; interval {rep['hit_rate']['interval']} "
          f"({rep['hit_rate']['interval_status']})")
    if p.returncode != 0 and "Traceback" in p.stderr and not wrote:
        print("FAIL reproduced: a two-evening run dies with a traceback and writes nothing")
    else:
        print("PASS not reproduced")
