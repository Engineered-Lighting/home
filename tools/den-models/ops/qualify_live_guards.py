"""Qualify the real read-only guard actors before a bounded trial (M4).

Reviewed repository successor of the local provisioning harness. It never
starts the native worker. Without --execute it only prints the plan. With
--execute, which needs the Ops lease and the trial preflight, this file is
sent over ssh and runs as root inside a bounded transient scope: it pins the
host state, verifies (or once creates) private staging directories, starts the
guard units, samples control receipts and probes the model UID read-only.

It never runs `systemctl daemon-reload`: a target unit that reports
NeedDaemonReload other than "no" refuses the run. Samples hold aggregates and
fixed reason codes only, never dependency values, frames or positions.
"""
import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import time

SOURCES = ("qwen", "den_ops", "dependencies", "gpu", "host", "broker")
REQUIRED_CONSECUTIVE = 8
SAMPLER_NAME = "measure_trial_scope.py"
CONTROL_MAX_AGE = 1.5  # The producer's own source bound; it publishes at least once a second.
SOURCE_MAX_AGE = 2.0  # ControlState.max_age.
PRODUCER_MIN_POLL = .2  # control_producer.load_config; sampling faster skips no receipt.
# Host-side timeouts; the scope's RuntimeMaxSec is derived from them.
SHOW_TIMEOUT, START_TIMEOUT, PROBE_TIMEOUT = 10, 30, 10
HEALTH_ALLOWANCE, SCOPE_MARGIN, SSH_MARGIN = 30, 15, 30


class QualificationError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise QualificationError(code)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ---- offline decision logic (unit-tested) -------------------------------------------------------

def failed_sample(code, now):
    return {"observed_mono": now, "error": code, "sources_ok": {}, "blocked": None, "reasons": {},
            "dependency_count": None, "ops_mirror_exact": False}


def observation(control, queue, *, now, ops_mirror_exact):
    """One control receipt as aggregates: per-source ok, reason codes and dependency count."""
    try:
        sources = control["sources"]
        return {"observed_mono": now, "error": None, "control_observed_at": control["observed_at"],
                "control_age_s": now - control["observed_at"],
                "source_ages_s": {name: now - source["observed_at"] for name, source in sources.items()},
                "blocked": control["admission_blocked"], "blocked_reasons": list(control["blocked_reasons"]),
                "qwen_busy": control["qwen_busy"],
                "sources_ok": {name: source.get("ok") is True for name, source in sources.items()},
                "reasons": {name: source.get("reason") for name, source in sources.items()
                            if source.get("ok") is not True},
                "dependency_count": len(control["dependencies"]),
                "gpu_free_bytes": sources.get("gpu", {}).get("free_bytes"),
                "host_available_bytes": sources.get("host", {}).get("available_bytes"),
                "ops_level": sources.get("den_ops", {}).get("level"),
                "ops_latched": sources.get("den_ops", {}).get("latched"),
                "queue_schema": queue.get("schema"), "queue_age_s": now - queue.get("observed_at", now),
                "ops_mirror_exact": ops_mirror_exact is True}
    except (KeyError, TypeError, AttributeError):
        return failed_sample("control_receipt_incomplete", now)


def fresh(age, limit):
    return type(age) in (int, float) and 0 <= age <= limit


def sample_ok(sample):
    """Fully OK: a fresh receipt, every expected source present, ok and fresh, not blocked, exact Ops mirror."""
    ok, ages = sample.get("sources_ok"), sample.get("source_ages_s")
    return (isinstance(ok, dict) and set(ok) == set(SOURCES) and all(value is True for value in ok.values())
            and isinstance(ages, dict) and set(ages) == set(SOURCES)
            and all(fresh(age, SOURCE_MAX_AGE) for age in ages.values())
            and type(sample.get("control_observed_at")) in (int, float)
            and fresh(sample.get("control_age_s"), CONTROL_MAX_AGE)
            and sample.get("blocked") is False and sample.get("ops_mirror_exact") is True)


def receipts(samples):
    """One entry per distinct receipt; repeat reads of one receipt keep only the latest read."""
    distinct = []
    for sample in samples:
        key = sample.get("control_observed_at")
        if distinct and key is not None and distinct[-1].get("control_observed_at") == key:
            distinct[-1] = sample
        else:
            distinct.append(sample)
    return distinct


def qualified(samples, required=REQUIRED_CONSECUTIVE):
    # The local harness used all(x['sources_ok'].values() ...), which is always
    # truthy (a non-empty dict view) and accepted 1 OK sample followed by 7 failures.
    # Sampling is faster than the producer, so only distinct, advancing receipts count.
    window = receipts(samples)[-required:]
    return (len(window) >= required and all(sample_ok(sample) for sample in window)
            and all(a["control_observed_at"] < b["control_observed_at"] for a, b in zip(window, window[1:])))


def units_needing_reload(properties):
    """Units whose NeedDaemonReload is anything but an explicit "no" (fail closed)."""
    return sorted(unit for unit, values in properties.items() if values.get("NeedDaemonReload") != "no")


def directory_acceptable(info, *, owner=0, mode=0o700):
    return stat.S_ISDIR(info.st_mode) and info.st_uid == owner and stat.S_IMODE(info.st_mode) == mode


def staged_file_acceptable(info, data, digest, *, owner=0, mode=0o444):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == owner and stat.S_IMODE(info.st_mode) == mode
            and sha256(data) == digest)


# ---- host side (runs only under --execute, as root in the bounded scope) ------------------------

def unit_properties(unit, names):
    done = subprocess.run(["systemctl", "show", unit, *("--property=" + name for name in names)],
                          capture_output=True, text=True, timeout=SHOW_TIMEOUT, check=True)
    values = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)
    require(set(values) == set(names), "unit_properties_unreadable")
    return values


def private_directory(path):
    """Exclusive create when absent; an existing directory must already be root-owned 0700."""
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    require(directory_acceptable(os.lstat(path)), "existing_directory_not_private_root")


def stage_once(path, data, digest):
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    except FileExistsError:
        info = os.lstat(path)
        data = path.read_bytes() if stat.S_ISREG(info.st_mode) else b""
        require(staged_file_acceptable(info, data, digest), "staged_sampler_mismatch")
        return "verified_existing"
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    return "staged"


def load_pinned_module(path, digest):
    import importlib.util
    path = Path(path)
    require(sha256(path.read_bytes()) == digest, "pinned_canary_changed")
    spec = importlib.util.spec_from_file_location("accepted_canary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def model_uid_probe(cfg):
    source = (f"import json,sys,time\nsys.path.insert(0,{cfg['worker_source']!r})\n"
              "from den_models.artifacts import load_profile\n"
              "from den_models.contracts import ContractError\n"
              "from den_models.runtime import Runtime\n"
              "from den_models.server import operator_config\n"
              f"cfg=operator_config({cfg['operator_config']!r});r=Runtime(cfg,load_profile(cfg['profile_id']))\n"
              "def code(call):\n"
              " try:\n  return call(),None\n"
              " except ContractError as exc:\n  return None,str(exc)\n"
              "_,admission_error=code(r.refresh_admission)\n"
              "s,control_error=code(lambda:r.control.read(time.monotonic()))\n"
              "print(json.dumps({'artifact_admitted':r.admitted,'admission_error':admission_error,"
              "'resident':r.resident,'worker_present':r.worker is not None,'control_attested':s is not None,"
              "'control_error':control_error,'blocked':None if s is None else s['admission_blocked'],"
              "'qwen_busy':None if s is None else s['qwen_busy'],"
              "'dependency_count':None if s is None else len(s['dependencies']),"
              "'heavy_imports':[n for n in ('torch','transformers','numpy','cv2') if n in sys.modules]}))\n")
    done = subprocess.run(["runuser", "-u", cfg["model_user"], "--", "/usr/bin/python3", "-I", "-B", "-c", source],
                          capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    if done.returncode == 0:
        return done.returncode, json.loads(done.stdout)
    return done.returncode, {"error_type": "probe_failed", "stderr": done.stderr[-1000:]}


def host_main(cfg):
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    sampler = base64.b64decode(cfg["sampler_b64"])
    require(sha256(sampler) == cfg["sampler_sha256"], "sampler_digest_mismatch")
    canary = load_pinned_module(cfg["canary"], cfg["canary_sha256"])
    health = canary.s.host_health(cfg["health_since"], json.loads(Path(cfg["failed_units_baseline"]).read_text()))
    for unit, pid in cfg["expect_main_pid"].items():
        require(unit_properties(unit, ("MainPID",))["MainPID"] == str(pid), "unexpected_main_pid")
    for path, digest in cfg["expect_sha256"].items():
        require(sha256(Path(path).read_bytes()) == digest, "pinned_file_changed")
    require(unit_properties(cfg["model_unit"], ("MainPID",))["MainPID"] == "0", "model_unit_already_running")
    run_root = Path(cfg["run_root"])
    require(not (run_root / "gpu-execution.lease").exists() and not (run_root / "residency.json").exists(),
            "unresolved_native_accounting")
    targets = [*cfg["units"], cfg["model_unit"]]
    stale = units_needing_reload({unit: unit_properties(unit, ("NeedDaemonReload",)) for unit in targets})
    require(not stale, "unit_needs_daemon_reload")
    observer = Path(cfg["observer_dir"])
    private_directory(observer)
    staged = stage_once(observer / SAMPLER_NAME, sampler, cfg["sampler_sha256"])
    private_directory(Path(cfg["measurements_dir"]))
    for unit in cfg["units"]:
        subprocess.run(["systemctl", "start", unit], check=True, timeout=START_TIMEOUT)
    samples, until = [], time.monotonic() + cfg["seconds"]
    while time.monotonic() < until:
        now = time.monotonic()
        try:
            control = json.loads((run_root / "control.json").read_text())
            queue = json.loads(Path(cfg["queue_file"]).read_text())
            exact = Path(cfg["ops_status_file"]).read_bytes() == Path(cfg["ops_mirror_file"]).read_bytes()
            samples.append(observation(control, queue, now=now, ops_mirror_exact=exact))
        except (OSError, ValueError):
            samples.append(failed_sample("control_receipt_unreadable", now))
        if qualified(samples, cfg["required"]):
            break
        time.sleep(cfg["interval"])
    returncode, probe = model_uid_probe(cfg)
    return {"schema": "den.live-guard-qualification/v2", "fresh_host": health,
            "units": {unit: unit_properties(unit, ("MainPID", "ActiveState", "Result")) for unit in cfg["units"]},
            "daemon_reload": "not_run", "samples": samples, "consecutive_required": cfg["required"],
            "distinct_receipts": len(receipts(samples)),
            "qualified": qualified(samples, cfg["required"]), "model_uid_probe_returncode": returncode,
            "model_uid_probe": probe, "native_started": False, "model_loaded": False,
            "sampler_staged": str(observer / SAMPLER_NAME), "sampler_staging": staged}


# ---- workstation side -----------------------------------------------------------------------------

def pairs(values, convert=str):
    result = {}
    for value in values or ():
        key, separator, item = value.rpartition("=")
        require(separator and key and item and key not in result, "invalid_key_value_argument")
        result[key] = convert(item)
    return result


def scope_runtime_seconds(cfg):
    """host_main's worst case with every bounded call at its timeout, so the
    scope never kills it before it prints its receipt."""
    units = len(cfg["units"])
    shows = len(cfg["expect_main_pid"]) + 1 + (units + 1) + units  # PID pins, model unit, reload check, final state
    return math.ceil(HEALTH_ALLOWANCE + SHOW_TIMEOUT * shows + START_TIMEOUT * units + cfg["seconds"]
                     + cfg["interval"] + PROBE_TIMEOUT + SCOPE_MARGIN)


def remote_command(args, payload, runtime_max):
    return ["sudo", "-n", "systemd-run", "--scope", "--unit=" + args.scope_unit, "--property=CPUQuota=25%",
            "--property=MemoryMax=128M", "--property=MemorySwapMax=0", "--property=TasksMax=32",
            f"--property=RuntimeMaxSec={runtime_max}", "--property=TimeoutStopSec=10", "/usr/bin/prlimit",
            "--core=0:0", "--", "/usr/bin/python3", "-I", "-B", "-", "--host-config", payload]


def text_tail(value, limit=None):
    value = value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
    return value if limit is None else value[-limit:]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host-config", help=argparse.SUPPRESS)
    parser.add_argument("--ssh-host")
    parser.add_argument("--scope-unit", help="transient scope name, e.g. one per source revision and attempt")
    parser.add_argument("--sampler", type=Path, help="local measure_trial_scope.py to stage")
    parser.add_argument("--sampler-sha256")
    parser.add_argument("--canary", help="host path of the accepted canary module (host_health)")
    parser.add_argument("--canary-sha256")
    parser.add_argument("--failed-units-baseline", help="host path")
    parser.add_argument("--health-since", help="UTC ISO timestamp for the fresh-host fault window")
    parser.add_argument("--expect-main-pid", action="append", metavar="UNIT=PID", help="repeat, e.g. den-live=<pid>")
    parser.add_argument("--expect-sha256", action="append", metavar="PATH=HEX", help="repeat for pinned host files")
    parser.add_argument("--model-unit")
    parser.add_argument("--unit", action="append", dest="units", help="guard unit to start, in order (repeat)")
    parser.add_argument("--run-root", help="host model run directory holding control.json")
    parser.add_argument("--queue-file")
    parser.add_argument("--ops-status-file")
    parser.add_argument("--ops-mirror-file")
    parser.add_argument("--observer-dir", help="root-owned 0700 sampler staging directory (verified if present)")
    parser.add_argument("--measurements-dir", help="root-owned 0700 measurement directory (verified if present)")
    parser.add_argument("--worker-source", help="host tools/den-models directory of the installed revision")
    parser.add_argument("--operator-config")
    parser.add_argument("--model-user")
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--interval", type=float, default=.1)
    parser.add_argument("--required", type=int, default=REQUIRED_CONSECUTIVE)
    parser.add_argument("--output", type=Path, help="new local receipt path (never overwritten)")
    parser.add_argument("--execute", action="store_true", help="run on the host; needs the Ops lease and preflight")
    args = parser.parse_args(argv)
    if args.host_config is not None:
        print(json.dumps(host_main(json.loads(base64.b64decode(args.host_config))), sort_keys=True))
        return 0
    names = ("ssh_host", "scope_unit", "sampler", "sampler_sha256", "canary", "canary_sha256", "failed_units_baseline",
             "health_since", "expect_main_pid", "model_unit", "units", "run_root", "queue_file", "ops_status_file", "ops_mirror_file",
             "observer_dir", "measurements_dir", "worker_source", "operator_config", "model_user", "output")
    missing = [name for name in names if getattr(args, name) in (None, [])]
    if missing:
        parser.error("missing required arguments: " + ", ".join("--" + name.replace("_", "-") for name in missing))
    require(args.required >= 1 and 0 < args.interval < PRODUCER_MIN_POLL and 0 < args.seconds <= 60,
            "invalid_sampling_bounds")
    sampler = args.sampler.read_bytes()
    require(sha256(sampler) == args.sampler_sha256, "sampler_digest_mismatch")
    cfg = {"sampler_b64": base64.b64encode(sampler).decode("ascii"), "sampler_sha256": args.sampler_sha256,
           "canary": args.canary, "canary_sha256": args.canary_sha256,
           "failed_units_baseline": args.failed_units_baseline, "health_since": args.health_since,
           "expect_main_pid": pairs(args.expect_main_pid, int), "expect_sha256": pairs(args.expect_sha256),
           "model_unit": args.model_unit, "units": args.units, "run_root": args.run_root,
           "queue_file": args.queue_file, "ops_status_file": args.ops_status_file,
           "ops_mirror_file": args.ops_mirror_file, "observer_dir": args.observer_dir,
           "measurements_dir": args.measurements_dir, "worker_source": args.worker_source,
           "operator_config": args.operator_config, "model_user": args.model_user, "seconds": args.seconds,
           "interval": args.interval, "required": args.required}
    payload = base64.b64encode(json.dumps(cfg, sort_keys=True).encode()).decode("ascii")
    runtime_max = scope_runtime_seconds(cfg)
    ssh_timeout = runtime_max + SSH_MARGIN
    command = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
               args.ssh_host, shlex.join(remote_command(args, payload, runtime_max))]
    if not args.execute:
        plan = {k: v for k, v in cfg.items() if k != "sampler_b64"}
        print(json.dumps({"executes": False, "ssh_host": args.ssh_host, "scope_unit": args.scope_unit,
                          "daemon_reload": "never", "payload_sha256": sha256(payload.encode()),
                          "scope_runtime_max_sec": runtime_max, "ssh_timeout_sec": ssh_timeout, **plan},
                         indent=2, sort_keys=True))
        return 0
    # Claim the new receipt before touching the host; earlier receipts stay intact.
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        receipt = {"returncode": None, "error": None, "scope_runtime_max_sec": runtime_max, "stdout": "", "stderr": ""}
        try:
            done = subprocess.run(command, input=Path(__file__).read_bytes(), capture_output=True, timeout=ssh_timeout)
            receipt.update(returncode=done.returncode, stdout=text_tail(done.stdout),
                           stderr=text_tail(done.stderr, 4000))
        except subprocess.TimeoutExpired as exc:
            receipt.update(error="ssh_timeout", stdout=text_tail(exc.stdout), stderr=text_tail(exc.stderr, 4000))
        except OSError:
            receipt.update(error="ssh_unavailable")
        output.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"returncode": receipt["returncode"], "error": receipt["error"], "receipt": str(args.output)}))
    return 0 if receipt["returncode"] == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QualificationError as exc:
        raise SystemExit("guard_qualification_refused: " + str(exc))
