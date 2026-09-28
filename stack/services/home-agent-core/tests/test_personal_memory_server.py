from contextlib import asynccontextmanager
import json

import pytest
from fastapi import FastAPI

from app import personal_memory_server as server
from sqlalchemy.engine import make_url
import httpx
from .test_personal_memory_runtime import fixture as core_fixture
from app.errors import OptionalWorkSuspendedError


@pytest.mark.parametrize("module_name", ["personal_memory_server", "shared_identity_site_server", "shared_link_coordinator_server"])
def test_private_entrypoints_bound_real_core_pool(tmp_path, monkeypatch, module_name):
    import base64
    import importlib
    import uvicorn
    from app import config
    module = importlib.import_module("app." + module_name)
    original_settings = config.Settings
    captured = []
    def settings(**kwargs):
        return original_settings(database_url="postgresql+psycopg://home_agent_api:fixture@localhost/home_agent",
            policy_digest="a"*64, service_token="b"*64,
            knowledge_encryption_key=base64.urlsafe_b64encode(b"k"*32).decode(),
            role="api", rollout_mode="shadow", **kwargs)
    monkeypatch.setattr(config, "Settings", settings)
    from types import SimpleNamespace
    provisioned = SimpleNamespace(address="127.0.0.1", port=9448, certificate="unused", private_key="unused")
    monkeypatch.setattr(module, "load_profile", lambda _: provisioned)
    def listener(core, profile):
        captured.append(core)
        return core
    monkeypatch.setattr(module, "build_listener", listener)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    module.main()
    pool = captured[0].state.database.engine.pool
    assert pool.size() == 2
    assert pool._max_overflow == 0
    assert captured[0].state.operator_database is None


def test_connection_limits_can_be_provisioned_by_environment(monkeypatch):
    import base64
    from app.config import Settings
    monkeypatch.setenv("HOME_AGENT_DATABASE_POOL_SIZE", "2")
    monkeypatch.setenv("HOME_AGENT_DATABASE_MAX_OVERFLOW", "0")
    settings = Settings(database_url="postgresql+psycopg://home_agent_api:fixture@localhost/home_agent",
        policy_digest="a"*64, service_token="b"*64,
        knowledge_encryption_key=base64.urlsafe_b64encode(b"k"*32).decode())
    assert settings.database_pool_size == 2
    assert settings.database_max_overflow == 0


def profile(tmp_path, **patch):
    for name, data in (("credential",b"a"*64),("review",b"b"*64),
                       ("cert",b"fixture certificate"),("key",b"fixture TLS key")):
        (tmp_path/name).write_bytes(data)
    value = dict(version=1,issuer="home-assistant:echo",address="127.0.0.1",port=9448,
        credential_file=str(tmp_path/"credential"),review_key_file=str(tmp_path/"review"),
        certificate_file=str(tmp_path/"cert"),private_key_file=str(tmp_path/"key"))
    value.update(patch)
    path=tmp_path/"profile.json"
    path.write_text(json.dumps(value),encoding="utf-8")
    return path


def test_profile_loads_distinct_secrets_without_exposing_them(tmp_path):
    loaded=server.load_profile(str(profile(tmp_path)))
    assert loaded.binding.issuer_id=="home-assistant:echo"
    assert loaded.review_key==bytes.fromhex("b"*64)
    assert "a"*64 not in repr(loaded) and "b"*64 not in repr(loaded)


@pytest.mark.parametrize("patch",[
    {"address":"0.0.0.0"},{"address":"::"},{"address":"8.8.8.8"},
    {"address":"example.com"},{"port":True},{"version":True},
    {"issuer":"home-assistant:other"},{"credential_file":"relative"},
    {"arbitrary_url":"https://example.com"},
])
def test_profile_rejects_unprovisioned_boundaries(tmp_path,patch):
    with pytest.raises((ValueError,TypeError)):
        server.load_profile(str(profile(tmp_path,**patch)))


def test_reused_key_duplicate_fields_and_oversize_profile_are_rejected(tmp_path):
    path=profile(tmp_path,review_key_file=str(tmp_path/"credential"))
    with pytest.raises(ValueError): server.load_profile(str(path))
    path.write_text('{"version":1,"version":1}',encoding="utf-8")
    with pytest.raises(ValueError): server.load_profile(str(path))
    path.write_bytes(b" "*4097)
    with pytest.raises(ValueError): server.load_profile(str(path))


def test_entrypoint_does_not_print_credential_bearing_errors(monkeypatch,capsys):
    def fail():
        raise ValueError("postgresql://user:private-password@host/db")
    monkeypatch.setattr(server,"main",fail)
    assert server.entrypoint()==78
    output=capsys.readouterr()
    assert output.out==""
    assert output.err=="Private preference listener startup or runtime failed\n"


@pytest.mark.asyncio
async def test_private_listener_runs_core_lifespan_without_exposing_core_routes(tmp_path,monkeypatch):
    events=[]
    @asynccontextmanager
    async def lifespan(app):
        events.append("started")
        try: yield
        finally: events.append("stopped")
    core=FastAPI(lifespan=lifespan)
    @core.get("/legacy-secret-route")
    def legacy(): return {}
    private=FastAPI()
    monkeypatch.setattr(server,"compose_personal_memory_ingress",lambda *args,**kwargs:private)
    app=server.build_listener(core,server.load_profile(str(profile(tmp_path))))
    assert not any(route.path=="/legacy-secret-route" for route in app.routes)
    async with app.router.lifespan_context(app):
        assert events==["started"]
    assert events==["started","stopped"]


def consent_profile(tmp_path, **patch):
    (tmp_path/"consent-review").write_text("c"*64)
    (tmp_path/"consent-journal-key").write_text("d"*64)
    (tmp_path/"consent-database").write_text("postgresql://home_agent_preference_consent@localhost/unused")
    config = dict(database_url_file=str(tmp_path/"consent-database"), review_key_file=str(tmp_path/"consent-review"),
        journal_key_file=str(tmp_path/"consent-journal-key"), journal_path=str(tmp_path/"consent.sqlite"),
        grant_lifetime_seconds=86400)
    config.update(patch)
    return server.load_profile(str(profile(tmp_path, consent=config)))


@pytest.mark.parametrize("change", ["key", "duration", "path", "extra"])
def test_consent_profile_requires_separate_keys_and_explicit_bounded_scope(tmp_path, change):
    patch = {}
    if change == "key": patch["journal_key_file"] = str(tmp_path/"consent-review")
    elif change == "duration": patch["grant_lifetime_seconds"] = True
    elif change == "path": patch["journal_path"] = "relative.sqlite"
    else: patch["capability"] = "lighting.execute"
    with pytest.raises(ValueError): consent_profile(tmp_path, **patch)


@pytest.mark.asyncio
async def test_consent_runtime_opens_only_after_core_admission_and_closes_routes(tmp_path, monkeypatch):
    core = core_fixture(monkeypatch)
    core.state.database.engine.url = make_url("postgresql://home_agent_api@localhost/unused")
    loaded = consent_profile(tmp_path)
    app = server.build_listener(core, loaded)
    assert not (tmp_path/"consent.sqlite").exists()
    async with app.router.lifespan_context(app):
        assert (tmp_path/"consent.sqlite").exists()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://private.test") as client:
            response = await client.post("/internal/personal-memory/v1/sharing-propose")
            assert response.status_code == 401
    assert not app.routes
    # Closed journal handles permit immediate recovery with the same key.
    journal = server.ConsentJournal(loaded.consent.journal_path, cipher=server.FieldCipher(loaded.consent.journal_key))
    journal.close()


@pytest.mark.asyncio
async def test_failed_core_admission_does_not_initialize_consent_storage(tmp_path, monkeypatch):
    core = core_fixture(monkeypatch)
    core.state.database.engine.url = make_url("postgresql://home_agent_api@localhost/unused")
    core.state.maintenance_observed_after = None
    app = server.build_listener(core, consent_profile(tmp_path))
    with pytest.raises(OptionalWorkSuspendedError):
        async with app.router.lifespan_context(app): pytest.fail("admission bypass")
    assert not (tmp_path/"consent.sqlite").exists()
