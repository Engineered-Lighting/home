"""Verifier probe for finding none-band-episode-gate-dropped (N2, den-n2 score.py).

Builds den-n2's own synthetic held-out World (temp folder, fake check.py), scores it, and prints the sample
gates score.py applies, the count of none-band (quiet) units from the cases table, and the verdict. Checks
whether a feasible verdict is given while the none band has fewer than sample.units (30) units, and whether
the report carries any none-band unit count.

Run:  python3 -B verify_none-band-episode-gate-dropped.py <den-n2 checkout root>
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402

with tempfile.TemporaryDirectory(prefix="n2-verify-none-") as tmp:
    world = T.World(Path(tmp), T.FOUR_DAYS)
    report, _ = world.run("held_out")
    names = [g["name"] for g in report["sample_gates"] if g["name"].startswith("units.")]
    none_units = sum(c["units"] for c in report["cases"].values() if c["band"] == "none")
    print(f"RESULT unit-count gates applied: {names} (sample.units={world and 30})")
    print(f"RESULT none-band units (quiet cases): {none_units}; phone band: {report['variant']['phone_band']}")
    print(f"RESULT verdict={report['verdict']} reasons={report['reasons']}")
    print("RESULT report keys mentioning none units:",
          [k for k in report if "none" in k] + [g["name"] for g in report["sample_gates"] if "none" in g["name"]])
    if report["verdict"] == "feasible" and none_units < 30:
        print(f"FAIL-IF-OWNER-MEANT-THREE-BANDS feasible with {none_units} none-band units (< 30)")
