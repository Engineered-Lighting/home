"""Explicit private TLS profile for the account-linking coordinator."""
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import sys

from fastapi import FastAPI
from sqlalchemy.engine import make_url
from starlette.routing import Mount

from .personal_memory_server import NETWORKS, _file, _pairs
from .personal_memory_runtime import private_core_admission
from .shared_link_coordinator_runtime import open_link_coordinator
from .shared_link_issuance_api import LinkIssuanceBinding
from .shared_link_review_api import LinkReviewBinding


@dataclass(frozen=True)
class CoordinatorProfile:
    address: str
    port: int
    certificate: str
    private_key: str = field(repr=False)
    arguments: dict = field(repr=False)


def load_profile(path):
    value = json.loads(_file(path, 8192).read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    key_names = ("commitment_key", "confirmation_key", "issuance_journal_key", "confirmation_journal_key")
    url_names = ("coordinator_database_url", "echo_proof_database_url", "victoria_proof_database_url")
    path_names = ("issuance_journal_path", "confirmation_journal_path")
    fields = {"version", "address", "port", "certificate_file", "private_key_file",
              "issuance_credential_file", "review_credential_file", "commitment_key_id", "confirmation_key_id",
              *path_names, *(name+"_file" for name in (*key_names,*url_names))}
    if (type(value) is not dict or set(value) != fields or type(value["version"]) is not int or value["version"] != 1
            or type(value["port"]) is not int or not 1024 <= value["port"] <= 65535 or type(value["address"]) is not str):
        raise ValueError("invalid coordinator profile")
    address = ipaddress.ip_address(value["address"])
    if str(address) != value["address"] or not any(address in network for network in NETWORKS):
        raise ValueError("explicit private coordinator address required")
    def secret(name):
        raw = _file(value[name+"_file"],65).read_bytes().removesuffix(b"\n")
        if re.fullmatch(b"[a-f0-9]{64}", raw) is None: raise ValueError("dedicated coordinator secret required")
        return raw
    secrets = {name: secret(name) for name in (*key_names, "issuance_credential", "review_credential")}
    if len(set(secrets.values())) != len(secrets): raise ValueError("coordinator secrets must be distinct")
    arguments = {name: bytes.fromhex(secrets[name].decode("ascii")) for name in key_names}
    arguments.update(issuance_binding=LinkIssuanceBinding(secrets["issuance_credential"]),
                     review_binding=LinkReviewBinding(secrets["review_credential"]))
    for name in ("commitment_key_id", "confirmation_key_id"):
        if type(value[name]) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", value[name]):
            raise ValueError("stable coordinator key reference required")
        arguments[name] = value[name]
    for name in url_names:
        raw = _file(value[name+"_file"],4096).read_text(encoding="utf-8").removesuffix("\n")
        if not raw or any(ord(c)<32 or ord(c)==127 for c in raw): raise ValueError("invalid database file")
        arguments[name] = raw
    for name in path_names:
        raw = value[name]
        if type(raw) is not str or not Path(raw).is_absolute() or Path(raw).is_symlink() or not Path(raw).parent.is_dir():
            raise ValueError("private durable journal path required")
        arguments[name] = Path(raw)
    if arguments[path_names[0]].resolve() == arguments[path_names[1]].resolve():
        raise ValueError("separate coordinator journals required")
    return CoordinatorProfile(str(address),value["port"],str(_file(value["certificate_file"],131072)),
                              str(_file(value["private_key_file"],131072)),arguments)


def build_listener(core_application, profile):
    if type(profile) is not CoordinatorProfile: raise TypeError("validated coordinator profile required")
    admission = private_core_admission(core_application)
    target = core_application.state.database.engine.url
    for name in ("coordinator_database_url", "echo_proof_database_url", "victoria_proof_database_url"):
        parsed = make_url(profile.arguments[name])
        if (parsed.host,parsed.port or 5432,parsed.database) != (target.host,target.port or 5432,target.database):
            raise ValueError("coordinator must use admitted Core database")

    @asynccontextmanager
    async def lifespan(app):
        async with core_application.router.lifespan_context(core_application):
            async with open_link_coordinator(**profile.arguments, admission=admission) as private:
                # Mount the whole ASGI application to preserve its admission and
                # concurrency middleware, not just the individual routes.
                app.router.routes.append(Mount("/",app=private))
                try: yield
                finally: app.router.routes.clear()
    return FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)


def main():
    profile = load_profile(os.environ.get("HOME_AGENT_LINK_COORDINATOR_PROFILE_FILE", ""))
    from .config import Settings
    from .main import create_app
    import uvicorn
    settings = Settings()
    if settings.operator_database_url is not None: raise ValueError("coordinator cannot use operator credentials")
    app = build_listener(create_app(settings),profile)
    uvicorn.run(app,host=profile.address,port=profile.port,ssl_certfile=profile.certificate,ssl_keyfile=profile.private_key,
        proxy_headers=False,access_log=False,workers=1,limit_concurrency=4,timeout_keep_alive=5,timeout_graceful_shutdown=25)


def entrypoint():
    try: main()
    except Exception:
        print("Private linking coordinator startup or runtime failed",file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__": raise SystemExit(entrypoint())
