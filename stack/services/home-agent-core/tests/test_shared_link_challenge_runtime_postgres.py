"""Guarded disposable-cluster checks. Every test mutation rolls back."""
import importlib.util
import os
import secrets
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, text
from sqlalchemy.exc import DBAPIError

from .e1_postgres_harness import assert_guarded_database_url
from app import schema
from app.crypto import sha256_json
from app.ids import uuid7
from app.store import binding_person_snapshot_digest, binding_proposal_digest
from app.shared_link_challenge_schema import shared_owner_generations, shared_link_ceremonies, shared_link_challenges

ENV = "TEST_SHARED_LINK_CHALLENGE_ADMIN_DATABASE_URL"
PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0034_shared_link_challenges.py"


def migration():
    spec = importlib.util.spec_from_file_location("challenge_runtime_migration", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guarded_connection(expected_revision):
    url = os.getenv(ENV)
    if not url:
        pytest.skip(f"{ENV} is required for guarded PostgreSQL execution")
    assert_guarded_database_url(url)
    engine = create_engine(url, isolation_level="SERIALIZABLE", pool_size=1, max_overflow=0, connect_args={
        "connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=5000"})
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                assert conn.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == expected_revision
                assert conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")).scalar_one()
                yield conn
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.fixture
def connection():
    yield from guarded_connection(migration().revision)


def test_new_storage_has_forced_rls_no_policies_and_no_nonowner_acl(connection):
    for table in migration().TABLE_NAMES:
        row = connection.execute(text("""
          SELECT c.relrowsecurity,c.relforcerowsecurity,
            (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid) AS policies,
            (SELECT count(*) FROM aclexplode(c.relacl) a WHERE a.grantee<>c.relowner) AS other_acl
          FROM pg_class c WHERE c.oid=to_regclass(:table)
        """), {"table": table}).mappings().one()
        assert row["relrowsecurity"] and row["relforcerowsecurity"]
        assert row["policies"] == 0 and row["other_acl"] == 0


def test_consumption_fk_is_validated_and_binds_the_full_proof_scope(connection):
    rows = connection.execute(text("""
      SELECT c.convalidated,
        ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(n,ord)
          JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.n ORDER BY k.ord) AS local_columns,
        ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(n,ord)
          JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.n ORDER BY k.ord) AS proof_columns
      FROM pg_constraint c WHERE c.contype='f'
        AND c.conrelid='identity.shared_link_challenges'::regclass
        AND c.confrelid='identity.shared_auth_proofs'::regclass
    """)).mappings().all()
    assert len(rows) == 1 and rows[0]["convalidated"]
    assert list(rows[0]["local_columns"]) == ["consumed_proof_id", "issuer_id", "consumed_subject",
        "challenge_commitment", "session_commitment", "registration_revision"]
    assert list(rows[0]["proof_columns"]) == ["proof_id", "issuer_id", "subject",
        "challenge_commitment", "session_commitment", "registration_revision"]


@pytest.fixture
def challenge_rows(connection):
    """Constraint fixtures only, under the disposable administrator transaction.

    Preserve the complete legacy binding lineage and every trigger/FK. This is
    not authentication or a substitute for the future governed linking kernel.
    """
    # Reuse the reviewed promotion/primary-person lineage selected by the E5b
    # runtime fixture. A freshly invented person has no authority lineage and
    # cannot satisfy the receipt FKs installed by migration 0016.
    promotion = connection.execute(text("""SELECT promotion_id,run_id,finalization_id,
        policy_digest,committed_at FROM operations.semantic_authority_promotions
        WHERE authority_scope='identity_semantics'""")).mappings().one()
    person_row = connection.execute(text("""SELECT p.*,l.lineage_id
        FROM operations.reviewed_identity_migration_projection_lineage l
        JOIN operations.reviewed_identity_migration_projection_subjects s ON s.lineage_id=l.lineage_id
        JOIN identity.people p ON p.person_id=s.person_id
        WHERE l.run_id=:run AND l.decision_kind='person'
          AND l.projection_table_kind='identity.people' AND l.projection_id=p.person_id
          AND s.subject_role='primary' AND p.status='active'
          AND NOT privacy.identity_person_is_blocked(p.person_id)
          AND NOT EXISTS (SELECT 1 FROM identity.ha_user_bindings b
                           WHERE b.person_id=p.person_id AND b.revoked_at IS NULL)
          AND NOT EXISTS (SELECT 1 FROM identity.principal_binding_proposals bp
                           WHERE bp.person_id=p.person_id AND bp.state='ready')
          AND NOT EXISTS (SELECT 1 FROM identity.privacy_directives d
                           WHERE d.person_id=p.person_id AND d.enabled
                             AND d.directive IN ('auto_expire','do_not_track','ignored','silent'))
          AND NOT EXISTS (SELECT 1 FROM identity.edge_privacy_user_blocks eb WHERE eb.person_id=p.person_id)
        ORDER BY p.person_id LIMIT 1"""), {"run": promotion["run_id"]}).mappings().one()
    person = person_row["person_id"]
    principal, request, proposal, artifact, binding, ceremony, receipt, operator = [uuid7() for _ in range(8)]
    staged_at = max(connection.execute(text("SELECT clock_timestamp()")).scalar_one(),
                    promotion["committed_at"] + timedelta(microseconds=1))
    now = staged_at + timedelta(microseconds=1)
    proposal_expires = staged_at + timedelta(minutes=10)
    subject = uuid.uuid4().hex
    digest = lambda: secrets.token_hex(32)
    owner, nonce = digest(), digest()
    review_code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(16))
    snapshot = binding_person_snapshot_digest(person_row)
    stage_digest = sha256_json({"contract": "principal-binding-stage-v1", "request_id": str(request),
        "review_code": review_code, "ha_user_id": subject, "person_id": str(person),
        "reviewed_display_label": person_row["display_name"], "person_snapshot_digest": snapshot,
        "operator_request_id": str(operator), "policy_version": "home-agent-mvp-v1",
        "policy_digest": promotion["policy_digest"]})
    proposal_digest = binding_proposal_digest(request_id=request, review_code=review_code,
        ha_user_id=subject, person_id=person, reviewed_display_label=person_row["display_name"],
        person_snapshot_digest=snapshot, operator_request_id=operator, stage_receipt_digest=stage_digest,
        staged_at=staged_at, expires_at=proposal_expires, policy_version="home-agent-mvp-v1",
        policy_digest=promotion["policy_digest"])
    connection.execute(insert(schema.principals).values(principal_id=principal, person_id=person,
        kind="ha_user", display_label="Challenge fixture"))
    connection.execute(insert(schema.principal_binding_requests).values(request_id=request,
        ha_user_id=subject, review_code=review_code,
        state="consumed", requested_at=staged_at-timedelta(seconds=1), staged_at=staged_at,
        closed_at=now, expires_at=proposal_expires))
    connection.execute(insert(schema.confirmation_artifacts).values(artifact_id=artifact,
        principal_id=principal, purpose="ha_user_person_binding.confirm", proposal_digest=proposal_digest,
        client_nonce_sha256=nonce, issued_at=now,
        consumed_at=now, expires_at=now+timedelta(minutes=5)))
    connection.execute(insert(schema.principal_binding_proposals).values(proposal_id=proposal,
        operator_request_id=operator, request_id=request, ha_user_id=subject, person_id=person,
        reviewed_display_label=person_row["display_name"], person_snapshot_digest=snapshot,
        proposal_digest=proposal_digest, state="consumed", stage_receipt_digest=stage_digest,
        staged_at=staged_at, expires_at=proposal_expires,
        consumed_at=now, result_principal_id=principal, confirmation_artifact_id=artifact))
    authority = dict(receipt_id=receipt, binding_id=binding, proposal_id=proposal,
        operator_request_id=operator, request_id=request, ha_user_id=subject, person_id=person,
        person_snapshot_digest=snapshot, proposal_digest=proposal_digest, stage_receipt_digest=stage_digest,
        proposal_staged_at=staged_at, proposal_expires_at=proposal_expires,
        principal_id=principal, principal_kind="ha_user", confirmation_artifact_id=artifact,
        confirmation_purpose="ha_user_person_binding.confirm", confirmation_nonce_sha256=nonce,
        confirmation_issued_at=now, confirmation_consumed_at=now,
        confirmation_expires_at=now+timedelta(minutes=5), promotion_id=promotion["promotion_id"],
        promotion_run_id=promotion["run_id"], promotion_finalization_id=promotion["finalization_id"],
        promotion_policy_digest=promotion["policy_digest"], promotion_committed_at=promotion["committed_at"],
        projection_lineage_id=person_row["lineage_id"], projection_decision_kind="person",
        projection_table_kind="identity.people", projection_id=person, projection_subject_role="primary",
        proposal_contract_version="principal-binding-proposal-v1", policy_version="home-agent-mvp-v1",
        authority_result="current_database_authority",
        database_transaction_id=connection.execute(text("SELECT txid_current()")).scalar_one(), evaluated_at=now)
    # These migrated fields are intentionally not represented in legacy app.schema.
    columns = ",".join(authority)
    placeholders = ",".join(":" + name for name in authority)
    connection.execute(text(f"INSERT INTO operations.principal_binding_authority_receipts ({columns}) VALUES ({placeholders})"), authority)
    connection.execute(text("""INSERT INTO identity.ha_user_bindings
        (binding_id,proposal_id,authority_receipt_id,ha_user_id,principal_id,person_id,
         confirmed_by_principal_id,confirmed_at,source_artifact_id)
        VALUES (:binding_id,:proposal_id,:receipt_id,:ha_user_id,:principal_id,:person_id,
                :principal_id,:evaluated_at,:confirmation_artifact_id)"""), authority)
    # Outer transactions roll back, so deferred graph triggers otherwise never
    # run. Validate the complete lineage before relying on it in child tests.
    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    connection.execute(insert(shared_owner_generations).values(owner_commitment=owner,
        authorization_generation=1, revision=1, state="active", updated_at=now))
    connection.execute(insert(shared_link_ceremonies).values(ceremony_id=ceremony, owner_commitment=owner,
        principal_id=principal, person_id=person, legacy_binding_id=binding,
        initiating_session_commitment=digest(), request_commitment=digest(), purpose="link_echo_victoria",
        authorization_generation=1, revision=1, state="pending", created_at=now,
        expires_at=now+timedelta(minutes=5)))
    children = []
    for site in ("echo", "victoria"):
        issuer = f"home-assistant:{site}"
        connection.execute(text("INSERT INTO identity.shared_issuers VALUES (:issuer,:site,1,'active')"),
                           {"issuer": issuer, "site": site})
        child = dict(challenge_id=uuid.uuid4(), ceremony_id=ceremony, authorization_generation=1,
            issuer_id=issuer, registration_revision=1, session_commitment=digest(),
            challenge_commitment=digest(), created_at=now, expires_at=now+timedelta(minutes=5))
        connection.execute(insert(shared_link_challenges).values(**child))
        children.append(child)
    return children, subject, owner


@pytest.mark.parametrize("mismatch", [None, "session", "challenge", "revision", "issuer", "subject"])
def test_real_consumption_requires_exact_proof_scope(connection, challenge_rows, mismatch):
    children, subject, _ = challenge_rows
    child = children[0]
    proof = dict(id=uuid.uuid4(), issuer=child["issuer_id"], subject=subject,
        session=child["session_commitment"], challenge=child["challenge_commitment"], revision=1,
        issued=child["created_at"], expires=child["expires_at"])
    if mismatch:
        proof[mismatch] = {"session": "a"*64, "challenge": "b"*64, "revision": 2,
                           "issuer": children[1]["issuer_id"], "subject": "other-subject"}[mismatch]
    connection.execute(text("""INSERT INTO identity.shared_auth_proofs
        (proof_id,issuer_id,subject,session_commitment,challenge_commitment,registration_revision,
         authenticated_at,issued_at,expires_at)
        VALUES (:id,:issuer,:subject,:session,:challenge,:revision,:issued,:issued,:expires)"""), proof)
    statement = shared_link_challenges.update().where(
        shared_link_challenges.c.challenge_id == child["challenge_id"]).values(
            consumed_proof_id=proof["id"], consumed_subject=subject, consumed_at=child["created_at"])
    if mismatch:
        with connection.begin_nested() as savepoint:
            with pytest.raises(DBAPIError) as error:
                connection.execute(statement)
            assert error.value.orig.sqlstate == "23503"
            savepoint.rollback()
    else:
        assert connection.execute(statement).rowcount == 1
    stored = connection.execute(text("SELECT consumed_proof_id FROM identity.shared_link_challenges WHERE challenge_id=:id"),
                                {"id": child["challenge_id"]}).scalar_one()
    assert stored == (None if mismatch else proof["id"])


def test_generation_advancement_preserves_parent_snapshot(connection, challenge_rows):
    children, _, owner = challenge_rows
    connection.execute(shared_owner_generations.update().where(
        shared_owner_generations.c.owner_commitment == owner).values(
            authorization_generation=2, revision=2, state="blocked"))
    assert connection.execute(text("SELECT authorization_generation FROM identity.shared_link_ceremonies WHERE ceremony_id=:id"),
                              {"id": children[0]["ceremony_id"]}).scalar_one() == 1
    with connection.begin_nested() as savepoint:
        with pytest.raises(DBAPIError) as error:
            connection.execute(shared_link_challenges.update().where(
                shared_link_challenges.c.challenge_id == children[0]["challenge_id"]).values(authorization_generation=2))
        assert error.value.orig.sqlstate == "23503"
        savepoint.rollback()


@pytest.mark.parametrize("generation,state", [(0, "active"), (-1, "blocked"), (1, "unknown")])
def test_generation_invalid_values_are_rejected(connection, generation, state):
    with connection.begin_nested() as savepoint:
        with pytest.raises(DBAPIError) as error:
            connection.execute(text("""INSERT INTO privacy.shared_owner_generations
                (owner_commitment,authorization_generation,revision,state,updated_at)
                VALUES (:owner,:generation,1,:state,clock_timestamp())"""),
                {"owner": "a" * 64, "generation": generation, "state": state})
        assert error.value.orig.sqlstate == "23514"
        savepoint.rollback()


def test_generation_history_is_not_foreign_keyed_to_live_identity(connection):
    assert connection.execute(text("""SELECT count(*) FROM pg_constraint
      WHERE contype='f' AND conrelid='privacy.shared_owner_generations'::regclass""")).scalar_one() == 0
    connection.execute(text("""INSERT INTO privacy.shared_owner_generations
      (owner_commitment,authorization_generation,revision,state,updated_at)
      VALUES (:owner,2,2,'blocked',clock_timestamp())"""), {"owner": "b" * 64})
    assert connection.execute(text("SELECT authorization_generation FROM privacy.shared_owner_generations WHERE owner_commitment=:owner"),
                              {"owner": "b" * 64}).scalar_one() == 2


def test_downgrade_cannot_remove_generation_history(connection, monkeypatch):
    connection.execute(text("""INSERT INTO privacy.shared_owner_generations
      (owner_commitment,authorization_generation,revision,state,updated_at)
      VALUES (:owner,3,3,'blocked',clock_timestamp())"""), {"owner": "c" * 64})
    module = migration()
    monkeypatch.setattr(module.op, "execute", lambda statement: connection.execute(text(statement)))
    with connection.begin_nested() as savepoint:
        connection.execute(text("SET LOCAL SESSION AUTHORIZATION home_agent_owner"))
        with pytest.raises(DBAPIError) as error:
            module.downgrade()
        assert error.value.orig.sqlstate in ("42501", "55000")
        if error.value.orig.sqlstate == "42501":
            assert "row-level security" in str(error.value.orig)
        else:
            assert "shared_challenge_downgrade_requires_empty_storage" in str(error.value.orig)
        savepoint.rollback()
    assert connection.execute(text("SELECT authorization_generation FROM privacy.shared_owner_generations WHERE owner_commitment=:owner"),
                              {"owner": "c" * 64}).scalar_one() == 3
