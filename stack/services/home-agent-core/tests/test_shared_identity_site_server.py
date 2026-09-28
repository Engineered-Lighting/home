from contextlib import asynccontextmanager
import json

from fastapi import FastAPI
import pytest

from app import shared_identity_site_server as server


def profile_file(tmp_path):
    config = {"version": 1, "issuer": "home-assistant:victoria", "address": "127.0.0.1", "port": 9451}
    contents = {"proof_credential_file": "a"*64, "session_credential_file": "b"*64,
        "proof_database_url_file": "postgresql://home_agent_shared_victoria_proof_ingress:private@localhost/home_agent",
        "session_database_url_file": "postgresql://home_agent_shared_victoria_session_ingress:private@localhost/home_agent",
        "certificate_file": "fixture certificate", "private_key_file": "fixture key"}
    for name, text in contents.items():
        path = tmp_path/name
        path.write_text(text, encoding="utf-8")
        config[name] = str(path)
    path = tmp_path/"profile.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path, config


def test_profile_requires_explicit_files_and_hides_secrets(tmp_path):
    path, _ = profile_file(tmp_path)
    profile = server.load_profile(str(path))
    assert profile.proof_binding.issuer_id == profile.session_binding.issuer_id == "home-assistant:victoria"
    assert "private@" not in repr(profile) and "a"*64 not in repr(profile)
    assert profile.address == "127.0.0.1"


@pytest.mark.parametrize("change", ["wildcard", "public", "port", "duplicate", "inline", "shared_key", "oversize"])
def test_profile_rejects_unapproved_bindings_and_secret_shapes(tmp_path, change):
    path, config = profile_file(tmp_path)
    if change == "wildcard": config["address"] = "0.0.0.0"
    elif change == "public": config["address"] = "8.8.8.8"
    elif change == "port": config["port"] = True
    elif change == "inline": config["credential"] = "a"*64
    elif change == "shared_key": config["session_credential_file"] = config["proof_credential_file"]
    elif change == "oversize": path.write_text("x"*4097, encoding="utf-8")
    if change != "oversize":
        raw = json.dumps(config)
        if change == "duplicate": raw = raw[:-1] + ', "issuer": "home-assistant:echo"}'
        path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError): server.load_profile(str(path))


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_listener_starts_core_before_identity_and_closes_in_reverse(tmp_path, monkeypatch, fail):
    path, _ = profile_file(tmp_path)
    profile = server.load_profile(str(path))
    events = []
    @asynccontextmanager
    async def core_lifespan(_):
        events.append("core start")
        try: yield
        finally: events.append("core stop")
    @asynccontextmanager
    async def identity_lifespan(_):
        events.append("identity start")
        try:
            if fail: raise ValueError("restore unavailable")
            yield
        finally: events.append("identity stop")
    core = FastAPI(lifespan=core_lifespan)
    identity = FastAPI(lifespan=identity_lifespan)
    def compose(actual, **kwargs):
        assert actual is core and kwargs["proof_binding"] == profile.proof_binding
        return identity
    monkeypatch.setattr(server, "compose_site_identity_ingress", compose)
    app = server.build_listener(core, profile)
    if fail:
        with pytest.raises(ValueError):
            async with app.router.lifespan_context(app): pytest.fail("startup failure ignored")
    else:
        async with app.router.lifespan_context(app):
            assert events == ["core start", "identity start"]
    assert events == ["core start", "identity start", "identity stop", "core stop"]


def test_entrypoint_redacts_configuration_errors(monkeypatch, capsys):
    def fail(): raise ValueError("postgresql://private-secret")
    monkeypatch.setattr(server, "main", fail)
    assert server.entrypoint() == 78
    assert "private-secret" not in capsys.readouterr().err
