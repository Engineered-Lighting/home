"""Explicit private TLS entrypoint for one home's linking proof/logout routes."""
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import ipaddress
import json
import os
import sys

from .personal_memory_server import NETWORKS, _file, _pairs
from .shared_auth_proof_api import ProofIngressBinding
from .shared_link_session_api import SessionRevocationBinding
from .shared_identity_site_runtime import compose_site_identity_ingress


@dataclass(frozen=True)
class SiteListenerProfile:
    address: str
    port: int
    certificate: str
    private_key: str = field(repr=False)
    proof_binding: ProofIngressBinding = field(repr=False)
    session_binding: SessionRevocationBinding = field(repr=False)
    proof_database_url: str = field(repr=False)
    session_database_url: str = field(repr=False)


def load_profile(path):
    value = json.loads(_file(path, 4096).read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    fields = {"version", "issuer", "address", "port", "certificate_file", "private_key_file",
              "proof_credential_file", "session_credential_file", "proof_database_url_file",
              "session_database_url_file"}
    if (type(value) is not dict or set(value) != fields or type(value["version"]) is not int or
            value["version"] != 1 or type(value["port"]) is not int or
            not 1024 <= value["port"] <= 65535 or type(value["address"]) is not str):
        raise ValueError("invalid site identity listener profile")
    address = ipaddress.ip_address(value["address"])
    if str(address) != value["address"] or not any(address in network for network in NETWORKS):
        raise ValueError("explicit private listener address required")
    def credential(name):
        return _file(value[name], 65).read_bytes().removesuffix(b"\n")
    proof = ProofIngressBinding(value["issuer"], credential("proof_credential_file"))
    session = SessionRevocationBinding(value["issuer"], credential("session_credential_file"))
    if proof.credential == session.credential:
        raise ValueError("separate proof and logout credentials required")
    def database_url(name):
        raw = _file(value[name], 4096).read_text(encoding="utf-8").removesuffix("\n")
        if not raw or any(ord(char) < 32 or ord(char) == 127 for char in raw):
            raise ValueError("invalid database credential file")
        return raw
    return SiteListenerProfile(str(address), value["port"],
        str(_file(value["certificate_file"], 131072)), str(_file(value["private_key_file"], 131072)),
        proof, session, database_url("proof_database_url_file"), database_url("session_database_url_file"))


def build_listener(core_application, profile):
    if type(profile) is not SiteListenerProfile:
        raise TypeError("validated site listener profile required")
    app = compose_site_identity_ingress(core_application, proof_binding=profile.proof_binding,
        session_binding=profile.session_binding, proof_database_url=profile.proof_database_url,
        session_database_url=profile.session_database_url)
    identity_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(_):
        async with core_application.router.lifespan_context(core_application):
            async with identity_lifespan(app):
                yield

    app.router.lifespan_context = lifespan
    return app


def main():
    profile = load_profile(os.environ.get("HOME_AGENT_SITE_IDENTITY_PROFILE_FILE", ""))
    from .config import Settings
    from .main import create_app
    import uvicorn
    settings = Settings(database_pool_size=1, database_max_overflow=0)
    if settings.operator_database_url is not None:
        raise ValueError("private identity listener cannot use operator credentials")
    app = build_listener(create_app(settings), profile)
    uvicorn.run(app, host=profile.address, port=profile.port,
        ssl_certfile=profile.certificate, ssl_keyfile=profile.private_key,
        proxy_headers=False, access_log=False, workers=1, limit_concurrency=4,
        timeout_keep_alive=5, timeout_graceful_shutdown=25)


def entrypoint():
    try:
        main()
    except Exception:
        print("Private site identity listener startup or runtime failed", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(entrypoint())
