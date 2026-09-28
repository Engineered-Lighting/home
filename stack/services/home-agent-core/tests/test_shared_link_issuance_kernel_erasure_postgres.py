"""Existing replayed erasure blocks must suppress new and replayed issuance.

Run alone in a fresh guarded 0040 clone. The tombstone is never removed. This
tests the database replay/issuance boundary, not external ledger authentication.
"""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.ids import uuid7
from app.shared_link_issuance import LOOKUP
from app.shared_link_commitments import SharedLinkBegin, SharedLinkCommitments
from .test_phase3_identity_erasure_e2_schema import _payload
from .test_shared_link_issuance_kernel_positive_postgres import positive_database, values, issue


def test_shared_link_issuance_kernel_replayed_erasure_blocks_new_and_existing_ceremonies(positive_database, values):
    engine, module, _ = positive_database
    receipt = issue(positive_database, values)
    block_id = uuid7()
    payload = _payload(person_id=values["person_id"], block_id=block_id)
    with engine.begin() as conn:
        # Choose an unused epoch in this isolated fixture; no real ledger is read.
        payload["ledger_epoch"] = conn.execute(text(
            "SELECT COALESCE(max(ledger_epoch),0)+1 FROM privacy.subject_retrieval_blocks")).scalar_one()
        conn.execute(text("SET LOCAL SESSION AUTHORIZATION home_agent_erasure"))
        returned = conn.execute(text("SELECT privacy.replay_identity_person_retrieval_block_v2(CAST(:payload AS jsonb))"),
            {"payload": json.dumps(payload)}).scalar_one()
        assert returned == block_id
    with pytest.raises(DBAPIError) as lookup_error:
        with engine.begin() as conn:
            conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {module.COORDINATOR_ROLE}"))
            conn.execute(LOOKUP, {"subject": values["echo_subject"]}).one()
    assert lookup_error.value.orig.sqlstate == "42501"
    fresh = SharedLinkBegin.model_validate({
        **{key: values[key] for key in SharedLinkBegin.model_fields},
        "ceremony_id": uuid7(), "echo_challenge_id": uuid7(), "victoria_challenge_id": uuid7(),
        "echo_challenge_commitment": uuid7().hex*2, "victoria_challenge_commitment": uuid7().hex*2})
    prepared = SharedLinkCommitments(b"x"*32, key_id="fixture-v1").prepare(fresh)
    new_request = {**prepared.parameters(), "key_id": prepared.key_id,
                   "key_fingerprint": prepared.key_fingerprint}
    for candidate in (values, new_request):
        with pytest.raises(DBAPIError) as error:
            issue(positive_database, candidate)
        assert error.value.orig.sqlstate == "42501"
        assert "shared_link_owner_unavailable" in str(error.value.orig)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT privacy.identity_person_is_blocked(:person_id)"), values).scalar_one()
        assert conn.execute(text("SELECT count(*) FROM identity.shared_link_ceremonies WHERE person_id=:person_id"), values).scalar_one() == 1
        assert conn.execute(text("SELECT expires_at FROM identity.shared_link_ceremonies WHERE ceremony_id=:ceremony_id"), values).scalar_one() == receipt["expires_at"]
        assert conn.execute(text("SELECT count(*) FROM identity.shared_owner_links WHERE person_id=:person_id"), values).scalar_one() == 0
