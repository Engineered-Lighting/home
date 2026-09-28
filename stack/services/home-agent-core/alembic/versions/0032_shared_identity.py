"""Isolated issuer-qualified identity storage; no runtime authority enabled.

DDL is frozen here, independent of evolving application metadata. Legacy Echo
bindings remain untouched. This revision is intentionally absent from runtime
Settings and deploy entrypoint targets until reviewed kernels, role grants,
fresh issuer authentication and erasure/restore integration pass their gates.

Revision ID: 0032_shared_identity_v1
Revises: 0031_relationship_uniqueness_e5r
"""
from alembic import op

revision = "0032_shared_identity_v1"
down_revision = "0031_relationship_uniqueness_e5r"
branch_labels = None
depends_on = None

TABLE_NAMES = ('identity.shared_issuers', 'identity.shared_auth_proofs', 'identity.shared_link_proposals', 'identity.shared_link_receipts', 'identity.shared_owner_links', 'identity.shared_subject_bindings', 'identity.shared_sources', 'identity.shared_source_grants', 'privacy.shared_identity_revocations')

CREATE_STATEMENTS = (
    """CREATE TABLE identity.shared_issuers (
	issuer_id VARCHAR(64) NOT NULL,
	site_id VARCHAR(16) NOT NULL,
	registration_revision BIGINT NOT NULL,
	state VARCHAR(16) NOT NULL,
	PRIMARY KEY (issuer_id),
	CONSTRAINT shared_fixed_issuer_site CHECK ((issuer_id, site_id) IN (('home-assistant:echo','echo'),('home-assistant:victoria','victoria'))),
	CONSTRAINT shared_issuer_lifecycle CHECK (registration_revision > 0 AND state IN ('active','revoked')),
	CONSTRAINT shared_issuer_site_identity UNIQUE (issuer_id, site_id),
	UNIQUE (site_id)
)""",
    """CREATE TABLE identity.shared_auth_proofs (
	proof_id UUID NOT NULL,
	issuer_id VARCHAR(64) NOT NULL,
	subject VARCHAR(64) NOT NULL,
	session_commitment VARCHAR(64) NOT NULL,
	challenge_commitment VARCHAR(64) NOT NULL,
	authenticated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	issued_at TIMESTAMP WITH TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	consumed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (proof_id),
	FOREIGN KEY(issuer_id) REFERENCES identity.shared_issuers (issuer_id),
	CONSTRAINT shared_proof_identity UNIQUE (proof_id, issuer_id, subject),
	CONSTRAINT shared_proof_subject CHECK (subject <> '' AND subject = btrim(subject)),
	CONSTRAINT shared_proof_window CHECK (authenticated_at <= issued_at AND issued_at < expires_at AND expires_at <= authenticated_at + interval '5 minutes' AND (consumed_at IS NULL OR (consumed_at >= issued_at AND consumed_at < expires_at))),
	CONSTRAINT shared_session_commitment_digest CHECK (session_commitment ~ '^[0-9a-f]{64}$'),
	CONSTRAINT shared_challenge_commitment_digest CHECK (challenge_commitment ~ '^[0-9a-f]{64}$'),
	UNIQUE (challenge_commitment)
)""",
    """CREATE TABLE identity.shared_link_proposals (
	proposal_id UUID NOT NULL,
	legacy_binding_id UUID NOT NULL,
	principal_id UUID NOT NULL,
	person_id UUID NOT NULL,
	confirmed_by_principal_id UUID NOT NULL,
	echo_proof_id UUID NOT NULL,
	echo_issuer VARCHAR(64) NOT NULL,
	echo_subject VARCHAR(64) NOT NULL,
	victoria_proof_id UUID NOT NULL,
	victoria_issuer VARCHAR(64) NOT NULL,
	victoria_subject VARCHAR(64) NOT NULL,
	proposal_digest VARCHAR(64) NOT NULL,
	expected_generation BIGINT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (proposal_id),
	FOREIGN KEY(legacy_binding_id) REFERENCES identity.ha_user_bindings (binding_id),
	FOREIGN KEY(principal_id, person_id) REFERENCES identity.principals (principal_id, person_id),
	FOREIGN KEY(echo_proof_id, echo_issuer, echo_subject) REFERENCES identity.shared_auth_proofs (proof_id, issuer_id, subject),
	FOREIGN KEY(victoria_proof_id, victoria_issuer, victoria_subject) REFERENCES identity.shared_auth_proofs (proof_id, issuer_id, subject),
	CONSTRAINT shared_two_issuer_proofs CHECK (echo_issuer = 'home-assistant:echo' AND victoria_issuer = 'home-assistant:victoria' AND echo_proof_id <> victoria_proof_id),
	CONSTRAINT shared_owner_self_confirmation CHECK (confirmed_by_principal_id = principal_id),
	CONSTRAINT shared_proposal_window CHECK (expected_generation >= 0 AND expires_at > created_at AND expires_at <= created_at + interval '5 minutes'),
	CONSTRAINT shared_proposal_owner_digest UNIQUE (proposal_id, principal_id, person_id, proposal_digest),
	CONSTRAINT shared_proposal_digest_digest CHECK (proposal_digest ~ '^[0-9a-f]{64}$'),
	UNIQUE (echo_proof_id),
	UNIQUE (victoria_proof_id)
)""",
    """CREATE TABLE identity.shared_link_receipts (
	receipt_id UUID NOT NULL,
	proposal_id UUID NOT NULL,
	principal_id UUID NOT NULL,
	person_id UUID NOT NULL,
	proposal_digest VARCHAR(64) NOT NULL,
	confirmation_commitment VARCHAR(64) NOT NULL,
	confirmed_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (receipt_id),
	FOREIGN KEY(proposal_id, principal_id, person_id, proposal_digest) REFERENCES identity.shared_link_proposals (proposal_id, principal_id, person_id, proposal_digest),
	CONSTRAINT shared_receipt_owner UNIQUE (receipt_id, principal_id, person_id),
	CONSTRAINT shared_confirmation_commitment_digest CHECK (confirmation_commitment ~ '^[0-9a-f]{64}$'),
	UNIQUE (proposal_id),
	UNIQUE (confirmation_commitment)
)""",
    """CREATE TABLE identity.shared_owner_links (
	link_id UUID NOT NULL,
	receipt_id UUID NOT NULL,
	principal_id UUID NOT NULL,
	person_id UUID NOT NULL,
	revision BIGINT NOT NULL,
	authorization_generation BIGINT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (link_id),
	FOREIGN KEY(receipt_id, principal_id, person_id) REFERENCES identity.shared_link_receipts (receipt_id, principal_id, person_id),
	CONSTRAINT shared_link_lifecycle CHECK (revision > 0 AND authorization_generation > 0 AND (revoked_at IS NULL OR revoked_at >= created_at)),
	UNIQUE (receipt_id)
)""",
    """CREATE UNIQUE INDEX uq_shared_active_owner ON identity.shared_owner_links (person_id) WHERE revoked_at IS NULL""",
    """CREATE TABLE identity.shared_subject_bindings (
	binding_id UUID NOT NULL,
	link_id UUID NOT NULL,
	issuer_id VARCHAR(64) NOT NULL,
	subject VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (binding_id),
	FOREIGN KEY(link_id) REFERENCES identity.shared_owner_links (link_id),
	FOREIGN KEY(issuer_id) REFERENCES identity.shared_issuers (issuer_id),
	CONSTRAINT shared_link_one_subject_per_issuer UNIQUE (link_id, issuer_id),
	CONSTRAINT shared_subject_lifecycle CHECK (subject <> '' AND subject = btrim(subject) AND (revoked_at IS NULL OR revoked_at >= created_at))
)""",
    """CREATE UNIQUE INDEX uq_shared_active_issuer_subject ON identity.shared_subject_bindings (issuer_id, subject) WHERE revoked_at IS NULL""",
    """CREATE TABLE identity.shared_sources (
	site_id VARCHAR(16) NOT NULL,
	source_id VARCHAR(128) NOT NULL,
	capability VARCHAR(32) NOT NULL,
	issuer_id VARCHAR(64) NOT NULL,
	registration_revision BIGINT NOT NULL,
	state VARCHAR(16) NOT NULL,
	PRIMARY KEY (site_id, source_id, capability),
	FOREIGN KEY(issuer_id, site_id) REFERENCES identity.shared_issuers (issuer_id, site_id),
	CONSTRAINT shared_exact_source_capability CHECK (source_id ~ '^[a-z0-9][a-z0-9._:-]{0,127}$' AND capability IN ('memory.read','personal_memory.write','lighting.execute')),
	CONSTRAINT shared_source_lifecycle CHECK (registration_revision > 0 AND state IN ('active','revoked'))
)""",
    """CREATE TABLE identity.shared_source_grants (
	grant_id UUID NOT NULL,
	link_id UUID NOT NULL,
	site_id VARCHAR(16) NOT NULL,
	source_id VARCHAR(128) NOT NULL,
	capability VARCHAR(32) NOT NULL,
	source_revision BIGINT NOT NULL,
	authorization_generation BIGINT NOT NULL,
	revision BIGINT NOT NULL,
	approval_commitment VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (grant_id),
	FOREIGN KEY(link_id) REFERENCES identity.shared_owner_links (link_id),
	FOREIGN KEY(site_id, source_id, capability) REFERENCES identity.shared_sources (site_id, source_id, capability),
	CONSTRAINT shared_grant_lifecycle CHECK (source_revision > 0 AND authorization_generation > 0 AND revision > 0 AND expires_at > created_at AND (revoked_at IS NULL OR revoked_at >= created_at)),
	CONSTRAINT shared_approval_commitment_digest CHECK (approval_commitment ~ '^[0-9a-f]{64}$'),
	UNIQUE (approval_commitment)
)""",
    """CREATE UNIQUE INDEX uq_shared_active_source_grant ON identity.shared_source_grants (link_id, site_id, source_id, capability) WHERE revoked_at IS NULL""",
    """CREATE TABLE privacy.shared_identity_revocations (
	revocation_id UUID NOT NULL,
	owner_commitment VARCHAR(64) NOT NULL,
	scope_commitment VARCHAR(64) NOT NULL,
	kind VARCHAR(32) NOT NULL,
	authorization_generation BIGINT NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE NOT NULL,
	ledger_record_digest VARCHAR(64),
	PRIMARY KEY (revocation_id),
	CONSTRAINT shared_revocation_kind_generation CHECK (kind IN ('owner_unlink','grant_revoke','person_erasure') AND authorization_generation > 0),
	CONSTRAINT shared_revocation_ledger_digest CHECK (ledger_record_digest IS NULL OR ledger_record_digest ~ '^[0-9a-f]{64}$'),
	CONSTRAINT shared_revocation_once UNIQUE (owner_commitment, scope_commitment, authorization_generation),
	CONSTRAINT shared_owner_commitment_digest CHECK (owner_commitment ~ '^[0-9a-f]{64}$'),
	CONSTRAINT shared_scope_commitment_digest CHECK (scope_commitment ~ '^[0-9a-f]{64}$')
)""",
)


def upgrade():
    for statement in CREATE_STATEMENTS:
        op.execute(statement)
    for table in TABLE_NAMES:
        namespace, name = table.split(".")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC;")
        # The migration owner's default privileges may have added recipients.
        # Remove every non-owner ACL, not just a guessed runtime-role list.
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
    # No database rollback may discard durable link/unlink history. Lock the
    # whole set before checking emptiness so a concurrent insert cannot race it.
    # FORCE RLS + no policies can hide populated tables even from their owner.
    # Off does not bypass RLS: it raises if a policy would filter the read, so
    # only an approved maintenance identity able to see all rows can proceed.
    op.execute("SET LOCAL row_security = off;")
    op.execute("LOCK TABLE " + ", ".join(TABLE_NAMES) + " IN ACCESS EXCLUSIVE MODE;")
    occupied = " OR ".join(f"EXISTS (SELECT 1 FROM {table})" for table in TABLE_NAMES)
    op.execute(f"""
      DO $empty$
      BEGIN
        IF {occupied} THEN
          RAISE EXCEPTION 'shared_identity_downgrade_requires_empty_storage'
            USING ERRCODE = '55000';
        END IF;
      END;
      $empty$;
    """)
    for table in reversed(TABLE_NAMES):
        op.execute(f"DROP TABLE {table};")
