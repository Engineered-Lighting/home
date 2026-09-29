#!/usr/bin/env python3
"""Write the Echo and Victoria account-linking profiles and Victoria ingress TLS.

With ``--lighting echo[,victoria]`` it also adds the Echo BFF's lighting
transport, writes the private lighting listener profile for the named homes
and issues that listener's TLS leaf. Without it, output is byte-identical to
before, so existing live profiles keep verifying.

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
    "lighting": "172.23.0.37",
}
VICTORIA_LINK_IP = "172.23.0.36"
BROWSER_PORT, INGRESS_PORT, CORE_PORT = 9450, 9451, 9448
CALLBACK = "/api/agent/auth/callback"
ECHO_SECRETS = ("commitment_key", "journal_key", "handoff_credential", "proof_credential",
                "session_credential", "issuance_credential", "review_credential", "preference_credential")
LIGHTING_SECRETS = ("credential", "database_url", "action_key", "consent_key", "journal_key")
LIGHTING_HOME_PORTS = {"echo": 10000, "victoria": 10001}
LIGHTING_GRANT_SECONDS = 30 * 86400  # the same displayed lifetime as preference sharing
VICTORIA_SECRETS = ("commitment_key", "journal_key", "session_encryption_key", "handoff_credential",
                    "proof_credential", "session_credential")


def build_profiles(tailnet, *, echo_root="/link", victoria_secrets="/run/secrets",
                   victoria_config="/config", victoria_journals="/journals", victoria_tls="/tls", lighting=False):
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
    if lighting:
        echo["lighting"] = {"origin": core("lighting"), "credentialFile": f"{echo_root}/secrets/lighting_credential"}
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
        # Exported by the victoria-agent tailnet node into a root-owned
        # directory, mounted read-only (see ../tailnet-origins/README.md).
        "browserTls": {"certificateFile": f"{victoria_tls}/browser.crt",
                       "privateKeyFile": f"{victoria_tls}/browser.key"},
        "ingressTls": {"certificateFile": f"{victoria_config}/ingress-tls/server.crt",
                       "privateKeyFile": f"{victoria_config}/ingress-tls/server.key"},
        "browserListener": {"address": VICTORIA_LINK_IP, "port": BROWSER_PORT},
        "ingressListener": {"address": VICTORIA_LINK_IP, "port": INGRESS_PORT},
    }
    return echo, victoria_profile


def build_lighting_profile(tailnet, homes, *, secrets="/run/secrets", config="/config", journals="/journals"):
    """The private lighting listener (app.lighting_server). Paths only, never values."""
    if not re.fullmatch(r"[a-z0-9-]+\.ts\.net", tailnet):
        raise ValueError("tailnet domain rejected")
    if not homes or len(set(homes)) != len(homes) or set(homes) - set(LIGHTING_HOME_PORTS):
        raise ValueError("lighting homes must be echo and/or victoria")
    return {
        "version": 1,
        "address": LISTENERS["lighting"],
        "port": CORE_PORT,
        "credential_file": f"{secrets}/credential",
        "certificate_file": f"{config}/server.crt",
        "private_key_file": f"{config}/server.key",
        "action_key_file": f"{secrets}/action_key",
        "consent_key_file": f"{secrets}/consent_key",
        "journal_key_file": f"{secrets}/journal_key",
        "journal_path": f"{journals}/lighting.sqlite",
        "database_url_file": f"{secrets}/database_url",
        "grant_lifetime_seconds": LIGHTING_GRANT_SECONDS,
        # Each home's light-only endpoint is reached through its Tailscale Serve port.
        "homes": {site: {"origin": f"https://home-app.{tailnet}:{LIGHTING_HOME_PORTS[site]}",
                         "secret_file": f"{secrets}/{site}_home_secret"} for site in homes},
    }


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
    return issue_leaf(root, root / "victoria-bff/config/ingress-tls", VICTORIA_LINK_IP,
                      "victoria-link-ingress", uid, days=days)


def issue_leaf(root: Path, target: Path, address: str, common_name: str, uid: int, days: int = 90) -> str:
    if (target / "server.crt").exists():
        return "unchanged"
    authority = root / "authority"
    # Stage key material on the prepared root's encrypted volume, never /tmp.
    with tempfile.TemporaryDirectory(dir=root / "authority") as work:
        work = Path(work)
        (work / "ext.cnf").write_text(
            f"subjectAltName=IP:{address}\nextendedKeyUsage=serverAuth\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\nbasicConstraints=critical,CA:FALSE\n")
        subprocess.run(["openssl", "req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                        "-nodes", "-subj", f"/CN={common_name}", "-keyout", str(work / "server.key"),
                        "-out", str(work / "server.csr")], check=True, capture_output=True)
        subprocess.run(["openssl", "x509", "-req", "-in", str(work / "server.csr"),
                        "-CA", str(authority / "ca.crt"), "-CAkey", str(authority / "ca.key"),
                        "-set_serial", str(int.from_bytes(os.urandom(16), "big")), "-days", str(days),
                        "-extfile", str(work / "ext.cnf"), "-out", str(work / "server.crt")],
                       check=True, capture_output=True)
        subprocess.run(["openssl", "verify", "-CAfile", str(authority / "ca.crt"), str(work / "server.crt")],
                       check=True, capture_output=True)
        # Create the target only once issuance succeeded, so a failed run can
        # simply be retried.
        target.mkdir(mode=0o700, exist_ok=True)
        os.chown(target, uid, uid)
        for name, mode in (("server.key", 0o400), ("server.crt", 0o444)):
            _write_once(target / name, (work / name).read_bytes(), uid)
            os.chmod(target / name, mode)
    return "issued"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--tailnet", required=True)
    parser.add_argument("--lighting", default="", help="comma-separated homes with lighting (echo,victoria)")
    args = parser.parse_args(argv)
    homes = [home for home in args.lighting.split(",") if home]
    if os.geteuid() != 0:
        raise SystemExit("link profile generation requires root")
    root = args.root
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("prepared root must be an absolute directory")
    uid = 1000
    _require_staged(root / "echo-bff/secrets", ECHO_SECRETS + (("lighting_credential",) if homes else ()))
    if homes:
        _require_staged(root / "lighting/secrets", LIGHTING_SECRETS + tuple(f"{h}_home_secret" for h in homes))
        for name in ("lighting/config", "lighting/journals"):
            if not (root / name).is_dir():
                raise ValueError(f"staged directory missing: {name}")
    _require_staged(root / "victoria-bff/secrets", VICTORIA_SECRETS)
    for name in ("echo-bff/config/internal-ca.crt", "victoria-bff/config/internal-ca.crt",
                 "authority/ca.crt", "authority/ca.key"):
        if not (root / name).is_file():
            raise ValueError(f"staged file missing: {name}")
    echo, victoria = build_profiles(args.tailnet, lighting=bool(homes))
    results = {
        "echo-bff/config/link.json": _write_once(root / "echo-bff/config/link.json", _render(echo), uid),
        "victoria-bff/config/link.json": _write_once(root / "victoria-bff/config/link.json", _render(victoria), uid),
        "victoria-bff/config/ingress-tls": issue_ingress_certificate(root, uid),
    }
    if homes:
        # The lighting listener runs as the Core UID, like the other private listeners.
        results["lighting/config/listener.json"] = _write_once(
            root / "lighting/config/listener.json", _render(build_lighting_profile(args.tailnet, homes)), 10001)
        results["lighting/config/server"] = issue_leaf(root, root / "lighting/config", LISTENERS["lighting"],
                                                       "lighting", 10001)
    print(json.dumps(results, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"link profile generation failed: {error}", file=sys.stderr)
        raise SystemExit(78)
