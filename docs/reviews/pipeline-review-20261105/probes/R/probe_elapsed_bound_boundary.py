"""probe_elapsed_bound_boundary: recompute CHECK-V1's slate offset, window and elapsed bound by hand.

What it checks
  * Builds synthetic takes with R's own test helper make_take() with a known start offset c0 and a
    known offset change D between the start and end slates (step_both=("begin", 15, D) shifts
    t_host and t_device together, so the end slate's true offset is c0 + D).
  * Recomputes each judged slate's consistent-offset window independently (own pixel subsampling,
    screen mask, percentiles, classes, pattern and change-distance code), on the tool's 1 ms grid and
    on a 0.1 ms grid, then the elapsed statistic |wrap(c_end - c_start)| + (w_start + w_end)/2.
  * Compares with check.py's A_elapsed_bound value and verdict, checks soundness (|D| <= bound)
    and reports where PASS turns into FAIL, including an exact-boundary case (bound == 0.200 s).
  * Prints how far the true (fine-grid) window extends past the tool's 1 ms-grid window.

How to run
  python3 -B probe_elapsed_bound_boundary.py <branch checkout root>
  e.g. python3 -B probe_elapsed_bound_boundary.py .../scratchpad/wt/rung-r
Synthetic data only, temp dirs only, no network.
"""
import io, json, math, shutil, sys, tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import check  # noqa: E402
import test_den_take as T  # noqa: E402  (R's own synthetic helpers)

M = check.load_method()


def lit(tau):
    s = math.floor(tau)
    k = s % 10
    if k == 0:
        return False
    f = tau - s
    return 0.02 * k <= f < 0.02 * k + 0.5


def near_change(tau, eps):
    s = math.floor(tau)
    for sec in (s - 1, s, s + 1):
        k = sec % 10
        if k == 0:
            continue
        rise = sec + 0.02 * k
        if abs(tau - rise) < eps or abs(tau - rise - 0.5) < eps:
            return True
    return False


def pct(values, p):
    v = sorted(values)
    pos = (len(v) - 1) * p / 100
    lo = math.floor(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def hand_fit(frames, grid):
    """frames: [(t_host, y-plane list)] -> (window [a, b] wrapped, width, mid) or None."""
    n = len(frames)
    cols = list(zip(*(y for _, y in frames)))
    stds = []
    for col in cols:
        mu = sum(col) / n
        stds.append(math.sqrt(max(0.0, sum((x - mu) ** 2 for x in col) / n)))
    top = max(stds)
    screen = [i for i, s in enumerate(stds) if s >= 0.5 * top]
    means = [sum(y[i] for i in screen) / len(screen) for _, y in frames]
    lo, hi = pct(means, 10), pct(means, 90)
    cls = []
    for mu in means:
        x = (mu - lo) / (hi - lo)
        cls.append(True if x > 0.75 else False if x < 0.25 else None)
    cons = [(t, c) for (t, _), c in zip(frames, cls) if c is not None]
    steps = round(10 / grid)
    ok = []
    for j in range(steps):
        c = -5 + j * grid
        if all(near_change(t - c, 0.025) or lit(t - c) == want for t, want in cons):
            ok.append(j)
    if not ok:
        return None
    runs = []
    for j in ok:
        if runs and j == runs[-1][1] + 1:
            runs[-1][1] = j
        else:
            runs.append([j, j])
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == steps - 1:
        runs[-1][1] = runs.pop(0)[1] + steps
    if len(runs) != 1:
        return ("windows", len(runs))
    a, b = -5 + runs[0][0] * grid, -5 + runs[0][1] * grid
    mid = ((a + b) / 2 + 5) % 10 - 5
    return (a, b), b - a, mid


def slate_inputs(folder, which, attempt):
    record = json.loads((folder / "take.json").read_text(encoding="utf-8"))
    slate = next(s for s in record["slates"] if s["slate"] == which and s["attempt"] == attempt)
    import csv
    rows = list(csv.DictReader(open(folder / "frames.csv", newline="", encoding="utf-8")))
    out = []
    for row in rows:
        recv = float(row["recv_pc_mono_s"])
        if slate["pc_start_s"] <= recv <= slate["pc_end_s"]:
            meta, rgb = check.read_bundle((folder / row["file"]).read_bytes(), with_rgb=True)
            y = [rgb[r * 640 + c] for r in range(0, 360, 4) for c in range(0, 640, 4)]
            out.append((meta["t_host"], y))
    return out


def wrap(v):
    return (v + 5) % 10 - 5


def main():
    tmp = Path(tempfile.mkdtemp(prefix="probe-elapsed-")).resolve()
    failures = 0
    try:
        cases = [(3.2137, d) for d in (0.0, 0.10, 0.12, 0.13, 0.14, 0.145, 0.15, -0.14, -0.15)]
        cases += [(4.98, 0.05), (-4.99, -0.03)]
        boundary_hits = []
        print("c0       D       tool_bound  hand_bound  fine_bound  verdict  hand_says  sound  extra_ms")
        for c0, d in cases:
            folder = T.make_take(tmp, c=c0, step_both=("begin", 15, d)) if d else T.make_take(tmp, c=c0)
            code, report, _ = T.run_check(folder)
            rule = report["rules"]["A_elapsed_bound"]
            fits, fine = {}, {}
            for which in ("start", "end"):
                judged = report["rules"][f"A_{which}_slate"]["judged_attempt"]
                if judged is None:
                    fits[which] = None
                    continue
                frames = slate_inputs(folder, which, judged)
                fits[which] = hand_fit(frames, 0.001)
                fine[which] = hand_fit(frames, 0.0001)
            if not (fits["start"] and fits["end"]) or isinstance(fits["start"][0], str):
                print(f"{c0:<8} {d:<7} {rule['elapsed_bound_s']}  hand: no window {fits}")
                continue
            (ws, wd_s, cs), (we, wd_e, ce) = fits["start"], fits["end"]
            hand = abs(wrap(ce - cs)) + (wd_s + wd_e) / 2
            (_, fwd_s, fcs), (_, fwd_e, fce) = fine["start"], fine["end"]
            fine_bound = abs(wrap(fce - fcs)) + (fwd_s + fwd_e) / 2
            tool = rule["elapsed_bound_s"]
            hand_pass = round(hand, 9) <= M["elapsed_bound_s"]
            tool_pass = rule["pass"]
            sound = abs(d) <= tool + 1e-9
            extra = max(abs(ws[0] - (fine['start'][0][0])), abs(ws[1] - fine['start'][0][1]),
                        abs(we[0] - fine['end'][0][0]), abs(we[1] - fine['end'][0][1])) * 1000
            ok = abs(tool - hand) < 1e-6 and tool_pass == hand_pass and sound
            failures += not ok
            if abs(round(tool, 9) - M["elapsed_bound_s"]) < 1e-9:
                boundary_hits.append((c0, d, tool, report["verdict"]))
            print(f"{c0:<8} {d:<7} {tool:<11.6f} {hand:<11.6f} {fine_bound:<11.6f} {report['verdict']:<8} "
                  f"{'PASS' if hand_pass else 'FAIL':<9}  {sound!s:<5}  {extra:.2f}"
                  f"{'' if ok else '   <-- MISMATCH'}")
        # exact boundary: search D on a 0.0005 grid near the edge for a bound of exactly 0.200
        print("searching for an exact 0.200 s bound ...")
        for k in range(0, 24):
            d = round(0.138 + 0.0005 * k, 4)
            folder = T.make_take(tmp, c=3.2137, step_both=("begin", 15, d))
            code, report, _ = T.run_check(folder)
            rule = report["rules"]["A_elapsed_bound"]
            if rule["elapsed_bound_s"] is not None and abs(rule["elapsed_bound_s"] - 0.2) < 1e-9:
                boundary_hits.append((3.2137, d, rule["elapsed_bound_s"], report["verdict"]))
                print(f"RESULT exact boundary: D={d} bound={rule['elapsed_bound_s']!r} verdict={report['verdict']} "
                      f"(CHECK-V1 says <= {M['elapsed_bound_s']}, so PASS is expected)")
                break
            last = (d, rule["elapsed_bound_s"], report["verdict"])
        else:
            print(f"RESULT no exact 0.200 hit on the grid; last tried {last}")
        print(f"RESULT boundary hits: {boundary_hits}")
        print("PASS" if failures == 0 else f"FAIL: {failures} case(s) disagree with the hand recomputation")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if T._CLEAN:
            shutil.rmtree(T._CLEAN[0].parent, ignore_errors=True)


if __name__ == "__main__":
    main()
