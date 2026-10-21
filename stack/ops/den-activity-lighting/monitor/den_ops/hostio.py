"""Host I/O: read-only commands, private files, HTTP and atomic state writes.

``Host`` is the one seam the monitor and the soak use to touch the machine;
the tests replace it with a fake. Its command runner only executes an
allow-list of read-only invocations, so the monitor cannot clear a failed
unit, restart a service, or edit the journal even through a config mistake.
"""
from __future__ import annotations

import json
import hashlib
import os
import ssl
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

# systemctl verbs that only read. Anything else (restart, reset-failed,
# start, stop, kill, ...) is refused before a process is spawned.
_SYSTEMCTL_READ_VERBS = {"list-units", "show", "is-system-running", "is-active", "is-failed"}
_SYSTEMCTL_READ_FLAGS = {"--user", "--failed", "--plain", "--no-legend", "--no-pager", "--value", "--all"}
_JOURNALCTL_READ_PREFIXES = ("-k", "-q", "-o", "json", "--no-pager", "--output=json", "--after-cursor=",
                             "--since=", "-n", "--lines=", "-b", "--boot", "--utc", "--show-cursor")
_JOURNALCTL_WRITE_FLAGS = ("--vacuum", "--rotate", "--flush", "--sync", "--relinquish", "--setup-keys",
                           "--update-catalog", "--smart-relinquish", "--cursor-file")


class RefusedCommand(Exception):
    pass


def check_readonly(argv):
    """Raise RefusedCommand unless argv is an allow-listed read-only call."""
    if not argv:
        raise RefusedCommand("empty command")
    name = os.path.basename(str(argv[0]))
    args = [str(a) for a in argv[1:]]
    if name == "systemctl":
        verbs = [a for a in args if not a.startswith("-")]
        flags = [a for a in args if a.startswith("-") and not a.startswith("-p") and not a.startswith("--property")]
        if not verbs or verbs[0] not in _SYSTEMCTL_READ_VERBS:
            raise RefusedCommand("systemctl verb not read-only: %s" % (verbs[:1] or ["<none>"])[0])
        if any(f not in _SYSTEMCTL_READ_FLAGS for f in flags):
            raise RefusedCommand("systemctl flag not allowed")
        return
    if name == "journalctl":
        for arg in args:
            if arg.startswith(_JOURNALCTL_WRITE_FLAGS):
                raise RefusedCommand("journalctl flag not read-only: %s" % arg)
            if not (arg.startswith(_JOURNALCTL_READ_PREFIXES) or arg.isdigit()):
                raise RefusedCommand("journalctl flag not allowed: %s" % arg)
        return
    if name == "nvidia-smi":
        if not all(a.startswith(("--query-gpu=", "--format=")) for a in args):
            raise RefusedCommand("nvidia-smi: only --query-gpu reads are allowed")
        return
    if name == "tegrastats":
        if any(a not in ("--interval",) and not a.isdigit() for a in args):
            raise RefusedCommand("tegrastats: only --interval is allowed")
        return
    raise RefusedCommand("command not allow-listed: %s" % name)


def read_private(path, max_bytes=64 * 1024):
    """A secret or private config file: no group/other permission bits, owned
    by this user, not a symlink, bounded size."""
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PermissionError("%s must be a regular file, not a link" % path)
    if os.name == "posix":
        if info.st_mode & 0o077:
            raise PermissionError("%s must not be readable by group or others (chmod 600)" % path)
        if info.st_uid != os.getuid():
            raise PermissionError("%s must be owned by the monitor's user" % path)
    if info.st_size > max_bytes:
        raise PermissionError("%s is larger than %d bytes" % (path, max_bytes))
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def read_token(path):
    token = read_private(path, 4096).strip()
    if not 16 <= len(token) <= 4000 or any(c.isspace() for c in token):
        raise ValueError("%s does not hold a single token" % path)
    return token


def write_atomic(path, doc):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(doc, out, indent=1, sort_keys=True)
            out.write("\n")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


class Host:
    """The real machine."""

    def __init__(self, command_timeout_s=10.0):
        self.command_timeout_s = command_timeout_s

    def now(self):
        return time.time()

    def read_text(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def run(self, argv):
        """(returncode, stdout) for an allow-listed read-only command."""
        check_readonly(argv)
        try:
            proc = subprocess.Popen(list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    env={**os.environ, "SYSTEMD_PAGER": "", "LC_ALL": "C"})
            timed_out = threading.Event()
            def expire():
                timed_out.set()
                proc.kill()
            timer = threading.Timer(self.command_timeout_s, expire)
            timer.start()
            try:
                raw = proc.stdout.read(1048577)
                if len(raw) > 1048576:
                    proc.kill()
                    proc.wait()
                    return 125, "command output exceeded 1 MiB"
                proc.wait()
                if timed_out.is_set():
                    return 124, "command timed out"
                return proc.returncode, raw.decode("utf-8", "replace")
            finally:
                timer.cancel()
                proc.stdout.close()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        except OSError as error:
            return 127, str(error)

    def file_sha256(self, path):
        if os.path.getsize(path) > 64 * 1048576:
            raise ValueError("individual provenance artifact exceeds 64 MiB")
        value = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                value.update(chunk)
        return value.hexdigest()

    def monotonic(self):
        return time.monotonic()

    def first_line(self, argv, timeout_s=5.0):
        """First stdout line of a streaming read-only command (tegrastats)."""
        check_readonly(argv)
        try:
            proc = subprocess.Popen(list(argv), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            return None
        timer = threading.Timer(timeout_s, proc.kill)
        timer.start()
        try:
            line = proc.stdout.readline()
        finally:
            timer.cancel()
            proc.kill()
            proc.wait()
        return line.strip() or None

    def http(self, method, url, *, token=None, body=None, ca_file=None, timeout_s=10.0, allow_plain=False):
        """(status, parsed JSON or None). Network errors give status 0.
        Plain http is refused unless it is loopback or ``allow_plain`` (Home
        Assistant on a LAN/tailnet-only path, set explicitly in the config)."""
        loopback = url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))
        if not url.startswith("https://") and not loopback and not (allow_plain and url.startswith("http://")):
            raise ValueError("refusing a plain-http URL that is not loopback: %s" % url)
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", "den-ops-monitor/1")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", "Bearer " + token)
        context = ssl.create_default_context(cafile=ca_file) if url.startswith("https://") else None
        try:
            with urllib.request.urlopen(request, timeout=timeout_s, context=context) as response:
                raw = response.read(1024 * 1024)
                status = response.status
        except urllib.error.HTTPError as error:
            raw, status = error.read(64 * 1024), error.code
        except (OSError, ValueError):
            return 0, None
        try:
            return status, json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return status, None
