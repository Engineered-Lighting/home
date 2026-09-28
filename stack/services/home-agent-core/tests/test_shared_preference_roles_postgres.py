"""Real login activation/deactivation of the shared-preference roles.

Runs last in the guarded 0047 clone stage. Unlike the rolled-back grant tests,
login can only be proven after commit, so this test commits, connects over TCP
as every role, and then restores the dormant state it found (roles are
cluster-wide; the consent role this test installs is removed from the clone).
"""
import os
import secrets

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app import shared_preference_roles as roles
from .e1_postgres_harness import assert_guarded_database_url


class _owner:
    """An owner-authorized session that never leaks back into the pool."""

    def __init__(self, engine):
        self.connection = engine.connect()
        self.connection.execute(text("SET SESSION AUTHORIZATION home_agent_owner"))
        self.connection.commit()

    def begin(self):
        return self.connection.begin()

    def execute(self, *args, **kwargs):
        return self.connection.execute(*args, **kwargs)

    def close(self):
        try:
            self.connection.rollback()
            self.connection.execute(text("RESET SESSION AUTHORIZATION"))
            self.connection.commit()
        finally:
            self.connection.close()


def _login(url, role, password):
    engine = create_engine(make_url(url).set(username=role, password=password),
        pool_size=1, max_overflow=0, connect_args={"connect_timeout": 5})
    return engine


def test_dedicated_logins_activate_with_exact_kernel_grants_and_return_dormant(tmp_path):
    url = os.getenv("TEST_PERSONAL_PREFERENCE_AUTHORITY_ADMIN_DATABASE_URL")
    if not url: pytest.skip("guarded hosted preference clone required")
    assert_guarded_database_url(url)
    admin = create_engine(url, isolation_level="SERIALIZABLE", pool_size=1, max_overflow=0,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=5000"})
    passwords = {role: secrets.token_hex(32) for role in roles.ROLES}
    for role, password in passwords.items():
        (tmp_path / f"{role}.password").write_text(password, encoding="utf-8")
    assert roles.read_passwords(tmp_path) == passwords
    consent_preexisting = False
    try:
        with admin.connect() as connection:
            assert connection.execute(text("SELECT current_database()")).scalar_one() == "personal_preference_authority_0047"
            consent_preexisting = connection.execute(text(
                "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:r)"), {"r": roles.CONSENT}).scalar_one()
            dormant = roles.status(connection)
        assert all(not value.get("login") for value in dormant.values())

        owner = _owner(admin)
        try:
            with owner.begin():
                roles.activate(owner.connection, passwords)
        finally:
            owner.close()

        with admin.connect() as connection:
            active = roles.status(connection)
        for role, (limit, functions) in roles.ROLES.items():
            state = active[role]
            assert state["login"] and state["connect"] and state["unexpired"], role
            assert state["connection_limit"] == limit
            assert state["functions_granted"] == len(functions)

        for role, (limit, functions) in roles.ROLES.items():
            login = _login(url, role, passwords[role])
            try:
                with login.connect() as connection:
                    assert connection.execute(text("SELECT current_user")).scalar_one() == role
                    for signature in functions:
                        assert connection.execute(text("SELECT has_function_privilege(current_user,:s,'EXECUTE')"),
                                                  {"s": signature}).scalar_one()
                    # No role may execute another role's kernel.
                    others = {s for r, (_l, fs) in roles.ROLES.items() if r != role for s in fs} - set(functions)
                    for signature in others:
                        assert not connection.execute(text("SELECT has_function_privilege(current_user,:s,'EXECUTE')"),
                                                      {"s": signature}).scalar_one()
                    if limit == 1:
                        extra = _login(url, role, passwords[role])
                        try:
                            with pytest.raises(OperationalError):
                                with extra.connect():
                                    pytest.fail("connection limit not enforced")
                        finally:
                            extra.dispose()
            finally:
                login.dispose()

        # Re-running activation is a bounded repeat, not a broadening.
        owner = _owner(admin)
        try:
            with owner.begin():
                roles.activate(owner.connection, passwords)
        finally:
            owner.close()
    finally:
        owner = _owner(admin)
        try:
            with owner.begin():
                roles.deactivate(owner.connection)
        finally:
            owner.close()
        with admin.connect() as connection:
            final = roles.status(connection)
        for role in roles.ROLES:
            if final[role].get("present"):
                assert not final[role]["login"] and not final[role]["connect"]
                assert final[role]["functions_granted"] == 0 and final[role]["sessions"] == 0
        for role in roles.ROLES:
            login = _login(url, role, passwords[role])
            try:
                with pytest.raises(OperationalError):
                    with login.connect():
                        pytest.fail("dormant role accepted a login")
            finally:
                login.dispose()
        if not consent_preexisting:
            with admin.begin() as connection:
                connection.execute(text(f"DROP OWNED BY {roles.CONSENT}"))
                connection.execute(text(f"DROP ROLE {roles.CONSENT}"))
        admin.dispose()
