"""Probe: does score.py --split held_out check that the ask lines follow the gate's pacing_s?

GATE-V1's `pacing_s` is a slot the development step sets (plan section 1(a)); the held-out asks must run at
that P, or onset is scored at a cadence the gate did not declare. score.py takes `pacing` from the gate,
computes w and m from it, and writes it to the EvalRecord's variant, but never compares it with the ask lines'
r values (r = a + k*P on each segment). This probe builds a held-out world whose asks run every 4 s and
scores it under a gate that declares pacing_s = 2 (and, separately, 8).

Run:  python3 -B probe_pacing_unverified.py <root of the den-n2 checkout>
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402
import score  # noqa: E402


def main():
    ok = True
    for pacing in (2, 8):
        with tempfile.TemporaryDirectory(prefix="n2-probe-pacing-") as tmp:
            world = T.World(Path(tmp), T.FOUR_DAYS)
            world.gate.write_text(T.gate_text(T.filled(pacing_s=pacing)), encoding="utf-8")
            try:
                report, _ = world.run("held_out")
            except score.Refused as exc:
                print(f"PASS pacing_s={pacing} with 4 s asks refused: {exc}")
                continue
            rs = sorted({o["r_next"] - o["t_on"] for o in report["onsets"]})[:3]
            print(f"RESULT gate pacing_s={pacing}, asks every 4 s: verdict={report['verdict']} "
                  f"variant.pacing_s={report['variant']['pacing_s']} first w values={rs} D={report['service']['D']}")
            print(f"FAIL the EvalRecord declares pacing_s={pacing} for asks made every 4 s, with no refusal")
            ok = False
    print("RESULT overall:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
