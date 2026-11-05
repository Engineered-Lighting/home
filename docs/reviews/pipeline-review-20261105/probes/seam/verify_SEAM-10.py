#!/usr/bin/env python3
"""verify_SEAM-10: den-register's pose CalibrationRecord keys n_pairs, loo_m_max, error_bound_m, inputs.

    TMPDIR=<scratch dir> python3 -B verify_SEAM-10.py <rung-1-register checkout> [<schemas.json>]

schemas.json defaults to <checkout>/docs/architecture/live-v1/schemas.json.
Synthetic data only, temp dirs only, no network, python -B in the child.

Steps
 1. den-register registers its synthetic scene (its tests' path).
 2. The CalibrationRecord's keys, against the canon field list of
    calibration_record (literal names, plus the residual/confidence family
    that the canon writes "with the unit in the key name").
 3. Is anything only in the CalibrationRecord? The leave-one-out maximum and
    the error bound, against the RoomModel camera entry's canon
    error_bound_m (the field consumers read).
Prints PASS/FAIL/RESULT lines.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

REGISTER = r'''
import json, sys, types
sys.dont_write_bytecode = True
reg, tmp = sys.argv[1], sys.argv[2]
sys.path.insert(0, reg)
import test_den_register as TR
s = TR.Scene(types.SimpleNamespace(tmp=types.SimpleNamespace(name=tmp)))
s.click_all()
code, report = s.fit()
print(json.dumps({"code": code, "rm": s.room_models()[-1], "cal": s.calibrations()[-1]}))
'''


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).resolve()
    schemas = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "docs" / "architecture" / "live-v1" / "schemas.json"
    tmp = Path(tempfile.mkdtemp(prefix="verify-seam10-"))
    proc = subprocess.run([sys.executable, "-B", "-c", REGISTER, str(root / "tools" / "den-register"), str(tmp)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print("FAIL setup\n" + proc.stderr[-2000:])
        return 2
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    cal, entry = info["cal"], info["rm"]["cameras"][-1]
    spec = next(r for r in json.loads(schemas.read_text(encoding="utf-8"))["records"] if r["id"] == "calibration_record")
    canon = set()
    for f in spec["fields"]:
        canon.update(n.strip() for n in f["name"].split("/"))
    family = ("residual_", "confidence_", "supersedes_", "vs_manual_")
    extra = sorted(k for k in cal if k not in canon and not k.startswith(family) and not k.endswith("_note"))
    print(f"RESULT step2 record keys {sorted(cal)}")
    print(f"RESULT step2 keys outside the canon names and the residual_/confidence_ family: {extra}")
    print(f"RESULT step2 canon example keys of the fit record: {sorted(spec['example'][1])}")
    print(f"RESULT step2 inputs sub-keys: {sorted(cal['inputs'])}")
    pairs = cal["inputs"]["pairs"]
    fit_res = [p["residual_m"] for p in pairs if p.get("role") == "fit"]
    checks = [p["error_m"] for p in pairs if p.get("role") == "check"]
    loos = [p["loo_m"] for p in pairs if "loo_m" in p]
    folded = max(fit_res + checks + loos)
    same = abs(entry["error_bound_m"] - round(folded, 6)) < 1e-6 and abs(cal["error_bound_m"] - entry["error_bound_m"]) < 1e-9
    print(("PASS" if same else "FAIL") + f" step3 RoomModel camera error_bound_m {entry['error_bound_m']} == record "
          f"error_bound_m {cal['error_bound_m']} == max(fit residuals, check errors, leave-one-out) {round(folded, 6)}; "
          f"record loo_m_max {cal['loo_m_max']} <= bound: {cal['loo_m_max'] is None or cal['loo_m_max'] <= entry['error_bound_m'] + 1e-9}")
    print("RESULT SEAM-10: extra keys exist; the bound (which includes the leave-one-out maximum) is in the canon "
          "RoomModel field" if same else "RESULT SEAM-10: the bound is NOT carried by the RoomModel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
