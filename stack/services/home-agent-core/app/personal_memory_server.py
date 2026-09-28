"""Explicitly provisioned TLS listener for one home's preference BFF."""
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import sys

from .personal_memory_api import PersonalMemoryBinding
from .personal_memory_runtime import compose_personal_memory_ingress

NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12",
    "192.168.0.0/16", "100.64.0.0/10", "fc00::/7"))


@dataclass(frozen=True)
class ListenerProfile:
    address: str
    port: int
    certificate: str
    private_key: str = field(repr=False)
    binding: PersonalMemoryBinding = field(repr=False)
    review_key: bytes = field(repr=False)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate profile field")
        result[key] = value
    return result


def _file(value, limit):
    if type(value) is not str or not Path(value).is_absolute():
        raise ValueError("absolute provisioned file required")
    path = Path(value)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("provisioned file unavailable or too large")
    return path


def load_profile(path):
    source = _file(path, 4096)
    value = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    keys = {"version", "issuer", "address", "port", "credential_file",
            "review_key_file", "certificate_file", "private_key_file"}
    if (type(value) is not dict or set(value) != keys or type(value["version"]) is not int
        or value["version"] != 1 or type(value["port"]) is not int
        or not 1024 <= value["port"] <= 65535 or type(value["address"]) is not str):
        raise ValueError("invalid private listener profile")
    address = ipaddress.ip_address(value["address"])
    if str(address) != value["address"] or not any(address in network for network in NETWORKS):
        raise ValueError("explicit private listener address required")
    credential = _file(value["credential_file"], 65).read_bytes().removesuffix(b"\n")
    key = _file(value["review_key_file"], 65).read_bytes().removesuffix(b"\n")
    if re.fullmatch(b"[a-f0-9]{64}", key) is None:
        raise ValueError("dedicated review key required")
    if credential == key:
        raise ValueError("credential and signing key must be separate")
    binding = PersonalMemoryBinding(value["issuer"], credential)
    certificate = _file(value["certificate_file"], 131072)
    private_key = _file(value["private_key_file"], 131072)
    return ListenerProfile(str(address), value["port"], str(certificate), str(private_key),
        binding, bytes.fromhex(key.decode("ascii")))


def build_listener(core_application, profile):
    if type(profile) is not ListenerProfile:
        raise TypeError("validated private listener profile required")
    app = compose_personal_memory_ingress(core_application,
        binding=profile.binding, review_key=profile.review_key)

    @asynccontextmanager
    async def lifespan(_app):
        async with core_application.router.lifespan_context(core_application):
            yield

    app.router.lifespan_context = lifespan
    return app


def main():
    # No listener exists unless an operator provides every required file.
    profile = load_profile(os.environ.get("HOME_AGENT_PERSONAL_MEMORY_PROFILE_FILE", ""))
    from .config import Settings
    from .main import create_app
    import uvicorn
    settings = Settings()
    if settings.operator_database_url is not None:
        raise ValueError("private preference listener does not use operator credentials")
    app = build_listener(create_app(settings), profile)
    uvicorn.run(app, host=profile.address, port=profile.port,
        ssl_certfile=profile.certificate, ssl_keyfile=profile.private_key,
        proxy_headers=False, access_log=False, workers=1, limit_concurrency=2,
        timeout_keep_alive=5, timeout_graceful_shutdown=10)


def entrypoint():
    try:
        main()
    except Exception:
        # Settings and TLS exceptions can contain provisioned paths or values.
        # Never emit their traceback on this credential-bearing entry point.
        print("Private preference listener startup or runtime failed", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(entrypoint())
