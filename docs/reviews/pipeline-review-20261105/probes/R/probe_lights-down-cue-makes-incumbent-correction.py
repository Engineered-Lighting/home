"""probe_lights-down-cue-makes-incumbent-correction: is the scripted dim's time recorded in wall-clock time?

What it checks
  * Runs take.run() end to end against the test suite's FakeCamera (bound to 127.0.0.1 only), with a
    short synthetic staged script whose second block is a lights_down block, then reads take.json from
    the temp take folder.
  * Prints the lights_down stage cue's pc_mono_s (perf_counter) and pc_unix, and checks that pc_unix is a
    wall-clock time inside [time.time() before the run, time.time() after the run]. If so, the scripted
    dim window can be joined to Home Assistant recorder times without the perf_counter clock.
  * Also loads the shipped hard-a/hard-b scripts and prints their lights_down block (at_s, gap to next).

How to run
  python3 -B probe_lights-down-cue-makes-incumbent-correction.py <den-take-script checkout root>
Synthetic data only, temp dirs only, the fake camera binds to 127.0.0.1, no speech (say is stubbed).
"""
import json, sys, time, unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import take  # noqa: E402
import test_den_take as T  # noqa: E402

SCRIPT = {"format": "den-take-script-1", "end_s": 1.2, "blocks": [
    {"at_s": 0.3, "case": "idle_seated", "say": "Sit."},
    {"at_s": 0.6, "case": "lights_down", "say": "Turn the den lights down low now."}]}
RESULT = {}


class Probe(T.StagedScriptTests):
    def runTest(self):
        self.scripts({"quick": SCRIPT})
        camera = self.camera()
        assert camera.url.startswith("http://127.0.0.1:"), camera.url
        before = time.time()
        code, record, _ = self.run_take(self.args(camera, script="quick"), enter=lambda: False)
        after = time.time()
        RESULT.update(code=code, record=record, before=before, after=after, url=camera.url)


def main():
    result = unittest.TestResult()
    Probe().run(result)
    if result.errors or result.failures:
        for _, tb in result.errors + result.failures:
            print(tb)
        print("FAIL: probe could not run")
        return
    record = RESULT["record"]
    dims = [c for c in record["cues"] if c.get("cue") == "stage" and c.get("case") == "lights_down"]
    print(f"RESULT fake camera {RESULT['url']}; exit code {RESULT['code']}; pc_clock={record.get('pc_clock')!r}; "
          f"started_unix={record.get('started_unix')}")
    for c in dims:
        print(f"RESULT lights_down cue: pc_mono_s={c['pc_mono_s']:.3f} pc_unix={c.get('pc_unix')} "
              f"({time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(c['pc_unix'])) if c.get('pc_unix') else None})")
    ok = bool(dims) and all(RESULT["before"] <= c.get("pc_unix", -1) <= RESULT["after"] for c in dims)
    settings = take.Settings(slate_s=T.M["slate_length_s"])
    for name in ("hard-a", "hard-b"):
        staged = take.load_script(name, settings)
        blocks = staged["blocks"]
        starts = [b["at_s"] for b in blocks] + [staged["end_s"]]
        for i, b in enumerate(blocks):
            if b["case"] == "lights_down":
                print(f"RESULT {name}: lights_down at {b['at_s']} s, next block {starts[i + 1] - b['at_s']} s later; "
                      f"say={b['say']!r}")
        other = [b for b in blocks if "light" in b["say"].lower() and b["case"] != "lights_down"]
        print(f"RESULT {name}: other blocks that mention the lights (a restore cue): {len(other)}")
    if ok:
        print("PASS: the scripted dim cue carries a wall-clock pc_unix inside the run (not perf_counter only)")
    else:
        print("FAIL: no wall-clock time on the lights_down cue")


if __name__ == "__main__":
    main()
