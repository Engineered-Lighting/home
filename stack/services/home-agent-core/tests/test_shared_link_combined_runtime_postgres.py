"""All isolated wrappers at one revision in a dedicated hosted clone.

This proves the database chain only: trusted fixture authentication assertions
are not live BFF sessions, owner gestures or production credential provisioning.
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.shared_link_commitments import SharedLinkBegin, SharedLinkCommitments
from app.shared_link_issuance import BEGIN, LOOKUP, INSPECT
from app.shared_auth_proof import SharedLinkProofDatabase
from app.shared_link_confirmation import CALL, INSPECT as CONFIRM_INSPECT
from .e1_postgres_harness import assert_guarded_database_url
from .test_shared_link_challenge_runtime_postgres import challenge_rows
from .test_shared_link_issuance_runtime_postgres import prepared, admitted
from .test_shared_link_session_runtime_postgres import REVOKE

COORDINATOR = "home_agent_shared_link_coordinator"
ECHO = "home_agent_shared_echo_proof_ingress"
VICTORIA = "home_agent_shared_victoria_proof_ingress"
PROOF_INSPECT = text("""SELECT * FROM identity.inspect_shared_link_auth_proof_v1(
 :proof_id,:subject,:session_commitment,:challenge_commitment,:authenticated_at,:registration_revision)""")
SESSION_ECHO = "home_agent_shared_echo_session_ingress"
SESSION_VICTORIA = "home_agent_shared_victoria_session_ingress"
SESSION_REVOKE = text("SELECT * FROM identity.revoke_shared_link_session_bound_v1(:session,:id)")
FUNCTIONS = {
    "identity.issue_shared_link_ceremony_v1(uuid,uuid,uuid,uuid,text,text,text,text,text,uuid,uuid,text,text,text,text)": COORDINATOR,
    "identity.resolve_shared_link_owner_v1(text)": COORDINATOR,
    "identity.inspect_shared_link_issuance_v1(uuid,uuid,uuid,uuid,text,text,text,text,text,uuid,uuid,text,text,text,text)": COORDINATOR,
    "identity.issue_shared_link_auth_proof_v1(uuid,text,text,text,timestamptz,bigint)": (ECHO, VICTORIA),
    "identity.confirm_shared_link_ceremony_v1(uuid,text,text,bigint,text,text,uuid,uuid,uuid,uuid,uuid)": COORDINATOR,
}


@pytest.fixture(scope="module")
def database():
    session_kernel_enabled = bool(os.getenv("TEST_SHARED_LINK_SESSION_KERNEL_ADMIN_DATABASE_URL"))
    proof_lookup_enabled = session_kernel_enabled or bool(os.getenv("TEST_SHARED_LINK_PROOF_LOOKUP_ADMIN_DATABASE_URL"))
    lookup_enabled = proof_lookup_enabled or bool(os.getenv("TEST_SHARED_LINK_CONFIRM_LOOKUP_ADMIN_DATABASE_URL"))
    url = os.getenv("TEST_SHARED_LINK_SESSION_KERNEL_ADMIN_DATABASE_URL") or os.getenv("TEST_SHARED_LINK_PROOF_LOOKUP_ADMIN_DATABASE_URL") or os.getenv("TEST_SHARED_LINK_CONFIRM_LOOKUP_ADMIN_DATABASE_URL") or os.getenv("TEST_SHARED_LINK_COMBINED_ADMIN_DATABASE_URL")
    if not url: pytest.skip("dedicated guarded combined linking clone required")
    assert_guarded_database_url(url)
    engine = create_engine(url, isolation_level="SERIALIZABLE", pool_size=2, max_overflow=0,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=8000"})
    installed = False
    functions = dict(FUNCTIONS)
    if lookup_enabled:
        functions["identity.inspect_shared_link_confirmation_v1(uuid,text,text,bigint,text,text,uuid,uuid,uuid,uuid,uuid)"] = COORDINATOR
    if proof_lookup_enabled:
        functions["identity.inspect_shared_link_auth_proof_v1(uuid,text,text,text,timestamptz,bigint)"] = (ECHO, VICTORIA)
    ingress_roles = (COORDINATOR,ECHO,VICTORIA) + ((SESSION_ECHO,SESSION_VICTORIA) if session_kernel_enabled else ())
    if session_kernel_enabled:
        functions["identity.revoke_shared_link_session_bound_v1(text,uuid)"] = (SESSION_ECHO,SESSION_VICTORIA)
    try:
        with engine.begin() as conn:
            assert conn.execute(text("SELECT current_database()")).scalar_one() == ("shared_link_session_kernel_0046" if session_kernel_enabled else "shared_link_proof_lookup_0045" if proof_lookup_enabled else "shared_link_lookup_0044" if lookup_enabled else "shared_link_combined_0043")
            assert conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")).scalar_one()
            assert conn.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == ("0046_shared_link_session_krnl_v1" if session_kernel_enabled else "0045_shared_link_proof_lookup_v1" if proof_lookup_enabled else "0044_shared_link_reconcile_v1" if lookup_enabled else "0043_shared_link_combined_v1")
            graph = challenge_rows.__wrapped__(conn)
            base = prepared.__wrapped__(conn, graph)
            admitted.__wrapped__(conn, base)
            for role in ingress_roles: conn.execute(text(f"GRANT USAGE ON SCHEMA identity TO {role}"))
            for signature, roles in functions.items():
                for role in (roles if isinstance(roles, tuple) else (roles,)):
                    assert not conn.execute(text("SELECT has_function_privilege(:role,:signature,'EXECUTE')"),
                        {"role": role, "signature": signature}).scalar_one()
                    conn.execute(text(f"GRANT EXECUTE ON FUNCTION {signature} TO {role}"))
        installed = True
        yield engine, base, lookup_enabled, proof_lookup_enabled, session_kernel_enabled
    finally:
        try:
            if installed:
                with engine.begin() as conn:
                    for signature, roles in functions.items():
                        for role in (roles if isinstance(roles, tuple) else (roles,)):
                            conn.execute(text(f"REVOKE EXECUTE ON FUNCTION {signature} FROM {role}"))
                    for role in ingress_roles: conn.execute(text(f"REVOKE USAGE ON SCHEMA identity FROM {role}"))
        finally: engine.dispose()


def invoke(engine, role, statement, params, *, optional=False):
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {role}"))
        result = conn.execute(statement, params).mappings()
        row = result.one_or_none() if optional else result.one()
        return None if row is None else dict(row)


def new_request(base):
    value = SharedLinkBegin(**{k: base[k] for k in ("principal_id", "person_id", "legacy_binding_id", "echo_subject")},
        ceremony_id=uuid.uuid4(), echo_challenge_id=uuid.uuid4(), victoria_challenge_id=uuid.uuid4(),
        **{k: uuid.uuid4().hex*2 for k in ("echo_session_commitment", "victoria_session_commitment",
                                         "echo_challenge_commitment", "victoria_challenge_commitment")})
    prepared_value = SharedLinkCommitments(b"x"*32, key_id="fixture-v1").prepare(value)
    return {**prepared_value.parameters(), "key_id": prepared_value.key_id, "key_fingerprint": prepared_value.key_fingerprint}


def test_shared_link_combined_issuer_chain_exact_replay_and_generation_fence(database):
    engine, base, lookup_enabled, proof_lookup_enabled, session_kernel_enabled = database
    # Pending-ceremony checks must precede confirmation: logout does not unlink.
    if proof_lookup_enabled:
        pending = new_request(base)
        pending_issuance = invoke(engine,COORDINATOR,BEGIN,pending)
        with engine.connect() as conn: stamp = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        pending_proof = dict(proof_id=uuid.uuid4(),subject=pending["echo_subject"],
            session_commitment=pending["echo_session_commitment"],challenge_commitment=pending["echo_challenge_commitment"],
            authenticated_at=stamp,registration_revision=pending_issuance["echo_registration_revision"])
        pending_receipt = invoke(engine,ECHO,SharedLinkProofDatabase._statement,pending_proof)
        assert invoke(engine,ECHO,PROOF_INSPECT,pending_proof,optional=True) == pending_receipt
        with engine.begin() as conn:
            conn.execute(REVOKE,{"issuer":"home-assistant:victoria","session":pending["victoria_session_commitment"],"id":uuid.uuid4()}).one()
        # Sibling revocation cancels the pending ceremony; the fresh proof stays
        # stored, but cannot be recovered as authority for a cancelled operation.
        assert invoke(engine,ECHO,PROOF_INSPECT,pending_proof,optional=True) is None
        with engine.connect() as conn:
            assert conn.execute(text("SELECT consumed_at FROM identity.shared_auth_proofs WHERE proof_id=:proof_id"),pending_proof).scalar_one() is None

    if session_kernel_enabled:
        pending = new_request(base)
        invoke(engine,COORDINATOR,BEGIN,pending)
        revoke = {"session":pending["victoria_session_commitment"],"id":uuid.uuid4()}
        invoke(engine,SESSION_ECHO,SESSION_REVOKE,revoke)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT state FROM identity.shared_link_ceremonies WHERE ceremony_id=:ceremony_id"),pending).scalar_one()=="pending"
        victoria_request = {**revoke,"id":uuid.uuid4()}
        result = invoke(engine,SESSION_VICTORIA,SESSION_REVOKE,victoria_request)
        assert result["issuer_id"] == "home-assistant:victoria"
        assert invoke(engine,SESSION_VICTORIA,SESSION_REVOKE,victoria_request) == result
        with engine.connect() as conn:
            row = conn.execute(text("SELECT state,revision,ended_at FROM identity.shared_link_ceremonies WHERE ceremony_id=:ceremony_id"),pending).mappings().one()
            assert row["state"]=="cancelled" and row["revision"]==2 and row["ended_at"]==result["revoked_at"]
            assert conn.execute(text("SELECT count(*) FROM identity.shared_source_grants")).scalar_one()==0

    params = new_request(base)
    anchor = invoke(engine, COORDINATOR, LOOKUP, {"subject": params["echo_subject"]})
    assert anchor == {k: params[k] for k in ("principal_id", "person_id", "legacy_binding_id")}
    issued = invoke(engine, COORDINATOR, BEGIN, params)
    assert invoke(engine, COORDINATOR, BEGIN, params) == issued
    assert invoke(engine, COORDINATOR, INSPECT, params) == issued
    stale = new_request(base)
    invoke(engine, COORDINATOR, BEGIN, stale)
    proofs = []
    submissions = []
    for site, role in (("echo", ECHO), ("victoria", VICTORIA)):
        with engine.connect() as conn: stamp = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        proof = dict(proof_id=uuid.uuid4(), subject=params["echo_subject"],
            session_commitment=params[f"{site}_session_commitment"],
            challenge_commitment=params[f"{site}_challenge_commitment"],
            authenticated_at=stamp, registration_revision=issued[f"{site}_registration_revision"])
        if proof_lookup_enabled:
            assert invoke(engine, role, PROOF_INSPECT, proof, optional=True) is None
            with engine.connect() as conn:
                assert conn.execute(text("SELECT consumed_proof_id FROM identity.shared_link_challenges WHERE challenge_commitment=:challenge_commitment"),proof).scalar_one() is None
                assert conn.execute(text("SELECT count(*) FROM identity.shared_auth_proofs WHERE proof_id=:proof_id"),proof).scalar_one() == 0
        receipt = invoke(engine, role, SharedLinkProofDatabase._statement, proof)
        assert invoke(engine, role, SharedLinkProofDatabase._statement, proof) == receipt
        assert receipt["issuer_id"] == f"home-assistant:{site}"
        proofs.append(receipt)
        submissions.append((role,proof))
        if proof_lookup_enabled:
            assert invoke(engine, role, PROOF_INSPECT, proof, optional=True) == receipt
            assert invoke(engine, VICTORIA if role == ECHO else ECHO, PROOF_INSPECT, proof, optional=True) is None
            with pytest.raises(DBAPIError) as error:
                invoke(engine, role, PROOF_INSPECT, {**proof,"proof_id":uuid.uuid4()}, optional=True)
            assert error.value.orig.sqlstate == "23505"
            with engine.connect() as conn:
                assert conn.execute(text("SELECT consumed_at FROM identity.shared_auth_proofs WHERE proof_id=:proof_id"),proof).scalar_one() is None
    assert proofs[0]["subject"] == proofs[1]["subject"]  # equal bare IDs stay issuer-qualified
    confirmation = dict(ceremony_id=params["ceremony_id"], echo_subject=params["echo_subject"],
        session_commitment=params["echo_session_commitment"], expected_revision=issued["revision"],
        confirmation_commitment=uuid.uuid4().hex*2, proposal_digest=uuid.uuid4().hex*2,
        **{k: uuid.uuid4() for k in ("proposal_id", "receipt_id", "link_id", "echo_binding_id", "victoria_binding_id")})
    if lookup_enabled:
        assert invoke(engine, COORDINATOR, CONFIRM_INSPECT, confirmation, optional=True) is None
        assert invoke(engine, COORDINATOR, CONFIRM_INSPECT, {**confirmation, "ceremony_id": uuid.uuid4()}, optional=True) is None
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM identity.shared_owner_links WHERE link_id=:link_id"), confirmation).scalar_one() == 0
    linked = invoke(engine, COORDINATOR, CALL, confirmation)
    assert invoke(engine, COORDINATOR, CALL, confirmation) == linked
    if lookup_enabled:
        assert invoke(engine, COORDINATOR, CONFIRM_INSPECT, confirmation, optional=True) == linked
        with pytest.raises(DBAPIError) as error:
            invoke(engine, COORDINATOR, CONFIRM_INSPECT, {**confirmation, "confirmation_commitment": uuid.uuid4().hex*2}, optional=True)
        assert error.value.orig.sqlstate == "23505"
    if proof_lookup_enabled:
        for role,proof in submissions:
            assert invoke(engine, role, PROOF_INSPECT, proof, optional=True) is None
    assert linked["authorization_generation"] == issued["authorization_generation"] + 1
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT issuer_id,subject FROM identity.shared_subject_bindings WHERE link_id=:link_id ORDER BY issuer_id"), confirmation).all()
        assert rows == [("home-assistant:echo", params["echo_subject"]), ("home-assistant:victoria", params["echo_subject"])]
        assert conn.execute(text("SELECT count(*) FROM identity.shared_source_grants")).scalar_one() == 0
        stamp = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
    with pytest.raises(DBAPIError) as error:
        invoke(engine, ECHO, SharedLinkProofDatabase._statement, dict(proof_id=uuid.uuid4(), subject=stale["echo_subject"],
            session_commitment=stale["echo_session_commitment"], challenge_commitment=stale["echo_challenge_commitment"],
            authenticated_at=stamp, registration_revision=issued["echo_registration_revision"]))
    assert error.value.orig.sqlstate == "42501"
    with engine.begin() as conn:
        conn.execute(REVOKE, {"issuer": "home-assistant:victoria", "session": params["victoria_session_commitment"], "id": uuid.uuid4()}).one()
    with pytest.raises(DBAPIError) as error: invoke(engine, COORDINATOR, CALL, confirmation)
    assert error.value.orig.sqlstate == "42501"
    if lookup_enabled:
        with pytest.raises(DBAPIError) as error:
            invoke(engine, COORDINATOR, CONFIRM_INSPECT, confirmation, optional=True)
        assert error.value.orig.sqlstate == "42501"

    if proof_lookup_enabled:
        with pytest.raises(DBAPIError) as error:
            invoke(engine, VICTORIA, PROOF_INSPECT, submissions[1][1], optional=True)
        assert error.value.orig.sqlstate == "42501"


    if session_kernel_enabled:
        _assert_bound_session_revocation_is_monotonic_and_issuer_scoped(database)


def _assert_bound_session_revocation_is_monotonic_and_issuer_scoped(database):
    engine,base,_,_,enabled = database
    if not enabled: pytest.skip("0046 session kernel clone required")
    with engine.connect() as conn:
        live_links_before=conn.execute(text("SELECT count(*) FROM identity.shared_owner_links WHERE revoked_at IS NULL")).scalar_one()
    session=uuid.uuid4().hex*2
    request={"session":session,"id":uuid.uuid4()}
    # A missing ceremony still produces the durable tombstone. Dedicated Echo
    # credentials cannot choose Victoria merely by knowing its session digest.
    receipt=invoke(engine,SESSION_ECHO,SESSION_REVOKE,request)
    assert receipt["issuer_id"]=="home-assistant:echo"
    assert receipt["session_commitment"]==session
    assert invoke(engine,SESSION_ECHO,SESSION_REVOKE,request)==receipt
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM privacy.shared_link_session_revocations WHERE session_commitment=:session AND issuer_id='home-assistant:victoria'"),request).scalar_one()==0
    for changed in ({**request,"id":uuid.uuid4()},{**request,"session":uuid.uuid4().hex*2}):
        with pytest.raises(DBAPIError) as error: invoke(engine,SESSION_ECHO,SESSION_REVOKE,changed)
        assert error.value.orig.sqlstate=="23505"
    for role in (ECHO,VICTORIA,COORDINATOR):
        with pytest.raises(DBAPIError) as error: invoke(engine,role,SESSION_REVOKE,request)
        assert error.value.orig.sqlstate=="42501"
    with pytest.raises(DBAPIError) as error:
        invoke(engine,SESSION_ECHO,text("SELECT * FROM identity.revoke_shared_link_session_bound_v1('home-assistant:victoria',:session,:id)"),request)
    assert error.value.orig.sqlstate=="42883"
    with pytest.raises(DBAPIError) as error:
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_current_xact_id()"))
            conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {SESSION_ECHO}"))
            conn.execute(SESSION_REVOKE,request).one()
    assert error.value.orig.sqlstate=="25001"
    with pytest.raises(DBAPIError) as error:
        invoke(engine,SESSION_ECHO,text("SELECT * FROM privacy.shared_link_session_revocations"),{})
    assert error.value.orig.sqlstate=="42501"
    # Tombstone delivery/replay remains possible when the issuer is disabled.
    # Move this fixture timestamp into history rather than waiting days.
    with engine.begin() as conn:
        historical=conn.execute(text("UPDATE privacy.shared_link_session_revocations SET revoked_at=revoked_at-interval '7 days' WHERE revocation_id=:id RETURNING revoked_at"),request).scalar_one()
        conn.execute(text("UPDATE identity.shared_issuers SET state='revoked' WHERE issuer_id='home-assistant:echo'"))
    try:
        assert invoke(engine,SESSION_ECHO,SESSION_REVOKE,request)=={**receipt,"revoked_at":historical}
        another=invoke(engine,SESSION_ECHO,SESSION_REVOKE,{"session":uuid.uuid4().hex*2,"id":uuid.uuid4()})
        assert another["issuer_id"]=="home-assistant:echo"
    finally:
        with engine.begin() as conn: conn.execute(text("UPDATE identity.shared_issuers SET state='active' WHERE issuer_id='home-assistant:echo'"))
    # A confirmed owner cannot start another linking ceremony merely by logging
    # out. Exercise the session boundary against the existing confirmed link.
    with pytest.raises(DBAPIError) as error:
        invoke(engine,COORDINATOR,BEGIN,new_request(base))
    assert error.value.orig.sqlstate == "23505"
    with engine.connect() as conn:
        session = conn.execute(text("SELECT initiating_session_commitment FROM identity.shared_link_ceremonies WHERE state='consumed'")).scalar_one()
    request = {"session": session, "id": uuid.uuid4()}
    invoke(engine,SESSION_ECHO,SESSION_REVOKE,request)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM identity.shared_owner_links WHERE revoked_at IS NULL")).scalar_one()==live_links_before
