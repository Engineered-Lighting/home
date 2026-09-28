"""Real ASGI, service, journal and adapters; simulated database transport only."""
import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest


from app import shared_link_issuance as issuance_module
from app.shared_link_review_api import create_link_review_ingress, LinkReviewBinding, REVIEW_PATH, CONFIRM_PATH
from app.shared_link_review_api import OUTCOME_PATH
from datetime import timedelta
from app.shared_link_review_service import SharedLinkReviewService
from .test_shared_link_confirmation_review import reviewed
from .test_shared_link_confirmation_adapter import database as confirmation_database
from .test_shared_link_issuance_adapter import database as issuance_database
from .test_shared_link_confirmation_preparation import NOW

TOKEN = 'e'*64
HEADERS = {'authorization': 'Bearer '+TOKEN, 'content-type': 'application/json'}


def fixture(tmp_path, monkeypatch):
    path, store, args, preparer, evidence, approval = reviewed(tmp_path)
    value = preparer.prepare(*args, review=evidence.review, approval=approval)
    confirmation, confirm_engine = confirmation_database(monkeypatch, value)
    issuance, issue_engine, _ = issuance_database(monkeypatch, **args[2].model_dump())
    calls = []
    async def inspect(sql, parameters):
        assert sql is issuance_module.INSPECT
        calls.append('authority')
        return issue_engine
    issue_engine.execute = inspect
    issue_engine.one_or_none = lambda: issue_engine.row
    original = confirm_engine.execute
    async def execute(sql, parameters):
        name = ('identity.inspect_shared_link_confirmation_v1' if 'inspect_shared_link_confirmation_v1' in str(sql)
                else 'identity.confirm_shared_link_ceremony_v1')
        confirm_engine.expected_function = name
        calls.append('lookup' if 'inspect_' in name else 'confirm')
        return await original(sql, parameters)
    confirm_engine.execute = execute
    confirm_engine.one_or_none = lambda: confirm_engine.row
    service = SharedLinkReviewService(journal=store, preparer=preparer, issuance=issuance,
        confirmation=confirmation, now=lambda: NOW)
    app = create_link_review_ingress(binding=LinkReviewBinding(TOKEN.encode()), service=service)
    request = {'version': 1, 'request': {'subject': args[0].ha_user_id,
        'session_commitment': args[5].session_commitment, 'ceremony_id': str(args[1].ceremony_id)}}
    return app, store, request, approval, calls, issue_engine, confirm_engine, service


def confirmation_body(body, approval):
    return {**body, 'request': {**body['request'], 'gesture_id': str(approval.gesture_id),
        'reviewed_digest': approval.reviewed_digest}}


@pytest.mark.asyncio
async def test_shared_link_review_api_positive_review_confirmation_and_exact_http_replay(tmp_path, monkeypatch):
    app, store, body, approval, calls, _, _, _ = fixture(tmp_path, monkeypatch)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            response = await client.post(REVIEW_PATH, headers=HEADERS, json=body)
            assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
            result = response.json()
            assert [a['site_id'] for a in result['accounts']] == ['echo', 'victoria']
            assert result['reviewed_digest'] == approval.reviewed_digest
            assert set(result) == {'version', 'ceremony_id', 'gesture_id', 'reviewed_digest', 'expires_at', 'accounts'}
            assert 'session_commitment' not in response.text and 'proof_id' not in response.text
            confirmed = await client.post(CONFIRM_PATH, headers=HEADERS, json=confirmation_body(body, approval))
            assert confirmed.status_code == 200 and confirmed.json()['status'] == 'confirmed'
            again = await client.post(CONFIRM_PATH, headers=HEADERS, json=confirmation_body(body, approval))
            assert again.status_code == 200 and again.json() == confirmed.json()
            assert calls == ['authority', 'authority', 'confirm', 'lookup', 'lookup']
            assert (await client.get('/openapi.json')).status_code == 404
    finally: store.close()


@pytest.mark.parametrize('headers,status', [
    ({'authorization': 'Bearer '+'f'*64}, 401), ({'origin': 'https://home.test'}, 403),
    ({'cookie': 'session=fake'}, 403), ({'x-authenticated-ha-user': 'owner'}, 400),
    ({'content-type': 'text/plain'}, 415), ({'content-encoding': 'gzip'}, 400),
])
@pytest.mark.asyncio
async def test_shared_link_review_api_rejects_transport_before_authority(tmp_path, monkeypatch, headers, status):
    app, store, body, _, calls, _, _, _ = fixture(tmp_path, monkeypatch)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            response = await client.post(REVIEW_PATH, headers={**HEADERS, **headers}, json=body)
            assert response.status_code == status
        assert calls == []
    finally: store.close()


@pytest.mark.parametrize('extra', [{'issuer_id': 'home-assistant:victoria'}, {'proof': {}},
    {'approved_at': NOW.isoformat()}, {'site_id': 'victoria'}])
@pytest.mark.asyncio
async def test_shared_link_review_api_rejects_caller_evidence_and_authority(tmp_path, monkeypatch, extra):
    app, store, body, _, calls, _, _, _ = fixture(tmp_path, monkeypatch)
    body['request'].update(extra)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            assert (await client.post(REVIEW_PATH, headers=HEADERS, json=body)).status_code == 422
        assert not calls
    finally: store.close()


@pytest.mark.parametrize('change', ['owner', 'session', 'revoked', 'generation'])
@pytest.mark.asyncio
async def test_shared_link_review_api_no_private_delivery_without_current_authority(tmp_path, monkeypatch, change):
    app, store, body, _, calls, issue, _, _ = fixture(tmp_path, monkeypatch)
    if change == 'owner': body['request']['subject'] = 'guest'
    if change == 'session': body['request']['session_commitment'] = 'f'*64
    if change == 'revoked': issue.row = None
    if change == 'generation': issue.row['authorization_generation'] += 1
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            response = await client.post(REVIEW_PATH, headers=HEADERS, json=body)
            assert response.status_code == 503
            assert response.json() == {'error': 'linking_unavailable'}
        assert 'confirm' not in calls
    finally: store.close()


@pytest.mark.asyncio
async def test_shared_link_review_api_rechecks_authority_after_commit_before_delivery(tmp_path, monkeypatch):
    app, store, body, approval, calls, _, confirmation, _ = fixture(tmp_path, monkeypatch)
    confirmation.one_or_none = lambda: None  # revocation / no accessible outcome
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            response = await client.post(CONFIRM_PATH, headers=HEADERS, json=confirmation_body(body, approval))
            assert response.status_code == 503 and 'link_id' not in response.text
            again = await client.post(CONFIRM_PATH, headers=HEADERS, json=confirmation_body(body, approval))
            assert again.status_code == 503
        assert calls == ['authority', 'confirm', 'lookup', 'lookup']
        assert store._db.execute('SELECT state FROM requests').fetchone()[0] == 'completed'
    finally: store.close()


@pytest.mark.asyncio
async def test_shared_link_review_api_uncertain_dispatch_reconciles_without_resend(tmp_path, monkeypatch):
    app, store, body, approval, calls, _, confirmation, _ = fixture(tmp_path, monkeypatch)
    original_begin = confirmation.begin
    @asynccontextmanager
    async def lost_commit():
        async with original_begin():
            yield
        raise ConnectionError('lost acknowledgment')
    confirmation.begin = lost_commit
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            response = await client.post(CONFIRM_PATH, headers=HEADERS, json=confirmation_body(body, approval))
            assert response.status_code == 503
            assert store._db.execute('SELECT state FROM requests').fetchone()[0] == 'dispatching'
            confirmation.begin = original_begin
            again = await client.post(CONFIRM_PATH, headers=HEADERS, json=confirmation_body(body, approval))
            assert again.status_code == 200
        assert calls == ['authority', 'confirm', 'lookup', 'lookup']
    finally: store.close()


@pytest.mark.asyncio
async def test_shared_link_review_api_disabled_cleartext_duplicate_headers_and_json(tmp_path, monkeypatch):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_link_review_ingress()), base_url='https://link.test') as client:
        assert (await client.post(REVIEW_PATH)).status_code == 503
    app, store, body, _, calls, _, _, _ = fixture(tmp_path, monkeypatch)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://link.test') as client:
            assert (await client.post(REVIEW_PATH, headers={**HEADERS, 'x-forwarded-proto': 'https'}, json=body)).status_code == 403
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            headers = list(HEADERS.items())+[('authorization', 'Bearer '+TOKEN)]
            assert (await client.post(REVIEW_PATH, headers=headers, json=body)).status_code == 401
            assert (await client.post(REVIEW_PATH, headers=HEADERS, content='{"version":1,"version":1,"request":{}}')).status_code == 422
            assert (await client.post(REVIEW_PATH, headers=HEADERS, content='x'*2049)).status_code == 413
            assert (await client.post(REVIEW_PATH+'?site=victoria', headers=HEADERS, json=body)).status_code == 400
        assert not calls
    finally: store.close()


@pytest.mark.asyncio
async def test_shared_link_review_api_bounded_concurrency(tmp_path, monkeypatch):
    app, store, body, _, calls, issue, _, _ = fixture(tmp_path, monkeypatch)
    both_entered, release = asyncio.Event(), asyncio.Event()
    original = issue.execute
    entered = 0
    async def blocked(sql, parameters):
        nonlocal entered
        entered += 1
        if entered == 2: both_entered.set()
        await release.wait()
        return await original(sql, parameters)
    issue.execute = blocked
    tasks = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            tasks = [asyncio.create_task(client.post(REVIEW_PATH, headers=HEADERS, json=body)) for _ in range(2)]
            await asyncio.wait_for(both_entered.wait(), 2)
            assert (await client.post(REVIEW_PATH, headers=HEADERS, json=body)).status_code == 429
            release.set()
            assert all(response.status_code == 200 for response in await asyncio.gather(*tasks))
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        store.close()


@pytest.mark.asyncio
async def test_shared_link_review_api_cancellation_preserves_indeterminate_dispatch(tmp_path, monkeypatch):
    app, store, body, approval, calls, _, confirmation, _ = fixture(tmp_path, monkeypatch)
    entered = asyncio.Event()
    original = confirmation.execute
    async def blocked(sql, parameters):
        entered.set()
        await asyncio.Event().wait()
    confirmation.execute = blocked
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            task = asyncio.create_task(client.post(CONFIRM_PATH, headers=HEADERS, json=confirmation_body(body, approval)))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert store._db.execute('SELECT state FROM requests').fetchone()[0] == 'dispatching'
            confirmation.execute = original
            confirmation.one_or_none = lambda: None
            response = await client.post(CONFIRM_PATH, headers=HEADERS, json=confirmation_body(body, approval))
            assert response.status_code == 503
            assert calls == ['authority', 'lookup']
    finally: store.close()


@pytest.mark.asyncio
async def test_shared_link_review_api_changed_digest_cannot_dispatch(tmp_path, monkeypatch):
    app, store, body, approval, calls, _, _, _ = fixture(tmp_path, monkeypatch)
    changed = confirmation_body(body, approval)
    changed['request']['reviewed_digest'] = 'f'*64
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            assert (await client.post(CONFIRM_PATH, headers=HEADERS, json=changed)).status_code == 503
        assert not calls
        assert store._db.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
    finally: store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['completed', 'indeterminate', 'prepared', 'missing'])
async def test_shared_link_review_api_historical_outcome_never_renews_or_dispatches(tmp_path, monkeypatch, state):
    app, store, body, approval, calls, _, _, service = fixture(tmp_path, monkeypatch)
    from app.auth import ServiceIdentity
    from uuid import UUID
    identity = ServiceIdentity(body['request']['subject'])
    ceremony = UUID(body['request']['ceremony_id'])
    if state != 'missing':
        value = store.approve_review(service._preparer, identity, ceremony, approval)
        if state == 'completed': await store.dispatch(ceremony, service._confirmation)
        elif state == 'indeterminate': store.claim(ceremony)
    # Recover from the real encrypted file after the owning process closes it.
    from pathlib import Path
    from .test_shared_link_confirmation_journal import journal
    journal_path = Path(store._db.execute('PRAGMA database_list').fetchone()[2])
    store.close()
    store = journal(journal_path)
    service._journal = store
    calls.clear()
    later = NOW+timedelta(minutes=10)
    service._preparer._now = lambda: later
    service._confirmation._now = lambda: later
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            assert (await client.post(CONFIRM_PATH, headers=HEADERS, json=confirmation_body(body, approval))).status_code == 503
            assert calls == []
            response = await client.post(OUTCOME_PATH, headers=HEADERS, json=body)
            assert response.status_code == (200 if state in ('completed', 'indeterminate') else 503)
            if response.status_code == 200:
                assert response.json()['ceremony_id'] == str(ceremony)
                assert response.json()['confirmed_at'] == NOW.isoformat()
                assert store.inspect(value).state == 'completed'
            elif state == 'prepared':
                assert store.inspect(value).state == 'prepared'
        assert calls == {'completed': ['lookup'], 'indeterminate': ['lookup', 'lookup'],
                         'prepared': [], 'missing': []}[state]
    finally: store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['owner', 'session', 'revoked', 'absent_outcome'])
async def test_shared_link_review_api_historical_outcome_requires_current_authority(tmp_path, monkeypatch, change):
    app, store, body, approval, calls, _, confirmation, _ = fixture(tmp_path, monkeypatch)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://link.test') as client:
            assert (await client.post(CONFIRM_PATH, headers=HEADERS, json=confirmation_body(body, approval))).status_code == 200
            calls.clear()
            if change == 'owner': body['request']['subject'] = 'guest'
            if change == 'session': body['request']['session_commitment'] = 'f'*64
            if change == 'absent_outcome': confirmation.one_or_none = lambda: None
            if change == 'revoked':
                def revoked(): raise PermissionError('private authority detail')
                confirmation.one_or_none = revoked
            response = await client.post(OUTCOME_PATH, headers=HEADERS, json=body)
            assert response.status_code == 503 and response.json() == {'error': 'linking_unavailable'}
            assert 'confirm' not in calls
            assert store._db.execute('SELECT state FROM requests').fetchone()[0] == 'completed'
    finally: store.close()
