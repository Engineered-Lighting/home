"""Diagnose SplatTransform CLI semantics: -t alone, -r alone (90deg about Z)."""
import subprocess
import sys
from pathlib import Path

import numpy as np

from pipeline_util import OUT, read_3dgs_ply

HERE = Path(__file__).resolve().parent
ST = HERE / "node_modules" / ".bin" / "splat-transform.cmd"
probe = OUT / "_probe.ply"
pos, _, _ = read_3dgs_ply(probe)

def run(args, name):
    out = OUT / f"_d_{name}.ply"
    r = subprocess.run([str(ST), "-w", str(probe), *args, str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(name, "FAILED", r.stderr[-150:])
        return None
    got, _, _ = read_3dgs_ply(out)
    return got

# translation only — print the actual delta
got = run(["-t", "10,20,30"], "t")
if got is not None:
    delta = np.median(got - pos, axis=0)
    print(f"-t 10,20,30 actual median delta: {delta.round(3)}")

got = run(["-r", "0,90,0"], "ry")
if got is not None:
    Ry = np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], float)
    for name, M in [("Ry(+90)", Ry), ("Ry(-90)", Ry.T)]:
        err = np.median(np.linalg.norm(got - pos @ M.T, axis=1))
        print(f"-r 0,90,0 vs {name}: median err {err:.4f} m")

# rotation only: 90 about Z
Rz = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], float)
got = run(["-r", "0,0,90"], "rz")
if got is not None:
    for name, M in [("Rz(+90)", Rz), ("Rz(-90)", Rz.T)]:
        err = np.median(np.linalg.norm(got - pos @ M.T, axis=1))
        print(f"-r 0,0,90 vs {name}: median err {err:.4f} m")

# rotation 90 about X
Rx = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)
got = run(["-r", "90,0,0"], "rx")
if got is not None:
    for name, M in [("Rx(+90)", Rx), ("Rx(-90)", Rx.T)]:
        err = np.median(np.linalg.norm(got - pos @ M.T, axis=1))
        print(f"-r 90,0,0 vs {name}: median err {err:.4f} m")

# combined: -r then -t in arg order
got = run(["-r", "0,0,90", "-t", "10,20,30"], "rt")
if got is not None:
    a = np.median(np.linalg.norm(got - (pos @ Rz.T + [10, 20, 30]), axis=1))
    b = np.median(np.linalg.norm(got - ((pos + [10, 20, 30]) @ Rz.T), axis=1))
    print(f"-r -t: vs R x + t = {a:.4f} | vs R(x+t) = {b:.4f}")
