"""Authenticated loopback HTTP transport; scheduling remains asynchronous."""
import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import signal
import threading
import time

from .artifacts import load_profile
from .contracts import ContractError, canonical, ident, strict_json
from .runtime import Runtime

MAX_BODY = 64 * 1024
CONFIG_KEYS = {"schema", "profile_id", "snapshot_root", "receipt_root", "frame_root", "cache_root",
               "control_state_file", "execution_lease_file", "residency_file", "token_file",
               "gpu_selector", "io_device", "port"}


def operator_config(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 16 * 1024:
        raise ContractError("invalid_operator_config")
    value = strict_json(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != CONFIG_KEYS or value["schema"] != "den.model-operator/v1":
        raise ContractError("invalid_operator_config")
    if type(value["port"]) is not int or not 1024 <= value["port"] <= 65535:
        raise ContractError("invalid_port")
    for key in CONFIG_KEYS - {"schema", "profile_id", "gpu_selector", "io_device", "port"}:
        item = value[key]
        if not isinstance(item, str) or not Path(item).is_absolute() or "://" in item:
            raise ContractError("operator_paths_must_be_local_absolute")
    # All directories are prepared by the operator, never created from a
    # request. The model environment and materialized snapshots are separate.
    for key in ("snapshot_root", "receipt_root", "frame_root", "cache_root"):
        directory = Path(value[key])
        if not directory.is_dir() or directory.is_symlink():
            raise ContractError("operator_directory_unavailable")
    for key in ("execution_lease_file", "residency_file", "token_file", "control_state_file"):
        if not Path(value[key]).parent.is_dir() or Path(value[key]).is_symlink():
            raise ContractError("operator_file_unavailable")
    load_profile(value["profile_id"])
    return value


def bearer_token(path):
    file = Path(path)
    if not file.is_file() or file.is_symlink() or file.stat().st_size > 256:
        raise ContractError("private_bearer_file_required")
    if os.name == "posix":
        stat = file.stat()
        if stat.st_mode & 0o077 or stat.st_uid != os.getuid():
            raise ContractError("private_bearer_file_required")
    value = file.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ContractError("private_bearer_file_required")
    return value


def write_residency(path, record):
    """Private atomic accounting record, removed only after process reap."""
    path = Path(path)
    if record is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(canonical(record))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def handler_for(runtime, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DenModels/1"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, *args):
            pass  # Never log bearer credentials, frame refs, questions, or output.

        def _respond(self, status, value):
            raw = canonical(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)
            self.close_connection = True

        def _auth(self):
            provided = self.headers.get("Authorization", "")
            if not hmac.compare_digest(provided.encode(), ("Bearer " + token).encode()):
                self._respond(401, {"error": "unauthorized"})
                return False
            return True

        def _body(self):
            if self.headers.get("Transfer-Encoding") is not None:
                raise ContractError("chunked_requests_unsupported")
            raw = self.headers.get("Content-Length", "")
            if not raw.isdigit() or not 0 < int(raw) <= MAX_BODY:
                raise ContractError("invalid_body_length")
            data = self.rfile.read(int(raw))
            if len(data) != int(raw):
                raise ContractError("incomplete_body")
            return strict_json(data)

        def do_GET(self):
            if not self._auth():
                return
            if self.path == "/healthz":
                return self._respond(200, runtime.health())
            if self.path == "/metrics":
                return self._respond(200, runtime.metrics())
            prefix = "/v1/intent-jobs/"
            if self.path.startswith(prefix) and ident(self.path[len(prefix):]):
                result = runtime.get(self.path[len(prefix):])
                return self._respond(200 if result else 404, result or {"error": "job_not_found"})
            self._respond(404, {"error": "route_not_found"})

        def do_POST(self):
            if not self._auth():
                return
            try:
                if self.path != "/v1/intent-jobs":
                    return self._respond(404, {"error": "route_not_found"})
                result = runtime.submit(self._body())
                self._respond(202, result)
            except (ContractError, UnicodeError, json.JSONDecodeError, OSError):
                self._respond(400, {"error": "invalid_request"})

        def do_DELETE(self):
            if not self._auth():
                return
            prefix = "/v1/intent-jobs/"
            if self.path.startswith(prefix) and ident(self.path[len(prefix):]):
                result = runtime.cancel(self.path[len(prefix):])
                return self._respond(200 if result else 404, result or {"error": "job_not_found"})
            self._respond(404, {"error": "route_not_found"})
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline, bounded optional Den V-JEPA-LM sidecar")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="Check admission without importing torch")
    parser.add_argument("--warmup", action="store_true", help="Operator startup only: prepare lazy residency")
    args = parser.parse_args(argv)
    config = operator_config(args.config)
    token = bearer_token(config["token_file"])
    profile = load_profile(config["profile_id"])
    runtime = Runtime(config, profile, residency_changed=lambda record: write_residency(config["residency_file"], record))
    try:
        runtime.refresh_admission()
    except (ContractError, OSError, ValueError):
        pass
    if args.check:
        print(canonical(runtime.health()))
        return 0
    if Path(config["residency_file"]).exists():
        # A previous orphan cannot be declared dead just because this host
        # process restarted. Operator must establish terminal proof first.
        raise ContractError("unresolved_existing_residency_record")
    server = HTTPServer(("127.0.0.1", config["port"]), handler_for(runtime, token))
    try:
        if args.warmup:
            runtime.warmup()
    except BaseException:
        runtime.close()
        server.server_close()
        raise
    stopped = threading.Event()
    def schedule():
        while not stopped.wait(.05):
            try:
                runtime.tick()
            except Exception:
                # Unknown scheduler failure must stop dispatch and retain
                # leases until process termination proof is established.
                runtime.close()
    scheduler = threading.Thread(target=schedule, name="den-model-scheduler", daemon=True)
    scheduler.start()
    def shutdown(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, shutdown)
    try:
        server.serve_forever(poll_interval=.1)
    finally:
        server.server_close()
        runtime.close()
        deadline = time.monotonic() + 5
        while runtime.worker is not None and time.monotonic() < deadline:
            runtime.tick()
            time.sleep(.05)
        stopped.set()
        scheduler.join(timeout=1)
        # On an uninterruptible child, deliberately retain both records. The
        # systemd KillMode=control-group/RuntimeMaxSec is the final boundary.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
