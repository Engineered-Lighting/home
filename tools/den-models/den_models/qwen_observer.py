"""ROOT-only, bounded, inspect -> direct scrape -> inspect queue observer.

Default CLI operation only checks local configuration. No daemon, process,
network, or model probe occurs without --observe and the fixed bounded unit.
Docker inspection prints a selected metadata projection, never Env or commands.
"""
import argparse
import hashlib
from http.client import HTTPConnection
import ipaddress
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from den_models.contracts import ContractError, canonical, finite, strict_json
from den_models.qwen_queue import (BINDING_KEYS, CLOCK_DOMAIN, MAX_AGE_SECONDS, MAX_METRICS_BYTES,
                                   OBSERVER_SCHEMA, SCHEMA, digest, observer_code_digest,
                                   parse_reported_gauges, root_owned_json, validate_binding, validate_policy)
from den_models.resources import current_boot_id, process_start_identity

ENDPOINT = "unix:///var/run/docker.sock"
UNIT = "den-qwen-queue-observer.service"
CONFIG_KEYS = {"schema", "daemon_endpoint", "policy", "receipt_file", "receipt_gid", "poll_seconds",
               "cycle_timeout_seconds", "receipt_ttl_seconds", "runtime_seconds"}


def validate_config(config):
    if not isinstance(config, dict) or set(config) != CONFIG_KEYS or config["schema"] != "den.qwen-observer-config/v1":
        raise ContractError("qwen_observer_config_invalid")
    if (config["daemon_endpoint"] != ENDPOINT or not isinstance(config["receipt_file"], str)
            or not Path(config["receipt_file"]).is_absolute() or "://" in config["receipt_file"]
            or type(config["receipt_gid"]) is not int or config["receipt_gid"] < 0
            or not finite(config["poll_seconds"]) or not .5 <= config["poll_seconds"] <= 2
            or not finite(config["cycle_timeout_seconds"]) or not .1 <= config["cycle_timeout_seconds"] <= 1.5
            or not finite(config["receipt_ttl_seconds"]) or not .1 <= config["receipt_ttl_seconds"] <= MAX_AGE_SECONDS
            or type(config["runtime_seconds"]) is not int or not 1 <= config["runtime_seconds"] <= 1800):
        raise ContractError("qwen_observer_limits_unconfigured")
    validate_policy(config["policy"])
    return config


def remaining(deadline, clock=time.monotonic):
    seconds = deadline - clock()
    if seconds <= 0:
        raise ContractError("qwen_observer_deadline")
    return seconds


def require_bounded_root(config):
    """Fail before Docker/socket calls unless the reviewed unit bounds apply."""
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ContractError("qwen_observer_requires_linux_root")
    group = next((line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::")), None)
    if group is None or Path(group).name != UNIT or ".." in Path(group).parts:
        raise ContractError("qwen_observer_bounded_unit_required")
    root = Path("/sys/fs/cgroup") / group.lstrip("/")
    for filename, maximum in (("memory.max", 96 * 1024**2), ("memory.swap.max", 0), ("pids.max", 16)):
        raw = (root / filename).read_text().strip()
        if raw == "max" or int(raw) > maximum:
            raise ContractError("qwen_observer_resource_cap_missing")
    quota, period = (root / "cpu.max").read_text().split()
    if quota == "max" or int(quota) / int(period) > .25:
        raise ContractError("qwen_observer_cpu_cap_missing")
    run = subprocess.run(["/usr/bin/systemctl", "show", UNIT, "--property=RuntimeMaxUSec", "--value"],
                         capture_output=True, text=True, timeout=2, check=False,
                         env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
    # systemctl's duration formatting is accepted only as exact integer seconds
    # or minutes; the example unit is RuntimeMaxSec=1800 (30min).
    raw = run.stdout.strip()
    if raw.endswith("min") and raw[:-3].isdigit():
        seconds = int(raw[:-3]) * 60
    elif raw.endswith("s") and raw[:-1].isdigit():
        seconds = int(raw[:-1])
    else:
        raise ContractError("qwen_observer_runtime_cap_unproven")
    if run.returncode or not 0 < seconds <= 1800 or config["runtime_seconds"] > seconds:
        raise ContractError("qwen_observer_runtime_cap_unproven")
    sock = Path("/var/run/docker.sock").stat()
    binary = Path("/usr/bin/docker").stat()
    if sock.st_uid != 0 or not stat.S_ISSOCK(sock.st_mode) or binary.st_uid != 0 or binary.st_mode & 0o022:
        raise ContractError("qwen_local_daemon_endpoint_unqualified")


class DockerInspector:
    """Fixed CLI/endpoint and selected fields only; never a general Docker API."""
    def __init__(self, config, *, clock=time.monotonic):
        require_bounded_root(config)
        self.config, self.clock = config, clock
        self.directory = tempfile.TemporaryDirectory(prefix="qwen-observer-docker-")

    def close(self):
        self.directory.cleanup()

    def query(self, args, deadline):
        run = subprocess.run(["/usr/bin/docker", "--host=" + ENDPOINT, "--config=" + self.directory.name, *args],
                             capture_output=True, timeout=remaining(deadline, self.clock), check=False,
                             env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "GOMAXPROCS": "1"})
        if run.returncode or len(run.stdout) > 64 * 1024:
            raise ContractError("qwen_selected_inspection_unavailable")
        remaining(deadline, self.clock)
        return strict_json(run.stdout)

    def inspect(self, policy, deadline):
        daemon = self.query(["info", "--format", "{{json .ID}}"], deadline)
        name = policy["network_name"]  # validate_policy excludes template metacharacters.
        projection = ('{"container_id":{{json .Id}},"image_id":{{json .Image}},'
                      '"compose_project":{{json (index .Config.Labels "com.docker.compose.project")}},'
                      '"compose_service":{{json (index .Config.Labels "com.docker.compose.service")}},'
                      '"started_at":{{json .State.StartedAt}},"restart_count":{{json .RestartCount}},'
                      '"init_pid":{{json .State.Pid}},"running":{{json .State.Running}},'
                      '"paused":{{json .State.Paused}},"restarting":{{json .State.Restarting}},'
                      '"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},'
                      '"network":{{with index .NetworkSettings.Networks "' + name + '"}}'
                      '{"network_id":{{json .NetworkID}},"endpoint_id":{{json .EndpointID}},'
                      '"ip_address":{{json .IPAddress}}}{{else}}null{{end}}}')
        container = self.query(["container", "inspect", "--format", projection, policy["container_id"]], deadline)
        net_projection = ('{"network_id":{{json .Id}},"name":{{json .Name}},"driver":{{json .Driver}},'
                          '"scope":{{json .Scope}},"ipam":{{json .IPAM.Config}}}')
        network = self.query(["network", "inspect", "--format", net_projection, policy["network_id"]], deadline)
        if (not isinstance(container, dict) or container.get("running") is not True
                or container.get("paused") is not False or container.get("restarting") is not False
                or not isinstance(container.get("network"), dict) or not isinstance(network, dict)
                or network.get("name") != name or network.get("network_id") != policy["network_id"]):
            raise ContractError("qwen_container_or_network_unavailable")
        binding = {key: container[key] for key in ("container_id", "image_id", "compose_project", "compose_service",
                                                   "started_at", "restart_count", "init_pid", "health")}
        binding.update(container["network"], daemon_id=daemon, network_name=name,
                       network_driver=network.get("driver"), network_scope=network.get("scope"),
                       init_process_start_identity=process_start_identity(container["init_pid"]))
        matches = []
        for row in network.get("ipam") or []:
            try:
                subnet = ipaddress.IPv4Network(row["Subnet"], strict=True)
                if ipaddress.IPv4Address(binding["ip_address"]) in subnet:
                    matches.append(str(subnet))
            except (KeyError, ValueError, TypeError):
                continue
        if len(matches) != 1:
            raise ContractError("qwen_private_bridge_subnet_unqualified")
        binding["subnet"] = matches[0]
        return validate_binding(binding, policy)


def direct_metrics(binding, deadline, *, clock=time.monotonic, connect=HTTPConnection):
    """Bounded direct origin request; no redirects, proxies, API POST or cache."""
    connection = connect(binding["ip_address"], 8000, timeout=min(.5, remaining(deadline, clock)))
    timer = None
    try:
        connection.connect()
        active_socket = connection.sock
        def abort():
            try:
                # HTTPConnection can detach .sock for Connection: close while
                # HTTPResponse still owns its makefile. Retain that socket so
                # a slow-drip body cannot outlive the whole-cycle deadline.
                if active_socket is not None:
                    active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(remaining(deadline, clock), abort)
        timer.daemon = True
        timer.start()
        started = clock()
        connection.request("GET", "/metrics", headers={"Accept": "text/plain", "Cache-Control": "no-cache", "Connection": "close"})
        response = connection.getresponse()
        kind = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        length = response.getheader("Content-Length")
        age = response.getheader("Age")
        if (response.status != 200 or kind not in ("text/plain", "application/openmetrics-text")
                or age not in (None, "0") or length is not None and (not length.isdigit() or int(length) > MAX_METRICS_BYTES)):
            raise ContractError("qwen_direct_metrics_response_unqualified")
        raw = response.read(MAX_METRICS_BYTES + 1)
        received = clock()
        remaining(deadline, clock)
        if not raw or len(raw) > MAX_METRICS_BYTES or length is not None and len(raw) != int(length):
            raise ContractError("qwen_direct_metrics_body_unqualified")
        return raw, started, received
    finally:
        if timer is not None:
            timer.cancel()
        connection.close()


class Observer:
    """Pure orchestration with narrow inspection/HTTP seams for unit fakes."""
    def __init__(self, config, inspector, *, fetch=direct_metrics, clock=time.monotonic,
                 boot=current_boot_id, identity=process_start_identity):
        self.config, self.inspector, self.fetch, self.clock = validate_config(config), inspector, fetch, clock
        self.boot_id, self.identity, self.sequence = boot(), identity, 0
        self.code_sha256 = observer_code_digest()

    def sample(self):
        self.sequence += 1
        config, policy = self.config, self.config["policy"]
        cycle_started = self.clock()
        deadline = cycle_started + config["cycle_timeout_seconds"]
        attestation = {"schema": OBSERVER_SCHEMA, "uid": 0, "pid": os.getpid(),
                       "process_start_identity": self.identity(os.getpid()), "boot_id": self.boot_id,
                       "clock_domain": CLOCK_DOMAIN, "code_sha256": self.code_sha256, "config_sha256": digest(config)}
        try:
            before = validate_binding(self.inspector.inspect(policy, deadline), policy)
            raw, started, received = self.fetch(before, deadline)
            gauges = parse_reported_gauges(raw, policy["expected_series"])
            after = validate_binding(self.inspector.inspect(policy, deadline), policy)
            checked = self.clock()
            remaining(deadline, self.clock)
            if before != after:
                raise ContractError("qwen_binding_changed_during_scrape")
            if not finite(started) or not finite(received) or not cycle_started <= started <= received <= checked:
                raise ContractError("qwen_scrape_clock_invalid")
            return {"schema": SCHEMA, "status": "observed", "sequence": self.sequence, "host_boot_id": self.boot_id,
                    "request_started_at": started, "observed_at": received, "postcheck_at": checked,
                    "published_at": self.clock(), "valid_until": received + config["receipt_ttl_seconds"],
                    "policy": policy, "policy_sha256": digest(policy), "binding": before, "binding_sha256": digest(before),
                    "gauges": gauges, "response_sha256": hashlib.sha256(raw).hexdigest(), "response_bytes": len(raw),
                    "observer": attestation}
        except Exception:
            return {"schema": SCHEMA, "status": "unknown", "sequence": self.sequence, "host_boot_id": self.boot_id,
                    "published_at": self.clock(), "reason": "qwen_direct_observation_unavailable_or_binding_changed",
                    "observer": attestation}


def publish_root(path, value, gid):
    path = Path(path)
    if os.geteuid() != 0 or type(gid) is not int or gid < 0 or not path.is_absolute():
        raise ContractError("root_queue_publication_unconfigured")
    for parent in path.parents:
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ContractError("root_queue_publication_parent_unprotected")
    raw = canonical(value).encode()
    if len(raw) > 16 * 1024 or path.is_symlink():
        raise ContractError("root_queue_publication_invalid")
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchown(output.fileno(), 0, gid)
            os.fchmod(output.fileno(), 0o640)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="Default: validate local configuration, perform no probes")
    parser.add_argument("--observe", action="store_true", help="Explicit ROOT bounded-unit observation/publication")
    parser.add_argument("--once", action="store_true", help="One observation inside the bounded ROOT unit")
    args = parser.parse_args(argv)
    if args.observe and args.check or args.once and not args.observe:
        parser.error("--observe is explicit; --once requires --observe")
    # Check mode reads only the supplied source/template, so it is usable on
    # the workstation and never reaches Docker or proc/cgroup observation.
    if args.config.stat().st_size > 16 * 1024:
        raise ContractError("qwen_observer_config_oversize")
    with args.config.open("rb") as source:
        raw = source.read(16 * 1024 + 1)
    if len(raw) > 16 * 1024:
        raise ContractError("qwen_observer_config_oversize")
    config = strict_json(raw)
    if not args.observe:
        try:
            validate_config(config)
            configured = True
        except ContractError:
            configured = False
        print(canonical({"schema": "den.qwen-observer-check/v1", "configured": configured, "probes_performed": False}))
        return 0 if configured else 2
    config = root_owned_json(args.config, shared_gid=None)
    validate_config(config)
    inspector = DockerInspector(config)
    try:
        observer = Observer(config, inspector)
        stopped = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda _sig, _frame: stopped.set())
        end = time.monotonic() + config["runtime_seconds"]
        while time.monotonic() < end and not stopped.is_set():
            began = time.monotonic()
            value = observer.sample()
            publish_root(config["receipt_file"], value, config["receipt_gid"])
            if args.once:
                return 0 if value["status"] == "observed" else 2
            stopped.wait(max(.01, config["poll_seconds"] - (time.monotonic() - began)))
    finally:
        inspector.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
