"""Trusted, recorded operating-session fence; never a browser-selected port."""
from __future__ import annotations

from copy import deepcopy
import json

from journal import JournalError, canonical, digest, finite, identifier


def refuse(code, message, status=409):
    raise JournalError(code, message, status)


class ManualControlAuthority:
    def __init__(self, journal, *, identity, manifest_ref, policy, retire):
        if (not isinstance(identity, dict) or set(identity) != {'site_id','room_id','session_id','domain_epoch'}
                or identity != journal.live_config['manifest']['identity'] or manifest_ref != journal.live_config['manifest_ref']
                or not callable(retire) or not isinstance(policy, dict)
                or not finite(policy.get('operating_not_after')) or policy['operating_not_after'] <= 0):
            refuse('invalid_manual_authority', 'fixed commissioned control scope required')
        for value in identity.values():
            identifier(value, 'control_scope')
        self.j = journal
        self.identity, self.manifest_ref, self.policy = deepcopy(identity), deepcopy(manifest_ref), deepcopy(policy)
        self._retire = retire
        binding = {'scope':self.identity,'manifest_ref':self.manifest_ref,'policy_ref':self.policy['policy_ref'],
                   'stream_epoch':journal._meta('epoch'),'operating_not_after':self.policy['operating_not_after']}
        journal.db.executescript('''
            CREATE TABLE IF NOT EXISTS live_manual_control_meta(
                id INTEGER PRIMARY KEY CHECK(id=1),binding TEXT NOT NULL,generation INTEGER NOT NULL,active INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS live_manual_control_operations(
                operation_id TEXT PRIMARY KEY,request_digest TEXT NOT NULL,request TEXT NOT NULL,receipt TEXT NOT NULL);
        ''')
        def initialize():
            row = journal.db.execute('SELECT binding FROM live_manual_control_meta WHERE id=1').fetchone()
            if row is not None and row['binding'] != canonical(binding):
                refuse('manual_authority_binding_conflict', 'control history belongs to another exact operating session')
            journal.db.execute('INSERT OR IGNORE INTO live_manual_control_meta VALUES(1,?,0,0)', (canonical(binding),))
        journal._transaction(initialize)

    def state(self):
        with self.j.lock:
            row = self.j.db.execute('SELECT generation,active FROM live_manual_control_meta WHERE id=1').fetchone()
            return {'control_generation':row['generation'],'control_active':bool(row['active'])}

    def activate(self, operation_id):
        return self._apply(operation_id, 'activate', None)

    def retire(self, operation_id, reason):
        identifier(reason, 'control_retirement_reason')
        return self._apply(operation_id, 'retire', reason)

    def _apply(self, operation_id, operation, reason):
        identifier(operation_id, 'control_operation_id')
        event = {'schema':'home-live-control-operation-1','version':1,'id':operation_id,'operation':operation,
                 'scope':self.identity,'manifest_ref':self.manifest_ref,'policy_ref':self.policy['policy_ref'],'reason':reason}
        request_digest = digest(event)
        j = self.j
        with j.lock:
            original = j.db.execute('SELECT request_digest,request,receipt FROM live_manual_control_operations WHERE operation_id=?',
                                    (operation_id,)).fetchone()
            if original is not None:
                if original['request_digest'] != request_digest or original['request'] != canonical(event):
                    refuse('control_operation_conflict', 'an immutable control operation ID cannot change its request')
                return json.loads(original['receipt'])
            if j._recorder is None or j._recorder_failed:
                refuse('recorder_unavailable', 'original operating control requires a healthy attached recorder', 503)
            def apply():
                before, now = self.state(), j.clock()
                if not finite(now):
                    refuse('manual_clock_unavailable', 'server availability clock required')
                if operation == 'activate':
                    if before != {'control_generation':0,'control_active':False}:
                        refuse('manual_authority_terminal', 'same operating session cannot activate twice')
                    if not now < self.policy['operating_not_after']:
                        refuse('manual_operating_session_expired', 'original operating horizon has expired')
                    if self.policy['operating_not_after']-now > 120:
                        refuse('manual_operating_session_unbounded', 'activation must fit the bounded original operating session')
                elif not before['control_active'] and before['control_generation'] != 0:
                    refuse('manual_authority_terminal', 'operating control is already retired')
                after = {'control_generation':before['control_generation']+1,'control_active':operation=='activate'}
                receipt = {'schema':'home-live-control-receipt-1','version':1,'id':operation_id,
                           'request_digest':request_digest,'stream_epoch':j._meta('epoch'),'applied_at':now,
                           'before':before,'after':after,'operating_not_after':self.policy['operating_not_after']}
                j.db.execute('UPDATE live_manual_control_meta SET generation=?,active=? WHERE id=1',
                             (after['control_generation'],int(after['control_active'])))
                if operation == 'retire':
                    self._retire(control_generation=after['control_generation'],retired_at=now)
                j.db.execute('INSERT INTO live_manual_control_operations VALUES(?,?,?,?)',
                             (operation_id,request_digest,canonical(event),canonical(receipt)))
                j._observe_control_operation(event, receipt)
                return receipt
            return apply() if j.db.in_transaction and j._recorded_control_operations is not None else j._transaction(apply)
