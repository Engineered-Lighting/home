#!/usr/bin/env python3
"""Renew the shared-preference private TLS leaves from the retained internal CA.

Root-run on the LA host against the prepared shared-preferences directory.

  check-expiry  read-only; exits 10 when any leaf, or the CA, needs attention
  renew         reissue one leaf with its reviewed subject, IP SAN and key
                policy, staged beside the live files and renamed into place
  rollback      put back the leaf that a renew (or rollback) displaced
  probe         confirm from the listener's client container that the live
                listener serves the on-disk leaf, then clear restart-pending

Renewal never creates, replaces or re-signs the CA. It refuses when the CA
cannot back a full leaf lifetime plus the warning window, when any client trust
copy differs from authority/ca.crt, or when the current leaf no longer verifies
against it; CA rotation is the separate procedure in README.md. Key material is
read only through file descriptors and piped to openssl, and is never printed.
Displaced leaves go to the root-only tls-renewal/ store, which the
shared-runtime backup does not cover.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class Leaf:
    name: str
    directory: str  # relative to the prepared root
    common_name: str
    address: str
    port: int
    key_usage: tuple[str, ...]
    listener: str  # container that serves the leaf
    client: str  # container that probes it, trusting the CA like a real client
    optional: bool = False
    ends_sessions: bool = False  # restarting its listener ends linking use of current sessions


# The Core leaves were issued at preparation with digitalSignature only;
# generate_link_profiles.issue_leaf adds keyEncipherment. Each keeps its own.
CORE_USAGE = ("digitalSignature",)
GENERATOR_USAGE = ("digitalSignature", "keyEncipherment")
CORE_PORT, INGRESS_PORT = 9448, 9451
BFF = "home-agent-bff-1"
VICTORIA_LINK = "home-shared-preferences-victoria-link-1"


def _core(name: str, address: str, client: str = BFF) -> Leaf:
    return Leaf(name, f"{name}/config", name, address, CORE_PORT, CORE_USAGE,
                f"home-shared-preferences-{name}-1", client)


LEAVES = {leaf.name: leaf for leaf in (
    _core("echo-preferences", "172.23.0.31"),
    _core("victoria-preferences", "172.23.0.32"),  # staged; no client yet
    _core("echo-identity", "172.23.0.33"),
    _core("victoria-identity", "172.23.0.34", VICTORIA_LINK),
    _core("link-coordinator", "172.23.0.35"),
    Leaf("victoria-link-ingress", "victoria-bff/config/ingress-tls", "victoria-link-ingress", "172.23.0.36",
         INGRESS_PORT, GENERATOR_USAGE, VICTORIA_LINK, BFF, ends_sessions=True),
    Leaf("lighting", "lighting/config", "lighting", "172.23.0.37", CORE_PORT, GENERATOR_USAGE,
         "home-shared-preferences-lighting-1", BFF, optional=True),
)}
CA_SUBJECT = "CN=Home Shared Preferences Internal CA"
# Every client trust copy must stay byte-identical to authority/ca.crt; the
# Core config ca.crt copies are checked wherever they exist.
CA_TRUST_COPIES = ("echo-bff/config/internal-ca.crt", "victoria-bff/config/internal-ca.crt")
LEAF_DAYS = 90  # the original issuance lifetime
WARN_DAYS = 30
PENDING_WARN = timedelta(days=1)  # a renew is followed by its restart and probe in the same window
STAGED = (".server.key.renew", ".server.crt.renew")
# Distinct from 1 (an uncaught traceback) so the unit can treat warnings as
# success: a failed unit stops Lab GPU work, an expiry warning must not.
EXIT_WARNINGS = 10
CA_MIN_DAYS = LEAF_DAYS + WARN_DAYS  # renewal refuses below this; the alert warns from it
STORE = "tls-renewal"
STAMP = re.compile(r"^[0-9]{8}T[0-9]{6}Z-(renew|rollback)(-[0-9]{1,2})?$")
MAX_PEM = 64 * 1024
OPENSSL = "/usr/bin/openssl"
DOCKER = "/usr/bin/docker"
CHILD_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
KEY_USAGE_NAMES = {"Digital Signature": "digitalSignature", "Key Encipherment": "keyEncipherment",
                   "Non Repudiation": "nonRepudiation", "Data Encipherment": "dataEncipherment",
                   "Key Agreement": "keyAgreement", "Certificate Sign": "keyCertSign", "CRL Sign": "cRLSign"}
EKU_NAMES = {"TLS Web Server Authentication": "serverAuth", "TLS Web Client Authentication": "clientAuth"}
PROBE_JS = (
    "const tls=require('node:tls');const [host,port]=process.argv.slice(1);"
    "const s=tls.connect({host,port:Number(port),timeout:10000},()=>{const c=s.getPeerCertificate();"
    "process.stdout.write(JSON.stringify({authorized:s.authorized,sha256:c.fingerprint256}));s.end();});"
    "s.on('timeout',()=>{console.error('timeout');s.destroy();process.exit(3);});"
    "s.on('error',e=>{console.error(String(e.code||e.message));process.exit(2);});"
)


class Refused(Exception):
    """A reviewed precondition failed; nothing was changed."""


class Unsafe(Exception):
    """The filesystem layout is not the reviewed one."""


@dataclass(frozen=True)
class Context:
    root: Path
    trusted_uid: int = 0
    check_parents: bool = True


@dataclass(frozen=True)
class CertInfo:
    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime
    serial: str
    sha256: str
    sans: tuple[str, ...]
    key_usage: frozenset[str]
    key_usage_critical: bool
    eku: frozenset[str]
    is_ca: bool | None
    curve: str
    public_key: bytes


# --------------------------------------------------------------------------
# openssl


def _openssl(*args: str, data: bytes | None = None) -> bytes:
    result = subprocess.run([OPENSSL, *args], input=data, capture_output=True, env=CHILD_ENV, timeout=60)
    if result.returncode != 0:
        # stderr may echo input names but never key bytes; keep only one line.
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()[:1]
        raise Refused(f"openssl {args[0]} failed{': ' + detail[0] if detail else ''}")
    return result.stdout


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%b %d %H:%M:%S %Y GMT").replace(tzinfo=timezone.utc)


def _extensions(text: str) -> dict[str, tuple[bool, str]]:
    found: dict[str, tuple[bool, str]] = {}
    name, critical, values = None, False, []
    for line in text.splitlines():
        if line and not line[0].isspace() and line.startswith("X509v3 "):
            if name:
                found[name] = (critical, " ".join(values))
            header = line[len("X509v3 "):].rstrip()
            critical = header.endswith("critical")
            name, values = header.split(":")[0].strip(), []
        elif name and line.strip():
            values.append(line.strip())
    if name:
        found[name] = (critical, " ".join(values))
    return found


def inspect(pem: bytes) -> CertInfo:
    text = _openssl("x509", "-noout", "-nameopt", "RFC2253", "-subject", "-issuer", "-startdate", "-enddate",
                    "-serial", "-ext", "subjectAltName,keyUsage,extendedKeyUsage,basicConstraints",
                    data=pem).decode("utf-8", "replace")
    fields = dict(line.split("=", 1) for line in text.splitlines() if re.match(r"^(subject|issuer|notBefore|notAfter|serial)=", line))
    ext = _extensions(text)
    sans = tuple(part.strip().replace("IP Address:", "IP:") for part in ext.get("Subject Alternative Name", (False, ""))[1].split(",") if part.strip())
    usage_critical, usage = ext.get("Key Usage", (False, ""))
    eku = ext.get("Extended Key Usage", (False, ""))[1]
    basic = ext.get("Basic Constraints", (False, ""))[1]
    public_key = _openssl("x509", "-noout", "-pubkey", data=pem)
    curve_text = _openssl("pkey", "-pubin", "-noout", "-text", data=public_key).decode("utf-8", "replace")
    curve = re.search(r"ASN1 OID: (\S+)", curve_text)
    return CertInfo(
        subject=fields.get("subject", "").strip(),
        issuer=fields.get("issuer", "").strip(),
        not_before=_parse_time(fields["notBefore"]),
        not_after=_parse_time(fields["notAfter"]),
        serial=fields.get("serial", "").strip().upper(),
        sha256=hashlib.sha256(_openssl("x509", "-outform", "DER", data=pem)).hexdigest(),
        sans=sans,
        key_usage=frozenset(KEY_USAGE_NAMES.get(v.strip(), v.strip()) for v in usage.split(",") if v.strip()),
        key_usage_critical=usage_critical,
        eku=frozenset(EKU_NAMES.get(v.strip(), v.strip()) for v in eku.split(",") if v.strip()),
        is_ca=None if not basic else "CA:TRUE" in basic,
        curve=curve.group(1) if curve else "",
        public_key=public_key.strip(),
    )


def key_matches(key: bytes, info: CertInfo) -> bool:
    try:
        return _openssl("pkey", "-pubout", data=key).strip() == info.public_key
    except Refused:
        return False


def verifies(ca_path: Path, pem: bytes) -> bool:
    result = subprocess.run([OPENSSL, "verify", "-CAfile", str(ca_path), "-purpose", "sslserver"],
                            input=pem, capture_output=True, env=CHILD_ENV, timeout=60)
    return result.returncode == 0


def policy_errors(leaf: Leaf, info: CertInfo) -> list[str]:
    errors = []
    if info.subject != f"CN={leaf.common_name}":
        errors.append("subject")
    if info.issuer != CA_SUBJECT:
        errors.append("issuer")
    if info.sans != (f"IP:{leaf.address}",):
        errors.append("subjectAltName")
    if info.key_usage != frozenset(leaf.key_usage) or not info.key_usage_critical:
        errors.append("keyUsage")
    if info.eku != frozenset({"serverAuth"}):
        errors.append("extendedKeyUsage")
    if info.is_ca is not False:
        errors.append("basicConstraints")
    if info.curve != "prime256v1":
        errors.append("key type")
    return errors


# --------------------------------------------------------------------------
# Filesystem: every path below the prepared root is opened relative to a
# directory descriptor without following symlinks, so a writable directory
# (victoria-bff is owned by UID 1000) cannot redirect what root reads or writes.


def _nofollow_dir() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _require_trusted(details: os.stat_result, what: str, ctx: Context, *, private: bool) -> None:
    if details.st_uid != ctx.trusted_uid:
        raise Unsafe(f"{what} is not root-owned")
    if details.st_mode & (0o077 if private else 0o022):
        raise Unsafe(f"{what} is {'readable or ' if private else ''}writable by others")


def open_root(ctx: Context) -> int:
    root = ctx.root
    if not root.is_absolute():
        raise Unsafe("prepared root must be absolute")
    if ctx.check_parents:
        for parent in reversed(root.parents):
            details = parent.lstat()
            if stat.S_ISLNK(details.st_mode):
                raise Unsafe("symlink in prepared root path")
            _require_trusted(details, str(parent), ctx, private=False)
    try:
        fd = os.open(root, _nofollow_dir())
    except OSError as error:
        raise Unsafe("prepared root must be a real directory") from error
    _require_trusted(os.fstat(fd), "prepared root", ctx, private=False)
    return fd


def open_dir(base: int, relative: str) -> int:
    fd = os.dup(base)
    try:
        for part in PurePosixPath(relative).parts:
            child = os.open(part, _nofollow_dir(), dir_fd=fd)
            os.close(fd)
            fd = child
    except OSError:
        os.close(fd)
        raise
    return fd


def read_file(directory: int, name: str) -> tuple[bytes, os.stat_result]:
    # O_NONBLOCK: a FIFO planted in a writable directory must not hang root.
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as handle:
        details = os.fstat(handle.fileno())
        if not stat.S_ISREG(details.st_mode):
            raise Unsafe(f"{name} is not a regular file")
        data = handle.read(MAX_PEM + 1)
    if len(data) > MAX_PEM:
        raise Unsafe(f"{name} is too large")
    return data, details


def exists(directory: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def write_new(directory: int, name: str, data: bytes, *, uid: int, gid: int, mode: int) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def make_dir(directory: int, name: str, ctx: Context, *, exist_ok: bool) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=directory)
        created = os.open(name, _nofollow_dir(), dir_fd=directory)
        os.fchown(created, ctx.trusted_uid, -1)
        return created
    except FileExistsError:
        if not exist_ok:
            raise
    fd = os.open(name, _nofollow_dir(), dir_fd=directory)
    _require_trusted(os.fstat(fd), f"{STORE}/{name}", ctx, private=True)
    return fd


@contextlib.contextmanager
def _closing(fd: int):
    try:
        yield fd
    finally:
        os.close(fd)


def _lock(store: int) -> int:
    import fcntl

    fd = os.open(".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=store)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise Refused("another internal TLS renewal is running") from None
    return fd


# --------------------------------------------------------------------------
# Authority


@dataclass(frozen=True)
class Authority:
    path: Path
    info: CertInfo
    pem: bytes


def load_authority(ctx: Context, root: int, *, check_key: bool) -> Authority:
    try:
        authority = open_dir(root, "authority")
    except OSError as error:
        raise Unsafe("authority directory missing") from error
    with _closing(authority):
        _require_trusted(os.fstat(authority), "authority", ctx, private=True)
        pem, details = read_file(authority, "ca.crt")
        _require_trusted(details, "authority/ca.crt", ctx, private=False)
        info = inspect(pem)
        if info.subject != CA_SUBJECT or info.issuer != CA_SUBJECT or info.is_ca is not True:
            raise Refused("authority/ca.crt is not the retained internal CA")
        if check_key:
            key, key_details = read_file(authority, "ca.key")
            _require_trusted(key_details, "authority/ca.key", ctx, private=True)
            if not key_matches(key, info):
                raise Refused("authority/ca.key does not match authority/ca.crt")
    return Authority(ctx.root / "authority" / "ca.crt", info, pem)


def trust_drift(root: int, authority: Authority) -> list[str]:
    """Client trust copies that differ from authority/ca.crt (a sign of CA rotation)."""
    copies = list(CA_TRUST_COPIES) + [f"{leaf.directory}/ca.crt" for leaf in LEAVES.values()]
    drift = []
    for relative in copies:
        path = PurePosixPath(relative)
        try:
            with _closing(open_dir(root, str(path.parent))) as directory:
                if not exists(directory, path.name):
                    if relative in CA_TRUST_COPIES:
                        drift.append(f"{relative} missing")
                    continue
                data, _ = read_file(directory, path.name)
        except FileNotFoundError:
            if relative in CA_TRUST_COPIES:
                drift.append(f"{relative} missing")
            continue
        if data != authority.pem:
            drift.append(relative)
    return drift


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_left(moment: datetime, now: datetime) -> int:
    return int((moment - now).total_seconds() // 86400)


# --------------------------------------------------------------------------
# Restart-pending markers: the on-disk leaf is not what the listener serves
# until it restarts, so the alert keeps using the displaced leaf's expiry.


def read_pending(store: int | None, leaf: Leaf) -> dict | None:
    if store is None:
        return None
    try:
        with _closing(open_dir(store, "pending")) as pending:
            if not exists(pending, f"{leaf.name}.json"):
                return None
            data, _ = read_file(pending, f"{leaf.name}.json")
    except FileNotFoundError:
        return None
    return json.loads(data)


def write_pending(store: int, ctx: Context, leaf: Leaf, displaced_not_after: datetime, now: datetime) -> None:
    earlier = read_pending(store, leaf)
    previous = displaced_not_after
    if earlier:  # the listener may still serve an even older leaf
        previous = min(previous, datetime.fromisoformat(earlier["previous_not_after"].replace("Z", "+00:00")))
    marker = json.dumps({"since": _utc(now), "previous_not_after": _utc(previous)}, sort_keys=True).encode()
    with _closing(make_dir(store, "pending", ctx, exist_ok=True)) as pending:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(f".{leaf.name}.json.new", dir_fd=pending)
        write_new(pending, f".{leaf.name}.json.new", marker, uid=ctx.trusted_uid, gid=-1, mode=0o600)
        os.rename(f".{leaf.name}.json.new", f"{leaf.name}.json", src_dir_fd=pending, dst_dir_fd=pending)
        os.fsync(pending)


def clear_pending(store: int, leaf: Leaf) -> bool:
    try:
        with _closing(open_dir(store, "pending")) as pending:
            os.unlink(f"{leaf.name}.json", dir_fd=pending)
            os.fsync(pending)
            return True
    except FileNotFoundError:
        return False


# --------------------------------------------------------------------------
# check-expiry


def check_expiry(ctx: Context, *, now: datetime | None = None, warn_days: int = WARN_DAYS) -> dict:
    now = now or datetime.now(timezone.utc)
    warnings: list[str] = []
    report: dict = {"checked_at": _utc(now), "leaves": {}, "warnings": warnings}
    with _closing(open_root(ctx)) as root:
        authority = load_authority(ctx, root, check_key=False)
        ca_days = _days_left(authority.info.not_after, now)
        report["ca"] = {"not_after": _utc(authority.info.not_after), "days_left": ca_days, "sha256": authority.info.sha256}
        if authority.info.not_after <= now + timedelta(days=max(warn_days, CA_MIN_DAYS)):
            warnings.append(f"internal CA expires in {ca_days} days ({_utc(authority.info.not_after)}); renewal "
                            f"refuses below {CA_MIN_DAYS} days, so start the separate CA rotation procedure")
        for relative in trust_drift(root, authority):
            warnings.append(f"client trust copy {relative} differs from authority/ca.crt")
        store = None
        with contextlib.suppress(FileNotFoundError):
            store = open_dir(root, STORE)
        try:
            for leaf in LEAVES.values():
                report["leaves"][leaf.name] = entry = {}
                try:
                    with _closing(open_dir(root, leaf.directory)) as directory:
                        pem, _ = read_file(directory, "server.crt")
                        torn = [name for name in STAGED if exists(directory, name)]
                except FileNotFoundError:
                    entry["status"] = "absent"
                    if not leaf.optional:
                        warnings.append(f"{leaf.name}: server.crt missing")
                    continue
                if torn:
                    warnings.append(f"{leaf.name}: interrupted change ({', '.join(torn)} present); "
                                    "do not restart the listener, run rollback first")
                info = inspect(pem)
                pending = read_pending(store, leaf)
                effective = info.not_after
                if pending:
                    effective = min(effective, datetime.fromisoformat(pending["previous_not_after"].replace("Z", "+00:00")))
                    entry["restart_pending_since"] = pending["since"]
                    since = datetime.fromisoformat(pending["since"].replace("Z", "+00:00"))
                    if now - since > PENDING_WARN:
                        warnings.append(f"{leaf.name}: changed on disk {pending['since']} but not yet confirmed "
                                        f"live; restart {leaf.listener} and probe")
                days = _days_left(effective, now)
                entry.update(not_after=_utc(info.not_after), effective_not_after=_utc(effective), days_left=days,
                             serial=info.serial, status="ok")
                if effective <= now + timedelta(days=warn_days):
                    entry["status"] = "warn"
                    pending_note = " (the listener still serves the displaced leaf; restart it and probe)" if pending else ""
                    warnings.append(f"{leaf.name}: live leaf expires in {days} days ({_utc(effective)}){pending_note}")
                if not verifies(authority.path, pem) or policy_errors(leaf, info):
                    entry["status"] = "warn"
                    warnings.append(f"{leaf.name}: leaf no longer verifies against the CA or its reviewed policy")
        finally:
            if store is not None:
                os.close(store)
    return report


# --------------------------------------------------------------------------
# renew / rollback


def _stamp_dir(store: int, ctx: Context, leaf: Leaf, action: str, now: datetime) -> tuple[str, int]:
    with _closing(make_dir(store, leaf.name, ctx, exist_ok=True)) as history:
        base = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{action}"
        for attempt in range(1, 100):
            stamp = base if attempt == 1 else f"{base}-{attempt}"
            try:
                return stamp, make_dir(history, stamp, ctx, exist_ok=False)
            except FileExistsError:
                continue
    raise Refused("could not allocate a rollback directory")


def _live_pair(directory: int) -> tuple[bytes, os.stat_result, bytes, os.stat_result]:
    crt, crt_details = read_file(directory, "server.crt")
    key, key_details = read_file(directory, "server.key")
    return crt, crt_details, key, key_details


def _meta(details: os.stat_result) -> dict:
    return {"uid": details.st_uid, "gid": details.st_gid, "mode": oct(stat.S_IMODE(details.st_mode))}


def _remove_staged(directory: int) -> None:
    for temporary in STAGED:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=directory)


@contextlib.contextmanager
def _shielded():
    """Keep a dropped SSH session or ^C from landing between the two renames."""
    import signal

    names = [getattr(signal, n) for n in ("SIGHUP", "SIGINT", "SIGTERM", "SIGQUIT") if hasattr(signal, n)]
    previous = {number: signal.signal(number, signal.SIG_IGN) for number in names}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _install(directory: int, crt: bytes, key: bytes, crt_meta: dict, key_meta: dict) -> None:
    """Stage beside the live files, then rename key and certificate into place."""
    staged = ((STAGED[0], "server.key", key, key_meta), (STAGED[1], "server.crt", crt, crt_meta))
    try:
        _remove_staged(directory)
        for temporary, _, data, meta in staged:
            write_new(directory, temporary, data, uid=meta["uid"], gid=meta["gid"], mode=int(meta["mode"], 8))
        os.fsync(directory)
        with _shielded():
            for temporary, final, _, _ in staged:
                os.rename(temporary, final, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
    except BaseException:
        # Before the key rename nothing is live: never leave the staged key
        # behind. After it, the public .server.crt.renew stays as the torn-pair
        # marker that check-expiry reports until rollback repairs it.
        with contextlib.suppress(OSError):
            if exists(directory, STAGED[0]):
                _remove_staged(directory)
        raise
    installed_crt, _ = read_file(directory, "server.crt")
    installed_key, _ = read_file(directory, "server.key")
    if installed_crt != crt or installed_key != key:
        raise Refused("installed leaf differs from the staged leaf; roll back")


def _save_displaced(store: int, ctx: Context, leaf: Leaf, action: str, now: datetime, crt: bytes, key: bytes,
                    receipt: dict) -> str:
    stamp, saved = _stamp_dir(store, ctx, leaf, action, now)
    with _closing(saved):
        for name, data in (("server.crt", crt), ("server.key", key)):
            write_new(saved, name, data, uid=ctx.trusted_uid, gid=-1, mode=0o400)
        receipt = {**receipt, "stamp": stamp}
        write_new(saved, "receipt.json", (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(),
                  uid=ctx.trusted_uid, gid=-1, mode=0o400)
        os.fsync(saved)
    return stamp


def _check_leaf_modes(leaf: Leaf, crt_details: os.stat_result, key_details: os.stat_result) -> None:
    if key_details.st_mode & 0o007:
        raise Unsafe(f"{leaf.name}: server.key is accessible to others")
    if (crt_details.st_mode | key_details.st_mode) & 0o022:
        raise Unsafe(f"{leaf.name}: leaf files are writable by others")


def _issue(leaf: Leaf, authority: Authority, work: Path) -> tuple[bytes, bytes]:
    (work / "ext.cnf").write_text(
        f"subjectAltName=IP:{leaf.address}\nextendedKeyUsage=serverAuth\n"
        f"keyUsage=critical,{','.join(leaf.key_usage)}\nbasicConstraints=critical,CA:FALSE\n"
        "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n")
    _openssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
             "-subj", f"/CN={leaf.common_name}", "-keyout", str(work / "server.key"), "-out", str(work / "server.csr"))
    _openssl("x509", "-req", "-in", str(work / "server.csr"), "-CA", str(authority.path),
             "-CAkey", str(authority.path.with_name("ca.key")), "-set_serial", str(int.from_bytes(os.urandom(16), "big")),
             "-days", str(LEAF_DAYS), "-sha256", "-extfile", str(work / "ext.cnf"), "-out", str(work / "server.crt"))
    return (work / "server.crt").read_bytes(), (work / "server.key").read_bytes()


def _open_store(root: int, ctx: Context) -> int:
    return make_dir(root, STORE, ctx, exist_ok=True)


def renew(ctx: Context, leaf_name: str, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    leaf = LEAVES[leaf_name]
    with _closing(open_root(ctx)) as root, _closing(_open_store(root, ctx)) as store, _closing(_lock(store)):
        authority = load_authority(ctx, root, check_key=True)
        if authority.info.not_before > now:
            raise Refused("internal CA is not yet valid; check the host clock")
        if authority.info.not_after <= now + timedelta(days=CA_MIN_DAYS):
            raise Refused(f"internal CA expires {_utc(authority.info.not_after)}, under {CA_MIN_DAYS} days away; "
                          "a new leaf could outlive it. Rotate the CA first (separate procedure); "
                          "this command never replaces it")
        drift = trust_drift(root, authority)
        if drift:
            raise Refused(f"client trust differs from authority/ca.crt ({', '.join(drift)}); "
                          "a CA change needs the separate rotation procedure")
        if read_pending(store, leaf):
            raise Refused(f"{leaf.name}: the previous change still awaits a listener restart and probe")
        try:
            directory = open_dir(root, leaf.directory)
        except FileNotFoundError:
            raise Refused(f"{leaf.name}: {leaf.directory} is absent") from None
        with _closing(directory):
            crt, crt_details, key, key_details = _live_pair(directory)
            _check_leaf_modes(leaf, crt_details, key_details)
            current = inspect(crt)
            if not verifies(authority.path, crt):
                raise Refused(f"{leaf.name}: the current leaf does not verify against authority/ca.crt; "
                              "the CA may have been replaced. Stop and review")
            drifted = policy_errors(leaf, current)
            if drifted:
                raise Refused(f"{leaf.name}: the current leaf differs from the reviewed policy ({', '.join(drifted)})")
            if not key_matches(key, current):
                raise Refused(f"{leaf.name}: server.key does not match server.crt; use rollback")
            # Stage key material on the store's encrypted, root-only volume; never /tmp.
            with tempfile.TemporaryDirectory(dir=ctx.root / STORE, prefix=".work-") as work:
                new_crt, new_key = _issue(leaf, authority, Path(work))
            issued = inspect(new_crt)
            if not verifies(authority.path, new_crt) or policy_errors(leaf, issued) or not key_matches(new_key, issued):
                raise Refused(f"{leaf.name}: the issued leaf failed verification; nothing was installed")
            if issued.not_after > authority.info.not_after or issued.public_key == current.public_key:
                raise Refused(f"{leaf.name}: the issued leaf outlives the CA or reuses the key")
            receipt = {
                "action": "renew", "leaf": leaf.name, "directory": leaf.directory, "at": _utc(now),
                "ca_sha256": authority.info.sha256,
                "displaced": {"serial": current.serial, "sha256": current.sha256, "not_after": _utc(current.not_after),
                              "crt": _meta(crt_details), "key": _meta(key_details)},
                "installed": {"serial": issued.serial, "sha256": issued.sha256, "not_after": _utc(issued.not_after)},
            }
            stamp = _save_displaced(store, ctx, leaf, "renew", now, crt, key, receipt)
            write_pending(store, ctx, leaf, current.not_after, now)
            try:
                _install(directory, new_crt, new_key, _meta(crt_details), _meta(key_details))
            except BaseException:
                # Untouched live files need no restart; a torn pair keeps the
                # marker and is repaired with rollback --stamp <stamp>.
                with contextlib.suppress(OSError, Unsafe):
                    if _live_pair(directory)[0::2] == (crt, key):
                        clear_pending(store, leaf)
                raise
    return {"outcome": "renewed", "leaf": leaf.name, "rollback_stamp": stamp, "serial": issued.serial,
            "sha256": issued.sha256, "not_after": _utc(issued.not_after), "restart": leaf.listener,
            "ends_sessions": leaf.ends_sessions}


def rollback(ctx: Context, leaf_name: str, stamp: str, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    leaf = LEAVES[leaf_name]
    if not STAMP.fullmatch(stamp):
        raise Unsafe("rollback stamp rejected")
    with _closing(open_root(ctx)) as root, _closing(_open_store(root, ctx)) as store, _closing(_lock(store)):
        authority = load_authority(ctx, root, check_key=False)
        try:
            saved = open_dir(store, f"{leaf.name}/{stamp}")
        except FileNotFoundError:
            raise Refused(f"{leaf.name}: no saved leaf {stamp}") from None
        with _closing(saved):
            receipt = json.loads(read_file(saved, "receipt.json")[0])
            crt, _ = read_file(saved, "server.crt")
            key, _ = read_file(saved, "server.key")
        restored = inspect(crt)
        if receipt.get("leaf") != leaf.name or restored.sha256 != receipt["displaced"]["sha256"]:
            raise Refused(f"{leaf.name}: saved leaf {stamp} does not match its receipt")
        if not verifies(authority.path, crt) or policy_errors(leaf, restored) or not key_matches(key, restored):
            raise Refused(f"{leaf.name}: saved leaf {stamp} fails verification against the current CA")
        if restored.not_after <= now:
            raise Refused(f"{leaf.name}: saved leaf {stamp} has expired")
        with _closing(open_dir(root, leaf.directory)) as directory:
            live_crt, live_crt_details, live_key, live_key_details = _live_pair(directory)
            # Rollback must also work when an interrupted renew left a torn pair.
            try:
                live = inspect(live_crt)
            except Refused:
                live = None
            displaced = {"serial": live and live.serial, "sha256": live and live.sha256,
                         "not_after": live and _utc(live.not_after),
                         "crt": _meta(live_crt_details), "key": _meta(live_key_details)}
            displaced_not_after = live.not_after if live else restored.not_after
            new_stamp = _save_displaced(store, ctx, leaf, "rollback", now, live_crt, live_key, {
                "action": "rollback", "leaf": leaf.name, "directory": leaf.directory, "at": _utc(now),
                "ca_sha256": authority.info.sha256, "restored_from": stamp, "displaced": displaced,
                "installed": {"serial": restored.serial, "sha256": restored.sha256,
                              "not_after": _utc(restored.not_after)}})
            write_pending(store, ctx, leaf, displaced_not_after, now)
            _install(directory, crt, key, receipt["displaced"]["crt"], receipt["displaced"]["key"])
    return {"outcome": "rolled_back", "leaf": leaf.name, "restored_from": stamp, "rollback_stamp": new_stamp,
            "serial": restored.serial, "not_after": _utc(restored.not_after), "restart": leaf.listener,
            "ends_sessions": leaf.ends_sessions}


# --------------------------------------------------------------------------
# probe


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([DOCKER, *args], capture_output=True, text=True, env=CHILD_ENV, timeout=60)


def _running(container: str) -> bool:
    result = _docker("inspect", "-f", "{{.State.Running}}", container)
    state = result.stdout.strip() if result.returncode == 0 else ""
    if state not in ("true", "false"):
        raise Refused(f"cannot tell whether {container} is running (docker inspect failed)")
    return state == "true"


def probe(ctx: Context, leaf_name: str, *, client: str | None = None) -> dict:
    leaf = LEAVES[leaf_name]
    client = client or leaf.client
    with _closing(open_root(ctx)) as root, _closing(_open_store(root, ctx)) as store:
        with _closing(open_dir(root, leaf.directory)) as directory:
            on_disk = inspect(read_file(directory, "server.crt")[0])
        if not _running(leaf.listener):
            cleared = clear_pending(store, leaf)
            return {"outcome": "listener_not_running", "leaf": leaf.name, "pending_cleared": cleared,
                    "note": "it loads the on-disk leaf when it next starts"}
        if not _running(client):
            raise Refused(f"probe client {client} is not running")
        result = _docker("exec", client, "node", "-e", PROBE_JS, leaf.address, str(leaf.port))
        if result.returncode != 0:
            raise Refused(f"{leaf.name}: TLS from {client} failed ({result.stderr.strip()[:80]})")
        served = json.loads(result.stdout)
        if not served.get("authorized"):
            raise Refused(f"{leaf.name}: {client} does not trust the served leaf")
        if served.get("sha256", "").replace(":", "").lower() != on_disk.sha256:
            raise Refused(f"{leaf.name}: {leaf.listener} still serves a different leaf; restart it and probe again")
        cleared = clear_pending(store, leaf)
    return {"outcome": "serving", "leaf": leaf.name, "client": client, "serial": on_disk.serial,
            "not_after": _utc(on_disk.not_after), "pending_cleared": cleared}


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check-expiry", "renew", "rollback", "probe"):
        command = commands.add_parser(name)
        command.add_argument("--root", required=True, type=Path)
        if name != "check-expiry":
            command.add_argument("--leaf", required=True, choices=sorted(LEAVES))
    commands.choices["check-expiry"].add_argument("--warn-days", type=int, default=WARN_DAYS)
    commands.choices["rollback"].add_argument("--stamp", required=True)
    commands.choices["probe"].add_argument("--client-container")
    args = parser.parse_args(argv)
    if args.command != "check-expiry" and os.geteuid() != 0:
        raise SystemExit("internal TLS renewal requires root")
    os.umask(0o077)
    ctx = Context(args.root)
    try:
        if args.command == "check-expiry":
            report = check_expiry(ctx, warn_days=args.warn_days)
            print(json.dumps(report, sort_keys=True))
            for warning in report["warnings"]:
                print(f"internal TLS: {warning}", file=sys.stderr)
            return EXIT_WARNINGS if report["warnings"] else 0
        if args.command == "renew":
            result = renew(ctx, args.leaf)
        elif args.command == "rollback":
            result = rollback(ctx, args.leaf, args.stamp)
        else:
            result = probe(ctx, args.leaf, client=args.client_container)
    except Refused as error:
        print(f"internal TLS {args.command} refused: {error}", file=sys.stderr)
        return 3
    except (Unsafe, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"internal TLS {args.command} failed: {error}", file=sys.stderr)
        return 78
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
