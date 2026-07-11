from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.db import Database


pytestmark = pytest.mark.skipif(
    not all(
        os.getenv(name)
        for name in ("TEST_DATABASE_URL", "TEST_BACKUP_DATABASE_URL")
    ),
    reason="owner and backup-role database URLs are required",
)


@pytest.mark.asyncio
async def test_backup_role_has_only_control_and_settings_authority() -> None:
    owner = Database(os.environ["TEST_DATABASE_URL"])
    backup = Database(os.environ["TEST_BACKUP_DATABASE_URL"])
    try:
        async with owner.transaction() as connection:
            role = (
                await connection.execute(
                    text(
                        """
                        SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication,
                               rolbypassrls,
                               pg_has_role('home_agent_backup', 'pg_read_all_data', 'member')
                                 AS reads_all_data,
                               pg_has_role('home_agent_backup', 'pg_read_all_settings', 'member')
                                 AS reads_settings,
                               pg_has_role('home_agent_backup', 'pg_read_all_stats', 'member')
                                 AS reads_stats,
                               EXISTS (
                                 SELECT 1
                                 FROM unnest(ARRAY[
                                   'pg_monitor', 'pg_stat_scan_tables',
                                   'pg_write_all_data', 'pg_read_server_files',
                                   'pg_write_server_files', 'pg_execute_server_program',
                                   'pg_checkpoint', 'pg_maintain', 'pg_signal_backend'
                                 ]) AS forbidden(role_name)
                                 WHERE pg_has_role(
                                   'home_agent_backup', forbidden.role_name::name, 'member'
                                 )
                               ) AS has_forbidden_role,
                               has_table_privilege(
                                 'home_agent_backup', 'identity.people', 'SELECT'
                               ) AS reads_people
                        FROM pg_catalog.pg_roles
                        WHERE rolname = 'home_agent_backup'
                        """
                    )
                )
            ).mappings().one()
            assert dict(role) == {
                "rolsuper": False,
                "rolcreatedb": False,
                "rolcreaterole": False,
                "rolreplication": False,
                "rolbypassrls": False,
                "reads_all_data": False,
                "reads_settings": True,
                "reads_stats": False,
                "has_forbidden_role": False,
                "reads_people": False,
            }
            for signature in (
                "pg_catalog.pg_backup_start(text,boolean)",
                "pg_catalog.pg_backup_stop(boolean)",
                "pg_catalog.pg_switch_wal()",
                "pg_catalog.pg_create_restore_point(text)",
            ):
                permitted = (
                    await connection.execute(
                        text(
                            "SELECT has_function_privilege("
                            "'home_agent_backup', :signature, 'EXECUTE')"
                        ),
                        {"signature": signature},
                    )
                ).scalar_one()
                assert permitted is True

        async with backup.transaction() as connection:
            assert (
                await connection.execute(text("SELECT current_user"))
            ).scalar_one() == "home_agent_backup"

        with pytest.raises(ProgrammingError):
            async with backup.transaction() as connection:
                await connection.execute(
                    text("SELECT person_id FROM identity.people LIMIT 1")
                )
    finally:
        await backup.close()
        await owner.close()
