"""Dormant shared-link challenge storage. No runtime roles or kernels enabled."""
from alembic import op

revision = "0034_shared_link_challenges_v1"
down_revision = "0033_shared_auth_proof_v1"
branch_labels = None
depends_on = None

TABLE_NAMES = ("privacy.shared_owner_generations", "identity.shared_link_ceremonies", "identity.shared_link_challenges")
PROOF_SCOPE_CONSTRAINT = "shared_proof_full_challenge_scope"
PROOF_SCOPE_COLUMNS = "proof_id, issuer_id, subject, challenge_commitment, session_commitment, registration_revision"

CREATE_STATEMENTS = (
    """CREATE TABLE privacy.shared_owner_generations (
	owner_commitment VARCHAR(64) NOT NULL, 
	authorization_generation BIGINT NOT NULL, 
	revision BIGINT NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ledger_record_digest VARCHAR(64), 
	PRIMARY KEY (owner_commitment), 
	CONSTRAINT link_generation_state CHECK (authorization_generation > 0 AND revision > 0 AND state IN ('active','blocked')), 
	CONSTRAINT link_generation_ledger CHECK (ledger_record_digest IS NULL OR ledger_record_digest ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT link_owner_commitment_digest CHECK (owner_commitment ~ '^[0-9a-f]{64}$')
)""",
    """CREATE TABLE identity.shared_link_ceremonies (
	ceremony_id UUID NOT NULL, 
	owner_commitment VARCHAR(64) NOT NULL, 
	principal_id UUID NOT NULL, 
	person_id UUID NOT NULL, 
	legacy_binding_id UUID NOT NULL, 
	initiating_session_commitment VARCHAR(64) NOT NULL, 
	request_commitment VARCHAR(64) NOT NULL, 
	purpose VARCHAR(32) NOT NULL, 
	authorization_generation BIGINT NOT NULL, 
	revision BIGINT NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ended_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (ceremony_id), 
	FOREIGN KEY(owner_commitment) REFERENCES privacy.shared_owner_generations (owner_commitment), 
	FOREIGN KEY(principal_id, person_id) REFERENCES identity.principals (principal_id, person_id), 
	FOREIGN KEY(legacy_binding_id) REFERENCES identity.ha_user_bindings (binding_id), 
	CONSTRAINT link_ceremony_request_once UNIQUE (owner_commitment, request_commitment), 
	CONSTRAINT link_ceremony_generation_identity UNIQUE (ceremony_id, authorization_generation), 
	CONSTRAINT link_ceremony_purpose_generation CHECK (purpose = 'link_echo_victoria' AND authorization_generation > 0 AND revision > 0), 
	CONSTRAINT link_ceremony_window CHECK (expires_at > created_at AND expires_at <= created_at + interval '5 minutes'), 
	CONSTRAINT link_ceremony_lifecycle CHECK ((state = 'pending' AND ended_at IS NULL) OR (state = 'cancelled' AND ended_at IS NOT NULL AND ended_at >= created_at) OR (state = 'consumed' AND ended_at IS NOT NULL AND ended_at >= created_at AND ended_at < expires_at)), 
	CONSTRAINT link_owner_commitment_digest CHECK (owner_commitment ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT link_initiating_session_commitment_digest CHECK (initiating_session_commitment ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT link_request_commitment_digest CHECK (request_commitment ~ '^[0-9a-f]{64}$')
)""",
    """CREATE TABLE identity.shared_link_challenges (
	challenge_id UUID NOT NULL, 
	ceremony_id UUID NOT NULL, 
	authorization_generation BIGINT NOT NULL, 
	issuer_id VARCHAR(64) NOT NULL, 
	registration_revision BIGINT NOT NULL, 
	session_commitment VARCHAR(64) NOT NULL, 
	challenge_commitment VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	consumed_proof_id UUID, 
	consumed_subject VARCHAR(64), 
	consumed_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (challenge_id), 
	FOREIGN KEY(ceremony_id, authorization_generation) REFERENCES identity.shared_link_ceremonies (ceremony_id, authorization_generation), 
	FOREIGN KEY(issuer_id) REFERENCES identity.shared_issuers (issuer_id), 
	FOREIGN KEY(consumed_proof_id, issuer_id, consumed_subject, challenge_commitment, session_commitment, registration_revision) REFERENCES identity.shared_auth_proofs (proof_id, issuer_id, subject, challenge_commitment, session_commitment, registration_revision), 
	CONSTRAINT link_one_challenge_per_issuer UNIQUE (ceremony_id, issuer_id), 
	CONSTRAINT link_challenge_issuer_revision CHECK (issuer_id IN ('home-assistant:echo','home-assistant:victoria') AND authorization_generation > 0 AND registration_revision > 0), 
	CONSTRAINT link_challenge_window CHECK (expires_at > created_at AND expires_at <= created_at + interval '5 minutes'), 
	CONSTRAINT link_challenge_consumption CHECK ((consumed_proof_id IS NULL AND consumed_subject IS NULL AND consumed_at IS NULL) OR (consumed_proof_id IS NOT NULL AND consumed_subject IS NOT NULL AND consumed_subject <> '' AND consumed_subject = btrim(consumed_subject) AND consumed_at IS NOT NULL AND consumed_at >= created_at AND consumed_at < expires_at)), 
	CONSTRAINT link_session_commitment_digest CHECK (session_commitment ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT link_challenge_commitment_digest CHECK (challenge_commitment ~ '^[0-9a-f]{64}$'), 
	UNIQUE (challenge_commitment), 
	UNIQUE (consumed_proof_id)
)""",
)


def _guard(expected):
    op.execute(f"""
      DO $authority$
      BEGIN
        IF current_user <> 'home_agent_owner' OR session_user <> 'home_agent_owner' OR
           (SELECT count(*) FROM public.alembic_version) <> 1 OR
           NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num = '{expected}') THEN
          RAISE EXCEPTION 'shared_challenge_migration_authority_invalid';
        END IF;
      END
      $authority$;
    """)


def upgrade():
    _guard(down_revision)
    op.execute(f"ALTER TABLE identity.shared_auth_proofs ADD CONSTRAINT {PROOF_SCOPE_CONSTRAINT} UNIQUE ({PROOF_SCOPE_COLUMNS});")
    for statement in CREATE_STATEMENTS:
        op.execute(statement)
    for table in TABLE_NAMES:
        namespace, name = table.split('.')
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC;")
        op.execute(f"""
          DO $closed$
          DECLARE recipient record;
          BEGIN
            FOR recipient IN
              SELECT DISTINCT roles.rolname
                FROM pg_catalog.pg_class relation
                JOIN pg_catalog.pg_namespace ns ON ns.oid = relation.relnamespace
                CROSS JOIN LATERAL pg_catalog.aclexplode(relation.relacl) acl
                JOIN pg_catalog.pg_roles roles ON roles.oid = acl.grantee
               WHERE ns.nspname = '{namespace}' AND relation.relname = '{name}'
                 AND acl.grantee <> relation.relowner
            LOOP
              EXECUTE pg_catalog.format('REVOKE ALL ON TABLE %I.%I FROM %I',
                                        '{namespace}', '{name}', recipient.rolname);
            END LOOP;
          END;
          $closed$;
        """)


def downgrade():
    _guard(revision)
    # RLS-filtered emptiness is not evidence. Reject filtered reads instead of
    # silently discarding generation/challenge history. No CASCADE or data reset.
    op.execute("SET LOCAL row_security = off;")
    op.execute("LOCK TABLE " + ", ".join(TABLE_NAMES) + ", identity.shared_auth_proofs IN ACCESS EXCLUSIVE MODE;")
    occupied = " OR ".join(f"EXISTS (SELECT 1 FROM {table})" for table in TABLE_NAMES)
    op.execute(f"""
      DO $empty$
      BEGIN
        IF {occupied} THEN
          RAISE EXCEPTION 'shared_challenge_downgrade_requires_empty_storage' USING ERRCODE = '55000';
        END IF;
      END;
      $empty$;
    """)
    for table in reversed(TABLE_NAMES):
        op.execute(f"DROP TABLE {table};")
    op.execute(f"ALTER TABLE identity.shared_auth_proofs DROP CONSTRAINT {PROOF_SCOPE_CONSTRAINT};")
