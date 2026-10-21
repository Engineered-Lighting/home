"""python -m den_ops <command>  (README.md in the folder above lists them)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import signal

from . import monitor as mon
from . import soak
from . import deployment
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
    units.add_argument("--reviewed-unit", action="append", default=[], help="individually diagnosed scope:unit (repeat)")

    soak_cmd = sub.add_parser("soak", help="the supervised soak")
    soak_sub = soak_cmd.add_subparsers(dest="soak_command", required=True)
    for name in ("run", "checklist", "evaluate"):
        cmd = soak_sub.add_parser(name)
        cmd.add_argument("--soak-config", required=True)
        cmd.add_argument("--out", required=True, help="new attempt directory for run; existing attempt for evaluate/checklist")
        cmd.add_argument("--deployment-manifest", required=True)
        if name == "run":
            cmd.add_argument("--duration-s", type=float)
        if name == "evaluate":
            cmd.add_argument("--checklist", help="explicit matching checklist JSON; absent remains incomplete")
    combine = soak_sub.add_parser("combine")
    combine.add_argument("receipts", nargs="+")
    combine.add_argument("--out", required=True)
    combine.add_argument("--release")
    combine.add_argument("--deployment-manifest", required=True)

    args = parser.parse_args(argv)
    host = Host()

    if args.command == "soak" and args.soak_command == "combine":
        docs = []
        for path in args.receipts:
            with open(path, encoding="utf-8") as handle:
                docs.append(json.load(handle))
        doc = soak.combine(docs, args.release, deployment.load(args.deployment_manifest))
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
        ok = report["evidence"]["ok"] and status["level"] == 0 and not status["latched"] and not report["faults"]
        print(json.dumps({"ok": ok, "evidence": report["evidence"], "status": status, "faults": report["faults"],
                          "failed_units": report["failed_units"]}, indent=1, sort_keys=True))
        return 0 if ok else 1
    if args.command == "status":
        print(json.dumps(mon.load_state(os.path.join(mon.state_dir(cfg), "state.json"), cfg), indent=1, sort_keys=True))
        return 0
    if args.command == "release":
        state = mon.release(cfg, confirm=args.confirm)
        print("released: level %d (%s); recovery continues on the next checks" % (state["level"], state["step"]))
        return 0
    if args.command == "baseline-units":
        doc = mon.record_units_baseline(cfg, host, write=args.write, now=host.now(), reviewed_units=args.reviewed_unit)
        print(json.dumps(doc, indent=1, sort_keys=True))
        return 0

    soak_cfg = soak.load_soak_config(args.soak_config)
    soak_cfg["_deployment"] = deployment.load(args.deployment_manifest)
    stem = "soak-%s" % soak_cfg["host"]
    if args.soak_command == "run":
        def interrupted(signum, frame):
            raise KeyboardInterrupt("sampler interrupted")
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            path = soak.run(soak_cfg, cfg, host, args.out, duration_s=args.duration_s)
        finally:
            signal.signal(signal.SIGTERM, previous)
        with open(os.path.join(args.out, stem + ".json"), encoding="utf-8") as handle:
            doc = json.load(handle)
        print(soak.markdown(doc))
        return 0 if doc["verdict"] != "fail" else 1
    if args.soak_command == "checklist":
        brain = soak_cfg.get("brain") or {}

        def fetch_state():
            code, doc = host.http("GET", brain["state_url"], timeout_s=5)
            return doc if code == 200 else None

        if soak_cfg["host"] != "la-host":
            raise SystemExit("owner checklist runs only on LA")
        with open(os.path.join(args.out, "soak-meta-" + soak_cfg["host"] + ".json"), encoding="utf-8") as handle:
            meta = json.load(handle)
        public_cfg = {k:v for k,v in soak_cfg.items() if k != "_deployment"}
        proof = deployment.verify(soak_cfg["_deployment"], public_cfg, cfg, host)
        if not meta.get("complete") or not proof["ok"] or meta.get("manifest_sha256") != proof["manifest_sha256"]:
            raise SystemExit("checklist requires a completed matching unchanged soak attempt")
        started = host.now()
        result = soak.run_checklist(fetch_state, input)
        result.update(started_at=started, ended_at=host.now(), run_id=meta["run_id"], manifest_sha256=proof["manifest_sha256"])
        final = deployment.verify(soak_cfg["_deployment"], public_cfg, cfg, host)
        if not final["ok"]:
            raise SystemExit("deployment changed during checklist; no admitting checklist written")
        result["host"], result["release"] = soak_cfg["host"], soak_cfg.get("release")
        path = os.path.join(args.out, "checklist-%s-%d.json" % (soak_cfg["host"], int(host.now())))
        os.makedirs(args.out, exist_ok=True)
        with open(path, "x", encoding="utf-8") as out:
            json.dump(result, out, indent=1)
        print("checklist written to %s" % path)
        return 0
    if args.soak_command == "evaluate":
        samples = soak.read_samples(os.path.join(args.out, "samples-%s.jsonl" % soak_cfg["host"]))
        checklist_path = args.checklist
        checklist = None
        if checklist_path:
            with open(checklist_path, encoding="utf-8") as handle:
                checklist = json.load(handle)
        with open(os.path.join(args.out, "soak-meta-" + soak_cfg["host"] + ".json"), encoding="utf-8") as handle:
            meta = json.load(handle)
        public_cfg = {k:v for k,v in soak_cfg.items() if k != "_deployment"}
        proof = deployment.verify(soak_cfg["_deployment"], public_cfg, cfg, host)
        criteria = soak.evaluate(soak_cfg, cfg, samples, checklist=checklist)
        criteria.append(soak._criterion("attempt_complete", "Attempt completed with unchanged deployment", meta.get("complete") is True and meta.get("run_id") == (samples[0].get("run_id") if samples else None) and meta.get("manifest_sha256") == proof["manifest_sha256"] and meta.get("provenance_final", {}).get("ok") is True and proof["ok"], "metadata and current provenance checked"))
        doc = soak.receipt(soak_cfg, criteria, samples, checklist)
        soak.write_receipt(args.out, doc, stem)
        print(soak.markdown(doc))
        return 0 if doc["verdict"] == "pass" else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
