"""den-verify: rung 10's verify, run over records already on disk.

    python tools\\den-verify\\verify.py slots
    python tools\\den-verify\\verify.py run --decisions D --light-states L --model M --out DIR

It reads JSON Lines files and one light-model.json and writes three files into
--out: decisions.verified.jsonl, light_states.verified.jsonl and report.json.
It never opens a network connection, sends nothing to any light and contacts
no camera, Jetson, LA host or Home Assistant. A scored run refuses while
VERIFY-V1.md has an open slot; --preview runs on the proposed values and
writes only report-preview.json.
"""
import argparse
import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import method as method_mod  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock, read_jsonl, write_json, write_jsonl  # noqa: E402
from score import Run  # noqa: E402

OUTPUTS = ("decisions.verified.jsonl", "light_states.verified.jsonl", "report.json")


def _sha(path):
    if path is None:
        return None
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _say(text):
    # PowerShell 5.1 consoles are not UTF-8: keep what we print ASCII.
    print(text.encode("ascii", "replace").decode("ascii"))


def cmd_slots(args):
    m = method_mod.load(args.method)
    _say(f"{m.file} sha256 {m.sha256}")
    if m.open:
        _say(f"{len(m.open)} open slot(s); a scored run refuses until each is closed:")
        for line in m.describe_open():
            _say(f"  {line}")
    else:
        _say("no open slots")
    for name, p in method_mod.PENDING.items():
        _say(f"pending decision {name}: options {', '.join(p['options'])}; affects {p['affects']}")
    return 0


def cmd_run(args):
    m = method_mod.load(args.method)
    values = m.scored()
    if values is None and not args.preview:
        _say(f"refused: {m.file} has {len(m.open)} open slot(s): " + "; ".join(m.describe_open()))
        _say("close them in the method file, or add --preview to see the proposed values at work")
        return 2
    if values is None:
        values = m.preview()
    out = Path(args.out)
    names = ("report-preview.json",) if args.preview else OUTPUTS
    clash = [n for n in names if (out / n).exists()]
    if clash:
        _say(f"refused: {out} already holds {', '.join(clash)}; choose a new --out folder")
        return 2
    model = LightModel.load(args.model)
    clock = Clock(None if args.utc_offset_h is None else round(args.utc_offset_h * 3600))
    run = Run(values, model, read_jsonl(args.decisions), read_jsonl(args.light_states),
              corrections=read_jsonl(args.corrections), occupancy=read_jsonl(args.occupancy),
              gold=read_jsonl(args.gold), clock=clock, scored=not args.preview)
    decisions, states, report = run.run()
    report["method"] = {"file": m.file, "sha256": m.sha256, "open": m.describe_open(),
                        "values": values}
    report["inputs"] = {k: _sha(getattr(args, k)) for k in
                        ("decisions", "light_states", "model", "corrections", "occupancy", "gold")}
    out.mkdir(parents=True, exist_ok=True)
    if args.preview:
        report["scored"] = False
        report["preview_note"] = "proposed values for open slots; not a scored run; no records written"
        write_json(out / "report-preview.json", report)
    else:
        write_jsonl(out / OUTPUTS[0], decisions)
        write_jsonl(out / OUTPUTS[1], states)
        write_json(out / OUTPUTS[2], report)
    hr = report["hit_rate"]
    _say(f"decisions verified {report['decisions']['verified']} of {report['decisions']['total']}; "
         f"hit rate {hr['value']} ({hr['hits']} of {hr['hits'] + hr['misses']}), "
         f"gate {hr['gate']}: {hr['verdict']}{' (preview)' if args.preview else ''}")
    left, noise = report["excluded_held_or_superseded"], report["below_meter_noise"]
    if left:
        _say(f"left out of the hit rate: {left} point(s) lit by held or superseded bulbs")
    stale = report["decisions"]["read_frame_stale"]
    if stale:
        _say(f"{len(stale)} decision(s) read a frame from before a step landed: nothing taken from that frame was judged")
    if noise["count"]:
        _say(f"{noise['count']} counted point(s) have a change below the meter's noise "
             f"({noise['hits']} hit(s), {noise['misses']} miss(es)); "
             f"without them the hit rate would be {noise['value_without']}: {noise['verdict_without']}; a pass needs both")
    wide = report["hits_beyond_level_tolerance"]
    if wide["count"]:
        _say(f"{wide['count']} hit(s) are beyond {values['tolerance']:.0%} of the level; if they had missed, "
             f"the hit rate would be {wide['value_if_missed']}: {wide['verdict_if_missed']}")
    _say(f"wrote {', '.join(names)} in {out}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="verify.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--method", help="the method file (default: VERIFY-V1.md beside this script)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("slots", help="list open slots and pending decisions")
    r = sub.add_parser("run", help="verify decisions against metering frames")
    r.add_argument("--decisions", required=True, help="Decision records, JSON Lines")
    r.add_argument("--light-states", required=True, help="LightState records, JSON Lines")
    r.add_argument("--model", required=True, help="light-model.json from rung 8")
    r.add_argument("--corrections", help="Correction records (their holds), JSON Lines")
    r.add_argument("--occupancy", help="occupancy intervals {start, end, people}, JSON Lines")
    r.add_argument("--gold", help="gold need intervals with R_eval marks, JSON Lines")
    r.add_argument("--utc-offset-h", type=float,
                   help="UTC offset for clock strings, e.g. -7 (default: this PC's time zone)")
    r.add_argument("--out", required=True, help="a new folder for the outputs")
    r.add_argument("--preview", action="store_true", help="use proposed values; write no records")
    args = p.parse_args(argv)
    return cmd_slots(args) if args.cmd == "slots" else cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
