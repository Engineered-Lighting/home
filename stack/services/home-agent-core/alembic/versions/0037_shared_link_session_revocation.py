"""Durable linking-session revocation; no runtime caller is provisioned.

Tombstones survive missing ceremonies and prevent a later replay from recreating
pending authority. This cancels unfinished linking, not an established owner link.
"""
from alembic import op

revision = "0037_shared_link_session_v1"
down_revision = "0036_shared_link_proof_v1"
branch_labels = None
depends_on = None

TABLE = "privacy.shared_link_session_revocations"
FUNCTIONS = (
    "identity.require_shared_link_session_active_v1(text,text)",
    "identity.guard_shared_link_session_v1()",
    "identity.revoke_shared_link_session_v1(text,text,uuid)",
)
TRIGGERS = (
    ("shared_link_parent_session_guard", "identity.shared_link_ceremonies", "initiating_session_commitment, state"),
    ("shared_link_child_session_guard", "identity.shared_link_challenges", "ceremony_id, authorization_generation, issuer_id, session_commitment, consumed_proof_id"),
    ("shared_link_proof_session_guard", "identity.shared_auth_proofs", "issuer_id, session_commitment"),
)

CREATE_TABLE = f"""CREATE TABLE {TABLE} (
    issuer_id varchar(64) NOT NULL,
    session_commitment varchar(64) NOT NULL,
    revocation_id uuid NOT NULL UNIQUE,
    revoked_at timestamptz NOT NULL,
    PRIMARY KEY (issuer_id,session_commitment),
    CHECK (issuer_id IN ('home-assistant:echo','home-assistant:victoria')),
    CHECK (session_commitment ~ '^[0-9a-f]{{64}}$'),
    CHECK (isfinite(revoked_at))
);"""

REQUIRE_BODY = f"""
CREATE FUNCTION identity.require_shared_link_session_active_v1(p_issuer text,p_session text)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog SET row_security = on
AS $session$
BEGIN
    IF pg_catalog.current_setting('transaction_isolation') <> 'serializable' THEN
        RAISE EXCEPTION 'shared_link_requires_serializable' USING ERRCODE='25001';
    END IF;
    IF p_issuer IS NULL OR p_issuer NOT IN ('home-assistant:echo','home-assistant:victoria') OR
       p_session IS NULL OR p_session !~ '^[0-9a-f]{{64}}$' THEN
        RAISE EXCEPTION 'shared_link_session_invalid' USING ERRCODE='22023';
    END IF;
    PERFORM privacy.lock_identity_semantic_write_fence();
    IF EXISTS (SELECT 1 FROM {TABLE} revoked
                WHERE revoked.issuer_id=p_issuer AND revoked.session_commitment=p_session) THEN
        RAISE EXCEPTION 'shared_link_session_revoked' USING ERRCODE='42501';
    END IF;
END
$session$;
"""

TRIGGER_BODY = f"""
CREATE FUNCTION identity.guard_shared_link_session_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog SET row_security = on
AS $guard$
DECLARE parent_session text;
BEGIN
    IF TG_TABLE_SCHEMA <> 'identity' THEN
        RAISE EXCEPTION 'shared_link_session_trigger_invalid' USING ERRCODE='55000';
    END IF;
    IF TG_TABLE_NAME='shared_link_ceremonies' THEN
        IF NEW.state <> 'pending' THEN RETURN NEW; END IF;
        PERFORM identity.require_shared_link_session_active_v1('home-assistant:echo',NEW.initiating_session_commitment);
        IF EXISTS (SELECT 1 FROM identity.shared_link_challenges child JOIN {TABLE} revoked
                     ON revoked.issuer_id=child.issuer_id AND revoked.session_commitment=child.session_commitment
                    WHERE child.ceremony_id=NEW.ceremony_id) THEN
            RAISE EXCEPTION 'shared_link_session_revoked' USING ERRCODE='42501';
        END IF;
    ELSIF TG_TABLE_NAME='shared_link_challenges' THEN
        PERFORM identity.require_shared_link_session_active_v1(NEW.issuer_id,NEW.session_commitment);
        SELECT parent.initiating_session_commitment INTO parent_session
          FROM identity.shared_link_ceremonies parent WHERE parent.ceremony_id=NEW.ceremony_id;
        PERFORM identity.require_shared_link_session_active_v1('home-assistant:echo',parent_session);
    ELSIF TG_TABLE_NAME='shared_auth_proofs' THEN
        PERFORM identity.require_shared_link_session_active_v1(NEW.issuer_id,NEW.session_commitment);
    ELSE
        RAISE EXCEPTION 'shared_link_session_trigger_invalid' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END
$guard$;
"""

REVOKE_BODY = f"""
CREATE FUNCTION identity.revoke_shared_link_session_v1(p_issuer text,p_session text,p_revocation_id uuid)
RETURNS TABLE (revocation_id uuid,issuer_id varchar,session_commitment varchar,revoked_at timestamptz)
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog SET row_security = on
AS $revoke$
DECLARE prior record; stamp timestamptz;
BEGIN
    IF pg_catalog.current_setting('transaction_isolation') <> 'serializable' THEN
        RAISE EXCEPTION 'shared_link_requires_serializable' USING ERRCODE='25001';
    END IF;
    IF p_issuer IS NULL OR p_issuer NOT IN ('home-assistant:echo','home-assistant:victoria') OR
       p_session IS NULL OR p_session !~ '^[0-9a-f]{{64}}$' OR p_revocation_id IS NULL THEN
        RAISE EXCEPTION 'shared_link_session_invalid' USING ERRCODE='22023';
    END IF;
    PERFORM privacy.lock_identity_semantic_write_fence();
    SELECT revoked.* INTO prior FROM {TABLE} revoked
     WHERE revoked.revocation_id=p_revocation_id OR
           (revoked.issuer_id=p_issuer AND revoked.session_commitment=p_session);
    IF FOUND THEN
        IF prior.revocation_id IS DISTINCT FROM p_revocation_id OR prior.issuer_id IS DISTINCT FROM p_issuer OR
           prior.session_commitment IS DISTINCT FROM p_session THEN
            RAISE EXCEPTION 'shared_link_session_revocation_conflict' USING ERRCODE='23505';
        END IF;
    ELSE
        stamp := pg_catalog.clock_timestamp();
        INSERT INTO {TABLE} (issuer_id,session_commitment,revocation_id,revoked_at)
            VALUES (p_issuer,p_session,p_revocation_id,stamp);
        UPDATE identity.shared_link_ceremonies parent SET state='cancelled',ended_at=stamp,revision=parent.revision+1
         WHERE parent.state='pending' AND (
            (p_issuer='home-assistant:echo' AND parent.initiating_session_commitment=p_session) OR
            EXISTS (SELECT 1 FROM identity.shared_link_challenges child WHERE child.ceremony_id=parent.ceremony_id
                     AND child.issuer_id=p_issuer AND child.session_commitment=p_session));
    END IF;
    RETURN QUERY SELECT revoked.revocation_id,revoked.issuer_id,revoked.session_commitment,revoked.revoked_at
      FROM {TABLE} revoked WHERE revoked.revocation_id=p_revocation_id;
END
$revoke$;
"""


def _guard(expected):
    op.execute(f"""DO $guard$ BEGIN
      IF current_user <> 'home_agent_owner' OR session_user <> 'home_agent_owner' OR
         (SELECT count(*) FROM public.alembic_version) <> 1 OR
         NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num='{expected}') THEN
        RAISE EXCEPTION 'shared_session_migration_authority_invalid';
      END IF;
    END $guard$;""")


def upgrade():
    _guard(down_revision)
    op.execute(CREATE_TABLE)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM PUBLIC;")
    op.execute(f"""DO $acl$ DECLARE recipient record; BEGIN
      FOR recipient IN SELECT DISTINCT role.rolname FROM pg_catalog.pg_class relation
        CROSS JOIN LATERAL pg_catalog.aclexplode(relation.relacl) acl
        JOIN pg_catalog.pg_roles role ON role.oid=acl.grantee
        WHERE relation.oid='{TABLE}'::regclass AND acl.grantee<>relation.relowner
      LOOP EXECUTE pg_catalog.format('REVOKE ALL ON TABLE {TABLE} FROM %I',recipient.rolname); END LOOP;
    END $acl$;""")
    for body in (REQUIRE_BODY, TRIGGER_BODY, REVOKE_BODY):
        op.execute(body)
    for signature in FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;")
        op.execute(f"""DO $acl$ DECLARE recipient record; BEGIN
          FOR recipient IN SELECT DISTINCT role.rolname FROM pg_catalog.pg_proc proc
            CROSS JOIN LATERAL pg_catalog.aclexplode(proc.proacl) acl
            JOIN pg_catalog.pg_roles role ON role.oid=acl.grantee
            WHERE proc.oid='{signature}'::regprocedure AND acl.grantee<>proc.proowner
          LOOP EXECUTE pg_catalog.format('REVOKE ALL ON FUNCTION {signature} FROM %I',recipient.rolname); END LOOP;
        END $acl$;""")
    for name, table, columns in TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} BEFORE INSERT OR UPDATE OF {columns} ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION identity.guard_shared_link_session_v1();")


def downgrade():
    _guard(revision)
    op.execute("SET LOCAL row_security=off;")
    op.execute(f"LOCK TABLE {TABLE},identity.shared_link_ceremonies,identity.shared_link_challenges,"
               "identity.shared_auth_proofs IN ACCESS EXCLUSIVE MODE;")
    op.execute(f"""DO $empty$ BEGIN IF EXISTS (SELECT 1 FROM {TABLE}) THEN
      RAISE EXCEPTION 'shared_session_downgrade_requires_empty_history' USING ERRCODE='55000';
    END IF; END $empty$;""")
    for name, table, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER {name} ON {table};")
    for signature in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION {signature};")
    op.execute(f"DROP TABLE {TABLE};")
