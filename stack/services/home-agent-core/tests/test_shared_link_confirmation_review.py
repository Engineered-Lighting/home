"""Durable private review and approval using actual SQLite transactions."""
import asyncio
import sqlite3
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag

from app.shared_link_confirmation_preparation import SharedLinkConfirmationPreparer, SharedLinkConfirmationApproval
from .test_shared_link_confirmation_preparation import context, NOW
from .test_shared_link_confirmation_journal import journal
from .test_shared_link_confirmation_adapter import database, request


def reviewed(tmp_path):
    args = context()
    factory = SharedLinkConfirmationPreparer(b'p'*32, key_id='review-v1', now=lambda: NOW)
    path = tmp_path/'review.sqlite'
    store = journal(path)
    evidence = store.review(factory, *args)
    approval = SharedLinkConfirmationApproval(gesture_id=args[5].gesture_id,
        session_commitment=args[5].session_commitment,
        reviewed_digest=evidence.review.proposal_digest, approved_at=NOW)
    return path, store, args, factory, evidence, approval


def test_shared_link_confirmation_review_reopens_approves_and_dispatches(tmp_path, monkeypatch):
    path, store, args, factory, evidence, approval = reviewed(tmp_path)
    store.close()
    assert args[0].ha_user_id.encode() not in path.read_bytes()
    assert str(args[4].proof_id).encode() not in path.read_bytes()
    store = journal(path)
    try:
        assert store.load_review(factory, args[0], args[1].ceremony_id, args[5].session_commitment) == evidence
        value = store.approve_review(factory, args[0], args[1].ceremony_id, approval)
        assert store.inspect(value).state == 'prepared'
        store.close()
        store = journal(path)
        saved = store.load_review(factory, args[0], args[1].ceremony_id, args[5].session_commitment)
        assert saved.approval == approval
        assert store.approve_review(factory, args[0], args[1].ceremony_id, saved.approval) == value
        db, engine = database(monkeypatch, value)
        assert asyncio.run(store.dispatch(value.ceremony_id, db)).link_id == value.link_id
        assert engine.events.count('kernel') == 1
    finally: store.close()


@pytest.mark.parametrize('change', ['owner', 'issuer', 'session', 'missing', 'key', 'expired'])
def test_shared_link_confirmation_review_rejects_wrong_context(tmp_path, change):
    path, store, args, factory, evidence, approval = reviewed(tmp_path)
    identity, ceremony, session = args[0], args[1].ceremony_id, args[5].session_commitment
    if change == 'owner': identity = replace(identity, ha_user_id='guest')
    if change == 'issuer': identity = replace(identity, ha_issuer_id='home-assistant:victoria', site_id='victoria')
    if change == 'session': session = 'e'*64
    if change == 'missing': ceremony = uuid4()
    if change == 'key': factory = SharedLinkConfirmationPreparer(b'q'*32, key_id='review-v1', now=lambda: NOW)
    if change == 'expired': factory = SharedLinkConfirmationPreparer(b'p'*32, key_id='review-v1', now=lambda: args[2].expires_at)
    try:
        with pytest.raises(ValueError): store.load_review(factory, identity, ceremony, session)
        assert store._db.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
    finally: store.close()


def test_shared_link_confirmation_review_competing_approval_cannot_change_original(tmp_path):
    path, store, args, factory, evidence, approval = reviewed(tmp_path)
    second = journal(path)
    try:
        with pytest.raises(ValueError, match='already retained'): second.review(factory, *args)
        value = store.approve_review(factory, args[0], args[1].ceremony_id, approval)
        with pytest.raises(ValueError, match='conflict'):
            second.approve_review(factory, args[0], args[1].ceremony_id,
                approval.model_copy(update={'approved_at': NOW-timedelta(microseconds=1)}))
        assert second.approve_review(factory, args[0], args[1].ceremony_id, approval) == value
        store.claim(value.ceremony_id)
        assert second.approve_review(factory, args[0], args[1].ceremony_id, approval) == value
        assert second.inspect(value).state == 'indeterminate'
        with pytest.raises(ValueError, match='uncertain'): second.claim(value.ceremony_id)
    finally:
        store.close()
        second.close()


@pytest.mark.parametrize('table', ['reviews', 'requests'])
def test_shared_link_confirmation_review_atomic_approval_rolls_back_on_disk_failure(tmp_path, table):
    path, store, args, factory, evidence, approval = reviewed(tmp_path)
    try:
        operation = 'UPDATE' if table == 'reviews' else 'INSERT'
        # Fixed test table names, no caller-selected SQL.
        store._db.execute(f"CREATE TRIGGER fail_write BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match='disk failure'):
            store.approve_review(factory, args[0], args[1].ceremony_id, approval)
        assert store._db.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
        assert store.load_review(factory, args[0], args[1].ceremony_id, args[5].session_commitment).approval is None
        store._db.execute('DROP TRIGGER fail_write')
        value = store.approve_review(factory, args[0], args[1].ceremony_id, approval)
        assert store.inspect(value).state == 'prepared'
    finally: store.close()


def test_shared_link_confirmation_review_schema_one_upgrade_preserves_uncertain_request(tmp_path):
    path = tmp_path/'legacy.sqlite'
    value = request()
    store = journal(path)
    store.retain(value)
    store.claim(value.ceremony_id)
    store.close()
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TABLE reviews')
        connection.execute('PRAGMA user_version=1')
    # Wrong keys must not upgrade the schema.
    with pytest.raises(InvalidTag): journal(path, key=b'x'*32)
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 1
    store = journal(path)
    try:
        assert store._db.execute('PRAGMA user_version').fetchone()[0] == 2
        assert store.inspect(value).state == 'indeterminate'
        assert store._db.execute('SELECT count(*) FROM reviews').fetchone()[0] == 0
    finally: store.close()


def test_shared_link_confirmation_review_missing_approved_request_cannot_be_recreated(tmp_path):
    path, store, args, factory, evidence, approval = reviewed(tmp_path)
    try:
        value = store.approve_review(factory, args[0], args[1].ceremony_id, approval)
        store._db.execute('DELETE FROM requests WHERE id=?', (str(value.ceremony_id),))
        with pytest.raises(ValueError, match='inconsistent'):
            store.approve_review(factory, args[0], args[1].ceremony_id, approval)
        assert store._db.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
    finally: store.close()


def test_shared_link_confirmation_review_capacity_and_ciphertext_tampering(tmp_path):
    path, store, args, factory, evidence, approval = reviewed(tmp_path)
    try:
        store._db.executemany('INSERT INTO reviews VALUES (?,?,?,?)',
            [(str(uuid4()), b'nonce', b'cipher', 'digest') for _ in range(1023)])
        another = context()
        another[1] = another[1].model_copy(update={'ceremony_id': uuid4()})
        another[2] = another[2].model_copy(update={'ceremony_id': another[1].ceremony_id})
        with pytest.raises(ValueError, match='capacity'): store.review(factory, *another)
        store._db.execute('UPDATE reviews SET ciphertext=? WHERE id=?', (b'tampered', str(args[1].ceremony_id)))
        with pytest.raises(InvalidTag):
            store.approve_review(factory, args[0], args[1].ceremony_id, approval)
        assert store._db.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
    finally: store.close()
