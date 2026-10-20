"""python -m den_ops <command>  (README.md in the folder above lists them)."""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from . import monitor as mon
from . import soak
from .hostio import Host


def _config_path(args):
    path = args.config or os.environ.get("DEN_OPS_MONITOR_CONFIG")
    if not path:
        raise SystemExit("set --config or DEN_OPS_MONITOR_CONFIG")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="den_ops", description="Den activity-lighting always-on operations")
    parser.add_argument("--config", help="monitor config (default: $DEN_OPS_MONITOR_CONFIG)")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="one monitor pass (the timer runs this)")
    check.add_argument("--dry-run", action="store_true", help="evaluate only: no actions, alerts or state writes")
    sub.add_parser("validate", help="check the config and its private files")
    sub.add_parser("status", help="print the monitor's state")
    release = sub.add_parser("release", help="release the monitor's own level-3 latch")
    release.add_argument("--confirm", action="store_true")
    units = sub.add_parser("baseline-units", help="record the failed-unit baseline once, at install")
    units.add_argument("--write", action="store_true", help="write it (refuses to overwrite)")

    soak_cmd = sub.add_parser("soak", help="the supervised soak")
    soak_sub = soak_cmd.add_subparsers(dest="soak_command", required=True)
    for name in ("run", "checklist", "evaluate"):
        cmd = soak_sub.add_parser(name)
        cmd.add_argument("--soak-config", required=True)
        cmd.add_argument("--out", required=True, help="receipt directory")
        if name == "run":
            cmd.add_argument("--duration-s", type=float)
        if name == "evaluate":
            cmd.add_argument("--checklist", help="checklist JSON (default: the newest in --out)")
    combine = soak_sub.add_parser("combine")
    combine.add_argument("receipts", nargs="+")
    combine.add_argument("--out", required=True)
    combine.add_argument("--release")

    args = parser.parse_args(argv)
    host = Host()

    if args.command == "soak" and args.soak_command == "combine":
        docs = []
        for path in args.receipts:
            with open(path, encoding="utf-8") as handle:
                docs.append(json.load(handle))
        doc = soak.combine(docs, args.release)
        soak.write_receipt(args.out, doc, "soak-release")
        print(soak.markdown(doc))
        return 0 if doc["verdict"] == "pass" else 1

    cfg = mon.load_config(_config_path(args))
    if args.command == "check":
        state, status, report = mon.check(cfg, host, dry_run=args.dry_run)
        print(json.dumps({"level": status["level"], "step": status["step"], "latched": status["latched"],
                          "message": status["message"], "transition": report["transition"],
                          "effects": report["effects"], "gateway": report.get("gateway"),
                          "notify": report.get("notify")}, sort_keys=True))
        return 0
    if args.command == "validate":
        _, status, report = mon.check(cfg, host, dry_run=True)
        print(json.dumps({"ok": True, "status": status, "faults": report["faults"],
                          "failed_units": report["failed_units"]}, indent=1, sort_keys=True))
        return 0
    if args.command == "status":
        print(json.dumps(mon.load_state(os.path.join(mon.state_dir(cfg), "state.json"), cfg), indent=1, sort_keys=True))
        return 0
    if args.command == "release":
        state = mon.release(cfg, confirm=args.confirm)
        print("released: level %d (%s); recovery continues on the next checks" % (state["level"], state["step"]))
        return 0
    if args.command == "baseline-units":
        doc = mon.record_units_baseline(cfg, host, write=args.write, now=host.now())
        print(json.dumps(doc, indent=1, sort_keys=True))
        return 0

    soak_cfg = soak.load_soak_config(args.soak_config)
    stem = "soak-%s" % soak_cfg["host"]
    if args.soak_command == "run":
        path = soak.run(soak_cfg, cfg, host, args.out, duration_s=args.duration_s)
        samples = soak.read_samples(path)
        doc = soak.receipt(soak_cfg, soak.evaluate(soak_cfg, cfg, samples), samples)
        soak.write_receipt(args.out, doc, stem)
        print(soak.markdown(doc))
        return 0 if doc["verdict"] != "fail" else 1
    if args.soak_command == "checklist":
        brain = soak_cfg.get("brain") or {}

        def fetch_state():
            code, doc = host.http("GET", brain["state_url"], timeout_s=5)
            return doc if code == 200 else None

        result = soak.run_checklist(fetch_state, input)
        result["host"], result["release"] = soak_cfg["host"], soak_cfg.get("release")
        path = os.path.join(args.out, "checklist-%s-%d.json" % (soak_cfg["host"], int(host.now())))
        os.makedirs(args.out, exist_ok=True)
        with open(path, "w", encoding="utf-8") as out:
            json.dump(result, out, indent=1)
        print("checklist written to %s" % path)
        return 0
    if args.soak_command == "evaluate":
        samples = soak.read_samples(os.path.join(args.out, "samples-%s.jsonl" % soak_cfg["host"]))
        checklist_path = args.checklist or max(glob.glob(os.path.join(args.out, "checklist-%s-*.json" % soak_cfg["host"])),
                                               default=None, key=os.path.getmtime)
        checklist = None
        if checklist_path:
            with open(checklist_path, encoding="utf-8") as handle:
                checklist = json.load(handle)
        doc = soak.receipt(soak_cfg, soak.evaluate(soak_cfg, cfg, samples, checklist=checklist), samples, checklist)
        soak.write_receipt(args.out, doc, stem)
        print(soak.markdown(doc))
        return 0 if doc["verdict"] == "pass" else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
