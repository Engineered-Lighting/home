import json

import httpx
import pytest
from sqlalchemy.engine import make_url
from types import SimpleNamespace as NS

from app import shared_link_coordinator_server as server
from .test_personal_memory_runtime import fixture as core_fixture


def profile_file(tmp_path, **patch):
    value = dict(version=1,address="127.0.0.1",port=9452,commitment_key_id="owner-v1",confirmation_key_id="review-v1",
        issuance_journal_path=str(tmp_path/"issue.sqlite"),confirmation_journal_path=str(tmp_path/"confirm.sqlite"))
    for index,name in enumerate(("commitment_key","confirmation_key","issuance_journal_key","confirmation_journal_key","issuance_credential","review_credential")):
        path=tmp_path/name
        path.write_text("abcdef"[index]*64)
        value[name+"_file"]=str(path)
    for name,role in (("coordinator_database_url","home_agent_shared_link_coordinator"),
                      ("echo_proof_database_url","home_agent_shared_echo_proof_ingress"),
                      ("victoria_proof_database_url","home_agent_shared_victoria_proof_ingress")):
        path=tmp_path/name
        path.write_text(f"postgresql://{role}@localhost/unused")
        value[name+"_file"]=str(path)
    for name in ("certificate","private_key"):
        path=tmp_path/name;path.write_text("fixture")
        value[name+"_file"]=str(path)
    value.update(patch)
    path=tmp_path/"profile.json";path.write_text(json.dumps(value))
    return str(path)


@pytest.mark.parametrize("patch", [{"address":"0.0.0.0"},{"address":"8.8.8.8"},{"port":True},
    {"version":True},{"commitment_key_id":""},{"password":"inline"},{"issuance_journal_path":"relative"}])
def test_profile_rejects_unprovisioned_boundaries(tmp_path,patch):
    with pytest.raises(ValueError): server.load_profile(profile_file(tmp_path,**patch))


def test_profile_rejects_reused_keys_and_journals(tmp_path):
    with pytest.raises(ValueError):
        server.load_profile(profile_file(tmp_path,review_credential_file=str(tmp_path/"commitment_key")))
    with pytest.raises(ValueError):
        server.load_profile(profile_file(tmp_path,confirmation_journal_path=str(tmp_path/"issue.sqlite")))


@pytest.mark.asyncio
async def test_coordinator_lifespan_preserves_private_admission_middleware(tmp_path,monkeypatch):
    core=core_fixture(monkeypatch)
    core.state.database.engine.url=make_url("postgresql://home_agent_api@localhost/unused")
    profile=server.load_profile(profile_file(tmp_path))
    app=server.build_listener(core,profile)
    assert not (tmp_path/"issue.sqlite").exists()
    async with app.router.lifespan_context(app):
        assert (tmp_path/"issue.sqlite").exists()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://private.test") as client:
            path="/internal/shared-identity/v1/link-issuance"
            assert (await client.post(path)).status_code==401
            core.state.rollout_gate.status.return_value=NS(authorized=False)
            response=await client.post(path,headers={"authorization":"Bearer "+"e"*64})
            assert response.status_code==503
            assert response.json()["error"]=="coordinator_admission_unavailable"
    assert not app.routes


def test_coordinator_refuses_a_different_database(tmp_path,monkeypatch):
    core=core_fixture(monkeypatch)
    core.state.database.engine.url=make_url("postgresql://home_agent_api@elsewhere/unused")
    with pytest.raises(ValueError): server.build_listener(core,server.load_profile(profile_file(tmp_path)))
