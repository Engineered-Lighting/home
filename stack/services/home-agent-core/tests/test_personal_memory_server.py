from contextlib import asynccontextmanager
import json

import pytest
from fastapi import FastAPI

from app import personal_memory_server as server


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
