"""Dormant issuer-bound fresh-auth proof ingress; no runtime caller enabled.

Existing proofs receive NULL registration_revision: their provenance is never
fabricated. The new kernel only issues revision-bound proofs. This records a
trusted ingress assertion, not independent HA authentication; reviewed ingress
authentication, caller provisioning and replay/erasure integration remain gates.
"""
from alembic import op

revision = "0033_shared_auth_proof_v1"
down_revision = "0032_shared_identity_v1"
branch_labels = None
depends_on = None

KERNEL_ROLE = "home_agent_shared_proof_kernel"
ECHO_ROLE = "home_agent_shared_echo_proof_ingress"
VICTORIA_ROLE = "home_agent_shared_victoria_proof_ingress"
ROLES = (KERNEL_ROLE, ECHO_ROLE, VICTORIA_ROLE)
FUNCTION = "identity.issue_shared_auth_proof_v1"
SIGNATURE = "uuid, text, text, text, timestamptz, bigint"

BODY = f"""
CREATE FUNCTION {FUNCTION}(
    p_proof_id uuid, p_subject text, p_session_commitment text,
    p_challenge_commitment text, p_authenticated_at timestamptz,
    p_registration_revision bigint
) RETURNS TABLE (
    proof_id uuid, issuer_id varchar, subject varchar,
    session_commitment varchar, challenge_commitment varchar,
    authenticated_at timestamptz, issued_at timestamptz,
    expires_at timestamptz, registration_revision bigint
)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
SET row_security = on
AS $kernel$
DECLARE
    v_issuer text;
    v_now timestamptz;
    v_existing record;
BEGIN
    IF current_user <> '{KERNEL_ROLE}' OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles r
         WHERE r.rolname = current_user AND NOT r.rolsuper
           AND NOT r.rolbypassrls AND NOT r.rolcanlogin
           AND NOT r.rolcreaterole AND NOT r.rolcreatedb
           AND NOT r.rolreplication AND NOT r.rolinherit
    ) THEN
        RAISE EXCEPTION 'shared_proof_kernel_owner_invalid' USING ERRCODE = '42501';
    END IF;
    v_issuer := CASE session_user
        WHEN '{ECHO_ROLE}' THEN 'home-assistant:echo'
        WHEN '{VICTORIA_ROLE}' THEN 'home-assistant:victoria'
        ELSE NULL END;
    IF v_issuer IS NULL THEN
        RAISE EXCEPTION 'shared_proof_caller_denied' USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = session_user
                    AND NOT r.rolsuper AND NOT r.rolbypassrls
                    AND NOT r.rolcreaterole AND NOT r.rolcreatedb
                    AND NOT r.rolreplication AND NOT r.rolinherit) OR
       EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
               JOIN pg_catalog.pg_roles r ON r.oid IN (m.member, m.roleid)
              WHERE r.rolname = session_user) OR
       EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
               JOIN pg_catalog.pg_roles r ON r.oid = m.member
              WHERE r.rolname = current_user) THEN
        RAISE EXCEPTION 'shared_proof_role_boundary_invalid' USING ERRCODE = '42501';
    END IF;
    IF pg_catalog.current_setting('transaction_isolation') <> 'serializable' THEN
        RAISE EXCEPTION 'shared_proof_requires_serializable' USING ERRCODE = '25001';
    END IF;
    IF pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN
        RAISE EXCEPTION 'shared_proof_requires_first_write' USING ERRCODE = '25001';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.alembic_version a
                    WHERE a.version_num = '{revision}') OR
       (SELECT count(*) FROM public.alembic_version) <> 1 THEN
        RAISE EXCEPTION 'shared_proof_schema_revision_invalid' USING ERRCODE = '55000';
    END IF;
    v_now := pg_catalog.clock_timestamp();
    IF p_proof_id IS NULL OR p_subject IS NULL OR
       pg_catalog.char_length(p_subject) NOT BETWEEN 1 AND 64 OR
       p_subject <> pg_catalog.btrim(p_subject) OR
       p_subject ~ '[[:cntrl:]]' OR
       p_subject ~ '^[[:space:]]|[[:space:]]$' OR
       p_session_commitment IS NULL OR p_session_commitment !~ '^[0-9a-f]{{64}}$' OR
       p_challenge_commitment IS NULL OR p_challenge_commitment !~ '^[0-9a-f]{{64}}$' OR
       p_registration_revision IS NULL OR p_registration_revision <= 0 OR
       p_authenticated_at IS NULL OR NOT pg_catalog.isfinite(p_authenticated_at) OR
       p_authenticated_at > v_now OR
       p_authenticated_at + interval '5 minutes' <= v_now THEN
        RAISE EXCEPTION 'shared_proof_input_invalid' USING ERRCODE = '22023';
    END IF;
    PERFORM 1 FROM identity.shared_issuers i
         WHERE i.issuer_id = v_issuer AND i.state = 'active'
           AND i.registration_revision = p_registration_revision
         FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'shared_proof_issuer_not_current' USING ERRCODE = '42501';
    END IF;
    -- A registration writer can hold the row lock past the freshness window.
    v_now := pg_catalog.clock_timestamp();
    IF p_authenticated_at > v_now OR p_authenticated_at + interval '5 minutes' <= v_now THEN
        RAISE EXCEPTION 'shared_proof_input_invalid' USING ERRCODE = '22023';
    END IF;
    SELECT p.* INTO v_existing FROM identity.shared_auth_proofs p
      WHERE p.proof_id = p_proof_id OR p.challenge_commitment = p_challenge_commitment;
    IF FOUND THEN
        IF v_existing.proof_id IS DISTINCT FROM p_proof_id OR
           v_existing.issuer_id IS DISTINCT FROM v_issuer OR
           v_existing.subject IS DISTINCT FROM p_subject OR
           v_existing.session_commitment IS DISTINCT FROM p_session_commitment OR
           v_existing.challenge_commitment IS DISTINCT FROM p_challenge_commitment OR
           v_existing.authenticated_at IS DISTINCT FROM p_authenticated_at OR
           v_existing.registration_revision IS DISTINCT FROM p_registration_revision OR
           v_existing.consumed_at IS NOT NULL OR v_existing.expires_at <= v_now THEN
            RAISE EXCEPTION 'shared_proof_replay_conflict' USING ERRCODE = '23505';
        END IF;
        RETURN QUERY SELECT p.proof_id, p.issuer_id, p.subject,
            p.session_commitment, p.challenge_commitment, p.authenticated_at,
            p.issued_at, p.expires_at, p.registration_revision
          FROM identity.shared_auth_proofs p WHERE p.proof_id = p_proof_id;
        RETURN;
    END IF;
    RETURN QUERY INSERT INTO identity.shared_auth_proofs AS p
        (proof_id, issuer_id, subject, session_commitment, challenge_commitment,
         authenticated_at, issued_at, expires_at, registration_revision)
      VALUES (p_proof_id, v_issuer, p_subject, p_session_commitment,
              p_challenge_commitment, p_authenticated_at, v_now,
              p_authenticated_at + interval '5 minutes', p_registration_revision)
      RETURNING p.proof_id, p.issuer_id, p.subject, p.session_commitment,
        p.challenge_commitment, p.authenticated_at, p.issued_at, p.expires_at,
        p.registration_revision;
END
$kernel$;
"""


def upgrade():
    op.execute(f"""
      DO $precondition$
      BEGIN
        IF current_user <> 'home_agent_owner' OR session_user <> 'home_agent_owner' OR
           (SELECT count(*) FROM public.alembic_version) <> 1 OR
           NOT EXISTS (SELECT 1 FROM public.alembic_version
                        WHERE version_num = '{down_revision}') THEN
          RAISE EXCEPTION 'shared_proof_migration_authority_invalid';
        END IF;
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                    WHERE rolname IN ('{KERNEL_ROLE}','{ECHO_ROLE}','{VICTORIA_ROLE}')) THEN
          RAISE EXCEPTION 'shared_proof_role_collision';
        END IF;
      END
      $precondition$;
    """)
    for role in ROLES:
        op.execute(f"CREATE ROLE {role} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                   "NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 0 "
                   "VALID UNTIL '1970-01-01';")
    op.execute(f"GRANT {KERNEL_ROLE} TO home_agent_owner "
               "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;")
    op.execute("ALTER TABLE identity.shared_auth_proofs ADD COLUMN registration_revision bigint "
               "CHECK (registration_revision IS NULL OR registration_revision > 0);")
    op.execute(f"GRANT USAGE ON SCHEMA identity, public TO {KERNEL_ROLE};")
    op.execute(f"GRANT SELECT ON public.alembic_version TO {KERNEL_ROLE};")
    op.execute(f"GRANT SELECT ON identity.shared_issuers, identity.shared_auth_proofs TO {KERNEL_ROLE};")
    # PostgreSQL locking SELECT needs UPDATE privilege. The function never
    # updates registration; grant only this column, with issuer-restricted RLS.
    op.execute(f"GRANT UPDATE (registration_revision) ON identity.shared_issuers TO {KERNEL_ROLE};")
    op.execute(f"GRANT INSERT (proof_id,issuer_id,subject,session_commitment,challenge_commitment,"
               "authenticated_at,issued_at,expires_at,registration_revision) "
               f"ON identity.shared_auth_proofs TO {KERNEL_ROLE};")
    for table in ("shared_issuers", "shared_auth_proofs"):
        op.execute(f"CREATE POLICY shared_proof_ingress_select ON identity.{table} "
                   f"FOR SELECT TO {KERNEL_ROLE} USING (issuer_id = CASE session_user "
                   f"WHEN '{ECHO_ROLE}' THEN 'home-assistant:echo' "
                   f"WHEN '{VICTORIA_ROLE}' THEN 'home-assistant:victoria' ELSE NULL END);")
    op.execute(f"CREATE POLICY shared_proof_ingress_lock ON identity.shared_issuers "
               f"FOR UPDATE TO {KERNEL_ROLE} USING (issuer_id = CASE session_user "
               f"WHEN '{ECHO_ROLE}' THEN 'home-assistant:echo' "
               f"WHEN '{VICTORIA_ROLE}' THEN 'home-assistant:victoria' ELSE NULL END) "
               "WITH CHECK (false);")
    op.execute(f"CREATE POLICY shared_proof_ingress_insert ON identity.shared_auth_proofs "
               f"FOR INSERT TO {KERNEL_ROLE} WITH CHECK (registration_revision IS NOT NULL "
               "AND consumed_at IS NULL AND issuer_id = CASE session_user "
               f"WHEN '{ECHO_ROLE}' THEN 'home-assistant:echo' "
               f"WHEN '{VICTORIA_ROLE}' THEN 'home-assistant:victoria' ELSE NULL END);")
    op.execute(BODY)
    op.execute(f"GRANT CREATE ON SCHEMA identity TO {KERNEL_ROLE};")
    op.execute(f"ALTER FUNCTION {FUNCTION}({SIGNATURE}) OWNER TO {KERNEL_ROLE};")
    op.execute(f"REVOKE CREATE ON SCHEMA identity FROM {KERNEL_ROLE};")
    op.execute(f"SET LOCAL ROLE {KERNEL_ROLE};")
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION}({SIGNATURE}) FROM PUBLIC;")
    # Default function ACLs must not accidentally enable dormant ingress callers.
    op.execute(f"""
      DO $closed$
      DECLARE recipient record;
      BEGIN
        FOR recipient IN
          SELECT DISTINCT r.rolname FROM pg_catalog.pg_proc p
          CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) a
          JOIN pg_catalog.pg_roles r ON r.oid = a.grantee
          WHERE p.oid = '{FUNCTION}({SIGNATURE})'::regprocedure AND a.grantee <> p.proowner
        LOOP
          EXECUTE pg_catalog.format('REVOKE ALL ON FUNCTION {FUNCTION}({SIGNATURE}) FROM %I', recipient.rolname);
        END LOOP;
      END
      $closed$;
    """)
    op.execute("RESET ROLE;")


def downgrade():
    op.execute(f"""
      DO $precondition$
      BEGIN
        IF current_user <> 'home_agent_owner' OR session_user <> 'home_agent_owner' OR
           (SELECT count(*) FROM public.alembic_version) <> 1 OR
           NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num = '{revision}') THEN
          RAISE EXCEPTION 'shared_proof_migration_authority_invalid';
        END IF;
      END
      $precondition$;
    """)
    # FORCE RLS must never make populated proof storage appear empty.
    op.execute("SET LOCAL row_security = off;")
    op.execute("LOCK TABLE identity.shared_auth_proofs IN ACCESS EXCLUSIVE MODE;")
    op.execute("""
      DO $empty$
      BEGIN
        IF EXISTS (SELECT 1 FROM identity.shared_auth_proofs) THEN
          RAISE EXCEPTION 'shared_proof_downgrade_requires_empty_storage' USING ERRCODE = '55000';
        END IF;
      END
      $empty$;
    """)
    op.execute(f"SET LOCAL ROLE {KERNEL_ROLE};")
    op.execute(f"DROP FUNCTION {FUNCTION}({SIGNATURE});")
    op.execute("RESET ROLE;")
    for table in ("shared_issuers", "shared_auth_proofs"):
        op.execute(f"DROP POLICY shared_proof_ingress_select ON identity.{table};")
    op.execute("DROP POLICY shared_proof_ingress_lock ON identity.shared_issuers;")
    op.execute("DROP POLICY shared_proof_ingress_insert ON identity.shared_auth_proofs;")
    op.execute(f"REVOKE ALL ON identity.shared_issuers, identity.shared_auth_proofs, "
               f"public.alembic_version FROM {KERNEL_ROLE};")
    op.execute(f"REVOKE UPDATE (registration_revision) ON identity.shared_issuers FROM {KERNEL_ROLE};")
    op.execute(f"REVOKE INSERT (proof_id,issuer_id,subject,session_commitment,challenge_commitment,"
               "authenticated_at,issued_at,expires_at,registration_revision) "
               f"ON identity.shared_auth_proofs FROM {KERNEL_ROLE};")
    op.execute(f"REVOKE USAGE ON SCHEMA identity, public FROM {KERNEL_ROLE};")
    op.execute("ALTER TABLE identity.shared_auth_proofs DROP COLUMN registration_revision;")
    op.execute(f"REVOKE {KERNEL_ROLE} FROM home_agent_owner;")
    for role in reversed(ROLES):
        op.execute(f"DROP ROLE {role};")
