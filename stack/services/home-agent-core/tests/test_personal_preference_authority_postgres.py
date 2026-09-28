"""Real API-role authority resolution inside the resource-guarded hosted clone.

Administrator access seeds synthetic grants only. All authority calls execute
as home_agent_api, without granting it shared-table reads or writes.
"""
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from .e1_postgres_harness import assert_guarded_database_url

SIGNATURE = "identity.resolve_personal_preference_authority_v1(text,text,text,boolean)"
CALL = text("SELECT * FROM identity.resolve_personal_preference_authority_v1(:issuer,:subject,:session,:write)")


def test_api_role_resolves_only_current_linked_and_granted_preference_owner():
    url = os.getenv("TEST_PERSONAL_PREFERENCE_AUTHORITY_ADMIN_DATABASE_URL")
    if not url:
        pytest.skip("dedicated guarded preference authority clone required")
    assert_guarded_database_url(url)
    engine = create_engine(url, isolation_level="SERIALIZABLE", pool_size=1, max_overflow=0,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=5000"})
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                assert connection.execute(text("SELECT current_database()")).scalar_one() == "personal_preference_authority_0047"
                assert connection.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == "0047_personal_pref_authority_v1"
                link = connection.execute(text("SELECT * FROM identity.shared_owner_links WHERE revoked_at IS NULL")).mappings().one()
                subjects = dict(connection.execute(text("SELECT issuer_id,subject FROM identity.shared_subject_bindings WHERE link_id=:link AND revoked_at IS NULL"), {"link": link["link_id"]}).all())
                stamp = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
                for site in ("echo", "victoria"):
                    for capability in ("memory.read", "personal_memory.write"):
                        params = dict(site=site, source="core.personal-preferences.v1", capability=capability,
                            issuer=f"home-assistant:{site}", id=uuid4(), link=link["link_id"],
                            generation=link["authorization_generation"], approval=uuid4().hex * 2,
                            expires=stamp + timedelta(minutes=5))
                        connection.execute(text("INSERT INTO identity.shared_sources VALUES(:site,:source,:capability,:issuer,1,'active')"), params)
                        connection.execute(text("""INSERT INTO identity.shared_source_grants
                            (grant_id,link_id,site_id,source_id,capability,source_revision,authorization_generation,
                             revision,approval_commitment,created_at,expires_at)
                            VALUES(:id,:link,:site,:source,:capability,1,:generation,1,:approval,clock_timestamp(),:expires)"""), params)

                def invoke(params, statement=CALL):
                    # A failed query must not poison the enclosing fixture.
                    with connection.begin_nested():
                        connection.execute(text("SET LOCAL SESSION AUTHORIZATION home_agent_api"))
                        result = connection.execute(statement, params).mappings().one()
                        connection.execute(text("RESET SESSION AUTHORIZATION"))
                        return result

                params = dict(issuer="home-assistant:echo", subject=subjects["home-assistant:echo"], session=uuid4().hex * 2, write=True)
                # The migration itself must not activate this capability.
                with pytest.raises(DBAPIError) as denied:
                    invoke(params)
                assert denied.value.orig.sqlstate == "42501"
                connection.execute(text(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO home_agent_api"))
                for site in ("echo", "victoria"):
                    row = invoke({**params, "issuer": f"home-assistant:{site}", "subject": subjects[f"home-assistant:{site}"]})
                    assert row["principal_id"] == link["principal_id"]
                    assert row["person_id"] == link["person_id"]
                    assert row["authorization_generation"] == link["authorization_generation"]
                    assert row["echo_grant_revision"] == row["victoria_grant_revision"] == 1
                    assert stamp < row["valid_until"] <= stamp + timedelta(seconds=65)

                for table in ("identity.shared_owner_links", "identity.shared_source_grants", "privacy.shared_link_session_revocations"):
                    with pytest.raises(DBAPIError) as denied:
                        invoke({}, text(f"SELECT * FROM {table} LIMIT 1"))
                    assert denied.value.orig.sqlstate == "42501"
                with pytest.raises(DBAPIError) as denied:
                    invoke({**params, "subject": "unlinked-" + uuid4().hex})
                assert denied.value.orig.sqlstate == "42501"

                # A read grant cannot stand in for a missing write grant.
                connection.execute(text("UPDATE identity.shared_source_grants SET revoked_at=clock_timestamp() WHERE site_id='victoria' AND capability='personal_memory.write'"))
                assert invoke({**params, "write": False})["principal_id"] == link["principal_id"]
                with pytest.raises(DBAPIError) as denied:
                    invoke(params)
                assert denied.value.orig.sqlstate == "42501"
                connection.execute(text("UPDATE identity.shared_source_grants SET revoked_at=clock_timestamp() WHERE site_id='victoria' AND capability='memory.read'"))
                with pytest.raises(DBAPIError) as denied:
                    invoke({**params, "write": False})
                assert denied.value.orig.sqlstate == "42501"
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
