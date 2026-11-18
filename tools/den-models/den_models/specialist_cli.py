"""Trusted-local, bounded specialist evaluation; never fetches weights.

Run directly or with -m den_models.specialist_cli. A reviewed offline-evaluation
receipt is mandatory. --check performs no native load. Actual work needs the
existing Linux cgroup boundary, fresh shared control file, available host RAM,
and cooperative execution lease. A child timeout retains that lease until reap.
"""
import argparse
import contextlib
import multiprocessing
import os
from pathlib import Path
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from den_models.artifacts import cgroup_limits
from den_models.contracts import ContractError, canonical, profile_digest, strict_json
from den_models.resources import ControlState, FileExecutionLease, HostProbe
from den_models.specialists import (MAX_RESULT_BYTES, _read_json, check_admission, evaluate, load_manifest,
                                    load_profile)


def _child(pipe, profile, root, receipt, manifest, io_device):
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ[name] = "1"
    try:
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
            result = evaluate(profile, root, receipt, manifest, io_device=io_device)
        payload = {"ok": True, "result": result}
    except BaseException as error:
        payload = {"ok": False, "reason": str(error) if isinstance(error, ContractError) else "specialist_native_failed"}
    try:
        pipe.send_bytes(canonical(payload).encode())
    finally:
        pipe.close()


def reap(process):
    """True only once native work is no longer alive, including timeout paths."""
    if process.is_alive():
        process.terminate()
        process.join(3)
    if process.is_alive():
        process.kill()
        process.join(3)
    return not process.is_alive() and process.exitcode is not None


def run_bounded(profile, root, receipt, manifest, *, lease, io_device, control, host_probe=None, context=None):
    check_admission(profile, root, receipt)
    cgroup_limits(profile, io_device)
    host_probe = host_probe or HostProbe()
    minimum_host = receipt["resources"]["peak_host_bytes"] + 2 * 1024**3

    def guard():
        state = control.read(time.monotonic())
        if state["qwen_busy"]:
            raise ContractError("specialist_qwen_busy")
        if host_probe.available_bytes() < minimum_host:
            raise ContractError("specialist_host_headroom_unavailable")

    guard()
    token = lease.try_acquire("offline-specialist:" + profile["id"] + ":" + profile_digest(manifest))
    if token is None:
        raise ContractError("specialist_execution_busy")
    context = context or multiprocessing.get_context("spawn")
    reader = writer = process = None
    start_attempted = False
    try:
        reader, writer = context.Pipe(duplex=False)
        process = context.Process(target=_child, args=(writer, profile, str(root), receipt, manifest, io_device))
        start_attempted = True
        process.start()
        writer.close()
        deadline = time.monotonic() + receipt["resources"]["maximum_seconds"]
        response = None
        while process.is_alive() and time.monotonic() < deadline:
            guard()
            if response is None and reader.poll(0.05):
                try:
                    response = strict_json(reader.recv_bytes(MAX_RESULT_BYTES + 1024).decode())
                except (EOFError, OSError, UnicodeError, ValueError):
                    raise ContractError("specialist_worker_message_invalid") from None
            process.join(0.01)
        if process.is_alive():
            raise ContractError("specialist_evaluation_deadline")
        process.join()
        if response is None and reader.poll(0.1):
            try:
                response = strict_json(reader.recv_bytes(MAX_RESULT_BYTES + 1024).decode())
            except (EOFError, OSError, UnicodeError, ValueError):
                raise ContractError("specialist_worker_message_invalid") from None
        if process.exitcode != 0 or not isinstance(response, dict) or response.get("ok") is not True:
            reason = response.get("reason") if isinstance(response, dict) else None
            if not isinstance(reason, str) or not 0 < len(reason) <= 128 or any(c not in "abcdefghijklmnopqrstuvwxyz_0123456789.-" for c in reason):
                reason = "specialist_worker_failed"
            raise ContractError(reason)
        return response["result"]
    finally:
        # A start attempt that failed without returning a PID is not terminal
        # proof. Conservatively retain its lease rather than guess no child ran.
        proved = not start_attempted or (process.pid is not None and reap(process))
        for pipe in (reader, writer):
            if pipe is not None:
                pipe.close()
        if proved:
            if not lease.release(token, "process_reaped" if process is not None and process.pid is not None else "no_native_context"):
                raise ContractError("specialist_execution_lease_release_failed")
        # No proof means deliberately retain the broker lease for inspection.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--snapshot-root", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--lease-file")
    parser.add_argument("--control-file")
    parser.add_argument("--io-device")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.profile)
        receipt = _read_json(args.receipt)
        check_admission(profile, Path(args.snapshot_root), receipt)
        if args.check:
            result = {"schema": "den.specialist-check/v1", "profile_id": profile["id"],
                      "offline_receipt_ready": True, "production_admitted": False, "native_work_started": False}
        else:
            if not all((args.manifest, args.lease_file, args.control_file, args.io_device)):
                raise ContractError("specialist_bounded_operator_arguments_required")
            manifest = load_manifest(args.manifest)
            result = run_bounded(profile, args.snapshot_root, receipt, manifest,
                                 lease=FileExecutionLease(args.lease_file), io_device=args.io_device,
                                 control=ControlState(args.control_file, expected_lease_file=args.lease_file))
        print(canonical(result))
        return 0
    except (ContractError, OSError, ValueError) as error:
        reason = str(error) if isinstance(error, ContractError) else "specialist_local_input_unavailable"
        print(canonical({"schema": "den.specialist-check/v1", "profile_id": args.profile,
                         "offline_receipt_ready": False, "production_admitted": False, "reason": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
