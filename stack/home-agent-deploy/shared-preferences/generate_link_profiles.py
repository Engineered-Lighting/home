#!/usr/bin/env python3
"""Write the Echo and Victoria account-linking profiles and Victoria ingress TLS.

Root-run on the LA host against the prepared shared-preferences directory. The
profiles contain only fixed endpoints, origins and container paths; secrets
stay in their separate staged files and are never read or printed here.
Existing profiles are accepted only when byte-identical, so a changed origin
(which both session stores pin on first open) always requires review.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

LISTENERS = {
    "echo-preferences": "172.23.0.31",
    "echo-identity": "172.23.0.33",
    "victoria-identity": "172.23.0.34",
    "link-coordinator": "172.23.0.35",
}
VICTORIA_LINK_IP = "172.23.0.36"
BROWSER_PORT, INGRESS_PORT, CORE_PORT = 9450, 9451, 9448
CALLBACK = "/api/agent/auth/callback"
ECHO_SECRETS = ("commitment_key", "journal_key", "handoff_credential", "proof_credential",
                "session_credential", "issuance_credential", "review_credential", "preference_credential")
VICTORIA_SECRETS = ("commitment_key", "journal_key", "session_encryption_key", "handoff_credential",
                    "proof_credential", "session_credential")


def build_profiles(tailnet, *, echo_root="/link", victoria_secrets="/run/secrets",
                   victoria_config="/config", victoria_journals="/journals"):
    if not re.fullmatch(r"[a-z0-9-]+\.ts\.net", tailnet):
        raise ValueError("tailnet domain rejected")
    home = f"https://home-app.{tailnet}"
    echo_agent = f"https://echo-agent.{tailnet}"
    victoria = f"https://victoria-agent.{tailnet}"
    core = lambda name, path="": f"https://{LISTENERS[name]}:{CORE_PORT}{path}"
    identity = "/internal/shared-identity/v1/"
    echo = {
        "version": 1,
        "commitmentKeyFile": f"{echo_root}/secrets/commitment_key",
        "journalKeyFile": f"{echo_root}/secrets/journal_key",
        "pairingDbPath": f"{echo_root}/journals/pairing.sqlite",
        "proofDbPath": f"{echo_root}/journals/proof.sqlite",
        "revocationDbPath": f"{echo_root}/journals/revocation.sqlite",
        "sessionRevocation": {"endpoint": core("echo-identity", identity + "session-revocations"),
                              "credentialFile": f"{echo_root}/secrets/session_credential"},
        "proofIngress": {"endpoint": core("echo-identity", identity + "auth-proofs"),
                         "credentialFile": f"{echo_root}/secrets/proof_credential"},
        "linkIssuance": {"endpoint": core("link-coordinator", identity + "link-issuance"),
                         "credentialFile": f"{echo_root}/secrets/issuance_credential"},
        "victoriaHandoff": {"endpoint": f"https://{VICTORIA_LINK_IP}:{INGRESS_PORT}{identity}victoria-handoff",
                            "credentialFile": f"{echo_root}/secrets/handoff_credential"},
        "linkReview": {"origin": core("link-coordinator"),
                       "credentialFile": f"{echo_root}/secrets/review_credential"},
        "personalMemory": {"origin": core("echo-preferences"),
                           "credentialFile": f"{echo_root}/secrets/preference_credential"},
        "personalMemoryHomeOrigins": [home],
        "victoriaBrowserOrigin": victoria,
    }
    victoria_profile = {
        "version": 1,
        "browserOrigin": victoria,
        "echoOrigins": [echo_agent],
        "haOrigin": f"https://home-app.{tailnet}:10001",
        "clientId": f"{victoria}/",
        "redirectUri": f"{victoria}{CALLBACK}",
        "idleTtlMs": 1_800_000,
        "absoluteTtlMs": 43_200_000,
        "sessionDbPath": f"{victoria_journals}/sessions.sqlite",
        "proofDbPath": f"{victoria_journals}/proof.sqlite",
        "revocationDbPath": f"{victoria_journals}/revocation.sqlite",
        "sessionEncryptionKeyFile": f"{victoria_secrets}/session_encryption_key",
        "journalKeyFile": f"{victoria_secrets}/journal_key",
        "commitmentKeyFile": f"{victoria_secrets}/commitment_key",
        "sessionRevocation": {"endpoint": core("victoria-identity", identity + "session-revocations"),
                              "credentialFile": f"{victoria_secrets}/session_credential"},
        "proofIngress": {"endpoint": core("victoria-identity", identity + "auth-proofs"),
                         "credentialFile": f"{victoria_secrets}/proof_credential"},
        "handoffCredentialFile": f"{victoria_secrets}/handoff_credential",
        "browserTls": {"certificateFile": f"{victoria_config}/browser-tls/browser.crt",
                       "privateKeyFile": f"{victoria_config}/browser-tls/browser.key"},
        "ingressTls": {"certificateFile": f"{victoria_config}/ingress-tls/server.crt",
                       "privateKeyFile": f"{victoria_config}/ingress-tls/server.key"},
        "browserListener": {"address": VICTORIA_LINK_IP, "port": BROWSER_PORT},
        "ingressListener": {"address": VICTORIA_LINK_IP, "port": INGRESS_PORT},
    }
    return echo, victoria_profile


def _render(profile):
    return (json.dumps(profile, indent=2) + "\n").encode("utf-8")


def _write_once(path: Path, data: bytes, uid: int) -> str:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError(f"{path.name} exists and differs; review before replacing")
        return "unchanged"
    with open(path, "xb") as handle:
        os.fchmod(handle.fileno(), 0o400)
        os.fchown(handle.fileno(), uid, uid)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return "written"


def _require_staged(directory: Path, names):
    for name in names:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"staged secret missing: {directory.parent.name}/{path.name}")


def issue_ingress_certificate(root: Path, uid: int, days: int = 90) -> str:
    target = root / "victoria-bff/config/ingress-tls"
    if (target / "server.crt").exists():
        return "unchanged"
    authority = root / "authority"
    target.mkdir(mode=0o700)
    os.chown(target, uid, uid)
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        (work / "ext.cnf").write_text(
            f"subjectAltName=IP:{VICTORIA_LINK_IP}\nextendedKeyUsage=serverAuth\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\nbasicConstraints=critical,CA:FALSE\n")
        subprocess.run(["openssl", "req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                        "-nodes", "-subj", "/CN=victoria-link-ingress", "-keyout", str(work / "server.key"),
                        "-out", str(work / "server.csr")], check=True, capture_output=True)
        subprocess.run(["openssl", "x509", "-req", "-in", str(work / "server.csr"),
                        "-CA", str(authority / "ca.crt"), "-CAkey", str(authority / "ca.key"),
                        "-set_serial", str(int.from_bytes(os.urandom(16), "big")), "-days", str(days),
                        "-extfile", str(work / "ext.cnf"), "-out", str(work / "server.crt")],
                       check=True, capture_output=True)
        subprocess.run(["openssl", "verify", "-CAfile", str(authority / "ca.crt"), str(work / "server.crt")],
                       check=True, capture_output=True)
        for name, mode in (("server.key", 0o400), ("server.crt", 0o444)):
            _write_once(target / name, (work / name).read_bytes(), uid)
            os.chmod(target / name, mode)
    return "issued"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--tailnet", required=True)
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise SystemExit("link profile generation requires root")
    root = args.root
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("prepared root must be an absolute directory")
    uid = 1000
    _require_staged(root / "echo-bff/secrets", ECHO_SECRETS)
    _require_staged(root / "victoria-bff/secrets", VICTORIA_SECRETS)
    for name in ("echo-bff/config/internal-ca.crt", "victoria-bff/config/internal-ca.crt",
                 "authority/ca.crt", "authority/ca.key"):
        if not (root / name).is_file():
            raise ValueError(f"staged file missing: {name}")
    echo, victoria = build_profiles(args.tailnet)
    results = {
        "echo-bff/config/link.json": _write_once(root / "echo-bff/config/link.json", _render(echo), uid),
        "victoria-bff/config/link.json": _write_once(root / "victoria-bff/config/link.json", _render(victoria), uid),
        "victoria-bff/config/ingress-tls": issue_ingress_certificate(root, uid),
    }
    print(json.dumps(results, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"link profile generation failed: {error}", file=sys.stderr)
        raise SystemExit(78)
