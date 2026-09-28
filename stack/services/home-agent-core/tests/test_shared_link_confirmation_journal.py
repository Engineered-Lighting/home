"""Actual SQLite journal checks; PostgreSQL and runtime admission remain separate."""
import asyncio
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag

from app.crypto import FieldCipher
from app.shared_link_confirmation_journal import SharedLinkConfirmationJournal, PURPOSE
from app.shared_link_confirmation import SharedLinkConfirmationReceipt
from app.shared_link_journal import SharedLinkJournal
from .test_shared_link_confirmation_adapter import request, database


def journal(path, key=b'j'*32):
    return SharedLinkConfirmationJournal(path, cipher=FieldCipher(key))


@pytest.mark.parametrize('outcome', ['found', 'missing', 'failure', 'mismatch'])
def test_confirmation_journal_reconciles_by_lookup_without_resending(tmp_path, monkeypatch, outcome):
    value = request()
    db, engine = database(monkeypatch, value)
    engine.expected_function = 'identity.inspect_shared_link_confirmation_v1'
    if outcome == 'mismatch': engine.row['link_id'] = uuid4()
    def lookup():
        if outcome == 'failure': raise ConnectionError('lost lookup')
        return None if outcome == 'missing' else engine.row
    engine.one_or_none = lookup
    path = tmp_path/'confirm.sqlite'
    store = journal(path)
    store.retain(value)
    store.claim(value.ceremony_id)
    store.close()
    store = journal(path)
    try:
        if outcome in ('failure', 'mismatch'):
            with pytest.raises(ConnectionError if outcome == 'failure' else ValueError):
                asyncio.run(store.reconcile(value, db))
        else:
            observed = asyncio.run(store.reconcile(value, db))
            assert observed.state == ('completed' if outcome == 'found' else 'indeterminate')
        assert store.inspect(value).state == ('completed' if outcome == 'found' else 'indeterminate')
        with pytest.raises(ValueError, match='uncertain'): asyncio.run(store.dispatch(value.ceremony_id, db))
        assert engine.events.count('kernel') == 1
    finally: store.close()


def test_confirmation_journal_ciphertext_reopen_exact_request_and_no_resend(tmp_path):
    path = tmp_path/'confirm.sqlite'
    value = request().model_copy(update={'echo_subject':'distinct-private-owner'})
    store = journal(path)
    assert store.inspect(value).state == 'not_found'
    assert store.retain(value) == 'prepared'
    store.close()
    contents = path.read_bytes()
    for secret in (value.echo_subject,value.session_commitment,value.confirmation_commitment,str(value.link_id)):
        assert secret.encode() not in contents
    reopened = journal(path)
    try:
        assert reopened.claim(value.ceremony_id) == value
        assert reopened.inspect(value).state == 'indeterminate'
        with pytest.raises(ValueError, match='uncertain'):
            reopened.claim(value.ceremony_id)
    finally: reopened.close()


def test_confirmation_journal_two_connections_and_conflicting_ids(tmp_path):
    path = tmp_path/'confirm.sqlite'
    first, second = journal(path), journal(path)
    value = request()
    try:
        first.retain(value)
        assert second.retain(value) == 'prepared'
        for changed in ({'link_id':uuid4()},{'session_commitment':'d'*64},{'expected_generation':9}):
            with pytest.raises(ValueError, match='conflict'):
                second.retain(value.model_copy(update=changed))
        assert first.claim(value.ceremony_id) == value
        with pytest.raises(ValueError, match='uncertain'): second.claim(value.ceremony_id)
    finally:
        first.close(); second.close()


def test_confirmation_journal_actual_adapter_commits_claim_before_sql_and_reopens_receipt(tmp_path, monkeypatch):
    path = tmp_path/'confirm.sqlite'
    value = request()
    db, engine = database(monkeypatch,value)
    store = journal(path)
    store.retain(value)
    original_connect = engine.connect
    def connect():
        other = journal(path)
        try: assert other.inspect(value).state == 'indeterminate'
        finally: other.close()
        return original_connect()
    monkeypatch.setattr(engine,'connect',connect)
    result = asyncio.run(store.dispatch(value.ceremony_id,db))
    assert engine.parameters == value.model_dump(exclude={'expected_generation'})
    assert engine.events == ['connect','isolation','begin','kernel','commit']
    store.close()
    reopened = journal(path)
    try:
        observation = reopened.inspect(value)
        assert observation.state == 'completed' and observation.recorded_receipt == result
        assert str(value.link_id) not in repr(observation)
        with pytest.raises(ValueError, match='uncertain'):
            asyncio.run(reopened.dispatch(value.ceremony_id,db))
        assert engine.events.count('kernel') == 1
    finally: reopened.close()


@pytest.mark.parametrize('failure',[ConnectionError('fixture'),asyncio.CancelledError()])
def test_confirmation_journal_failed_or_cancelled_dispatch_never_retries(tmp_path,monkeypatch,failure):
    path=tmp_path/'confirm.sqlite'; value=request()
    db,engine=database(monkeypatch,value)
    def fail_result(): raise failure
    monkeypatch.setattr(engine,'one',fail_result)
    store=journal(path); store.retain(value)
    with pytest.raises(type(failure)): asyncio.run(store.dispatch(value.ceremony_id,db))
    store.close(); reopened=journal(path)
    try:
        assert reopened.inspect(value).state=='indeterminate'
        with pytest.raises(ValueError,match='uncertain'): asyncio.run(reopened.dispatch(value.ceremony_id,db))
        assert engine.events.count('kernel')==1
    finally: reopened.close()


def test_confirmation_journal_completion_storage_failure_is_indeterminate(tmp_path,monkeypatch):
    path=tmp_path/'confirm.sqlite'; value=request()
    db,engine=database(monkeypatch,value)
    store=journal(path); store.retain(value)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER fail_completion BEFORE UPDATE OF receipt_nonce ON requests BEGIN SELECT RAISE(ABORT,'disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError): asyncio.run(store.dispatch(value.ceremony_id,db))
    store.close(); reopened=journal(path)
    try:
        assert reopened.inspect(value).state=='indeterminate'
        with pytest.raises(ValueError,match='uncertain'): asyncio.run(reopened.dispatch(value.ceremony_id,db))
        assert engine.events.count('kernel')==1
    finally: reopened.close()


def test_confirmation_journal_wrong_key_and_issuance_file_rejected_without_mutation(tmp_path):
    path=tmp_path/'confirm.sqlite'; store=journal(path); store.retain(request()); store.close()
    before=path.read_bytes()
    with pytest.raises(InvalidTag): journal(path,key=b'k'*32)
    assert path.read_bytes()==before
    other=tmp_path/'issuance.sqlite'
    issuance=SharedLinkJournal(other,cipher=FieldCipher(b'j'*32)); issuance.close()
    before=other.read_bytes()
    with pytest.raises(InvalidTag): journal(other)
    assert other.read_bytes()==before


@pytest.mark.parametrize('setup',['CREATE TABLE alien (x TEXT)','PRAGMA user_version=99'])
def test_confirmation_journal_unknown_schema_rejected_without_mutation(tmp_path,setup):
    path=tmp_path/'unknown.sqlite'
    with sqlite3.connect(path) as conn: conn.execute(setup)
    before=path.read_bytes()
    with pytest.raises(ValueError,match='schema'): journal(path)
    assert path.read_bytes()==before


@pytest.mark.parametrize('state',['prepared','dispatching','completed'])
def test_confirmation_journal_partial_receipt_is_not_trusted(tmp_path,state):
    path=tmp_path/'confirm.sqlite'; value=request(); store=journal(path); store.retain(value)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE requests SET state=?,receipt_nonce=? WHERE id=?',(state,b'partial',str(value.ceremony_id)))
    try:
        with pytest.raises(ValueError,match='inconsistent'): store.inspect(value)
        with pytest.raises(ValueError): store.retain(value)
        with pytest.raises(ValueError): store.claim(value.ceremony_id)
    finally: store.close()


@pytest.mark.parametrize('change',[{'link_id':uuid4()},{'authorization_generation':9},{'revision':2}])
def test_confirmation_journal_revalidates_encrypted_historical_receipt(tmp_path,monkeypatch,change):
    path=tmp_path/'confirm.sqlite'; value=request(); store=journal(path); store.retain(value)
    db,_=database(monkeypatch,value)
    receipt=asyncio.run(store.dispatch(value.ceremony_id,db)).model_copy(update=change)
    cipher=FieldCipher(b'j'*32)
    sealed=cipher.seal(receipt.model_dump_json().encode(),purpose=PURPOSE+':receipt',artifact_id=str(value.ceremony_id))
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE requests SET receipt_nonce=?,receipt_ciphertext=?,receipt_digest=? WHERE id=?',(sealed.nonce,sealed.ciphertext,sealed.sha256,str(value.ceremony_id)))
    try:
        with pytest.raises(ValueError,match='mismatch'): store.inspect(value)
    finally: store.close()


def test_confirmation_journal_capacity_and_durability_settings(tmp_path):
    store=journal(tmp_path/'confirm.sqlite'); value=request()
    try:
        assert store._db.execute('PRAGMA page_size').fetchone()[0]==4096
        assert store._db.execute('PRAGMA max_page_count').fetchone()[0]==4096
        assert store._db.execute('PRAGMA synchronous').fetchone()[0]==2
        assert store._db.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
        for _ in range(1023): store.retain(request())
        store.retain(value)
        with pytest.raises(ValueError,match='capacity'): store.retain(request())
        assert store.retain(value)=='prepared'
    finally: store.close()


def test_confirmation_journal_invalid_input_does_not_create_file(tmp_path):
    with pytest.raises(ValueError): journal(Path('relative.sqlite'))
    path=tmp_path/'confirm.sqlite'
    class DerivedCipher(FieldCipher): pass
    with pytest.raises(ValueError): SharedLinkConfirmationJournal(path,cipher=DerivedCipher(b'j'*32))
    assert not path.exists()
