#!/usr/bin/env python3
"""Run and monitor the LA host's selected restic backup to the Victoria UNAS."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_CONFIG = Path("/etc/unas-linux-backup/backup.json")
REPOSITORY = re.compile(r"rclone:unas:LinuxBackup/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\Z")
SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


class BackupError(Exception):
    """A content-free failure that can be recorded in monitoring and the journal."""


def now():
    return datetime.now(timezone.utc)


def parse_config(config_path):
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not REPOSITORY.fullmatch(config["repository"]):
            raise ValueError("repository must be rclone:unas:LinuxBackup/<repository>")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", config["host"]):
            raise ValueError("invalid snapshot host label")
        for key in ("include", "exclude", "required_mounts", "exports"):
            if not isinstance(config.get(key, []), list):
                raise ValueError(f"{key} must be a list")
        if not (config.get("include") or config.get("exports")):
            raise ValueError("include at least one approved source or export")
        if not all(isinstance(pattern, str) and pattern for pattern in config.get("exclude", [])):
            raise ValueError("exclude entries must be nonempty restic patterns")
        if not re.fullmatch(r"[1-9][0-9]{0,3}[KM]", config.get("bandwidth_limit", "4M")):
            raise ValueError("bandwidth_limit must use K or M, for example 4M")
        return config
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise BackupError(f"invalid backup configuration ({type(exc).__name__})") from None


def local_path(value):
    if not isinstance(value, str):
        raise BackupError("source paths must be strings")
    path = Path(value)
    if not path.is_absolute() or path == Path(path.anchor) or ".." in path.parts:
        raise BackupError("source paths must name a specific absolute file or directory")
    try:
        if path.resolve(strict=True) != path:
            raise BackupError("source path contains a symbolic link; use its resolved path")
    except OSError:
        raise BackupError("required source path is unavailable") from None
    return path


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def export_identity(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        raise BackupError("export and checksum must be nonempty regular files")
    return identity(info)


def verify_export(entry):
    try:
        if "directory" in entry:
            directory = local_path(entry["directory"])
            pattern = entry["artifact_pattern"]
            if not isinstance(pattern, str) or not re.fullmatch(r"[a-zA-Z0-9_.*?-]+", pattern):
                raise ValueError
            sidecars = directory.glob(pattern + ".sha256")
            latest = max(sidecars, key=lambda path: (path.lstat().st_mtime_ns, path.name), default=None)
            if latest is None:
                raise BackupError("no finalized export checksum in the configured directory")
            checksum = local_path(str(latest))
            artifact = local_path(str(checksum.with_suffix("")))
        else:
            artifact = local_path(entry["artifact"])
            checksum = local_path(entry["checksum"])
        if artifact == checksum:
            raise ValueError
        max_age = entry["max_age_hours"]
        if type(max_age) is not int or max_age <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise BackupError("invalid export configuration") from None

    artifact_id, checksum_id = export_identity(artifact), export_identity(checksum)
    if checksum_id[2] > 4096:
        raise BackupError("export checksum sidecar is too large")
    cutoff = now()
    for file_id in (artifact_id, checksum_id):
        age = cutoff.timestamp() - file_id[3] / 1_000_000_000
        if age < -300 or age > timedelta(hours=max_age).total_seconds():
            raise BackupError("required export or checksum is stale or future dated")
    parts = checksum.read_text(encoding="utf-8").strip().split(maxsplit=1)
    if not parts or not SHA256.fullmatch(parts[0]):
        raise BackupError("invalid export SHA-256 sidecar")
    if len(parts) == 2 and parts[1].removeprefix("*") != artifact.name:
        raise BackupError("checksum sidecar names a different artifact")
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != parts[0].lower():
        raise BackupError("export does not match its SHA-256 sidecar")
    return {artifact: artifact_id, checksum: checksum_id}


def validate_export_identities(exports):
    if any(export_identity(path) != expected for path, expected in exports.items()):
        raise BackupError("an export changed during verification or backup")


def select_sources(config, protected):
    for entry in config.get("required_mounts", []):
        mount_path = local_path(entry)
        mounted = subprocess.run(["findmnt", "--mountpoint", str(mount_path)], capture_output=True, check=False)
        if mounted.returncode:
            raise BackupError("required source filesystem is not mounted")
    sources = [local_path(value) for value in config.get("include", [])]
    for source in sources:
        if not (source.is_file() or source.is_dir()) or not os.access(source, os.R_OK, effective_ids=True):
            raise BackupError("required source is unreadable or has an unsupported type")
    exports = {}
    for entry in config.get("exports", []):
        exports.update(verify_export(entry))
    sources = list(dict.fromkeys([*sources, *exports]))
    if any(source == path or source.is_relative_to(path) for source in sources for path in protected):
        raise BackupError("a source points into the backup state, cache, or credentials")
    validate_export_identities(exports)
    return sources, exports


def write_receipt(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def status(state_dir):
    try:
        receipt = json.loads((state_dir / "last-success.json").read_text(encoding="utf-8"))
        if receipt["outcome"] != "success":
            raise ValueError
        age = now() - datetime.fromisoformat(receipt["finished_at"])
        if age < timedelta(0) or age > timedelta(hours=36):
            raise BackupError("backup is stale; last success must be within 36 hours")
        print(json.dumps(receipt))
    except (OSError, KeyError, TypeError, ValueError):
        raise BackupError("no usable success receipt") from None


def run_restic(command, environment, diagnostics, needs_snapshot):
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
        result = subprocess.run(
            command, env=environment, stdin=subprocess.DEVNULL, stdout=output,
            stderr=None if diagnostics else subprocess.DEVNULL, check=False,
        )
        if result.returncode:
            reason = "incomplete snapshot" if result.returncode == 3 and "backup" in command else "command failed"
            raise BackupError(f"restic {reason} (exit {result.returncode})")
        if not needs_snapshot:
            return {}
        output.seek(0)
        summary = None
        for line in output:
            record = json.loads(line)
            if record["message_type"] == "error":
                raise BackupError("restic reported a source error")
            if record["message_type"] == "summary":
                summary = record
        if not summary or not re.fullmatch(r"[0-9a-f]{64}", summary.get("snapshot_id", "")):
            raise BackupError("restic returned no complete snapshot ID")
        return {
            "snapshot_id": summary["snapshot_id"],
            "bytes_added_packed": summary.get("data_added_packed"),
            "bytes_added": summary.get("data_added"),
            "files_processed": summary.get("total_files_processed"),
        }


def execute(args):
    import fcntl

    with (args.state_dir / "repository-operation.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("another repository operation is running") from None

        receipt = {"started_at": now().isoformat(), "outcome": "started"}
        attempt_path = args.state_dir / "last-attempt.json"
        try:
            if args.mode == "backup":
                write_receipt(attempt_path, receipt)
            config = parse_config(args.config)
            password = args.credentials_dir / "restic-password"
            rclone = args.credentials_dir / "rclone.conf"
            if not all(path.is_file() and os.access(path, os.R_OK) for path in (password, rclone)):
                raise BackupError("systemd credentials are missing or unreadable")
            environment = os.environ.copy()
            environment["RCLONE_CONFIG"] = str(rclone)
            command = [
                "restic", "--repo", config["repository"], "--password-file", str(password),
                "--cache-dir", str(args.cache_dir), "-o",
                f"rclone.args=serve restic --stdio --bwlimit {config.get('bandwidth_limit', '4M')}",
            ]
            if args.mode == "check":
                run_restic([*command, "check"], environment, args.diagnostics, False)
                print("Repository structure check passed")
                return
            protected = [args.config.parent, args.credentials_dir, args.state_dir, args.cache_dir]
            sources, exports = select_sources(config, protected)
            command += ["backup", "--json", "--host", config["host"], "--one-file-system", "--read-concurrency", "1"]
            for pattern in [*config.get("exclude", []), *(str(path) for path in protected)]:
                command += ["--exclude", pattern]
            if args.mode == "dry-run":
                command.append("--dry-run")
            result = run_restic(
                [*command, "--", *map(str, sources)], environment, args.diagnostics, args.mode == "backup",
            )
            validate_export_identities(exports)
            if args.mode == "dry-run":
                print(f"Dry run passed for {len(sources)} selected paths and {len(exports) // 2} verified exports")
                return
            receipt.update(result, finished_at=now().isoformat(), outcome="success")
            write_receipt(args.state_dir / "last-success.json", receipt)
        except (BackupError, OSError, KeyError, TypeError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, BackupError) else type(exc).__name__
            if args.mode == "backup":
                receipt.update(outcome="failed", finished_at=now().isoformat(), error=reason)
                write_receipt(attempt_path, receipt)
            raise BackupError(reason) from None
        write_receipt(attempt_path, receipt)
        print(json.dumps(receipt))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("backup", "dry-run", "check", "status"), default="backup")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/unas-linux-backup"))
    parser.add_argument("--cache-dir", type=Path, default=Path("/var/cache/unas-linux-backup"))
    parser.add_argument("--credentials-dir", type=Path, default=os.environ.get("CREDENTIALS_DIRECTORY"))
    parser.add_argument("--diagnostics", action="store_true", help="show restic stderr, which can contain source paths")
    args = parser.parse_args()
    try:
        if args.mode == "status":
            status(args.state_dir)
        else:
            if args.credentials_dir is None:
                raise BackupError("use the systemd unit or supply the directory holding its two credentials")
            execute(args)
    except (BackupError, OSError) as exc:
        reason = str(exc) if isinstance(exc, BackupError) else type(exc).__name__
        print(f"UNAS Linux backup: {reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
