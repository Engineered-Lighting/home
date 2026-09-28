"""Explicitly provisioned TLS listener for one home's preference BFF."""
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
import ipaddress
import json
import os
from pathlib import Path
import re
import sys

from fastapi import FastAPI
from sqlalchemy.engine import make_url
from .crypto import FieldCipher
from .personal_memory_api import PersonalMemoryBinding, create_personal_memory_ingress
from .personal_memory_runtime import compose_personal_memory_ingress, build_personal_memory_service
from .personal_memory_consent import SharingReviewCommitment
from .personal_memory_consent_service import ConsentDatabase, PreferenceConsentService
from .personal_memory_consent_journal import ConsentJournal
from .personal_memory_grants import PreferenceGrantStorage

NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12",
    "192.168.0.0/16", "100.64.0.0/10", "fc00::/7"))


@dataclass(frozen=True)
class ConsentProfile:
    database_url: str = field(repr=False)
    review_key: bytes = field(repr=False)
    journal_key: bytes = field(repr=False)
    journal_path: Path = field(repr=False)
    grant_lifetime_seconds: int


@dataclass(frozen=True)
class ListenerProfile:
    address: str
    port: int
    certificate: str
    private_key: str = field(repr=False)
    binding: PersonalMemoryBinding = field(repr=False)
    review_key: bytes = field(repr=False)
    consent: ConsentProfile | None = field(default=None, repr=False)


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
    if (type(value) is not dict or set(value) not in (keys, keys|{"consent"}) or type(value["version"]) is not int
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
    consent = None
    if "consent" in value:
        supplied = value["consent"]
        fields = {"database_url_file", "review_key_file", "journal_key_file", "journal_path", "grant_lifetime_seconds"}
        if (binding.issuer_id != "home-assistant:echo" or type(supplied) is not dict or set(supplied) != fields
                or type(supplied["grant_lifetime_seconds"]) is not int
                or not 60 <= supplied["grant_lifetime_seconds"] <= 365*86400
                or type(supplied["journal_path"]) is not str):
            raise ValueError("explicit Echo sharing profile required")
        keys = [_file(supplied[name], 65).read_bytes().removesuffix(b"\n") for name in ("review_key_file", "journal_key_file")]
        if (any(re.fullmatch(b"[a-f0-9]{64}", part) is None for part in keys)
                or len(set([credential,key,*keys])) != 4):
            raise ValueError("separate consent keys required")
        journal_path = Path(supplied["journal_path"])
        if not journal_path.is_absolute() or journal_path.is_symlink() or not journal_path.parent.is_dir():
            raise ValueError("private durable journal path required")
        raw = _file(supplied["database_url_file"], 4096).read_text(encoding="utf-8").removesuffix("\n")
        if not raw or any(ord(char)<32 or ord(char)==127 for char in raw):
            raise ValueError("invalid consent database file")
        consent = ConsentProfile(raw, bytes.fromhex(keys[0].decode()), bytes.fromhex(keys[1].decode()),
                                 journal_path, supplied["grant_lifetime_seconds"])
    return ListenerProfile(str(address), value["port"], str(certificate), str(private_key),
        binding, bytes.fromhex(key.decode("ascii")), consent)


def build_listener(core_application, profile):
    if type(profile) is not ListenerProfile:
        raise TypeError("validated private listener profile required")
    if profile.consent is not None:
        return build_consent_listener(core_application, profile)
    app = compose_personal_memory_ingress(core_application,
        binding=profile.binding, review_key=profile.review_key)

    @asynccontextmanager
    async def lifespan(_app):
        async with core_application.router.lifespan_context(core_application):
            yield

    app.router.lifespan_context = lifespan
    return app


def build_consent_listener(core_application, profile):
    config = profile.consent
    target = core_application.state.database.engine.url
    parsed = make_url(config.database_url)
    if (parsed.host, parsed.port or 5432, parsed.database) != (target.host, target.port or 5432, target.database):
        raise ValueError("consent must use the admitted Core database")

    @asynccontextmanager
    async def lifespan(app):
        async with core_application.router.lifespan_context(core_application):
            memory = build_personal_memory_service(core_application, review_key=profile.review_key)
            await memory.admission()
            async with AsyncExitStack() as cleanup:
                database = ConsentDatabase(config.database_url)
                cleanup.push_async_callback(database.close)
                journal = ConsentJournal(config.journal_path, cipher=FieldCipher(config.journal_key))
                cleanup.callback(journal.close)
                consent = PreferenceConsentService(database=database,
                    storage=PreferenceGrantStorage(SharingReviewCommitment(config.review_key)),
                    journal=journal, admission=memory.admission,
                    grant_lifetime=timedelta(seconds=config.grant_lifetime_seconds))
                private = create_personal_memory_ingress(binding=profile.binding, service=memory, consent=consent)
                app.router.routes.extend(private.routes)
                try:
                    yield
                finally:
                    app.router.routes.clear()

    return FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def main():
    # No listener exists unless an operator provides every required file.
    profile = load_profile(os.environ.get("HOME_AGENT_PERSONAL_MEMORY_PROFILE_FILE", ""))
    from .config import Settings
    from .main import create_app
    import uvicorn
    settings = Settings(database_pool_size=2, database_max_overflow=0)
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
