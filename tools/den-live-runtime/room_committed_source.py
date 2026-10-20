"""Read the exact accepted room commit from the sole existing Home Journal.

This is the input boundary for canonical command projection, not an authority,
fixture mixer, executor or source of achieved native state. A prepared worker
body is usable only while its exact receipt, checkpoint and publication remain
current in the same Journal. No archive row or JSON label alone suffices.
"""
from copy import deepcopy
from dataclasses import dataclass

from journal import canonical, digest, finite


class RoomCommitUnavailable(ValueError):
    pass


def need(value, reason):
    if not value:
        raise RoomCommitUnavailable(reason)


@dataclass(frozen=True)
class RoomCommitBinding:
    scope: dict
    worker_id: str
    producer_id: str
    configuration_digest: str
    manifest_digest: str
    worker_version: str
    fixture_ids: tuple


class CommittedRoomSource:
    """Fixed installation binding; no caller-selected Journal or upstream.

    ``current`` is the installed supervisor's currentness capability. It must
    raise or return false after quiesce/retirement, independently of leases.
    Installation changes require a new object; no automatic scope migration.
    """
    def __init__(self, journal, *, binding, current, clock=None):
        need(type(binding) is RoomCommitBinding and callable(current), 'fixed_room_source_binding_required')
        self.journal, self.binding = journal, deepcopy(binding)
        self.current_scope = current
        self.clock = journal.clock if clock is None else clock
        need(callable(self.clock), 'fixed_room_source_clock_required')
        b = self.binding
        need(type(b.scope) is dict and set(b.scope) == {'site_id','room_id','session_id','domain_epoch'}
             and all(type(v) is str and 0 < len(v) <= 160 for v in b.scope.values()), 'exact_room_scope_required')
        need(type(b.fixture_ids) is tuple and len(b.fixture_ids) == len(set(b.fixture_ids)) == 4
             and all(type(v) is str and 0 < len(v) <= 128 for v in b.fixture_ids), 'four_fixed_room_members_required')
        self._commit = None
        with journal.lock:
            self._installation()

    def _installation(self):
        j, b = self.journal, self.binding
        need(not j._recorder_failed and callable(j._recorder), 'room_recorder_not_ready')
        need(self.current_scope() is True, 'room_session_not_current')
        ingress = getattr(j, 'dynamic', None)
        worker = getattr(ingress, 'worker', None)
        need(ingress is not None and worker is not None and ingress.identity == b.scope,
             'installed_room_scope_changed')
        policy = worker.policy
        need(policy['worker_id'] == b.worker_id and policy['producer_id'] == b.producer_id
             and policy['config_digest'] == b.configuration_digest
             and ingress.config['manifest_ref']['digest'] == b.manifest_digest
             and worker.worker_version == b.worker_version
             and tuple(f['fixture_id'] for f in policy['room_policy']['fixtures']) == b.fixture_ids,
             'installed_room_configuration_changed')
        return worker

    def install_commit(self, commit):
        """Adopt only exact bytes of an independently accepted current commit."""
        need(type(commit) is dict, 'complete_original_room_commit_required')
        original = deepcopy(commit)
        with self.journal.lock:
            result = self._read(original)
            self._commit = original
            return result

    def current(self):
        with self.journal.lock:
            need(self._commit is not None, 'no_accepted_room_commit_selected')
            return self._read(self._commit)

    def _read(self, commit):
        import json
        j, b = self.journal, self.binding
        worker = self._installation()
        now = self.clock()
        need(finite(now) and now >= 0, 'qualified_room_semantic_clock_required')
        need(commit.get('schema') == worker.commit_type and commit.get('worker_id') == b.worker_id
             and commit.get('producer_id') == b.producer_id
             and all(commit.get(k) == b.scope[k] for k in ('site_id','room_id'))
             and commit.get('stream_epoch') == j._meta('epoch'), 'original_room_commit_scope_changed')
        rows = j.db.execute('SELECT digest,receipt FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?',
            (b.producer_id, commit['stream_epoch'], 'worker-commit/'+commit['id'])).fetchall()
        need(len(rows) == 1 and rows[0]['digest'] == digest(commit), 'room_commit_not_durably_accepted')
        receipt = json.loads(rows[0]['receipt'])
        need(receipt.get('status') == 'accepted' and receipt.get('id') == commit['id']
             and receipt.get('commit_digest') == digest(commit), 'room_commit_receipt_mismatch')
        cp = commit['checkpoint']
        rows = j.db.execute('SELECT checkpoint,input_cursor,context_seq FROM workers WHERE worker_id=? AND site_id=? AND room_id=?',
            (b.worker_id,b.scope['site_id'],b.scope['room_id'])).fetchall()
        need(len(rows) == 1 and canonical(json.loads(rows[0]['checkpoint'])) == canonical(cp)
             and rows[0]['input_cursor'] == commit['input_cursor'] == receipt['input_cursor']
             and rows[0]['context_seq'] == receipt['context_seq'], 'room_commit_checkpoint_superseded')
        need(cp['scope'] == b.scope and cp['config_digest'] == b.configuration_digest
             and cp['manifest_ref']['digest'] == b.manifest_digest
             and cp['authority_generation'] == j.revocation_generation(b.scope['site_id'],b.scope['room_id']),
             'room_commit_authority_changed')
        generations = {row['source_id']:row['generation'] for row in j.db.execute('SELECT source_id,generation FROM live_sources')}
        need(cp['state']['engagement']['source_generations'] == generations, 'room_commit_source_generation_changed')
        # A newly admitted source head/hazard must be consumed by the sole
        # worker before any command can be based on its previous cut.
        newer = j.db.execute('SELECT 1 FROM live_heads WHERE body IS NOT NULL AND cursor>? LIMIT 1',
                             (commit['input_cursor'],)).fetchone()
        need(newer is None, 'new_room_input_requires_worker_commit')
        events = [e for e in commit['publications'] if e.get('payload_type') == worker.decision_type]
        need(len(events) == 1, 'one_original_room_decision_required')
        event, decision = events[0], events[0]['payload']
        need(all(decision[k] == v for k,v in b.scope.items()) and decision['worker_id'] == b.worker_id
             and decision['t'] == cp['last_committed_at'] <= now, 'room_decision_scope_or_time_changed')
        rows = j.db.execute('SELECT body FROM live_worker_current WHERE payload_type=?', (worker.decision_type,)).fetchall()
        need(len(rows) == 1, 'room_decision_not_current')
        publication = json.loads(rows[0]['body'])
        need(publication['id'] == event['id'] and publication['producer_id'] == b.producer_id
             and publication['payload_digest'] == digest(decision)
             and canonical(publication['payload']) == canonical(decision)
             and publication['committed_at'] <= now, 'room_decision_publication_mismatch')
        output = decision['room_output']
        need(output['schema'] == 'home-live-room-output-1' and type(output['version']) is int and output['version'] == 1
             and tuple(f['fixture_id'] for f in output['fixtures']) == b.fixture_ids
             and output['sample_at'] == decision['t'], 'exact_committed_room_inventory_required')
        ref = dict(configuration_digest=b.configuration_digest, manifest_digest=b.manifest_digest,
            worker_version=b.worker_version, room_output_schema=output['schema'],
            commit_id=commit['id'], commit_body_sha256=digest(commit), decision_sha256=digest(decision),
            room_output_revision_id=output['revision_id'],room_output_sha256=digest(output))
        self._installation()  # A currentness check cannot keep a retired source alive.
        completed = self.clock()
        need(finite(completed) and completed >= now, 'room_source_clock_rollback')
        need(cp['authority_generation'] == j.revocation_generation(b.scope['site_id'],b.scope['room_id'])
             and generations == {row['source_id']:row['generation'] for row in j.db.execute('SELECT source_id,generation FROM live_sources')},
             'room_commit_authority_changed_during_read')
        return deepcopy(dict(schema='home-accepted-room-command-source-1',version=1,room_commit_ref=ref,
            home_scope=b.scope,commit=commit,receipt=receipt,publication=publication,decision=decision,
            sample_at=decision['t'],checked_at=completed,physical_permission=False,native_achieved_truth=False))

    def sample_components(self, fixture_id):
        """Return current *sampled* domains with explicit expired/unavailable rows.

        Desired endpoints and scene nominal settings are deliberately absent.
        No unavailable row creates a zero-duty command or absence assertion.
        """
        value = self.current()
        need(fixture_id in self.binding.fixture_ids, 'foreign_room_fixture')
        fixture = next(f for f in value['decision']['room_output']['fixtures'] if f['fixture_id'] == fixture_id)
        now = value['checked_at']
        rows = []
        for domain in fixture['domains']:
            usable = domain['sample'] is not None and now < domain['valid_until']
            rows.append(dict(domain=domain['domain'],status=domain['status'] if usable else 'unavailable',
                reason=domain['reason'] if usable else 'committed_sample_unavailable_or_expired',
                sample=deepcopy(domain['sample']) if usable else None,valid_until=domain['valid_until'],
                evidence_refs=deepcopy(domain['evidence_refs']),assessment_refs=deepcopy(domain['assessment_refs'])))
        return dict(schema='home-committed-fixture-components-1',version=1,fixture_id=fixture_id,
            room_commit_ref=value['room_commit_ref'],sample_at=value['decision']['t'],checked_at=now,
            domains=rows,physical_permission=False,native_achieved_truth=False)
