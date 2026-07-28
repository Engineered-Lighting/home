from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine


OWNER_DATABASE_ENV = "TEST_PHASE3_PARENT_RELATIONSHIP_E5H_OWNER_DATABASE_URL"
COMMITTER_DATABASE_ENV = "TEST_PHASE3_PARENT_RELATIONSHIP_E5H_COMMITTER_DATABASE_URL"
STATUS_FUNCTION = (
    "identity.recover_authenticated_parent_relationship_e5h(" "character varying)"
)


def _configured() -> bool:
    return bool(os.getenv(OWNER_DATABASE_ENV) and os.getenv(COMMITTER_DATABASE_ENV))


def _engine(environment_name: str, *, serializable: bool = False):
    url = make_url(os.environ[environment_name]).set(drivername="postgresql+psycopg")
    options: dict[str, object] = {
        "pool_pre_ping": True,
        "hide_parameters": True,
    }
    if serializable:
        options["isolation_level"] = "SERIALIZABLE"
    return create_async_engine(url, **options)


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5h URLs are not configured")
async def test_e5h_recovers_committed_parent_authority_table_blind() -> None:
    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        async with owner.connect() as connection:
            ha_user_ids = (
                (
                    await connection.execute(
                        text(
                            "SELECT binding.ha_user_id "
                            "FROM identity.ha_user_bindings AS binding "
                            "JOIN identity.parent_relationship_proposals AS proposal "
                            "ON proposal.principal_id=binding.principal_id "
                            "AND proposal.child_person_id=binding.person_id "
                            "WHERE binding.revoked_at IS NULL "
                            "AND proposal.state='consumed'"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(ha_user_ids) == 1

        async with committer.begin() as connection:
            status = (
                (
                    await connection.execute(
                        text(
                            "SELECT * FROM identity."
                            "recover_authenticated_parent_relationship_e5h("
                            "CAST(:ha_user_id AS varchar))"
                        ),
                        {"ha_user_id": ha_user_ids[0]},
                    )
                )
                .mappings()
                .one()
            )
        assert status["state"] == "confirmed"
        assert status["fact_count"] == 2
        assert status["confirmed_at"] is not None
        for private_field in (
            "proposal_id",
            "proposal_digest",
            "expires_at",
            "child_display_label",
            "parent_0_display_label",
            "parent_0_review_code",
            "parent_1_display_label",
            "parent_1_review_code",
        ):
            assert status[private_field] is None

        with pytest.raises(DBAPIError) as direct_read:
            async with committer.begin() as connection:
                await connection.execute(
                    text(
                        "SELECT count(*) FROM " "identity.parent_relationship_proposals"
                    )
                )
        assert getattr(direct_read.value.orig, "sqlstate", None) == "42501"

        async with owner.connect() as connection:
            privileges = (
                await connection.execute(
                    text(
                        "SELECT "
                        "pg_catalog.has_function_privilege("
                        "'home_agent_binding_committer',"
                        "pg_catalog.to_regprocedure(:status),'EXECUTE'),"
                        "pg_catalog.has_function_privilege("
                        "'home_agent_api',"
                        "pg_catalog.to_regprocedure(:status),'EXECUTE'),"
                        "pg_catalog.has_function_privilege("
                        "'home_agent_binding_operator',"
                        "pg_catalog.to_regprocedure(:status),'EXECUTE')"
                    ),
                    {"status": STATUS_FUNCTION},
                )
            ).one()
        assert privileges == (True, False, False)
    finally:
        await committer.dispose()
        await owner.dispose()
