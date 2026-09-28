"""Private evidence to actual retained review and ASGI owner delivery."""
import json
import sqlite3
from dataclasses import replace

import httpx
import pytest

from app.shared_link_issuance_service import SharedLinkIssuedCeremony
from app.shared_link_review_api import REVIEW_PATH, CONFIRM_PATH
from .test_shared_link_review_api import fixture, HEADERS, confirmation_body
from app.shared_link_review_assembly import SharedLinkReviewAssembly, ReviewEvidence
from app.shared_link_issuance_service import SharedLinkIssuanceService
from app.shared_auth_proof import SharedLinkProofDatabase
from app.shared_link_commitments import SharedLinkCeremonyContext
from app.shared_link_review_api import create_link_review_ingress, LinkReviewBinding, PREPARE_PATH


def unreviewed(tmp_path, monkeypatch):
    app, store, body, approval, calls, issue, confirmation, service = fixture(tmp_path, monkeypatch)
    # Fixture creates valid private issuance/proof evidence. Remove only its
    # preassembled review so the production composition must retain it itself.
    from app.auth import ServiceIdentity
    from uuid import UUID
    identity = ServiceIdentity(body['request']['subject'])
    evidence = store.load_review(service._preparer, identity, UUID(body['request']['ceremony_id']),
        body['request']['session_commitment'])
    store._db.execute('DELETE FROM reviews')
    issued = SharedLinkIssuedCeremony(evidence.beginning, evidence.issuance)
    return app, store, body, approval, calls, issue, confirmation, service, identity, evidence, issued


@pytest.mark.parametrize('failure', [None, 'echo_revoked', 'victoria_revoked', 'wrong_owner', 'wrong_challenge'])
@pytest.mark.asyncio
async def test_coordinator_preparation_rechecks_both_proofs_without_confirming(tmp_path, monkeypatch, failure):
    _, store, body, _, calls, _, _, service, identity, evidence, issued = unreviewed(tmp_path, monkeypatch)
    # Exact adapters with simulated database/issuance I/O; no production DB gate.
    issuance = object.__new__(SharedLinkIssuanceService)
    async def recover(owner, context):
        assert owner == identity
        assert context.ceremony_id == issued.beginning.ceremony_id
        return issued
    issuance.recover = recover
    adapters = []
    for site in ('echo', 'victoria'):
        database = object.__new__(SharedLinkProofDatabase)
        database.issuer_id = f'home-assistant:{site}'
        async def inspect(submission, site=site):
            calls.append('proof:'+site)
            assert submission.proof_id == getattr(evidence, site).proof_id
            return None if failure == site+'_revoked' else getattr(evidence, site)
        database.inspect = inspect
        adapters.append(database)
    assembly = SharedLinkReviewAssembly(issuance=issuance, review=service, echo_proofs=adapters[0], victoria_proofs=adapters[1])
    context = SharedLinkCeremonyContext.model_validate({name: getattr(issued.beginning, name) for name in SharedLinkCeremonyContext.model_fields})
    value = ReviewEvidence(context=context, echo=evidence.echo, victoria=evidence.victoria, choice=evidence.choice)
    request = {'version': 1, 'request': {'subject': identity.ha_user_id, **value.model_dump(mode='json')}}
    if failure == 'wrong_owner': request['request']['subject'] = 'another-owner'
    if failure == 'wrong_challenge': request['request']['victoria']['challenge_commitment'] = 'f'*64
    app = create_link_review_ingress(binding=LinkReviewBinding(b'e'*64), service=service, assembly=assembly)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            response = await client.post(PREPARE_PATH, headers=HEADERS, json=request)
            assert response.status_code == (200 if failure is None else 503)
            if failure is None:
                assert calls[:2] == ['proof:echo', 'proof:victoria']
                assert response.json()['accounts'][1]['site_id'] == 'victoria'
                assert 'proof_id' not in response.text
                repeated = await client.post(PREPARE_PATH, headers=HEADERS, json=request)
                assert repeated.status_code == 200 and repeated.json() == response.json()
                assert store._db.execute('SELECT count(*) FROM reviews').fetchone()[0] == 1
            assert 'confirm' not in calls
            assert store._db.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_shared_link_review_assembly_persists_before_review_and_requires_later_approval(tmp_path, monkeypatch):
    app, store, body, approval, calls, _, _, service, identity, evidence, issued = unreviewed(tmp_path, monkeypatch)
    try:
        result = await service.prepare_review(identity, issued, evidence.echo, evidence.victoria, evidence.choice)
        assert calls == ['authority', 'authority']
        assert store._db.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
        assert store.load_review(service._preparer, identity, issued.beginning.ceremony_id,
            evidence.choice.session_commitment).approval is None
        assert 'proof_id' not in json.dumps(result) and 'session_commitment' not in json.dumps(result)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            assert (await client.post(REVIEW_PATH, headers=HEADERS, json=body)).json() == result
            assert (await client.post(CONFIRM_PATH, headers=HEADERS, json=confirmation_body(body, approval))).status_code == 200
        assert calls.count('confirm') == 1
    finally: store.close()


@pytest.mark.parametrize('change', ['owner', 'issuer', 'session', 'challenge', 'expiry'])
@pytest.mark.asyncio
async def test_shared_link_review_assembly_rejects_inconsistent_proofs_before_authority(tmp_path, monkeypatch, change):
    _, store, _, _, calls, _, _, service, identity, evidence, issued = unreviewed(tmp_path, monkeypatch)
    proof = evidence.victoria
    if change == 'owner': identity = replace(identity, ha_user_id='other')
    if change == 'issuer': proof = proof.model_copy(update={'issuer_id': 'home-assistant:echo'})
    if change == 'session': proof = proof.model_copy(update={'session_commitment': 'e'*64})
    if change == 'challenge': proof = proof.model_copy(update={'challenge_commitment': 'f'*64})
    if change == 'expiry': proof = proof.model_copy(update={'expires_at': proof.issued_at})
    try:
        with pytest.raises(ValueError): await service.prepare_review(identity, issued, evidence.echo, proof, evidence.choice)
        assert calls == []
        assert store._db.execute('SELECT count(*) FROM reviews').fetchone()[0] == 0
    finally: store.close()


@pytest.mark.parametrize('failure', ['revoked', 'generation', 'after_storage', 'storage'])
@pytest.mark.asyncio
async def test_shared_link_review_assembly_suppresses_unavailable_or_unretained_results(tmp_path, monkeypatch, failure):
    _, store, _, _, calls, issue, _, service, identity, evidence, issued = unreviewed(tmp_path, monkeypatch)
    if failure == 'revoked': issue.row = None
    if failure == 'generation': issue.row['authorization_generation'] += 1
    if failure == 'after_storage':
        original = issue.one_or_none
        issue.one_or_none = lambda: None if len(calls) > 1 else original()
    if failure == 'storage':
        store._db.execute("CREATE TRIGGER fail_review BEFORE INSERT ON reviews BEGIN SELECT RAISE(ABORT,'disk failure'); END")
    try:
        with pytest.raises((ValueError, sqlite3.IntegrityError)):
            await service.prepare_review(identity, issued, evidence.echo, evidence.victoria, evidence.choice)
        assert store._db.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
        assert store._db.execute('SELECT count(*) FROM reviews').fetchone()[0] == (1 if failure == 'after_storage' else 0)
        assert 'confirm' not in calls
    finally: store.close()
