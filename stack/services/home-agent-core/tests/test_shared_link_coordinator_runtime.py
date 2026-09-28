from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.exceptions import InvalidTag

from app.shared_link_coordinator_runtime import open_link_coordinator
from app.shared_link_issuance_api import LinkIssuanceBinding, BEGIN_PATH, RECOVER_PATH
from app.shared_link_review_api import LinkReviewBinding, REVIEW_PATH, CONFIRM_PATH, OUTCOME_PATH, PREPARE_PATH


def configuration(tmp_path):
    return dict(issuance_binding=LinkIssuanceBinding(b"a"*64), review_binding=LinkReviewBinding(b"b"*64),
        coordinator_database_url="postgresql://home_agent_shared_link_coordinator@localhost/unused",
        echo_proof_database_url="postgresql://home_agent_shared_echo_proof_ingress@localhost/unused",
        victoria_proof_database_url="postgresql://home_agent_shared_victoria_proof_ingress@localhost/unused",
        issuance_journal_path=tmp_path/"issuance.sqlite", confirmation_journal_path=tmp_path/"confirmation.sqlite",
        commitment_key=b"c"*32, confirmation_key=b"d"*32, issuance_journal_key=b"e"*32,
        confirmation_journal_key=b"f"*32, commitment_key_id="owner-v1", confirmation_key_id="review-v1",
        admission=AsyncMock())


@pytest.mark.asyncio
async def test_composed_routes_reopen_durable_journals_and_require_live_admission(tmp_path):
    config = configuration(tmp_path)
    for _ in range(2):
        async with open_link_coordinator(**config) as app:
            assert {route.path for route in app.routes} == {BEGIN_PATH, RECOVER_PATH, REVIEW_PATH,
                                                          CONFIRM_PATH, OUTCOME_PATH, PREPARE_PATH}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://private.test") as client:
                calls = config["admission"].await_count
                assert (await client.post(BEGIN_PATH)).status_code == 401
                assert config["admission"].await_count == calls
                # Issuance credentials cannot use confirmation/review routes.
                assert (await client.post(CONFIRM_PATH, headers={"authorization": "Bearer " + "a"*64})).status_code == 401
                config["admission"].side_effect = ValueError("private restore details")
                response = await client.post(BEGIN_PATH, headers={"authorization": "Bearer " + "a"*64})
                assert response.status_code == 503 and "private restore details" not in response.text
                config["admission"].side_effect = None
                assert (await client.post(PREPARE_PATH, headers={"authorization": "Bearer " + "b"*64})).status_code == 415
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://private.test") as client:
            assert (await client.post(BEGIN_PATH)).status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["keys", "paths", "credentials", "database", "role"])
async def test_invalid_provisioning_does_not_create_journals(tmp_path, change):
    config = configuration(tmp_path)
    if change == "keys": config["confirmation_key"] = config["commitment_key"]
    elif change == "paths": config["confirmation_journal_path"] = config["issuance_journal_path"]
    elif change == "credentials": config["review_binding"] = LinkReviewBinding(b"a"*64)
    elif change == "database": config["victoria_proof_database_url"] = "postgresql://home_agent_shared_victoria_proof_ingress@other/unused"
    else: config["coordinator_database_url"] = "postgresql://home_agent_owner@localhost/unused"
    with pytest.raises(ValueError):
        async with open_link_coordinator(**config): pytest.fail("invalid configuration admitted")
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_startup_requires_admission_before_creating_journals(tmp_path):
    config = configuration(tmp_path)
    config["admission"].side_effect = RuntimeError("restore pending")
    with pytest.raises(RuntimeError):
        async with open_link_coordinator(**config): pytest.fail("restore bypass")
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_wrong_recovery_key_does_not_replace_existing_journal(tmp_path):
    config = configuration(tmp_path)
    async with open_link_coordinator(**config): pass
    original = config["confirmation_journal_path"].read_bytes()
    wrong = {**config, "confirmation_journal_key": b"z"*32}
    with pytest.raises(InvalidTag):
        async with open_link_coordinator(**wrong): pytest.fail("wrong key admitted")
    assert config["confirmation_journal_path"].read_bytes() == original
    # Partial startup closed the first journal; the original configuration can
    # immediately reopen both retained journals without deleting or resetting.
    async with open_link_coordinator(**config): pass
