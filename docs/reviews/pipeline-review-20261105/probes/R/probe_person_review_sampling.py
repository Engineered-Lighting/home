"""probe_person_review_sampling: which frames does the owner ever see in check.py's second-person review?

What it checks
  * Builds a clean synthetic take with R's make_take() (bundles at the camera's ~4 fps cadence),
    runs check.run_check with the console answer "none", and lists the frames that review/ holds
    as thumbnails (fNNNNNN.png, one per second of t_s, as CHECK-V1 specifies).
  * Reports the share of usable-part frames that are never shown, the longest run of consecutive
    never-shown frames, and whether a second person visible only in such a run (for example a
    hand reaching in for 0.7 s) is cut: it marks that run as "second person" and checks whether any
    second_person cut or any thumbnail covers it when the owner, having seen nothing, types none.
  * Also reports the thumbnail resolution (every 4th pixel: 160x90 of 640x360).

How to run
  python3 -B probe_person_review_sampling.py <branch checkout root>
Synthetic data only, temp dirs only, no network, no browser (show is stubbed).
"""
import json, shutil, sys, tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import check  # noqa: E402
import test_den_take as T  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp(prefix="probe-review-")).resolve()
    try:
        folder = T.make_take(tmp, activity=60.0)
        code, report, path = T.run_check(folder, typed="none")
        assert report["verdict"] == "PASS", report["failures"]
        shown = sorted(int(p.stem[1:]) for p in (folder / "review").glob("f*.png"))
        import csv
        rows = list(csv.DictReader(open(folder / "frames.csv", newline="", encoding="utf-8")))
        segs = report["usable"]["segments"]
        usable = [int(r["n"]) for r in rows if any(s["first_n"] <= int(r["n"]) <= s["last_n"] for s in segs)]
        unseen = [n for n in usable if n not in set(shown)]
        runs, run = [], []
        for n in usable:
            if n in set(shown):
                if run:
                    runs.append(run)
                run = []
            else:
                run.append(n)
        if run:
            runs.append(run)
        longest = max(runs, key=len)
        t = {int(r["n"]): float(r["t_s"]) for r in rows}
        span_s = t[longest[-1]] - t[longest[0]]
        print(f"RESULT usable frames {len(usable)}, shown as thumbnails {len(usable) - len(unseen)}, "
              f"never shown {len(unseen)} ({100 * len(unseen) / len(usable):.0f}%)")
        print(f"RESULT longest never-shown run: frames {longest[0]}..{longest[-1]} ({len(longest)} frames, "
              f"t_s {t[longest[0]]:.2f}..{t[longest[-1]]:.2f}, {span_s:.2f} s between first and last)")
        # A second person visible only in that run: the owner sees no thumbnail of it and types none.
        cut = [c for c in report["cuts"] if c["kind"] == "second_person"]
        covered = any(c["start_t_s"] <= t[longest[0]] and t[longest[-1]] < c["end_t_s"] for c in cut)
        still_usable = all(any(s["first_n"] <= n <= s["last_n"] for s in segs) for n in longest)
        print(f"RESULT a person seen only in frames {longest[0]}..{longest[-1]}: cut={covered}, "
              f"left in the usable part={still_usable}, retained={report['retained'] or 'commissioning'}")
        w, h = len(range(0, 640, 4)), len(range(0, 360, 4))
        print(f"RESULT thumbnail size {w}x{h} of 640x360 ({100 * w * h / (640 * 360):.2f}% of pixels)")
        if unseen and not covered and still_usable:
            print("FAIL: frames between 1 Hz thumbnails are never reviewed; a sub-second second person "
                  "there stays in the usable part")
        else:
            print("PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
