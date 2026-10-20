"""Static Victoria service launcher. Never installs/enables a system service.

--check-config is offline: no sockets or stores. run is Linux/Jetson-only,
requires provisioned credentials/trust and external commissioned timing
authority, and uses actual transport/storage classes. Absence of timing evidence
keeps inference closed while local reconciliation/retention remain operational.
No model host, authority issuer or clock evidence is fabricated here.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import signal
import socket
import stat
import sys
import threading
import time

from .adapter import VictoriaAdapter
from .camera_client import CameraClient
from .gateway_client import GatewayClient
from .health import HealthProbe
from .mqtt_transport import MqttTransport
from .observations import VictoriaObservationStore
from .protocol import FrigateWire
from .runtime import VictoriaRuntime
from .service_config import ServiceConfig, load_config
from .submissions import SubmissionJournal
from .timing_client import TimingClient


def validate_host(config):
    if sys.platform != "linux" or platform.machine().lower() not in ("aarch64", "arm64"):
        raise ValueError("service requires the provisioned Linux ARM64 Jetson")
    hostname = socket.gethostname().lower()
    if hostname in ("engineeredlightingserver1", "home-app") or hostname != config.expected_hostname:
        raise ValueError("service host identity does not match provisioned Victoria host")
    model = Path("/proc/device-tree/model").read_text().rstrip("\x00\n")
    if "Orin Nano" not in model:
        raise ValueError("provisioned camera owner must run on its reviewed Orin Nano host")
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if not boot:
        raise ValueError("camera-host kernel boot identity unavailable")
    return boot


def _state_directory(config):
    path = Path(config.state_directory)
    # Installation, filesystem allocation and ownership are commissioning work.
    # The running service never silently creates a new data root.
    for item in (*reversed(path.parents), path):
        info = item.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid())
                or info.st_mode & 0o022):
            raise ValueError("state directory must have protected nonsymlink ancestors")
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError("state directory must be owner-only for service account")
    for name in ("submissions.sqlite", "observations.sqlite"):
        for suffix in ("", "-wal", "-shm", ".lock"):
            item = path / (name + suffix)
            if item.exists() or item.is_symlink():
                info = item.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_mode & 0o077):
                    raise ValueError("state artifact ownership or type invalid")
    return path


class VictoriaService:
    """One owner thread composes actual clients; no provider plugin injection."""
    def __init__(self, config: ServiceConfig):
        boot = validate_host(config)
        # Host/SoC provisioning is checked before reading the current kernel
        # boot. This is identity binding, not new timing evidence: the authority
        # must still supply freshly commissioned receipts for this actual boot.
        # Pi/LA boot bindings are never inferred or silently advanced here.
        config = replace(config, health=replace(config.health, camera_boot_id=boot))
        if (importlib.metadata.version("paho-mqtt") != "2.1.0"
                or importlib.metadata.version("Pillow") != "12.3.0"):
            raise ValueError("reviewed pinned service dependencies required")
        state = _state_directory(config)
        self.config = config
        self.runtime = self.store = self.journal = self.health = self.timing = None
        self._stopped = False
        self._last_step_end = None
        os.umask(0o077)
        try:
            adapter = VictoriaAdapter()
            gateway = GatewayClient(config.gateway)
            camera = CameraClient(config.health.camera_bearer)
            mqtt = MqttTransport(config.mqtt, will=FrigateWire(adapter).last_will())
            self.timing = TimingClient(config.timing)
            self.health = HealthProbe(config.health, clock_provider=self.timing, stats_timing_provider=self.timing)
            # Acquire journal lifetime ownership before opening the observation
            # store, so a second process cannot mutate source storage at startup.
            self.journal = SubmissionJournal(state / "submissions.sqlite", device_id=config.runtime.device_id,
                                             gateway_identity=gateway.gateway_identity,
                                             max_records=config.journal_max_records,
                                             free_space_floor_bytes=config.free_space_floor_bytes)
            self.store = VictoriaObservationStore(state / "observations.sqlite", config.binding)
            self.runtime = VictoriaRuntime(adapter=adapter, mqtt=mqtt, camera=camera, gateway=gateway,
                                            journal=self.journal, store=self.store, settings=config.runtime,
                                            health_reader=self.health.read)
        except Exception:
            if self.health:
                self.health.stop()
            if self.timing:
                self.timing.stop()
            if self.store:
                self.store.close()
            if self.journal:
                self.journal.close()
            raise

    def step(self):
        if self._stopped:
            raise RuntimeError("service is stopping")
        before = time.monotonic()
        # Scheduler/storage stalls invalidate context; they are not hidden by
        # substituting a newer timestamp for an older adapter decision.
        if self._last_step_end is not None and before - self._last_step_end > 2:
            self.runtime.adapter.unavailable()
        self.timing.poll()
        self.health.poll()
        self.runtime.step()
        self._last_step_end = time.monotonic()
        if self._last_step_end - before > 1:
            self.runtime.adapter.unavailable()

    def close(self):
        self._stopped = True
        self.health.stop()
        self.timing.stop()
        if not self.runtime.close(wait_seconds=0):
            return False  # Keep journal and process ownership until worker exits.
        if self.store:
            self.store.close()
            self.store = None
        if self.journal:
            self.journal.close()
            self.journal = None
        return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check-config", action="store_true", help="offline configuration inspection; not commissioning")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config, enforce_linux=not args.check_config)
        if args.check_config:
            # Constructing HTTPS clients validates trust bytes without sockets.
            GatewayClient(config.gateway)
            context = MqttTransport._tls_context(config.mqtt)
            del context
            timing = TimingClient(config.timing)
            health = HealthProbe(config.health, clock_provider=timing, stats_timing_provider=timing)
            health.stop()
            timing.stop()
            print(json.dumps({"configuration_valid": True, "deployment_ready": False,
                              "requires": ["host_storage_resource_commissioning", "timing_authority",
                                           "clock_and_stats_measurement_evidence", "network_acl_tls_commissioning",
                                           "la_admission_and_authorization"]}))
            return 0
        service = VictoriaService(config)
    except Exception:
        print("Victoria service provisioning failed; no secret details logged.", file=sys.stderr)
        return 2
    stopping = threading.Event()
    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.signal(sig, lambda *_: stopping.set())
    failed = False
    try:
        while not stopping.is_set():
            started = time.monotonic()
            service.step()
            stopping.wait(max(0, 1 - (time.monotonic() - started)))
    except Exception:
        failed = True
        print("Victoria service stopped after a runtime fault; durable accounting retained.", file=sys.stderr)
    finally:
        # No replacement process may own this source while its worker remains
        # live. A supervisor may terminate the entire process group on timeout;
        # the next process then finds durable indeterminate submission state.
        while not service.close():
            time.sleep(.1)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
