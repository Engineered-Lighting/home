from unittest.mock import Mock

import pytest

from app.personal_memory_registration import register_sources, REVISION


def fixture(*, existing=False, drift=None, owner="home_agent_owner", protected=True):
    connection=Mock()
    def execute(statement, params=None):
        sql=str(statement)
        result=Mock()
        if sql.startswith("SELECT current_user"):
            result.one.return_value=(owner,owner,"serializable",[REVISION])
        elif "pg_catalog.pg_class" in sql:
            result.scalar_one_or_none.return_value=protected
        elif sql.startswith("SELECT issuer_id,site_id"):
            result.all.return_value=[(params['issuer'],params['site'],1,'revoked' if drift=='issuer' else 'active')] if existing else []
        elif sql.startswith("SELECT issuer_id,registration_revision"):
            result.one_or_none.return_value=(params['issuer'],2 if drift=='revision' else 1,'revoked' if drift=='source' else 'active') if existing else None
        return result
    connection.execute.side_effect=execute
    return connection


def writes(connection):
    return [(str(call.args[0]),call.args[1]) for call in connection.execute.call_args_list if str(call.args[0]).startswith('INSERT')]


def test_registration_creates_only_fixed_issuers_and_sources():
    connection=fixture()
    register_sources(connection)
    inserts=writes(connection)
    assert len(inserts)==6
    assert all('shared_source_grants' not in sql for sql,_ in inserts)
    assert {(p['site'],p['capability']) for _,p in inserts if 'capability' in p} == {
        ('echo','memory.read'),('echo','personal_memory.write'),('victoria','memory.read'),('victoria','personal_memory.write')}


def test_exact_repeat_is_read_only():
    connection=fixture(existing=True)
    register_sources(connection)
    assert writes(connection)==[]


@pytest.mark.parametrize('drift',['issuer','revision','source'])
def test_registration_never_reactivates_or_replaces_existing_authority(drift):
    connection=fixture(existing=True,drift=drift)
    with pytest.raises(ValueError): register_sources(connection)
    assert writes(connection)==[]


@pytest.mark.parametrize('options',[{'owner':'home_agent_api'},{'protected':False}])
def test_registration_requires_owner_and_protected_schema(options):
    connection=fixture(**options)
    with pytest.raises(ValueError): register_sources(connection)
    assert writes(connection)==[]
