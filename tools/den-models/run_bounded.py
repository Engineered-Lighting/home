"""Prepare/print a bounded Linux systemd command; execution is explicit.

This does not provision environments, fetch weights, or modify running units.
Use --execute only during an owner-authorized, host-qualified bounded trial.
"""
import argparse
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from den_models.artifacts import load_profile
from den_models.contracts import ContractError
from den_models.server import operator_config


def command(config_path, python_path, block_device, *, warmup=False):
    config = operator_config(config_path)
    profile = load_profile(config["profile_id"])
    python_path, block_device = Path(python_path), Path(block_device)
    if (not python_path.is_absolute() or not python_path.is_file() or not block_device.is_absolute()
            or any(c.isspace() for value in (str(config_path), str(python_path), str(block_device),
                                            *[v for v in config.values() if isinstance(v, str)]) for c in value)):
        raise ContractError("invalid_bounded_runner_path")
    info = block_device.stat()
    if not stat.S_ISBLK(info.st_mode) or f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}" != config["io_device"]:
        raise ContractError("io_block_device_mismatch")
    package = Path(__file__).resolve().parent
    writable = sorted({config["cache_root"], str(Path(config["execution_lease_file"]).parent),
                       str(Path(config["residency_file"]).parent)})
    properties = ["CPUQuota=400%", "MemoryMax=25769803776", "MemorySwapMax=0", "TasksMax=128",
                  "IOReadBandwidthMax=" + str(block_device) + " 104857600", "RuntimeMaxSec=1800",
                  # OOMPolicy=kill sets memory.oom.group itself. There is no
                  # MemoryOOMGroup= key: systemd 255 ignores it in unit files
                  # and systemd-run would refuse it as a transient property.
                  "KillMode=control-group", "TimeoutStopSec=5", "SendSIGKILL=yes", "OOMPolicy=kill",
                  "NoNewPrivileges=yes", "PrivateTmp=yes", "UMask=0077",
                  "ProtectSystem=strict", "ReadWritePaths=" + " ".join(writable),
                  "ReadOnlyPaths=" + " ".join([config["snapshot_root"], config["receipt_root"], config["frame_root"]])]
    result = ["systemd-run", "--user", "--collect", "--unit=den-models-" + profile["id"]]
    result.extend("--property=" + value for value in properties)
    result.extend(["--", str(python_path), "-I", "-B", str(package / "serve.py"), "--config", str(config_path)])
    if warmup:
        result.append("--warmup")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--block-device", required=True, type=Path)
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Execute the qualified bounded unit")
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        raise ContractError("bounded_runner_requires_linux")
    args.config = args.config.resolve(strict=True)
    cmd = command(args.config, args.python, args.block_device, warmup=args.warmup)
    if args.execute:
        return subprocess.run(cmd, check=False).returncode
    print(shlex.join(cmd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
