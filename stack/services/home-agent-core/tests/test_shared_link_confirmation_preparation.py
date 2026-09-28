"""Consent binding and actual SQLite retention; no authenticated HTTP fixture."""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.auth import ServiceIdentity
from app.shared_auth_proof import FreshAuthReceipt
from app.shared_link_issuance import SharedLinkIssuanceReceipt
from app.shared_link_confirmation_preparation import (
    SharedLinkConfirmationChoice, SharedLinkConfirmationApproval,
    SharedLinkConfirmationPreparer,
)
from .test_shared_link_commitments import submission
from .test_shared_link_confirmation_journal import journal
from .test_shared_link_confirmation_adapter import database

NOW = datetime(2026, 9, 26, tzinfo=UTC)


def context():
    beginning = submission()
    issuance = SharedLinkIssuanceReceipt(ceremony_id=beginning.ceremony_id,
        authorization_generation=7, revision=1, created_at=NOW-timedelta(seconds=30),
        expires_at=NOW+timedelta(seconds=270), echo_registration_revision=2,
        victoria_registration_revision=3)
    proofs = [FreshAuthReceipt(proof_id=uuid4(), issuer_id=f'home-assistant:{site}',
        subject=beginning.echo_subject, session_commitment=getattr(beginning, f'{site}_session_commitment'),
        challenge_commitment=getattr(beginning, f'{site}_challenge_commitment'),
        authenticated_at=NOW-timedelta(seconds=20), issued_at=NOW-timedelta(seconds=10),
        expires_at=issuance.expires_at, registration_revision=getattr(issuance, f'{site}_registration_revision'))
        for site in ('echo', 'victoria')]
    choice = SharedLinkConfirmationChoice(gesture_id=uuid4(),
        session_commitment=beginning.echo_session_commitment, proposal_id=uuid4(), receipt_id=uuid4(),
        link_id=uuid4(), echo_binding_id=uuid4(), victoria_binding_id=uuid4())
    return [ServiceIdentity(beginning.echo_subject), beginning, issuance, *proofs, choice]


def approved(args, *, key=b'p'*32):
    factory = SharedLinkConfirmationPreparer(key, key_id='confirmation-v1', now=lambda: NOW)
    review = factory.review(*args)
    approval = SharedLinkConfirmationApproval(gesture_id=args[5].gesture_id,
        session_commitment=args[5].session_commitment, reviewed_digest=review.proposal_digest, approved_at=NOW)
    return factory, dict(review=review, approval=approval)


def test_shared_link_confirmation_preparation_same_bare_subject_is_issuer_qualified_and_stable():
    args = context()
    factory, consent = approved(args)
    value = factory.prepare(*args, **consent)
    assert factory.prepare(*args, **consent) == value
    assert value.expected_generation == 7 and value.expected_revision == 1
    assert value.link_id == args[5].link_id
    assert value.proposal_digest != value.confirmation_commitment
    assert args[0].ha_user_id not in repr(value)


@pytest.mark.parametrize('index,change', [
    (1, {'principal_id': uuid4()}), (1, {'person_id': uuid4()}), (1, {'legacy_binding_id': uuid4()}),
    (1, {'echo_challenge_id': uuid4()}), (2, {'authorization_generation': 8}),
    (3, {'proof_id': uuid4()}), (4, {'proof_id': uuid4()}), (4, {'subject': 'different-owner'}),
    (4, {'authenticated_at': NOW-timedelta(seconds=15)}),
    (4, {'issued_at': NOW-timedelta(seconds=5)}),
    (5, {'gesture_id': uuid4()}), (5, {'proposal_id': uuid4()}), (5, {'receipt_id': uuid4()}),
    (5, {'link_id': uuid4()}), (5, {'echo_binding_id': uuid4()}), (5, {'victoria_binding_id': uuid4()}),
])
def test_shared_link_confirmation_preparation_changed_valid_evidence_cannot_reuse_consent(index, change):
    args = context()
    factory, consent = approved(args)
    args[index] = args[index].model_copy(update=change)
    with pytest.raises(ValueError, match='approved review'):
        factory.prepare(*args, **consent)


@pytest.mark.parametrize('index,change', [
    (1, {'echo_subject': ' padded '}), (2, {'revision': 2}),
    (2, {'authorization_generation': 9223372036854775807}),
    (2, {'ceremony_id': uuid4()}), (2, {'expires_at': NOW+timedelta(minutes=10)}),
    (3, {'issuer_id': 'home-assistant:victoria'}), (3, {'subject': 'another-owner'}),
    (4, {'session_commitment': 'e'*64}), (4, {'challenge_commitment': 'e'*64}),
    (4, {'registration_revision': 4}), (4, {'issued_at': NOW+timedelta(seconds=1)}),
    (4, {'authenticated_at': NOW-timedelta(minutes=1)}),
    (4, {'expires_at': NOW}), (4, {'expires_at': NOW+timedelta(seconds=271)}),
    (5, {'session_commitment': 'f'*64}),
])
def test_shared_link_confirmation_preparation_rejects_invalid_context_even_before_review(index, change):
    args = context()
    args[index] = args[index].model_copy(update=change)
    factory = SharedLinkConfirmationPreparer(b'p'*32, key_id='v1', now=lambda: NOW)
    with pytest.raises(ValueError): factory.review(*args)


@pytest.mark.parametrize('change', [{'ha_user_id': 'guest'}, {'site_id': 'victoria'},
    {'ha_issuer_id': 'home-assistant:victoria'}])
def test_shared_link_confirmation_preparation_requires_echo_owner(change):
    args = context()
    args[0] = replace(args[0], **change)
    factory = SharedLinkConfirmationPreparer(b'p'*32, key_id='v1', now=lambda: NOW)
    with pytest.raises(ValueError): factory.review(*args)


@pytest.mark.parametrize('field,value', [('gesture_id', uuid4()), ('session_commitment', 'e'*64),
    ('reviewed_digest', 'f'*64), ('approved_at', NOW-timedelta(seconds=1)),
    ('approved_at', NOW+timedelta(seconds=1)), ('approved_at', NOW.replace(tzinfo=None))])
def test_shared_link_confirmation_preparation_rejects_wrong_gesture_or_time(field, value):
    args = context()
    factory, consent = approved(args)
    consent['approval'] = consent['approval'].model_copy(update={field: value})
    with pytest.raises(ValueError): factory.prepare(*args, **consent)


@pytest.mark.parametrize('index', range(6))
def test_shared_link_confirmation_preparation_rejects_untyped_context(index):
    args = context()
    factory, consent = approved(args)
    args[index] = {}
    with pytest.raises(TypeError): factory.prepare(*args, **consent)


def test_shared_link_confirmation_preparation_expiry_rotation_and_changed_review():
    args = context()
    factory, consent = approved(args)
    for key, key_id, now in [(b'q'*32, 'confirmation-v1', NOW),
        (b'p'*32, 'confirmation-v2', NOW), (b'p'*32, 'confirmation-v1', args[2].expires_at)]:
        other = SharedLinkConfirmationPreparer(key, key_id=key_id, now=lambda: now)
        with pytest.raises(ValueError): other.prepare(*args, **consent)
    for change in ({'created_at': NOW-timedelta(seconds=1)}, {'expires_at': NOW+timedelta(seconds=1)},
                   {'proposal_digest': 'f'*64}):
        with pytest.raises(ValueError):
            factory.prepare(*args, **{**consent, 'review': consent['review'].model_copy(update=change)})


@pytest.mark.parametrize('key,key_id', [(b'p'*31, 'v1'), ('p'*32, 'v1'),
    (b'p'*32, ''), (b'p'*32, '../key')])
def test_shared_link_confirmation_preparation_rejects_bad_key_configuration(key, key_id):
    with pytest.raises(ValueError): SharedLinkConfirmationPreparer(key, key_id=key_id)


def test_shared_link_confirmation_preparation_revalidates_duplicate_ids_and_clock():
    args = context()
    factory, consent = approved(args)
    duplicate = list(args)
    duplicate[4] = args[4].model_copy(update={'proof_id': args[3].proof_id})
    with pytest.raises(ValueError): factory.review(*duplicate)
    duplicate = list(args)
    duplicate[5] = args[5].model_copy(update={'link_id': args[5].receipt_id})
    with pytest.raises(ValueError): factory.prepare(*duplicate, **consent)
    for now in (None, NOW.replace(tzinfo=None), NOW-timedelta(minutes=1)):
        other = SharedLinkConfirmationPreparer(b'p'*32, key_id='v1', now=lambda: now)
        with pytest.raises(ValueError): other.review(*args)


def test_shared_link_confirmation_preparation_uncertain_dispatch_cannot_be_reprepared(tmp_path, monkeypatch):
    args = context()
    factory, consent = approved(args)
    store = journal(tmp_path/'confirmation.sqlite')
    value = store.prepare(factory, *args, **consent)
    db, engine = database(monkeypatch, value)
    async def lost(): raise ConnectionError('lost response')
    db.confirm = lambda value: lost()
    try:
        with pytest.raises(ConnectionError): asyncio.run(store.dispatch(value.ceremony_id, db))
        assert store.prepare(factory, *args, **consent) == value
        assert store.inspect(value).state == 'indeterminate'
        with pytest.raises(ValueError, match='uncertain'):
            asyncio.run(store.dispatch(value.ceremony_id, db))
        changed = list(args)
        changed[5] = args[5].model_copy(update={'link_id': uuid4()})
        other, new_consent = approved(changed)
        with pytest.raises(ValueError, match='conflict'):
            store.prepare(other, *changed, **new_consent)
    finally: store.close()


def test_shared_link_confirmation_preparation_journal_reopens_and_dispatches_exact_approved_request(tmp_path, monkeypatch):
    args = context()
    factory, consent = approved(args)
    path = tmp_path/'confirmation.sqlite'
    store = journal(path)
    value = store.prepare(factory, *args, **consent)
    assert store.inspect(value).state == 'prepared'
    store.close()
    assert args[0].ha_user_id.encode() not in path.read_bytes()
    store = journal(path)
    db, engine = database(monkeypatch, value)
    try:
        receipt = asyncio.run(store.dispatch(value.ceremony_id, db))
        assert receipt.link_id == value.link_id
        assert store.inspect(value).state == 'completed'
        assert engine.events.count('kernel') == 1
        changed = list(args)
        changed[5] = args[5].model_copy(update={'link_id': uuid4()})
        other, new_consent = approved(changed)
        with pytest.raises(ValueError, match='conflict'):
            store.prepare(other, *changed, **new_consent)
    finally: store.close()
