#!/usr/bin/env python3
"""Bounded four-container voice retirement. Read-only dry run unless --apply.

This does not deploy caller gates or edit Compose/HA configuration. Review and
disable those callers first, and complete the host health/resource checks in
the runbook. The receipt backs up only container identity and restart state;
it deliberately never reads container environment, logs, mounts or media.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

TARGETS = (
    "hav-wyoming-parakeet", "hav-kokoro-tts",
    "hav-chatterbox-tts", "hav-wyoming-kokoro",
)
INSPECT_FORMAT = ('{"id":{{json .Id}},"name":{{json .Name}},'
                  '"running":{{json .State.Running}},'
                  '"restart":{{json .HostConfig.RestartPolicy}}}')


class RetirementError(RuntimeError):
    pass


class DockerClient:
    def _run(self, args):
        return subprocess.run(["docker", *args], capture_output=True, text=True,
                              timeout=45, check=False)

    def daemon_id(self):
        proc = self._run(["info", "--format", "{{.ID}}"])
        value = proc.stdout.strip()
        if proc.returncode or not re.fullmatch(r"[A-Za-z0-9:._-]{8,128}", value):
            raise RetirementError("Cannot identify the Docker daemon")
        return value

    def inspect(self, identity):
        proc = self._run(["container", "inspect", "--format", INSPECT_FORMAT, identity])
        if proc.returncode:
            if "No such object" in proc.stderr or "No such container" in proc.stderr:
                return None
            raise RetirementError("Cannot inspect a target container")
        if len(proc.stdout) > 16384:
            raise RetirementError("Unexpected target metadata size")
        row = json.loads(proc.stdout)
        row["name"] = row["name"].lstrip("/")
        validate_target(row)
        return row

    def mutate(self, args):
        if self._run(args).returncode:
            raise RetirementError("Docker target operation failed")


def validate_target(row):
    if (not isinstance(row, dict) or row.get("name") not in TARGETS
            or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("id", "")))
            or type(row.get("running")) is not bool):
        raise RetirementError("Receipt contains an invalid target")
    policy = row.get("restart", {})
    if (not isinstance(policy, dict)
            or policy.get("Name") not in ("no", "always", "unless-stopped", "on-failure")
            or type(policy.get("MaximumRetryCount")) is not int
            or not 0 <= policy["MaximumRetryCount"] <= 2147483647):
        raise RetirementError("Receipt contains an invalid restart policy")


def plan(client):
    rows = []
    for name in TARGETS:
        row = client.inspect(name)
        if row is not None:
            validate_target(row)
            if row["name"] != name:
                raise RetirementError("Target name changed during inspection")
            rows.append(row)
    return {"schema": "home.voice-retirement.v1", "daemon_id": client.daemon_id(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "targets": rows, "missing": [n for n in TARGETS if n not in {r["name"] for r in rows}],
            "state": "planned", "operations": []}


def validate_receipt(receipt, client, expected_daemon_id):
    if (not expected_daemon_id or receipt.get("schema") != "home.voice-retirement.v1"
            or receipt.get("daemon_id") != expected_daemon_id
            or client.daemon_id() != expected_daemon_id):
        raise RetirementError("Receipt and explicitly expected Docker daemon must match")
    rows = receipt.get("targets")
    if not isinstance(rows, list) or len(rows) > len(TARGETS):
        raise RetirementError("Invalid receipt targets")
    for row in rows:
        validate_target(row)
    if len({r["name"] for r in rows}) != len(rows) or len({r["id"] for r in rows}) != len(rows):
        raise RetirementError("Duplicate receipt targets")
    if not isinstance(receipt.get("operations"), list):
        raise RetirementError("Invalid receipt operations")


def save_receipt(path, receipt, *, create=False):
    """Private durable metadata backup before any Docker mutation."""
    payload = json.dumps(receipt, indent=2) + "\n"
    if create:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        sync_parent(path)
        return
    fd, temporary = tempfile.mkstemp(prefix=".voice-retirement-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_parent(path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync_parent(path):
    if os.name == "posix":
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def verify_identity(client, row):
    current = client.inspect(row["id"])
    if current is None or current["id"] != row["id"] or current["name"] != row["name"]:
        raise RetirementError("Target was replaced or renamed; refusing mutation")
    return current


def execute(client, receipt, path, expected_daemon_id, *, rollback=False):
    validate_receipt(receipt, client, expected_daemon_id)
    # Preflight every recorded identity before the first change. Recheck each
    # immediately before use; fixed IDs cannot select replacement containers.
    for row in receipt["targets"]:
        verify_identity(client, row)
    receipt["state"] = "rolling_back" if rollback else "retiring"
    if not rollback:
        save_receipt(path, receipt, create=True)
    else:
        save_receipt(path, receipt)
    try:
        for row in receipt["targets"]:
            current = verify_identity(client, row)
            policy = row["restart"]
            restart = policy["Name"] if rollback else "no"
            if rollback and restart == "on-failure" and policy["MaximumRetryCount"]:
                restart += ":" + str(policy["MaximumRetryCount"])
            operations = [["update", "--restart=" + restart, row["id"]]]
            if (row["running"] if rollback else current["running"]):
                operations.append(["start", row["id"]] if rollback
                                  else ["stop", "--time", "30", row["id"]])
            for operation in operations:
                client.mutate(operation)
                receipt["operations"].append({"action": operation[0], "name": row["name"],
                                              "rollback": rollback})
                save_receipt(path, receipt)
            result = verify_identity(client, row)
            if not rollback and (result["running"] or result["restart"]["Name"] != "no"):
                raise RetirementError("Voice target remains running or restartable")
            if rollback and (result["restart"] != policy or (row["running"] and not result["running"])):
                raise RetirementError("Voice target rollback did not reach its recorded baseline")
        receipt["state"] = "rolled_back" if rollback else "retired"
        save_receipt(path, receipt)
    except BaseException as exc:
        # Never automatically re-start models after a partial failure. Keep the
        # original baseline and completed operations for operator recovery.
        receipt["state"] = "partial_rollback" if rollback else "partial_retirement"
        receipt["failure_type"] = type(exc).__name__
        save_receipt(path, receipt)
        raise
    return receipt


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform target-only operations")
    parser.add_argument("--rollback", action="store_true", help="restore receipt policies and originally running targets")
    parser.add_argument("--receipt", type=Path, help="private metadata backup (must not exist for retirement)")
    parser.add_argument("--expected-daemon-id", help="daemon identity verified during host preflight")
    args = parser.parse_args(argv)
    client = client or DockerClient()
    if args.rollback:
        if not args.receipt:
            parser.error("--rollback requires --receipt")
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        validate_receipt(receipt, client, args.expected_daemon_id)
    else:
        receipt = plan(client)
    if args.apply:
        if not args.receipt or not args.expected_daemon_id:
            parser.error("--apply requires --receipt and --expected-daemon-id")
        receipt = execute(client, copy.deepcopy(receipt), args.receipt,
                          args.expected_daemon_id, rollback=args.rollback)
    print(json.dumps({"dry_run": not args.apply, "rollback": args.rollback, **receipt}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RetirementError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Voice retirement stopped: {exc}", file=sys.stderr)
        raise SystemExit(1)
