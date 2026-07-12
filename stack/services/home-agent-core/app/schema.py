from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSTZRANGE, UUID


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING_CONVENTION)

UUID_PK = UUID(as_uuid=True)
NOW = text("timezone('utc', now())")


def _uuidv7_shape(column: str) -> str:
    return (
        f"substring({column}::text from 15 for 1) = '7' AND "
        f"substring({column}::text from 20 for 1) IN ('8','9','a','b')"
    )


def _hex64(*columns: str) -> str:
    return " AND ".join(f"{column} ~ '^[0-9a-f]{{64}}$'" for column in columns)


source_streams = Table(
    "source_streams",
    metadata,
    Column("stream_id", UUID_PK, primary_key=True),
    Column("edge_instance_id", String(128), nullable=False),
    Column("source_name", String(128), nullable=False),
    Column("epoch", UUID_PK, nullable=False),
    Column("last_acked_sequence", BigInteger, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    UniqueConstraint(
        "edge_instance_id", "source_name", "epoch", name="source_stream_identity"
    ),
    schema="ingest",
)

envelopes = Table(
    "envelopes",
    metadata,
    Column("envelope_id", UUID_PK, primary_key=True),
    Column(
        "stream_id",
        UUID_PK,
        ForeignKey("ingest.source_streams.stream_id"),
        nullable=False,
    ),
    Column("sequence", BigInteger, nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("payload_bytes", BigInteger, nullable=False, server_default="0"),
    Column("entity_id", String(255)),
    Column("source_event_id", String(255)),
    Column("source_observed_at", DateTime(timezone=True), nullable=False),
    Column("edge_received_at", DateTime(timezone=True), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    Column("payload_sha256", String(64), nullable=False),
    Column("root_observation_id", UUID_PK, nullable=False),
    Column("evidence_family_id", UUID_PK, nullable=False),
    Column("dependency_domain", String(128), nullable=False),
    Column("freshness", String(32), nullable=False),
    Column("coverage", String(32), nullable=False),
    Column("clock_state", String(32), nullable=False),
    Column("ha_context", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    UniqueConstraint("stream_id", "sequence", name="stream_sequence"),
    CheckConstraint("sequence > 0", name="positive_sequence"),
    CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name="payload_sha256"),
    schema="ingest",
)
Index(
    "ix_envelopes_entity_observed",
    envelopes.c.entity_id,
    envelopes.c.source_observed_at,
)
Index("ix_envelopes_family", envelopes.c.evidence_family_id)
Index(
    "ix_envelopes_resource_window",
    envelopes.c.event_type,
    envelopes.c.ingested_at,
)

quarantine = Table(
    "quarantine",
    metadata,
    Column("quarantine_id", UUID_PK, primary_key=True),
    Column("edge_instance_id", String(128), nullable=False),
    Column("source_name", String(128), nullable=False),
    Column("epoch", UUID_PK),
    Column("sequence", BigInteger),
    Column("payload_sha256", String(64), nullable=False),
    Column("reason_code", String(64), nullable=False),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    UniqueConstraint(
        "edge_instance_id",
        "source_name",
        "epoch",
        "sequence",
        name="quarantine_sequence",
    ),
    schema="ingest",
)

coverage_intervals = Table(
    "coverage_intervals",
    metadata,
    Column("coverage_id", UUID_PK, primary_key=True),
    Column(
        "stream_id",
        UUID_PK,
        ForeignKey("ingest.source_streams.stream_id"),
        nullable=False,
    ),
    Column("entity_id", String(255)),
    Column("interval", TSTZRANGE, nullable=False),
    Column("status", String(32), nullable=False),
    Column("reason_code", String(64)),
    Column("source_sequence_start", BigInteger),
    Column("source_sequence_end", BigInteger),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    CheckConstraint(
        "status IN ('continuous','recorder_reconstructed','snapshot_only','gap','unknown')",
        name="coverage_status",
    ),
    schema="ingest",
)

artifact_links = Table(
    "artifact_links",
    metadata,
    Column("link_id", UUID_PK, primary_key=True),
    Column("parent_artifact_id", UUID_PK, nullable=False),
    Column("child_artifact_id", UUID_PK, nullable=False),
    Column("relation", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    UniqueConstraint(
        "parent_artifact_id", "child_artifact_id", "relation", name="artifact_relation"
    ),
    CheckConstraint("parent_artifact_id <> child_artifact_id", name="not_self_link"),
    schema="ingest",
)

people = Table(
    "people",
    metadata,
    Column("person_id", UUID_PK, primary_key=True),
    Column("display_name", String(255), nullable=False),
    Column("pronouns", String(64)),
    Column("status", String(32), nullable=False, server_default="active"),
    Column("privacy_scope", String(32), nullable=False, server_default="private"),
    Column("legacy_source_ref", String(255)),
    Column("legacy_source_version", Integer),
    Column("legacy_source_sha256", String(64)),
    Column("status_source_ref", String(255)),
    Column("status_source_version", Integer),
    Column("status_source_sha256", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    CheckConstraint("status IN ('active','archived','erased')", name="person_status"),
    CheckConstraint(
        "legacy_source_sha256 IS NULL OR legacy_source_sha256 ~ '^[0-9a-f]{64}$'",
        name="person_legacy_source_sha256",
    ),
    CheckConstraint(
        "status_source_sha256 IS NULL OR status_source_sha256 ~ '^[0-9a-f]{64}$'",
        name="person_status_source_sha256",
    ),
    schema="identity",
)

principals = Table(
    "principals",
    metadata,
    Column("principal_id", UUID_PK, primary_key=True),
    Column("person_id", UUID_PK, ForeignKey("identity.people.person_id")),
    Column("kind", String(32), nullable=False),
    Column("display_label", String(255), nullable=False),
    Column("status", String(32), nullable=False, server_default="active"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    CheckConstraint(
        "kind IN ('ha_user','voice_unknown','service')", name="principal_kind"
    ),
    UniqueConstraint("principal_id", "person_id", name="principal_person_identity"),
    schema="identity",
)

confirmation_artifacts = Table(
    "confirmation_artifacts",
    metadata,
    Column("artifact_id", UUID_PK, primary_key=True),
    Column(
        "principal_id",
        UUID_PK,
        ForeignKey("identity.principals.principal_id"),
        nullable=False,
    ),
    Column("purpose", String(128), nullable=False),
    Column("proposal_digest", String(64), nullable=False),
    Column("client_nonce_sha256", String(64), nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "principal_id", "client_nonce_sha256", name="confirmation_nonce_once"
    ),
    CheckConstraint(
        "proposal_digest ~ '^[0-9a-f]{64}$' AND "
        "client_nonce_sha256 ~ '^[0-9a-f]{64}$'",
        name="confirmation_digests",
    ),
    CheckConstraint(
        "consumed_at >= issued_at AND consumed_at <= expires_at",
        name="confirmation_consumed_in_window",
    ),
    schema="identity",
)

ha_user_bindings = Table(
    "ha_user_bindings",
    metadata,
    Column("binding_id", UUID_PK, primary_key=True),
    Column(
        "proposal_id",
        UUID_PK,
        ForeignKey(
            "identity.principal_binding_proposals.proposal_id", ondelete="CASCADE"
        ),
        nullable=False,
        unique=True,
    ),
    Column("ha_user_id", String(64), nullable=False),
    Column(
        "principal_id",
        UUID_PK,
        ForeignKey("identity.principals.principal_id"),
        nullable=False,
    ),
    Column(
        "person_id", UUID_PK, ForeignKey("identity.people.person_id"), nullable=False
    ),
    Column(
        "confirmed_by_principal_id",
        UUID_PK,
        ForeignKey("identity.principals.principal_id"),
        nullable=False,
    ),
    Column("confirmed_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    Column(
        "source_artifact_id",
        UUID_PK,
        ForeignKey("identity.confirmation_artifacts.artifact_id"),
    ),
    ForeignKeyConstraint(
        ["principal_id", "person_id"],
        ["identity.principals.principal_id", "identity.principals.person_id"],
        name="ha_binding_principal_person",
    ),
    CheckConstraint(
        "confirmed_by_principal_id = principal_id",
        name="ha_binding_self_confirmation",
    ),
    CheckConstraint(
        "revoked_at IS NOT NULL OR source_artifact_id IS NOT NULL",
        name="ha_binding_active_artifact",
    ),
    schema="identity",
)
Index(
    "uq_ha_user_bindings_active_ha_user",
    ha_user_bindings.c.ha_user_id,
    unique=True,
    postgresql_where=ha_user_bindings.c.revoked_at.is_(None),
)
Index(
    "uq_ha_user_bindings_active_person",
    ha_user_bindings.c.person_id,
    unique=True,
    postgresql_where=ha_user_bindings.c.revoked_at.is_(None),
)

principal_binding_requests = Table(
    "principal_binding_requests",
    metadata,
    Column("request_id", UUID_PK, primary_key=True),
    Column("ha_user_id", String(64), nullable=False),
    Column("review_code", String(16), nullable=False, unique=True),
    Column("state", String(32), nullable=False, server_default="pending"),
    Column("requested_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("staged_at", DateTime(timezone=True)),
    Column("closed_at", DateTime(timezone=True)),
    UniqueConstraint("request_id", "ha_user_id", name="binding_request_user_identity"),
    CheckConstraint(
        "state IN ('pending','staged','consumed','cancelled','expired')",
        name="binding_request_state",
    ),
    CheckConstraint(
        "review_code ~ '^[A-HJ-NP-Z2-9]{16}$'",
        name="binding_request_review_code",
    ),
    CheckConstraint(
        "expires_at > requested_at AND "
        "(staged_at IS NULL OR "
        "(staged_at >= requested_at AND staged_at < expires_at)) AND "
        "(closed_at IS NULL OR closed_at >= requested_at)",
        name="binding_request_temporal_order",
    ),
    CheckConstraint(
        "(state = 'pending' AND staged_at IS NULL AND closed_at IS NULL) OR "
        "(state = 'staged' AND staged_at IS NOT NULL AND closed_at IS NULL) OR "
        "(state = 'consumed' AND staged_at IS NOT NULL AND "
        "closed_at IS NOT NULL AND closed_at <= expires_at) OR "
        "(state = 'cancelled' AND closed_at IS NOT NULL) OR "
        "(state = 'expired' AND closed_at IS NOT NULL AND closed_at >= expires_at)",
        name="binding_request_state_shape",
    ),
    schema="identity",
)
Index(
    "uq_principal_binding_requests_active_user",
    principal_binding_requests.c.ha_user_id,
    unique=True,
    postgresql_where=principal_binding_requests.c.state.in_(("pending", "staged")),
)

principal_binding_proposals = Table(
    "principal_binding_proposals",
    metadata,
    Column("proposal_id", UUID_PK, primary_key=True),
    Column("operator_request_id", UUID_PK, nullable=False, unique=True),
    Column(
        "request_id",
        UUID_PK,
        ForeignKey(
            "identity.principal_binding_requests.request_id", ondelete="CASCADE"
        ),
        nullable=False,
    ),
    Column("ha_user_id", String(64), nullable=False),
    Column(
        "person_id",
        UUID_PK,
        ForeignKey("identity.people.person_id"),
        nullable=False,
    ),
    Column("reviewed_display_label", String(255), nullable=False),
    Column("person_snapshot_digest", String(64), nullable=False),
    Column("proposal_digest", String(64), nullable=False, unique=True),
    Column("state", String(32), nullable=False, server_default="ready"),
    Column("stage_receipt_digest", String(64), nullable=False),
    Column("staged_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True)),
    Column(
        "result_principal_id",
        UUID_PK,
        ForeignKey("identity.principals.principal_id"),
    ),
    Column(
        "confirmation_artifact_id",
        UUID_PK,
        ForeignKey("identity.confirmation_artifacts.artifact_id"),
    ),
    UniqueConstraint("request_id", name="binding_proposal_request_once"),
    UniqueConstraint(
        "confirmation_artifact_id", name="binding_proposal_confirmation_once"
    ),
    ForeignKeyConstraint(
        ["request_id", "ha_user_id"],
        [
            "identity.principal_binding_requests.request_id",
            "identity.principal_binding_requests.ha_user_id",
        ],
        name="binding_proposal_request_user",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "proposal_digest ~ '^[0-9a-f]{64}$' AND "
        "person_snapshot_digest ~ '^[0-9a-f]{64}$' AND "
        "stage_receipt_digest ~ '^[0-9a-f]{64}$'",
        name="binding_proposal_digests",
    ),
    CheckConstraint(
        "btrim(reviewed_display_label) <> ''",
        name="binding_proposal_reviewed_label",
    ),
    CheckConstraint(
        "state IN ('ready','consumed','cancelled','expired')",
        name="binding_proposal_state",
    ),
    CheckConstraint(
        "expires_at > staged_at AND "
        "(consumed_at IS NULL OR "
        "(consumed_at >= staged_at AND consumed_at <= expires_at))",
        name="binding_proposal_temporal_order",
    ),
    CheckConstraint(
        "(state = 'ready' AND consumed_at IS NULL AND "
        "result_principal_id IS NULL AND confirmation_artifact_id IS NULL) OR "
        "(state = 'consumed' AND consumed_at IS NOT NULL AND "
        "result_principal_id IS NOT NULL AND confirmation_artifact_id IS NOT NULL) OR "
        "(state IN ('cancelled','expired') AND consumed_at IS NULL AND "
        "result_principal_id IS NULL AND confirmation_artifact_id IS NULL)",
        name="binding_proposal_state_shape",
    ),
    schema="identity",
)
Index(
    "uq_principal_binding_proposals_ready_user",
    principal_binding_proposals.c.ha_user_id,
    unique=True,
    postgresql_where=principal_binding_proposals.c.state == "ready",
)
Index(
    "uq_principal_binding_proposals_ready_person",
    principal_binding_proposals.c.person_id,
    unique=True,
    postgresql_where=principal_binding_proposals.c.state == "ready",
)

source_entity_bindings = Table(
    "source_entity_bindings",
    metadata,
    Column("binding_id", UUID_PK, primary_key=True),
    Column("source_system", String(64), nullable=False),
    Column("entity_id", String(255), nullable=False),
    Column(
        "principal_id",
        UUID_PK,
        ForeignKey("identity.principals.principal_id"),
        nullable=False,
    ),
    Column(
        "person_id", UUID_PK, ForeignKey("identity.people.person_id"), nullable=False
    ),
    Column(
        "confirmed_by_principal_id",
        UUID_PK,
        ForeignKey("identity.principals.principal_id"),
        nullable=False,
    ),
    Column("confirmation_artifact_id", UUID_PK, nullable=False),
    Column("confirmed_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    Column("revoked_at", DateTime(timezone=True)),
    UniqueConstraint("source_system", "entity_id", name="source_entity_identity"),
    schema="identity",
)

edge_privacy_blocks = Table(
    "edge_privacy_blocks",
    metadata,
    Column("block_id", UUID_PK, primary_key=True),
    Column("source_system", String(64), nullable=False),
    Column("entity_id", String(255), nullable=False),
    Column("reason_code", String(64), nullable=False),
    Column(
        "person_id", UUID_PK, ForeignKey("identity.people.person_id"), nullable=False
    ),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    UniqueConstraint("source_system", "entity_id", name="edge_privacy_block_entity"),
    CheckConstraint(
        "reason_code IN ('do_not_track','ignored','auto_expired')",
        name="edge_privacy_block_reason",
    ),
    schema="identity",
)

edge_privacy_user_blocks = Table(
    "edge_privacy_user_blocks",
    metadata,
    Column("block_id", UUID_PK, primary_key=True),
    Column("ha_user_id", String(64), nullable=False, unique=True),
    Column("reason_code", String(64), nullable=False),
    Column(
        "person_id", UUID_PK, ForeignKey("identity.people.person_id"), nullable=False
    ),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    CheckConstraint(
        "reason_code IN ('do_not_track','ignored','auto_expired')",
        name="edge_privacy_user_block_reason",
    ),
    schema="identity",
)

aliases = Table(
    "aliases",
    metadata,
    Column("alias_id", UUID_PK, primary_key=True),
    Column(
        "person_id", UUID_PK, ForeignKey("identity.people.person_id"), nullable=False
    ),
    Column("alias", String(255), nullable=False),
    Column("normalized_alias", String(255), nullable=False),
    Column("alias_kind", String(32), nullable=False, server_default="nickname"),
    Column("source_artifact_id", UUID_PK),
    Column("source_ref", String(255)),
    Column("source_version", Integer),
    Column("source_snapshot_sha256", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    UniqueConstraint("person_id", "normalized_alias", name="person_alias"),
    UniqueConstraint("normalized_alias", name="global_normalized_alias"),
    CheckConstraint("alias_kind IN ('name','nickname')", name="alias_kind"),
    CheckConstraint(
        "source_snapshot_sha256 IS NULL OR source_snapshot_sha256 ~ '^[0-9a-f]{64}$'",
        name="alias_source_sha256",
    ),
    schema="identity",
)

external_recognition_bindings = Table(
    "external_recognition_bindings",
    metadata,
    Column("binding_id", UUID_PK, primary_key=True),
    Column(
        "person_id", UUID_PK, ForeignKey("identity.people.person_id"), nullable=False
    ),
    Column("external_system", String(64), nullable=False),
    Column("external_id", String(255), nullable=False),
    Column("status", String(32), nullable=False),
    Column("source_ref", String(255), nullable=False),
    Column("source_version", Integer, nullable=False),
    Column("source_snapshot_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    UniqueConstraint("external_system", "external_id", name="recognition_external_id"),
    CheckConstraint("external_system IN ('frigate')", name="recognition_system"),
    CheckConstraint("status IN ('active','retired')", name="recognition_status"),
    CheckConstraint(
        "source_snapshot_sha256 ~ '^[0-9a-f]{64}$'",
        name="recognition_source_sha256",
    ),
    schema="identity",
)

privacy_directives = Table(
    "privacy_directives",
    metadata,
    Column("directive_id", UUID_PK, primary_key=True),
    Column(
        "person_id", UUID_PK, ForeignKey("identity.people.person_id"), nullable=False
    ),
    Column("directive", String(32), nullable=False),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("expires_at", DateTime(timezone=True)),
    Column("source_artifact_id", UUID_PK),
    Column("source_ref", String(255)),
    Column("source_version", Integer),
    Column("source_snapshot_sha256", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    UniqueConstraint("person_id", "directive", name="person_directive"),
    CheckConstraint(
        "directive IN ('do_not_track','ignored','silent','private','auto_expire')",
        name="privacy_directive",
    ),
    CheckConstraint(
        "(directive = 'auto_expire' AND expires_at IS NOT NULL) OR "
        "(directive <> 'auto_expire' AND expires_at IS NULL)",
        name="privacy_expiry_shape",
    ),
    CheckConstraint(
        "source_snapshot_sha256 IS NULL OR source_snapshot_sha256 ~ '^[0-9a-f]{64}$'",
        name="privacy_source_sha256",
    ),
    schema="identity",
)

legacy_role_labels = Table(
    "legacy_role_labels",
    metadata,
    Column("label_id", UUID_PK, primary_key=True),
    Column(
        "person_id", UUID_PK, ForeignKey("identity.people.person_id"), nullable=False
    ),
    Column("role_label", String(64), nullable=False),
    Column("perspective", String(255), nullable=False, server_default="unknown"),
    Column("source_ref", String(255), nullable=False),
    Column("source_version", Integer),
    Column("source_snapshot_sha256", String(64), nullable=False),
    Column("imported_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    UniqueConstraint("person_id", "source_ref", name="legacy_person_role"),
    schema="identity",
)

legacy_relationship_candidates = Table(
    "legacy_relationship_candidates",
    metadata,
    Column("candidate_id", UUID_PK, primary_key=True),
    Column(
        "from_person_id",
        UUID_PK,
        ForeignKey("identity.people.person_id"),
        nullable=False,
    ),
    Column(
        "to_person_id", UUID_PK, ForeignKey("identity.people.person_id"), nullable=False
    ),
    Column("relationship_label", String(64), nullable=False),
    Column("relationship_status", String(16), nullable=False),
    Column("perspective", String(32), nullable=False, server_default="unknown"),
    Column("authoritative", Boolean, nullable=False, server_default=text("false")),
    Column("source_ref", String(255), nullable=False, unique=True),
    Column("source_snapshot_sha256", String(64), nullable=False),
    Column("imported_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    CheckConstraint(
        "from_person_id <> to_person_id", name="legacy_relationship_not_self"
    ),
    CheckConstraint(
        "relationship_status IN ('active','ended','paused')",
        name="legacy_relationship_status",
    ),
    CheckConstraint("perspective = 'unknown'", name="legacy_relationship_perspective"),
    CheckConstraint("authoritative = false", name="legacy_relationship_not_authority"),
    CheckConstraint(
        "source_snapshot_sha256 ~ '^[0-9a-f]{64}$'",
        name="legacy_relationship_source_sha256",
    ),
    schema="identity",
)

places = Table(
    "places",
    metadata,
    Column("place_id", UUID_PK, primary_key=True),
    Column("place_type", String(32), nullable=False),
    Column("canonical_name", String(255)),
    Column("parent_place_id", UUID_PK, ForeignKey("knowledge.places.place_id")),
    Column(
        "owner_principal_id",
        UUID_PK,
        ForeignKey("identity.principals.principal_id"),
        nullable=False,
    ),
    Column("privacy_scope", String(32), nullable=False, server_default="private"),
    Column(
        "travel_greeting_eligible",
        Boolean,
        nullable=False,
        server_default=text("false"),
    ),
    Column("status", String(32), nullable=False, server_default="active"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    CheckConstraint(
        "place_type IN ('locality','property','room','other')", name="place_type"
    ),
    CheckConstraint(
        "status IN ('provisional','active','superseded','erased')", name="place_status"
    ),
    schema="knowledge",
)

place_locators = Table(
    "place_locators",
    metadata,
    Column("locator_id", UUID_PK, primary_key=True),
    Column(
        "place_id", UUID_PK, ForeignKey("knowledge.places.place_id"), nullable=False
    ),
    Column("ciphertext", LargeBinary, nullable=False),
    Column("nonce", LargeBinary, nullable=False),
    Column("key_id", String(128), nullable=False),
    Column("locator_sha256", String(64), nullable=False),
    Column("radius_m", Integer, nullable=False),
    Column("valid_range", TSTZRANGE, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    CheckConstraint("radius_m BETWEEN 1 AND 50000", name="locator_radius"),
    schema="knowledge",
)

visits = Table(
    "visits",
    metadata,
    Column("visit_id", UUID_PK, primary_key=True),
    Column(
        "principal_id",
        UUID_PK,
        ForeignKey("identity.principals.principal_id"),
        nullable=False,
    ),
    Column("place_id", UUID_PK, ForeignKey("knowledge.places.place_id")),
    Column("first_root_observation_id", UUID_PK, nullable=False),
    Column("observed_range", TSTZRANGE, nullable=False),
    Column("state", String(32), nullable=False),
    Column("freshness", String(32), nullable=False),
    Column("coverage", String(32), nullable=False),
    Column("anchor_ciphertext", LargeBinary, nullable=False),
    Column("anchor_nonce", LargeBinary, nullable=False),
    Column("anchor_sha256", String(64), nullable=False),
    Column("anchor_kind", String(32), nullable=False, server_default="specific"),
    Column("anchor_radius_m", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    UniqueConstraint(
        "principal_id", "first_root_observation_id", name="stable_visit_identity"
    ),
    CheckConstraint(
        "state IN ('candidate','visiting','departed','conflicted')", name="visit_state"
    ),
    CheckConstraint(
        "(anchor_kind = 'specific' AND anchor_radius_m BETWEEN 1 AND 100) OR "
        "(anchor_kind = 'locality' AND anchor_radius_m BETWEEN 1 AND 250)",
        name="visit_anchor_kind_radius",
    ),
    schema="knowledge",
)

memory_transactions = Table(
    "memory_transactions",
    metadata,
    Column("transaction_id", UUID_PK, primary_key=True),
    Column(
        "principal_id",
        UUID_PK,
        ForeignKey("identity.principals.principal_id"),
        nullable=False,
    ),
    Column("visit_id", UUID_PK, ForeignKey("knowledge.visits.visit_id")),
    Column("kind", String(64), nullable=False),
    Column("state", String(32), nullable=False),
    Column("exact_text_ciphertext", LargeBinary),
    Column("exact_text_nonce", LargeBinary),
    Column("exact_text_sha256", String(64)),
    Column("candidate", JSONB, nullable=False),
    Column("preview", JSONB, nullable=False),
    Column(
        "verifier_results", JSONB, nullable=False, server_default=text("'[]'::jsonb")
    ),
    Column("policy_version", String(128), nullable=False),
    Column("policy_digest", String(64), nullable=False),
    Column("confirmation_digest", String(64)),
    Column("confirmed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    CheckConstraint(
        "state IN ('draft','verifying','needs_confirmation','quarantined','rejected','committed','cancelled')",
        name="memory_state",
    ),
    schema="knowledge",
)

fact_versions = Table(
    "fact_versions",
    metadata,
    Column("fact_version_id", UUID_PK, primary_key=True),
    Column("fact_id", UUID_PK, nullable=False),
    Column("version", Integer, nullable=False),
    Column("subject_type", String(64), nullable=False),
    Column("subject_id", UUID_PK, nullable=False),
    Column("predicate", String(128), nullable=False),
    Column("object", JSONB, nullable=False),
    Column(
        "perspective_principal_id",
        UUID_PK,
        ForeignKey("identity.principals.principal_id"),
    ),
    Column("valid_range", TSTZRANGE, nullable=False),
    Column("system_range", TSTZRANGE, nullable=False),
    Column("authority", String(32), nullable=False),
    Column("support", String(32), nullable=False),
    Column("contradiction", String(32), nullable=False),
    Column("freshness", String(32), nullable=False),
    Column("coverage", String(32), nullable=False),
    Column("resolution", String(32), nullable=False),
    Column("privacy_scope", String(32), nullable=False),
    Column(
        "memory_transaction_id",
        UUID_PK,
        ForeignKey("knowledge.memory_transactions.transaction_id"),
        nullable=False,
    ),
    Column("committed_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    UniqueConstraint("fact_id", "version", name="fact_version"),
    CheckConstraint(
        "authority IN ('explicit_subject','explicit_related_party',"
        "'authorized_administrator','sensor','derived_rule','model_proposal',"
        "'legacy_unverified')",
        name="fact_authority_axis",
    ),
    CheckConstraint(
        "support IN ('none','single_root','multiple_independent_roots',"
        "'explicit_authority')",
        name="fact_support_axis",
    ),
    CheckConstraint(
        "contradiction IN ('none','present','unresolved')",
        name="fact_contradiction_axis",
    ),
    CheckConstraint(
        "freshness IN ('fresh','aging','stale','not_applicable')",
        name="fact_freshness_axis",
    ),
    CheckConstraint(
        "coverage IN ('sufficient','partial','gap','not_applicable')",
        name="fact_coverage_axis",
    ),
    CheckConstraint(
        "resolution IN ('accepted','contested','unknown','suppressed')",
        name="fact_resolution_axis",
    ),
    CheckConstraint(
        "privacy_scope IN ('private','household')",
        name="fact_privacy_axis",
    ),
    schema="knowledge",
)
Index(
    "uq_active_scalar_fact",
    fact_versions.c.subject_id,
    fact_versions.c.predicate,
    fact_versions.c.perspective_principal_id,
    unique=True,
    postgresql_where=text(
        "predicate = 'place_social_descriptor' "
        "AND upper_inf(system_range) AND resolution = 'accepted'"
    ),
)
Index(
    "uq_active_parent_relationship",
    fact_versions.c.subject_id,
    fact_versions.c.object["person_id"].astext,
    fact_versions.c.perspective_principal_id,
    unique=True,
    postgresql_where=text(
        "predicate = 'parent_of' "
        "AND upper_inf(system_range) AND resolution = 'accepted'"
    ),
)

fact_support = Table(
    "fact_support",
    metadata,
    Column("support_id", UUID_PK, primary_key=True),
    Column(
        "fact_version_id",
        UUID_PK,
        ForeignKey("knowledge.fact_versions.fact_version_id"),
        nullable=False,
    ),
    Column("artifact_id", UUID_PK, nullable=False),
    Column("root_observation_id", UUID_PK),
    Column("dependency_domain", String(128)),
    Column("support_role", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    UniqueConstraint("fact_version_id", "artifact_id", name="fact_artifact_support"),
    schema="knowledge",
)

preferences = Table(
    "preferences",
    metadata,
    Column("preference_id", UUID_PK, primary_key=True),
    Column(
        "principal_id",
        UUID_PK,
        ForeignKey("identity.principals.principal_id"),
        nullable=False,
    ),
    Column("key", String(128), nullable=False),
    Column("enabled", Boolean, nullable=False, server_default=text("false")),
    Column("source_transaction_id", UUID_PK),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    UniqueConstraint("principal_id", "key", name="principal_preference"),
    schema="engagement",
)

initiatives = Table(
    "initiatives",
    metadata,
    Column("initiative_id", UUID_PK, primary_key=True),
    Column(
        "principal_id",
        UUID_PK,
        ForeignKey("identity.principals.principal_id"),
        nullable=False,
    ),
    Column("visit_id", UUID_PK, ForeignKey("knowledge.visits.visit_id")),
    Column(
        "source_fact_version_id",
        UUID_PK,
        ForeignKey("knowledge.fact_versions.fact_version_id"),
    ),
    Column("purpose", String(64), nullable=False),
    Column("dedupe_key", String(255), nullable=False, unique=True),
    Column("sensitivity", String(32), nullable=False),
    Column("allowed_surfaces", ARRAY(String(32)), nullable=False),
    Column("template_key", String(128), nullable=False),
    Column("state", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("claimed_at", DateTime(timezone=True)),
    Column("claimed_by_session", String(128)),
    Column("presented_at", DateTime(timezone=True)),
    Column("acknowledged_at", DateTime(timezone=True)),
    Column("suppression_reason", String(64)),
    CheckConstraint(
        "state IN ('pending','claimed','presented','acknowledged','suppressed','expired')",
        name="initiative_state",
    ),
    schema="engagement",
)
Index("ix_initiative_source_fact_version", initiatives.c.source_fact_version_id)

presentation_attempts = Table(
    "presentation_attempts",
    metadata,
    Column("attempt_id", UUID_PK, primary_key=True),
    Column(
        "initiative_id",
        UUID_PK,
        ForeignKey("engagement.initiatives.initiative_id"),
        nullable=False,
    ),
    Column("session_id", String(128), nullable=False),
    Column("surface", String(32), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    schema="engagement",
)

retrieval_blocks = Table(
    "retrieval_blocks",
    metadata,
    Column("block_id", UUID_PK, primary_key=True),
    Column("artifact_id", UUID_PK, nullable=False, unique=True),
    Column("erasure_request_id", UUID_PK, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    schema="privacy",
)

artifact_registry = Table(
    "artifact_registry",
    metadata,
    Column("artifact_id", UUID_PK, primary_key=True),
    Column("artifact_kind", String(64), nullable=False),
    Column("store", String(64), nullable=False),
    Column("external_ref", String(512)),
    Column("content_sha256", String(64)),
    Column(
        "owner_principal_id", UUID_PK, ForeignKey("identity.principals.principal_id")
    ),
    Column("retention_class", String(64), nullable=False),
    Column("status", String(32), nullable=False, server_default="active"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    schema="privacy",
)

erasure_requests = Table(
    "erasure_requests",
    metadata,
    Column("erasure_request_id", UUID_PK, primary_key=True),
    Column(
        "principal_id",
        UUID_PK,
        ForeignKey("identity.principals.principal_id"),
        nullable=False,
    ),
    Column("scope", JSONB, nullable=False),
    Column("state", String(32), nullable=False),
    Column("policy_digest", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    Column("completed_at", DateTime(timezone=True)),
    CheckConstraint(
        "state IN ('preview','blocked','running','ledger_pending','complete','partial','failed')",
        name="erasure_state",
    ),
    schema="privacy",
)

erasure_tasks = Table(
    "erasure_tasks",
    metadata,
    Column("task_id", UUID_PK, primary_key=True),
    Column(
        "erasure_request_id",
        UUID_PK,
        ForeignKey("privacy.erasure_requests.erasure_request_id"),
        nullable=False,
    ),
    Column("artifact_id", UUID_PK),
    Column("operation_code", String(64), nullable=False),
    Column("state", String(32), nullable=False),
    Column("residual_reason", String(128)),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    schema="privacy",
)

auto_expiry_schedules = Table(
    "auto_expiry_schedules",
    metadata,
    Column("schedule_id", UUID_PK, primary_key=True),
    Column(
        "person_id", UUID_PK, ForeignKey("identity.people.person_id"), nullable=False
    ),
    Column(
        "directive_id",
        UUID_PK,
        ForeignKey("identity.privacy_directives.directive_id"),
        nullable=False,
    ),
    Column("outbox_id", UUID_PK, nullable=False, unique=True),
    Column("due_at", DateTime(timezone=True), nullable=False),
    Column("state", String(32), nullable=False, server_default="scheduled"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    Column("completed_at", DateTime(timezone=True)),
    UniqueConstraint("person_id", name="person_auto_expiry_schedule"),
    CheckConstraint(
        "state IN ('scheduled','running','ledger_pending','complete','cancelled','failed')",
        name="auto_expiry_schedule_state",
    ),
    schema="privacy",
)

auto_expiry_receipts = Table(
    "auto_expiry_receipts",
    metadata,
    Column("receipt_id", UUID_PK, primary_key=True),
    Column("schedule_id", UUID_PK, nullable=False, unique=True),
    Column("outbox_id", UUID_PK, nullable=False, unique=True),
    Column("ledger_outbox_id", UUID_PK, nullable=False, unique=True),
    Column("person_id", UUID_PK, nullable=False, unique=True),
    Column("operation_codes", ARRAY(String(64)), nullable=False),
    Column("cascade_counts", JSONB, nullable=False),
    Column("residual_codes", ARRAY(String(64)), nullable=False),
    Column("receipt_sha256", String(64), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "receipt_sha256 ~ '^[0-9a-f]{64}$'", name="auto_expiry_receipt_sha256"
    ),
    schema="privacy",
)

outbox = Table(
    "outbox",
    metadata,
    Column("outbox_id", UUID_PK, primary_key=True),
    Column("topic", String(128), nullable=False),
    Column("aggregate_id", UUID_PK, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("state", String(32), nullable=False, server_default="pending"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("available_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    Column("claimed_at", DateTime(timezone=True)),
    Column("claim_token", UUID_PK),
    Column("last_error_code", String(64)),
    Column("completed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    CheckConstraint(
        "state IN ('pending','running','complete','failed')", name="outbox_state"
    ),
    schema="operations",
)
Index("ix_outbox_claim", outbox.c.state, outbox.c.available_at)

erasure_ledger_state = Table(
    "erasure_ledger_state",
    metadata,
    Column("state_key", String(32), primary_key=True),
    Column("recorded_epoch", BigInteger, nullable=False, server_default="0"),
    Column("recorded_head_hash", String(64), nullable=False, server_default="0" * 64),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    CheckConstraint("recorded_epoch >= 0", name="ledger_epoch_nonnegative"),
    CheckConstraint("recorded_head_hash ~ '^[0-9a-f]{64}$'", name="ledger_head_hash"),
    schema="operations",
)

erasure_replay_receipts = Table(
    "erasure_replay_receipts",
    metadata,
    Column("ledger_epoch", BigInteger, primary_key=True),
    Column("outbox_id", UUID_PK, nullable=False, unique=True),
    Column("erasure_request_id", UUID_PK, nullable=False),
    Column("record_hash", String(64), nullable=False),
    Column("record_digest", String(64), nullable=False),
    Column("applied_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    CheckConstraint("ledger_epoch > 0", name="replay_epoch_positive"),
    CheckConstraint("record_hash ~ '^[0-9a-f]{64}$'", name="replay_record_hash"),
    CheckConstraint("record_digest ~ '^[0-9a-f]{64}$'", name="replay_record_digest"),
    schema="operations",
)

policy_digests = Table(
    "policy_digests",
    metadata,
    Column("policy_version", String(128), primary_key=True),
    Column("policy_digest", String(64), nullable=False),
    Column("activated_at", DateTime(timezone=True), nullable=False, server_default=NOW),
    schema="operations",
)


worker_maintenance_state = Table(
    "worker_maintenance_state",
    metadata,
    Column("state_key", String(32), primary_key=True),
    Column("worker_instance_id", UUID_PK, nullable=False),
    Column("database_started_at", DateTime(timezone=True), nullable=False),
    Column("kernel_version", String(128), nullable=False),
    Column("state", String(16), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("heartbeat_at", DateTime(timezone=True), nullable=False),
    Column("heartbeat_sequence", BigInteger, nullable=False),
    Column("maintenance_attempted_at", DateTime(timezone=True)),
    Column("maintenance_succeeded_at", DateTime(timezone=True)),
    Column("success_sequence", BigInteger, nullable=False),
    Column("consecutive_failures", Integer, nullable=False),
    Column("last_error_code", String(64)),
    Column("stopped_at", DateTime(timezone=True)),
    Column("spool_rows_pruned", BigInteger),
    Column("binding_retention_receipt", JSONB),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("state_key = 'durable_worker'", name="singleton"),
    CheckConstraint(
        "substring(worker_instance_id::text from 15 for 1) IN ('4','7') "
        "AND substring(worker_instance_id::text from 20 for 1) IN "
        "('8','9','a','b')",
        name="random_instance",
    ),
    CheckConstraint(
        "kernel_version = 'worker-maintenance-cycle-v1'",
        name="kernel_version",
    ),
    CheckConstraint(
        "state IN ('starting','running','degraded','stopping')",
        name="state_value",
    ),
    CheckConstraint(
        "heartbeat_sequence >= 1 AND success_sequence >= 0 "
        "AND consecutive_failures >= 0",
        name="sequences",
    ),
    CheckConstraint(
        "database_started_at <= started_at "
        "AND started_at <= heartbeat_at "
        "AND heartbeat_at <= updated_at "
        "AND (maintenance_attempted_at IS NULL "
        "OR (maintenance_attempted_at >= started_at "
        "AND maintenance_attempted_at <= updated_at)) "
        "AND (maintenance_succeeded_at IS NULL "
        "OR (maintenance_succeeded_at >= started_at "
        "AND maintenance_succeeded_at <= updated_at)) "
        "AND (stopped_at IS NULL "
        "OR (stopped_at >= started_at AND stopped_at <= updated_at))",
        name="time_order",
    ),
    CheckConstraint(
        "(success_sequence = 0 "
        "AND maintenance_succeeded_at IS NULL "
        "AND spool_rows_pruned IS NULL "
        "AND binding_retention_receipt IS NULL) "
        "OR (success_sequence > 0 "
        "AND maintenance_succeeded_at IS NOT NULL "
        "AND spool_rows_pruned IS NOT NULL "
        "AND spool_rows_pruned >= 0 "
        "AND binding_retention_receipt IS NOT NULL)",
        name="success_shape",
    ),
    CheckConstraint(
        "(consecutive_failures = 0 AND last_error_code IS NULL) "
        "OR (consecutive_failures > 0 AND last_error_code IN "
        "('runtime_spool_prune_failed','binding_retention_failed',"
        "'worker_loop_failed'))",
        name="failure_shape",
    ),
    CheckConstraint(
        "(state = 'starting' AND success_sequence = 0 "
        "AND maintenance_attempted_at IS NULL "
        "AND consecutive_failures = 0 AND stopped_at IS NULL) "
        "OR (state = 'running' AND success_sequence > 0 "
        "AND maintenance_attempted_at IS NOT NULL "
        "AND maintenance_succeeded_at IS NOT NULL "
        "AND maintenance_attempted_at <= maintenance_succeeded_at "
        "AND consecutive_failures = 0 AND stopped_at IS NULL) "
        "OR (state = 'degraded' AND consecutive_failures > 0 "
        "AND maintenance_attempted_at IS NOT NULL AND stopped_at IS NULL) "
        "OR (state = 'stopping' AND stopped_at IS NOT NULL)",
        name="state_shape",
    ),
    CheckConstraint(
        "binding_retention_receipt IS NULL OR ("
        "jsonb_typeof(binding_retention_receipt) = 'object' "
        "AND binding_retention_receipt ?& ARRAY["
        "'proposals_expired','staged_requests_expired',"
        "'pending_requests_expired','requests_expired',"
        "'proposals_purged','requests_purged'] "
        "AND binding_retention_receipt - ARRAY["
        "'proposals_expired','staged_requests_expired',"
        "'pending_requests_expired','requests_expired',"
        "'proposals_purged','requests_purged'] = '{}'::jsonb "
        "AND jsonb_typeof(binding_retention_receipt->"
        "'proposals_expired') = 'number' "
        "AND (binding_retention_receipt->>'proposals_expired') "
        "~ '^[0-9]+$' "
        "AND jsonb_typeof(binding_retention_receipt->"
        "'staged_requests_expired') = 'number' "
        "AND (binding_retention_receipt->>'staged_requests_expired') "
        "~ '^[0-9]+$' "
        "AND jsonb_typeof(binding_retention_receipt->"
        "'pending_requests_expired') = 'number' "
        "AND (binding_retention_receipt->>'pending_requests_expired') "
        "~ '^[0-9]+$' "
        "AND jsonb_typeof(binding_retention_receipt->"
        "'requests_expired') = 'number' "
        "AND (binding_retention_receipt->>'requests_expired') "
        "~ '^[0-9]+$' "
        "AND jsonb_typeof(binding_retention_receipt->"
        "'proposals_purged') = 'number' "
        "AND (binding_retention_receipt->>'proposals_purged') "
        "~ '^[0-9]+$' "
        "AND jsonb_typeof(binding_retention_receipt->"
        "'requests_purged') = 'number' "
        "AND (binding_retention_receipt->>'requests_purged') "
        "~ '^[0-9]+$' "
        "AND (binding_retention_receipt->>'requests_expired')::numeric = "
        "(binding_retention_receipt->>'staged_requests_expired')::numeric + "
        "(binding_retention_receipt->>'pending_requests_expired')::numeric)",
        name="receipt_shape",
    ),
    prefixes=["UNLOGGED"],
    schema="operations",
)


rollout_authorizations = Table(
    "rollout_authorizations",
    metadata,
    Column("authorization_id", UUID_PK, primary_key=True),
    Column("operator_request_id", UUID_PK, nullable=False, unique=True),
    Column("from_mode", String(32), nullable=False),
    Column("to_mode", String(32), nullable=False),
    Column("rule_version", String(128), nullable=False),
    Column("policy_version", String(128), nullable=False),
    Column("policy_digest", String(64), nullable=False),
    Column("input_digest", String(64), nullable=False),
    Column("worker_instance_id", UUID_PK, nullable=False),
    Column("worker_success_sequence", BigInteger, nullable=False),
    Column("worker_kernel_version", String(128), nullable=False),
    Column(
        "worker_maintenance_succeeded_at",
        DateTime(timezone=True),
        nullable=False,
    ),
    Column("worker_proof_digest", String(64), nullable=False),
    Column("readiness_evaluated_at", DateTime(timezone=True), nullable=False),
    Column(
        "authorized_at", DateTime(timezone=True), nullable=False, server_default=NOW
    ),
    UniqueConstraint("from_mode", "to_mode", name="rollout_transition_once"),
    CheckConstraint(
        "from_mode = 'record_only' AND to_mode = 'shadow'",
        name="rollout_forward_transition",
    ),
    CheckConstraint(
        "policy_digest ~ '^[0-9a-f]{64}$'",
        name="rollout_policy_digest",
    ),
    CheckConstraint(
        "input_digest ~ '^[0-9a-f]{64}$'",
        name="rollout_input_digest",
    ),
    CheckConstraint(
        "substring(worker_instance_id::text from 15 for 1) IN ('4','7') "
        "AND substring(worker_instance_id::text from 20 for 1) IN "
        "('8','9','a','b')",
        name="worker_random_instance",
    ),
    CheckConstraint(
        "worker_success_sequence > 0 "
        "AND worker_kernel_version = 'worker-maintenance-cycle-v1' "
        "AND worker_proof_digest ~ '^[0-9a-f]{64}$'",
        name="worker_proof_shape",
    ),
    CheckConstraint(
        "worker_maintenance_succeeded_at <= readiness_evaluated_at "
        "AND readiness_evaluated_at <= authorized_at",
        name="worker_proof_time",
    ),
    schema="operations",
)


# Phase 3 identity-migration authority foundation. Commitments below are
# domain-separated keyed HMACs, never plain hashes of private source values.
# No table provides an adoption path for pre-0007 legacy-source projections.
reviewed_identity_migration_runs = Table(
    "reviewed_identity_migration_runs",
    metadata,
    Column("run_id", UUID_PK, primary_key=True),
    Column("operator_request_id", UUID_PK, nullable=False, unique=True),
    Column("contract_version", String(64), nullable=False),
    Column("source_schema_version", Integer, nullable=False),
    Column("source_projection_contract_version", String(64), nullable=False),
    Column("importer_version", String(64), nullable=False),
    Column("canonicalization_version", String(64), nullable=False),
    Column("projection_version", String(64), nullable=False),
    Column("shadow_rule_version", String(128), nullable=False),
    Column("commitment_algorithm", String(32), nullable=False),
    Column("commitment_key_fingerprint", String(64), nullable=False),
    Column("commitment_key_epoch", BigInteger, nullable=False),
    Column("source_item_count", Integer, nullable=False),
    Column("decision_count", Integer, nullable=False),
    Column("logical_source_manifest_commitment", String(64), nullable=False),
    Column("projection_manifest_commitment", String(64), nullable=False),
    Column("source_projection_contract_digest", String(64), nullable=False),
    Column("review_receipt_commitment", String(64), nullable=False, unique=True),
    Column("policy_version", String(128), nullable=False),
    Column("policy_digest", String(64), nullable=False),
    Column(
        "shadow_authorization_id",
        UUID_PK,
        ForeignKey(
            "operations.rollout_authorizations.authorization_id",
            name="fk_identity_migration_run_shadow_authorization",
        ),
        nullable=False,
    ),
    Column("release_manifest_digest", String(64), nullable=False),
    Column("migration_tool_bundle_digest", String(64), nullable=False),
    Column("core_oci_manifest_digest", String(64), nullable=False),
    Column("core_schema_digest", String(64), nullable=False),
    Column("core_capability_digest", String(64), nullable=False),
    Column("signature_algorithm", String(16), nullable=False),
    Column("signing_key_fingerprint", String(64), nullable=False),
    Column("review_signature", String(128), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        f"{_uuidv7_shape('run_id')} AND {_uuidv7_shape('operator_request_id')}",
        name="uuidv7_ids",
    ),
    CheckConstraint(
        "contract_version = 'reviewed-identity-migration-run-v1' "
        "AND source_schema_version = 1 "
        "AND source_projection_contract_version = "
        "'legacy-identity-source-projection-v1' "
        "AND importer_version = 'legacy-identity-importer-v1' "
        "AND canonicalization_version = 'identity-canonicalization-v1' "
        "AND projection_version = 'semantic-people-projection-v1' "
        "AND shadow_rule_version = 'record-only-envelope-worker-gate-v3' "
        "AND commitment_algorithm = 'hmac-sha256-v1'",
        name="fixed_contract_versions",
    ),
    CheckConstraint(
        "source_item_count BETWEEN 1 AND 10000 "
        "AND decision_count BETWEEN 1 AND 10000",
        name="admission_caps",
    ),
    CheckConstraint(
        _hex64(
            "logical_source_manifest_commitment",
            "projection_manifest_commitment",
            "source_projection_contract_digest",
            "review_receipt_commitment",
            "commitment_key_fingerprint",
            "policy_digest",
            "release_manifest_digest",
            "migration_tool_bundle_digest",
            "core_oci_manifest_digest",
            "core_schema_digest",
            "core_capability_digest",
            "signing_key_fingerprint",
        )
        + " AND review_signature ~ '^[0-9a-f]{128}$'",
        name="signed_commitment_shape",
    ),
    CheckConstraint(
        "signature_algorithm = 'ed25519' "
        "AND commitment_key_epoch > 0 "
        "AND policy_version ~ '^[a-z0-9][a-z0-9._-]{0,127}$'",
        name="signature_and_policy_shape",
    ),
    CheckConstraint(
        "expires_at > created_at " "AND expires_at <= created_at + interval '24 hours'",
        name="database_expiry",
    ),
    schema="operations",
)

reviewed_identity_migration_source_items = Table(
    "reviewed_identity_migration_source_items",
    metadata,
    Column("source_item_id", UUID_PK, primary_key=True),
    Column("run_id", UUID_PK, nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("source_table_kind", String(32), nullable=False),
    Column("row_key_commitment", String(64), nullable=False),
    Column("allowed_projection_commitment", String(64), nullable=False),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    ForeignKeyConstraint(
        ["run_id"],
        ["operations.reviewed_identity_migration_runs.run_id"],
        name="fk_identity_migration_source_item_run",
    ),
    UniqueConstraint("run_id", "ordinal", name="identity_source_item_ordinal"),
    UniqueConstraint("run_id", "source_item_id", name="identity_source_item_id"),
    UniqueConstraint("run_id", "row_key_commitment", name="identity_source_row_key"),
    CheckConstraint(_uuidv7_shape("source_item_id"), name="uuidv7_id"),
    CheckConstraint("ordinal BETWEEN 0 AND 9999", name="ordinal_cap"),
    CheckConstraint(
        "source_table_kind IN ('schema_meta','identities','identity_aliases',"
        "'enrollments','relationships')",
        name="source_table_kind",
    ),
    CheckConstraint(
        _hex64("row_key_commitment", "allowed_projection_commitment"),
        name="commitment_shape",
    ),
    schema="operations",
)

reviewed_identity_migration_decisions = Table(
    "reviewed_identity_migration_decisions",
    metadata,
    Column("decision_id", UUID_PK, primary_key=True),
    Column("run_id", UUID_PK, nullable=False),
    Column("source_item_id", UUID_PK, nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("decision_kind", String(48), nullable=False),
    Column("disposition", String(32), nullable=False),
    Column("candidate_commitment", String(64)),
    Column("canonical_apply_decision_id", UUID_PK),
    Column("canonical_apply_disposition", String(32)),
    Column("decision_commitment", String(64), nullable=False, unique=True),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    ForeignKeyConstraint(
        ["run_id", "source_item_id"],
        [
            "operations.reviewed_identity_migration_source_items.run_id",
            "operations.reviewed_identity_migration_source_items.source_item_id",
        ],
        name="fk_identity_migration_decision_source_item",
    ),
    UniqueConstraint("run_id", "ordinal", name="identity_decision_ordinal"),
    UniqueConstraint("run_id", "decision_id", name="identity_decision_id"),
    UniqueConstraint(
        "run_id",
        "decision_id",
        "decision_kind",
        "disposition",
        "candidate_commitment",
        name="identity_decision_exact",
    ),
    ForeignKeyConstraint(
        [
            "run_id",
            "canonical_apply_decision_id",
            "decision_kind",
            "canonical_apply_disposition",
            "candidate_commitment",
        ],
        [
            "operations.reviewed_identity_migration_decisions.run_id",
            "operations.reviewed_identity_migration_decisions.decision_id",
            "operations.reviewed_identity_migration_decisions.decision_kind",
            "operations.reviewed_identity_migration_decisions.disposition",
            "operations.reviewed_identity_migration_decisions.candidate_commitment",
        ],
        name="fk_identity_decision_canonical_apply",
    ),
    CheckConstraint(_uuidv7_shape("decision_id"), name="uuidv7_id"),
    CheckConstraint("ordinal BETWEEN 0 AND 9999", name="ordinal_cap"),
    CheckConstraint(
        "decision_kind IN ('person','privacy_directive','person_status','alias',"
        "'recognition_binding','legacy_role_candidate',"
        "'legacy_relationship_candidate','explicit_omission')",
        name="decision_kind",
    ),
    CheckConstraint(
        "disposition IN ('apply','privacy_suppressed','out_of_scope_by_rule',"
        "'coalesced_duplicate')",
        name="disposition",
    ),
    CheckConstraint(
        "decision_commitment ~ '^[0-9a-f]{64}$' "
        "AND (candidate_commitment IS NULL "
        "OR candidate_commitment ~ '^[0-9a-f]{64}$')",
        name="commitment_shape",
    ),
    CheckConstraint(
        "(disposition = 'apply' AND decision_kind <> 'explicit_omission' "
        "AND candidate_commitment IS NOT NULL "
        "AND canonical_apply_decision_id IS NULL "
        "AND canonical_apply_disposition IS NULL) "
        "OR (disposition IN ('privacy_suppressed','out_of_scope_by_rule') "
        "AND decision_kind = 'explicit_omission' "
        "AND candidate_commitment IS NULL "
        "AND canonical_apply_decision_id IS NULL "
        "AND canonical_apply_disposition IS NULL) "
        "OR (disposition = 'coalesced_duplicate' "
        "AND decision_kind <> 'explicit_omission' "
        "AND candidate_commitment IS NOT NULL "
        "AND canonical_apply_decision_id IS NOT NULL "
        "AND canonical_apply_decision_id <> decision_id "
        "AND canonical_apply_disposition = 'apply')",
        name="decision_shape",
    ),
    schema="operations",
)

reviewed_identity_migration_item_receipts = Table(
    "reviewed_identity_migration_item_receipts",
    metadata,
    Column("receipt_id", UUID_PK, primary_key=True),
    Column("run_id", UUID_PK, nullable=False),
    Column("decision_id", UUID_PK, nullable=False),
    Column("decision_kind", String(48), nullable=False),
    Column("decision_disposition", String(32), nullable=False),
    Column("candidate_commitment", String(64), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("projection_table_kind", String(64), nullable=False),
    Column("projection_ref_commitment", String(64), nullable=False),
    Column("projection_commitment", String(64), nullable=False),
    Column("receipt_commitment", String(64), nullable=False, unique=True),
    Column("database_transaction_id", BigInteger, nullable=False),
    Column(
        "applied_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    ForeignKeyConstraint(
        [
            "run_id",
            "decision_id",
            "decision_kind",
            "decision_disposition",
            "candidate_commitment",
        ],
        [
            "operations.reviewed_identity_migration_decisions.run_id",
            "operations.reviewed_identity_migration_decisions.decision_id",
            "operations.reviewed_identity_migration_decisions.decision_kind",
            "operations.reviewed_identity_migration_decisions.disposition",
            "operations.reviewed_identity_migration_decisions.candidate_commitment",
        ],
        name="fk_identity_migration_receipt_apply_decision",
    ),
    UniqueConstraint("run_id", "decision_id", name="identity_receipt_decision"),
    UniqueConstraint("run_id", "receipt_id", name="identity_receipt_id"),
    UniqueConstraint(
        "run_id",
        "receipt_id",
        "decision_id",
        "decision_kind",
        "projection_table_kind",
        "projection_ref_commitment",
        name="lineage_identity",
    ),
    UniqueConstraint(
        "run_id",
        "decision_kind",
        "projection_ref_commitment",
        name="identity_receipt_projection_ref",
    ),
    CheckConstraint(_uuidv7_shape("receipt_id"), name="uuidv7_id"),
    CheckConstraint(
        "decision_disposition = 'apply' "
        "AND outcome IN ('inserted_exact','replayed_exact')",
        name="apply_outcome",
    ),
    CheckConstraint(
        _hex64(
            "candidate_commitment",
            "projection_ref_commitment",
            "projection_commitment",
            "receipt_commitment",
        ),
        name="commitment_shape",
    ),
    CheckConstraint("database_transaction_id > 0", name="transaction_marker"),
    CheckConstraint(
        "(decision_kind IN ('person','person_status') "
        "AND projection_table_kind = 'identity.people') "
        "OR (decision_kind = 'alias' "
        "AND projection_table_kind = 'identity.aliases') "
        "OR (decision_kind = 'recognition_binding' "
        "AND projection_table_kind = 'identity.external_recognition_bindings') "
        "OR (decision_kind = 'privacy_directive' "
        "AND projection_table_kind = 'identity.privacy_directives') "
        "OR (decision_kind = 'legacy_role_candidate' "
        "AND projection_table_kind = 'identity.legacy_role_labels') "
        "OR (decision_kind = 'legacy_relationship_candidate' "
        "AND projection_table_kind = 'identity.legacy_relationship_candidates')",
        name="projection_table_for_kind",
    ),
    schema="operations",
)

# Dormant Phase 3 finalizer-lineage foundation. These normalized rows contain
# identifiers and keyed commitments only: no names, aliases, source refs,
# external identifiers, utterances, or relationship assertions. Revision 0009
# installs no writer and grants the finalizer roles no table privileges.
reviewed_identity_migration_projection_lineage = Table(
    "reviewed_identity_migration_projection_lineage",
    metadata,
    Column("lineage_id", UUID_PK, primary_key=True),
    Column("run_id", UUID_PK, nullable=False),
    Column("receipt_id", UUID_PK, nullable=False),
    Column("decision_id", UUID_PK, nullable=False),
    Column("decision_kind", String(48), nullable=False),
    Column("projection_table_kind", String(64), nullable=False),
    Column("projection_id", UUID_PK, nullable=False),
    Column("projection_ref_commitment", String(64), nullable=False),
    Column("lineage_commitment", String(64), nullable=False, unique=True),
    Column(
        "linked_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    ForeignKeyConstraint(
        [
            "run_id",
            "receipt_id",
            "decision_id",
            "decision_kind",
            "projection_table_kind",
            "projection_ref_commitment",
        ],
        [
            "operations.reviewed_identity_migration_item_receipts.run_id",
            "operations.reviewed_identity_migration_item_receipts.receipt_id",
            "operations.reviewed_identity_migration_item_receipts.decision_id",
            "operations.reviewed_identity_migration_item_receipts.decision_kind",
            "operations.reviewed_identity_migration_item_receipts.projection_table_kind",
            "operations.reviewed_identity_migration_item_receipts.projection_ref_commitment",
        ],
        name="fk_identity_projection_lineage_exact_receipt",
    ),
    UniqueConstraint(
        "run_id", "receipt_id", name="identity_projection_lineage_receipt"
    ),
    UniqueConstraint(
        "run_id", "lineage_id", name="identity_projection_lineage_identity"
    ),
    CheckConstraint(_uuidv7_shape("lineage_id"), name="uuidv7_id"),
    CheckConstraint(
        _hex64("projection_ref_commitment", "lineage_commitment"),
        name="commitment_shape",
    ),
    CheckConstraint(
        "(decision_kind IN ('person','person_status') "
        "AND projection_table_kind = 'identity.people') "
        "OR (decision_kind = 'alias' "
        "AND projection_table_kind = 'identity.aliases') "
        "OR (decision_kind = 'recognition_binding' "
        "AND projection_table_kind = 'identity.external_recognition_bindings') "
        "OR (decision_kind = 'privacy_directive' "
        "AND projection_table_kind = 'identity.privacy_directives') "
        "OR (decision_kind = 'legacy_role_candidate' "
        "AND projection_table_kind = 'identity.legacy_role_labels') "
        "OR (decision_kind = 'legacy_relationship_candidate' "
        "AND projection_table_kind = 'identity.legacy_relationship_candidates')",
        name="projection_table_for_kind",
    ),
    schema="operations",
)

reviewed_identity_migration_projection_subjects = Table(
    "reviewed_identity_migration_projection_subjects",
    metadata,
    Column(
        "lineage_id",
        UUID_PK,
        ForeignKey(
            "operations.reviewed_identity_migration_projection_lineage.lineage_id"
        ),
        primary_key=True,
    ),
    Column(
        "person_id",
        UUID_PK,
        ForeignKey("identity.people.person_id"),
        primary_key=True,
    ),
    Column("subject_role", String(16), primary_key=True),
    UniqueConstraint(
        "lineage_id", "subject_role", name="identity_projection_subject_role"
    ),
    CheckConstraint("subject_role IN ('primary','from','to')", name="subject_role"),
    schema="operations",
)

reviewed_identity_migration_finalizations = Table(
    "reviewed_identity_migration_finalizations",
    metadata,
    Column("finalization_id", UUID_PK, primary_key=True),
    Column("run_id", UUID_PK, nullable=False, unique=True),
    Column("contract_version", String(64), nullable=False),
    Column("verification_status", String(32), nullable=False),
    Column("authoritative", Boolean, nullable=False, server_default=text("false")),
    Column("source_item_count", Integer, nullable=False),
    Column("decision_count", Integer, nullable=False),
    Column("apply_decision_count", Integer, nullable=False),
    Column("receipt_count", Integer, nullable=False),
    Column("source_manifest_commitment", String(64), nullable=False),
    Column("projection_manifest_commitment", String(64), nullable=False),
    Column("decision_manifest_commitment", String(64), nullable=False),
    Column("receipt_set_commitment", String(64), nullable=False),
    Column("finalization_commitment", String(64), nullable=False, unique=True),
    Column("database_transaction_id", BigInteger, nullable=False),
    Column("signature_algorithm", String(16), nullable=False),
    Column("signing_key_fingerprint", String(64), nullable=False),
    Column("finalization_signature", String(128), nullable=False),
    Column(
        "finalized_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    ForeignKeyConstraint(
        ["run_id"],
        ["operations.reviewed_identity_migration_runs.run_id"],
        name="fk_identity_migration_finalization_run",
    ),
    UniqueConstraint(
        "run_id", "finalization_id", name="identity_finalization_identity"
    ),
    CheckConstraint(_uuidv7_shape("finalization_id"), name="uuidv7_id"),
    CheckConstraint(
        "contract_version = 'reviewed-identity-migration-finalization-v1' "
        "AND verification_status = 'candidate_unverified' "
        "AND authoritative = false",
        name="non_authoritative_contract",
    ),
    CheckConstraint(
        "source_item_count BETWEEN 1 AND 10000 "
        "AND decision_count BETWEEN 1 AND 10000 "
        "AND apply_decision_count BETWEEN 0 AND decision_count "
        "AND receipt_count = apply_decision_count",
        name="bounded_counts",
    ),
    CheckConstraint(
        _hex64(
            "source_manifest_commitment",
            "projection_manifest_commitment",
            "decision_manifest_commitment",
            "receipt_set_commitment",
            "finalization_commitment",
            "signing_key_fingerprint",
        )
        + " AND finalization_signature ~ '^[0-9a-f]{128}$'",
        name="signed_commitment_shape",
    ),
    CheckConstraint(
        "database_transaction_id > 0 AND signature_algorithm = 'ed25519'",
        name="transaction_and_signature",
    ),
    schema="operations",
)

# This is point-in-time evidence only. Revision 0007 deliberately has no
# physically-frozen/enforced-offline strength and cannot authorize cutover.
legacy_identity_writer_evidence = Table(
    "legacy_identity_writer_evidence",
    metadata,
    Column("evidence_id", UUID_PK, primary_key=True),
    Column("run_id", UUID_PK, nullable=False),
    Column("source_installation_id", UUID_PK, nullable=False),
    Column("semantic_generation", BigInteger, nullable=False),
    Column("source_projection_commitment", String(64), nullable=False),
    Column("evidence_strength", String(32), nullable=False),
    Column("integrity_result", String(24), nullable=False),
    Column("checkpoint_result", String(24), nullable=False),
    Column("journal_result", String(24), nullable=False),
    Column("legacy_context_cutoff_status", String(32), nullable=False),
    Column("release_manifest_digest", String(64), nullable=False),
    Column("freeze_kernel_build_digest", String(64), nullable=False),
    Column("evidence_commitment", String(64), nullable=False, unique=True),
    Column("signature_algorithm", String(16), nullable=False),
    Column("signing_key_fingerprint", String(64), nullable=False),
    Column("evidence_signature", String(128), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    ForeignKeyConstraint(
        ["run_id"],
        ["operations.reviewed_identity_migration_runs.run_id"],
        name="fk_legacy_identity_writer_evidence_run",
    ),
    UniqueConstraint("run_id", "evidence_id", name="legacy_writer_evidence_id"),
    CheckConstraint(
        f"{_uuidv7_shape('evidence_id')} AND "
        f"{_uuidv7_shape('source_installation_id')}",
        name="uuidv7_ids",
    ),
    CheckConstraint(
        "evidence_strength IN ('observed_stopped','operator_attested') "
        "AND integrity_result IN ('passed','failed','not_observed') "
        "AND checkpoint_result IN ('complete','incomplete','not_observed') "
        "AND journal_result IN ('clean','pending','not_observed') "
        "AND legacy_context_cutoff_status IN "
        "('observed_cutoff','operator_attested_cutoff','not_observed')",
        name="categorical_results",
    ),
    CheckConstraint(
        _hex64(
            "release_manifest_digest",
            "freeze_kernel_build_digest",
            "source_projection_commitment",
            "evidence_commitment",
            "signing_key_fingerprint",
        )
        + " AND evidence_signature ~ '^[0-9a-f]{128}$'",
        name="signed_commitment_shape",
    ),
    CheckConstraint(
        "semantic_generation > 0 AND signature_algorithm = 'ed25519' "
        "AND observed_at <= recorded_at",
        name="signature_and_time",
    ),
    schema="operations",
)

privacy_cutover_check_receipts = Table(
    "privacy_cutover_check_receipts",
    metadata,
    Column("check_id", UUID_PK, primary_key=True),
    Column("run_id", UUID_PK, nullable=False),
    Column("finalization_id", UUID_PK, nullable=False),
    Column("check_category", String(24), nullable=False),
    Column("check_result", String(16), nullable=False),
    Column("residual_code", String(32), nullable=False),
    Column("check_commitment", String(64), nullable=False, unique=True),
    Column("receipt_commitment", String(64), nullable=False, unique=True),
    Column("policy_digest", String(64), nullable=False),
    Column(
        "checked_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    ForeignKeyConstraint(
        ["run_id", "finalization_id"],
        [
            "operations.reviewed_identity_migration_finalizations.run_id",
            "operations.reviewed_identity_migration_finalizations.finalization_id",
        ],
        name="fk_privacy_check_identity_finalization",
    ),
    UniqueConstraint("run_id", "check_category", name="privacy_check_category"),
    UniqueConstraint(
        "run_id", "check_id", "check_category", name="privacy_check_identity"
    ),
    UniqueConstraint(
        "run_id",
        "check_id",
        "check_category",
        "check_result",
        "residual_code",
        name="privacy_check_exact_identity",
    ),
    CheckConstraint(_uuidv7_shape("check_id"), name="uuidv7_id"),
    CheckConstraint(
        "check_category IN "
        "('ingress','retrieval','prompt','initiative','export','edge_block')",
        name="check_category",
    ),
    CheckConstraint(
        "check_result IN ('passed','blocked') "
        "AND residual_code IN ('none','legacy_untracked',"
        "'backup_expiry_pending','external_deletion_pending',"
        "'coverage_unproven')",
        name="result_codes",
    ),
    CheckConstraint(
        "(check_result = 'passed' AND residual_code = 'none') "
        "OR (check_result = 'blocked' AND residual_code <> 'none')",
        name="result_residual_shape",
    ),
    CheckConstraint(
        _hex64(
            "check_commitment",
            "receipt_commitment",
            "policy_digest",
        ),
        name="commitment_shape",
    ),
    schema="operations",
)

semantic_authority_cutovers = Table(
    "semantic_authority_cutovers",
    metadata,
    Column("cutover_id", UUID_PK, primary_key=True),
    Column("run_id", UUID_PK, nullable=False, unique=True),
    Column("finalization_id", UUID_PK, nullable=False, unique=True),
    Column("writer_evidence_id", UUID_PK, nullable=False),
    Column("contract_version", String(64), nullable=False),
    Column("authority_status", String(32), nullable=False),
    Column("authoritative", Boolean, nullable=False, server_default=text("false")),
    Column("ingress_check_id", UUID_PK, nullable=False),
    Column("ingress_check_category", String(24), nullable=False),
    Column("retrieval_check_id", UUID_PK, nullable=False),
    Column("retrieval_check_category", String(24), nullable=False),
    Column("prompt_check_id", UUID_PK, nullable=False),
    Column("prompt_check_category", String(24), nullable=False),
    Column("initiative_check_id", UUID_PK, nullable=False),
    Column("initiative_check_category", String(24), nullable=False),
    Column("export_check_id", UUID_PK, nullable=False),
    Column("export_check_category", String(24), nullable=False),
    Column("edge_block_check_id", UUID_PK, nullable=False),
    Column("edge_block_check_category", String(24), nullable=False),
    Column("required_privacy_check_result", String(16), nullable=False),
    Column("required_privacy_residual_code", String(32), nullable=False),
    Column("privacy_check_set_commitment", String(64), nullable=False),
    Column("cutover_commitment", String(64), nullable=False, unique=True),
    Column("policy_digest", String(64), nullable=False),
    Column("signature_algorithm", String(16), nullable=False),
    Column("signing_key_fingerprint", String(64), nullable=False),
    Column("cutover_signature", String(128), nullable=False),
    Column(
        "attested_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    ForeignKeyConstraint(
        ["run_id", "finalization_id"],
        [
            "operations.reviewed_identity_migration_finalizations.run_id",
            "operations.reviewed_identity_migration_finalizations.finalization_id",
        ],
        name="fk_semantic_cutover_identity_finalization",
    ),
    ForeignKeyConstraint(
        ["run_id", "writer_evidence_id"],
        [
            "operations.legacy_identity_writer_evidence.run_id",
            "operations.legacy_identity_writer_evidence.evidence_id",
        ],
        name="fk_semantic_cutover_writer_evidence",
    ),
    ForeignKeyConstraint(
        [
            "run_id",
            "ingress_check_id",
            "ingress_check_category",
            "required_privacy_check_result",
            "required_privacy_residual_code",
        ],
        [
            "operations.privacy_cutover_check_receipts.run_id",
            "operations.privacy_cutover_check_receipts.check_id",
            "operations.privacy_cutover_check_receipts.check_category",
            "operations.privacy_cutover_check_receipts.check_result",
            "operations.privacy_cutover_check_receipts.residual_code",
        ],
        name="fk_semantic_cutover_ingress_check",
    ),
    ForeignKeyConstraint(
        [
            "run_id",
            "retrieval_check_id",
            "retrieval_check_category",
            "required_privacy_check_result",
            "required_privacy_residual_code",
        ],
        [
            "operations.privacy_cutover_check_receipts.run_id",
            "operations.privacy_cutover_check_receipts.check_id",
            "operations.privacy_cutover_check_receipts.check_category",
            "operations.privacy_cutover_check_receipts.check_result",
            "operations.privacy_cutover_check_receipts.residual_code",
        ],
        name="fk_semantic_cutover_retrieval_check",
    ),
    ForeignKeyConstraint(
        [
            "run_id",
            "prompt_check_id",
            "prompt_check_category",
            "required_privacy_check_result",
            "required_privacy_residual_code",
        ],
        [
            "operations.privacy_cutover_check_receipts.run_id",
            "operations.privacy_cutover_check_receipts.check_id",
            "operations.privacy_cutover_check_receipts.check_category",
            "operations.privacy_cutover_check_receipts.check_result",
            "operations.privacy_cutover_check_receipts.residual_code",
        ],
        name="fk_semantic_cutover_prompt_check",
    ),
    ForeignKeyConstraint(
        [
            "run_id",
            "initiative_check_id",
            "initiative_check_category",
            "required_privacy_check_result",
            "required_privacy_residual_code",
        ],
        [
            "operations.privacy_cutover_check_receipts.run_id",
            "operations.privacy_cutover_check_receipts.check_id",
            "operations.privacy_cutover_check_receipts.check_category",
            "operations.privacy_cutover_check_receipts.check_result",
            "operations.privacy_cutover_check_receipts.residual_code",
        ],
        name="fk_semantic_cutover_initiative_check",
    ),
    ForeignKeyConstraint(
        [
            "run_id",
            "export_check_id",
            "export_check_category",
            "required_privacy_check_result",
            "required_privacy_residual_code",
        ],
        [
            "operations.privacy_cutover_check_receipts.run_id",
            "operations.privacy_cutover_check_receipts.check_id",
            "operations.privacy_cutover_check_receipts.check_category",
            "operations.privacy_cutover_check_receipts.check_result",
            "operations.privacy_cutover_check_receipts.residual_code",
        ],
        name="fk_semantic_cutover_export_check",
    ),
    ForeignKeyConstraint(
        [
            "run_id",
            "edge_block_check_id",
            "edge_block_check_category",
            "required_privacy_check_result",
            "required_privacy_residual_code",
        ],
        [
            "operations.privacy_cutover_check_receipts.run_id",
            "operations.privacy_cutover_check_receipts.check_id",
            "operations.privacy_cutover_check_receipts.check_category",
            "operations.privacy_cutover_check_receipts.check_result",
            "operations.privacy_cutover_check_receipts.residual_code",
        ],
        name="fk_semantic_cutover_edge_block_check",
    ),
    CheckConstraint(_uuidv7_shape("cutover_id"), name="uuidv7_id"),
    CheckConstraint(
        "contract_version = 'semantic-authority-cutover-candidate-v1' "
        "AND authority_status = 'candidate_unenforced' "
        "AND authoritative = false",
        name="non_authoritative_contract",
    ),
    CheckConstraint(
        "ingress_check_category = 'ingress' "
        "AND retrieval_check_category = 'retrieval' "
        "AND prompt_check_category = 'prompt' "
        "AND initiative_check_category = 'initiative' "
        "AND export_check_category = 'export' "
        "AND edge_block_check_category = 'edge_block' "
        "AND required_privacy_check_result = 'passed' "
        "AND required_privacy_residual_code = 'none'",
        name="privacy_check_categories",
    ),
    CheckConstraint(
        _hex64(
            "privacy_check_set_commitment",
            "cutover_commitment",
            "policy_digest",
            "signing_key_fingerprint",
        )
        + " AND cutover_signature ~ '^[0-9a-f]{128}$'",
        name="signed_commitment_shape",
    ),
    CheckConstraint(
        "signature_algorithm = 'ed25519'",
        name="signature_algorithm",
    ),
    schema="operations",
)

reviewed_identity_migration_erasure_operations = Table(
    "reviewed_identity_migration_erasure_operations",
    metadata,
    Column("operation_id", UUID_PK, primary_key=True),
    Column("source_kind", String(32), nullable=False),
    Column("erasure_request_id", UUID_PK),
    Column("auto_expiry_schedule_id", UUID_PK),
    Column("operation_commitment", String(64), nullable=False),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    ForeignKeyConstraint(
        ["erasure_request_id"],
        ["privacy.erasure_requests.erasure_request_id"],
        name="fk_identity_erasure_operation_request",
    ),
    ForeignKeyConstraint(
        ["auto_expiry_schedule_id"],
        ["privacy.auto_expiry_schedules.schedule_id"],
        name="fk_identity_erasure_operation_auto_expiry",
    ),
    UniqueConstraint(
        "erasure_request_id", name="identity_erasure_operation_request"
    ),
    UniqueConstraint(
        "auto_expiry_schedule_id", name="identity_erasure_operation_auto_expiry"
    ),
    UniqueConstraint(
        "operation_commitment", name="identity_erasure_operation_commitment"
    ),
    CheckConstraint(
        "(source_kind = 'erasure_request' "
        "AND erasure_request_id IS NOT NULL "
        "AND auto_expiry_schedule_id IS NULL) "
        "OR (source_kind = 'auto_expiry_schedule' "
        "AND erasure_request_id IS NULL "
        "AND auto_expiry_schedule_id IS NOT NULL)",
        name="exact_source",
    ),
    CheckConstraint(
        f"{_uuidv7_shape('operation_id')} AND "
        f"(erasure_request_id IS NULL OR ({_uuidv7_shape('erasure_request_id')})) "
        f"AND (auto_expiry_schedule_id IS NULL OR "
        f"({_uuidv7_shape('auto_expiry_schedule_id')}))",
        name="uuidv7_ids",
    ),
    CheckConstraint(
        "operation_commitment ~ '^[0-9a-f]{64}$'",
        name="commitment_shape",
    ),
    schema="operations",
)

# The future erasure kernel must physically remove linkable pseudonymous leaf
# commitments first, then append only this run-level impact. There is
# intentionally no FK back to a decision or receipt that would retain a leaf.
# The operation source can be a principal-authorized erasure request or an
# imported-person auto-expiry schedule; neither source is inferred here.
reviewed_identity_migration_erasure_impacts = Table(
    "reviewed_identity_migration_erasure_impacts",
    metadata,
    Column("impact_id", UUID_PK, primary_key=True),
    Column("run_id", UUID_PK, nullable=False),
    Column("operation_id", UUID_PK, nullable=False),
    Column("impact_code", String(32), nullable=False),
    Column("readiness_suspension", String(16), nullable=False),
    Column("removed_leaf_commitment_count", Integer, nullable=False),
    Column("unlinked_projection_count", Integer, nullable=False),
    Column("impact_commitment", String(64), nullable=False, unique=True),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    ),
    ForeignKeyConstraint(
        ["run_id"],
        ["operations.reviewed_identity_migration_runs.run_id"],
        name="fk_identity_migration_erasure_impact_run",
    ),
    ForeignKeyConstraint(
        ["operation_id"],
        [
            "operations.reviewed_identity_migration_erasure_operations.operation_id"
        ],
        name="fk_identity_migration_erasure_operation",
    ),
    UniqueConstraint(
        "run_id", "operation_id", name="identity_erasure_impact_operation"
    ),
    CheckConstraint(
        f"{_uuidv7_shape('impact_id')} AND " f"{_uuidv7_shape('operation_id')}",
        name="uuidv7_ids",
    ),
    CheckConstraint(
        "impact_code IN ('leaf_commitments_removed','linkage_unavailable') "
        "AND readiness_suspension = 'required'",
        name="impact_shape",
    ),
    CheckConstraint(
        "removed_leaf_commitment_count BETWEEN 0 AND 30000 "
        "AND unlinked_projection_count BETWEEN 0 AND 10000",
        name="impact_caps",
    ),
    CheckConstraint(
        "(impact_code = 'leaf_commitments_removed' "
        "AND removed_leaf_commitment_count > 0 "
        "AND unlinked_projection_count = 0) "
        "OR (impact_code = 'linkage_unavailable' "
        "AND unlinked_projection_count > 0)",
        name="impact_count_shape",
    ),
    CheckConstraint(
        "impact_commitment ~ '^[0-9a-f]{64}$'",
        name="commitment_shape",
    ),
    schema="operations",
)
