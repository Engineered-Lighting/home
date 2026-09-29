"""Explicitly provisioned TLS listener for explicit cross-home lighting."""
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
import ipaddress
import json
import os
from pathlib import Path
import re
import sys

import httpx
from fastapi import FastAPI
from sqlalchemy.engine import make_url

from .crypto import FieldCipher
from .lighting_api import create_lighting_ingress
from .lighting_authority import LightingConsentCommitment, LightingGrantStorage
from .lighting_edge_client import EdgeLightingClient
from .lighting_journal import LightingJournal
from .lighting_review import LightingActionCommitment
from .lighting_service import LightingDatabase, LightingService
from .personal_memory_api import PersonalMemoryBinding
from .personal_memory_runtime import private_core_admission
from .personal_memory_server import NETWORKS, _file, _pairs
from .startup_admission import admit_at_startup

FIELDS = {"version", "address", "port", "credential_file", "certificate_file", "private_key_file",
          "action_key_file", "consent_key_file", "journal_key_file", "journal_path", "database_url_file",
          "grant_lifetime_seconds", "homes"}


@dataclass(frozen=True)
class Home:
    origin: str
    secret: bytes = field(repr=False)


@dataclass(frozen=True)
class LightingProfile:
    address: str
    port: int
    certificate: str
    private_key: str = field(repr=False)
    binding: PersonalMemoryBinding = field(repr=False)
    action_key: bytes = field(repr=False)
    consent_key: bytes = field(repr=False)
    journal_key: bytes = field(repr=False)
    journal_path: Path = field(repr=False)
    database_url: str = field(repr=False)
    grant_lifetime_seconds: int = 0
    homes: dict = field(default_factory=dict, repr=False)


def _hex_secret(path):
    raw = _file(path, 65).read_bytes().removesuffix(b"\n")
    if re.fullmatch(b"[a-f0-9]{64}", raw) is None:
        raise ValueError("dedicated lighting secret required")
    return raw


def load_profile(path):
    value = json.loads(_file(path, 8192).read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    if (type(value) is not dict or set(value) != FIELDS or type(value["version"]) is not int or value["version"] != 1
            or type(value["port"]) is not int or not 1024 <= value["port"] <= 65535 or type(value["address"]) is not str
            or type(value["grant_lifetime_seconds"]) is not int or not 60 <= value["grant_lifetime_seconds"] <= 365 * 86400
            or type(value["journal_path"]) is not str or type(value["homes"]) is not dict
            or not value["homes"] or set(value["homes"]) - {"echo", "victoria"}):
        raise ValueError("invalid lighting listener profile")
    address = ipaddress.ip_address(value["address"])
    if str(address) != value["address"] or not any(address in network for network in NETWORKS):
        raise ValueError("explicit private listener address required")
    credential = _hex_secret(value["credential_file"])
    keys = [_hex_secret(value[name]) for name in ("action_key_file", "consent_key_file", "journal_key_file")]
    homes = {}
    for site, home in value["homes"].items():
        if type(home) is not dict or set(home) != {"origin", "secret_file"} or type(home["origin"]) is not str:
            raise ValueError("each home needs an origin and a secret file")
        homes[site] = Home(home["origin"], _hex_secret(home["secret_file"]))
    material = [credential, *keys, *(home.secret for home in homes.values())]
    if len(set(material)) != len(material):
        raise ValueError("every lighting credential, key and home secret must be distinct")
    journal_path = Path(value["journal_path"])
    if not journal_path.is_absolute() or journal_path.is_symlink() or not journal_path.parent.is_dir():
        raise ValueError("private durable journal path required")
    raw = _file(value["database_url_file"], 4096).read_text(encoding="utf-8").removesuffix("\n")
    if not raw or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError("invalid lighting database file")
    return LightingProfile(str(address), value["port"], str(_file(value["certificate_file"], 131072)),
        str(_file(value["private_key_file"], 131072)), PersonalMemoryBinding("home-assistant:echo", credential),
        *(bytes.fromhex(key.decode()) for key in keys), journal_path, raw, value["grant_lifetime_seconds"],
        {site: Home(home.origin, bytes.fromhex(home.secret.decode())) for site, home in homes.items()})


def build_listener(core_application, profile):
    if type(profile) is not LightingProfile:
        raise TypeError("validated lighting profile required")
    target = core_application.state.database.engine.url
    parsed = make_url(profile.database_url)
    if (parsed.host, parsed.port or 5432, parsed.database) != (target.host, target.port or 5432, target.database):
        raise ValueError("lighting must use the admitted Core database")
    admission = private_core_admission(core_application)

    @asynccontextmanager
    async def lifespan(app):
        async with core_application.router.lifespan_context(core_application):
            await admit_at_startup(admission)
            async with AsyncExitStack() as cleanup:
                http = httpx.AsyncClient(follow_redirects=False, trust_env=False)
                cleanup.push_async_callback(http.aclose)
                database = LightingDatabase(profile.database_url)
                cleanup.push_async_callback(database.close)
                journal = LightingJournal(profile.journal_path, cipher=FieldCipher(profile.journal_key))
                cleanup.callback(journal.close)
                clients = {site: EdgeLightingClient(site_id=site, base_url=home.origin, secret=home.secret, client=http)
                           for site, home in profile.homes.items()}
                service = LightingService(database=database,
                    storage=LightingGrantStorage(LightingConsentCommitment(profile.consent_key)),
                    commitment=LightingActionCommitment(profile.action_key), journal=journal, clients=clients,
                    admission=admission, grant_lifetime=timedelta(seconds=profile.grant_lifetime_seconds))
                private = create_lighting_ingress(binding=profile.binding, service=service)
                app.router.routes.extend(private.routes)
                try:
                    yield
                finally:
                    app.router.routes.clear()

    return FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def main():
    profile = load_profile(os.environ.get("HOME_AGENT_LIGHTING_PROFILE_FILE", ""))
    from .config import Settings
    from .main import create_app
    import uvicorn
    settings = Settings(database_pool_size=1, database_max_overflow=0)
    if settings.operator_database_url is not None:
        raise ValueError("lighting listener does not use operator credentials")
    app = build_listener(create_app(settings), profile)
    uvicorn.run(app, host=profile.address, port=profile.port, ssl_certfile=profile.certificate,
        ssl_keyfile=profile.private_key, proxy_headers=False, access_log=False, workers=1, limit_concurrency=4,
        timeout_keep_alive=5, timeout_graceful_shutdown=25)


def entrypoint():
    try:
        main()
    except Exception:
        # Profile, TLS and settings errors can contain provisioned paths or values.
        print("Private lighting listener startup or runtime failed", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(entrypoint())
