"""Config12: qualified PC support for the same conservative room authority.

Private originals travel only through the sole worker cut/checkpoint. Facts,
public decisions, room execution and the shared temporal algorithm keep their
separate authorities. This boundary independently validates the PC clause and
physical-input progress against the trusted preceding checkpoint.
"""
from copy import deepcopy
import json

from journal import canonical, digest
from live_pc_support import validate_pc_support_policy, pc_support_clause
from live_scene import validate_scene_worker_grant
from live_worker import reject
from room_delivery import validate_room_worker_policy
from room_worker import RoomLiveDeliveryWorkerBoundary
from schema_registry import LIVE_PC_SUPPORT_VERSIONS, LIVE_PC_SUPPORT_PUBLICATIONS


PC_TYPES = frozenset(LIVE_PC_SUPPORT_VERSIONS)


def validate_pc_worker_policy(manifest, registry, *, allow_assessed_subset=False):
    worker = manifest['worker']
    if (not registry.pc_support_interface_digest
            or manifest.get('pc_support_interface_digest') != registry.pc_support_interface_digest
            or worker.get('pc_support_schema_pins') != registry.pc_support_schema_pins):
        raise ValueError('exact PC support successor pins required')
    validate_pc_support_policy(worker['pc_support_policy'], manifest['sources'])
    registry.validate(worker['pc_support_policy'])
    base = deepcopy(manifest)
    del base['pc_support_interface_digest']
    del base['worker']['pc_support_policy']
    del base['worker']['pc_support_schema_pins']
    validate_room_worker_policy(base, registry, allow_assessed_subset=allow_assessed_subset)


def validate_pc_grant(grant, config):
    if grant.payload_types & (PC_TYPES - LIVE_PC_SUPPORT_PUBLICATIONS) and 'worker_commit' not in grant.operations:
        # Shared original room leaves/lifecycle/intent are also ordinary granted
        # inputs. Only the new private types are restricted here.
        private = PC_TYPES - {'home-live-room-output-1', 'home-live-room-snapshot-1',
                             'home-live-person-lifecycle-1', 'home-live-intent-event-2'} - LIVE_PC_SUPPORT_PUBLICATIONS
        if grant.payload_types & private:
            raise ValueError('private PC proof state requires the sole worker capability')
    if 'home-live-shadow-decision-11' in grant.payload_types and 'home-live-geometry-1' not in grant.payload_types:
        raise ValueError('complete PC room decision requires separate geometry authority')


def validate_pc_worker_grant(grant, config):
    validate_scene_worker_grant(grant, config)
    if not PC_TYPES <= grant.payload_types:
        raise ValueError('PC worker requires its complete explicit successor closure')


class PcSupportWorkerBoundary(RoomLiveDeliveryWorkerBoundary):
    schema_types = RoomLiveDeliveryWorkerBoundary.schema_types | PC_TYPES
    output_types = frozenset({'home-live-engagement-state-1', 'home-live-shadow-decision-11'})
    decision_type = 'home-live-shadow-decision-11'
    checkpoint_type = 'home-live-worker-checkpoint-11'
    commit_type = 'home-live-worker-commit-11'
    cut_type = 'home-live-input-cut-11'
    version = 11
    worker_version = 'home-live-context-worker-11.0.0'
    module_names = RoomLiveDeliveryWorkerBoundary.module_names | {'app/src/home-live-pc-support.js'}

    def configuration_description(self, nested):
        value = super().configuration_description(nested)
        value.update(version=self.worker_version, pc_support_policy=self.policy['pc_support_policy'],
                     pc_support_schema_pins=self.policy['pc_support_schema_pins'],
                     pc_support_interface_digest=self.j.schemas.pc_support_interface_digest)
        return value

    @staticmethod
    def _candidate(payload):
        return (payload.get('schema') == 'home-live-fact-1' and payload.get('status') == 'supplied'
                and payload.get('kind') == 'pc_input_interaction' and payload.get('value') == 'attributed_recent')

    def private_cut_material(self, items):
        records = {}
        bindings = self.policy['pc_support_policy']['bindings']
        for publication in items:
            payload = publication['payload']
            if not self._candidate(payload):
                continue
            configured = [binding for binding in bindings if binding['source_id'] == payload['source_id']
                          and binding['method_ref'] == payload['method_ref']]
            if len(configured) != 1:
                reject('invalid_pc_support', 'PC input source/method is not commissioned')
            component = configured[0]['proof_component_id']
            refs = [ref for ref in payload['original_receipt_refs'] if ref['component_id'] == component]
            if len(refs) != 1:
                reject('invalid_pc_support', 'one exact original PC input proof required')
            ref = refs[0]
            try:
                record = self.j.live_receipt_resolver(ref['store_id'], ref['receipt_id'])
            except Exception:
                reject('receipt_unavailable', 'original PC input proof is unavailable')
            if (record.get('ref') != ref or 'pc_input_original' not in record
                    or digest(record['pc_input_original']) != ref['digest']):
                reject('receipt_mismatch', 'original PC proof changed after admission')
            key = canonical([ref['store_id'], ref['receipt_id']])
            admitted = self.j.db.execute('SELECT binding_digest FROM live_original_receipts WHERE key=?', (key,)).fetchone()
            if admitted is None or admitted[0] != digest(record):
                reject('receipt_mismatch', 'private proof metadata must equal its immutable admitted original')
            key = canonical(ref)
            if key in records and records[key] != record:
                reject('receipt_mismatch', 'original PC proof identity conflict')
            records[key] = record
        if len(records) > 16:
            reject('journal_full', 'bounded private PC proof catalog exhausted', 507)
        return {'pc_input_originals': [deepcopy(records[key]) for key in sorted(records)]}

    def _original(self, payload, checkpoint):
        refs = {canonical(ref) for ref in payload['original_receipt_refs']}
        found = [record['pc_input_original'] for record in checkpoint['state']['pc_input_originals']
                 if canonical(record['ref']) in refs]
        if len(found) != 1:
            reject('invalid_pc_support', 'exact private original for the chosen fact required')
        return found[0]

    def qualified_pc_support(self, facts, checkpoint, t):
        if len(facts) != 1 or not self._candidate(facts[0]['payload']):
            return False
        payload = facts[0]['payload']
        try:
            return pc_support_clause(payload, self._original(payload, checkpoint),
                                     self.policy['pc_support_policy'], now=t) is not None
        except ValueError:
            reject('invalid_pc_support', 'chosen PC support lacks its original qualified input')

    def _private_inputs(self, checkpoint, publications):
        super()._private_inputs(checkpoint, publications)
        expected = self.private_cut_material(list(publications.values()))['pc_input_originals']
        if checkpoint['state']['pc_input_originals'] != expected:
            reject('invalid_pc_support', 'checkpoint must preserve the exact private input originals')

    def _checkpoint(self, cp, commit, next_seq, old, now):
        if (cp['pc_support_schema_pins'] != self.policy['pc_support_schema_pins']
                or cp['pc_support_policy_digest'] != digest(self.policy['pc_support_policy'])):
            reject('checkpoint_conflict', 'PC checkpoint policy/schema differs from commissioning')
        t = super()._checkpoint(cp, commit, next_seq, old, now)
        snapshot = cp['state']['engagement']['reducer']
        expected_config = {'version': 'home-engagement-reducer-1.1.0', 'profile': 'home-live-shadow-1',
            'identity': self.ingress.identity, 'timing': {'enter_s': 5, 'exit_s': 20, 'refinement_enter_s': 10, 'refinement_hold_s': 30},
            'policy': self.policy['engagement_policy'], 'root_time_limit': 1024, 'blocking_repairs': True,
            'lifecycle_policy': self.policy['lifecycle_policy'], 'pc_support_policy': self.policy['pc_support_policy']}
        if snapshot['version'] != expected_config['version'] or snapshot['config_digest'] != digest(expected_config):
            reject('checkpoint_conflict', 'nested reducer must bind the exact PC policy and shared temporal version')
        cache = snapshot['state']['cache']
        previous = old['state']['engagement']['reducer']['state']['relations'] if old else {}
        from live_lifecycle import commissioned_method
        for key, relation in snapshot['state']['relations'].items():
            chosen, prior = relation.get('chosen_clause'), previous.get(key)
            prior_dwell = prior['enter_dwell'] if prior else 0
            if relation['state'] not in {'candidate', 'supported'}:
                # A paused/no-clause state cannot launder dwell or seed a fake
                # earlier progress tick for a later positive PC assertion.
                if (relation['enter_dwell'] > prior_dwell + 1e-7 or relation['enter_observed'] is not None
                        or relation['enter_progress_tick'] is not None
                        or not set(relation['enter_roots']) <= set(prior['enter_roots'] if prior else [])):
                    reject('invalid_pc_support', 'inactive entry phase cannot invent dwell, input roots or clocks')
                continue
            if not chosen:
                reject('invalid_pc_support', 'positive entry phase needs its exact original clause')
            facts = [fact for fact in cache.values() if fact['id'] in chosen['fact_ids']]
            if len(facts) != 1:
                reject('invalid_pc_support', 'one selected original positive clause required')
            fact = facts[0]
            if self._candidate(fact):
                try:
                    expected = pc_support_clause(fact, self._original(fact, cp), self.policy['pc_support_policy'], now=t)
                except ValueError:
                    reject('invalid_pc_support', 'checkpoint PC clause proof is invalid')
            else:
                # Cover every entry phase in this successor, so a forged
                # legacy-looking clause cannot be used to seed later PC dwell.
                expected = {'id': 'clause:' + digest({'kind': fact['kind'], 'ids': [fact['id']]})[:24],
                    'kind': fact['kind'], 'fact_ids': [fact['id']], 'source_ids': [fact['source_id']],
                    'sensor_roots': sorted(set(fact['sensor_roots'])), 'observation_roots': sorted(set(fact['observation_roots'])),
                    'progression_roots': sorted(set(fact['observation_roots'])),
                    'parent_evidence_ids': sorted({ref['id'] for ref in fact['parent_refs']}),
                    'observed_at': fact['observed_at'], 'valid_until': fact['valid_until'],
                    'qualification': 'experimental_shadow', 'progression_start_at': fact['observed_at']}
            if expected is None or chosen != expected:
                reject('invalid_pc_support', 'selected clause must preserve original progression and full ancestry')
            anchor = prior.get('enter_observed') if prior else None
            roots = prior.get('enter_roots', []) if prior else []
            tick = prior.get('enter_progress_tick') if prior else None
            gap = prior.get('gap_since') if prior else None
            expired = prior is not None and prior['last_basis_expiry'] is not None and prior['last_basis_expiry'] <= t
            overlap = (prior is not None and gap is None and prior['last_basis_expiry'] is not None
                       and fact['available_at'] < prior['last_basis_expiry'] and fact['observed_at'] < prior['last_basis_expiry'])
            if expired and not overlap and gap is None:
                gap = prior['last_basis_expiry']
            reacquiring = prior is not None and (gap is not None or prior['state'] in {'suspended', 'conflicted'})
            if reacquiring:
                continuity = []
                for event in snapshot['state']['lifecycle_cache'].values():
                    method = commissioned_method(event, self.policy['lifecycle_policy'])
                    if (method is not None and event['disposition'] == 'continuity_qualified'
                            and event['target_person_ref'] == relation['person_ref'] and event['target_subject_ref'] == relation['subject_ref']
                            and event['available_at'] <= t < event['valid_until'] and gap is not None
                            and event['window']['start_at'] <= gap and t <= event['window']['start_at'] + method['max_gap_s']
                            and event['window']['end_at'] >= expected['observed_at']):
                        continuity.append(event)
                if not continuity:
                    reject('invalid_pc_support', 'entry recovery needs an original qualified continuity interval')
                anchor = tick = None
            progress = anchor is not None and expected['observed_at'] > anchor and any(root not in roots for root in expected['progression_roots'])
            increment = min(expected['observed_at'] - anchor, max(0, t - tick)) if progress and tick is not None else 0
            expected_tick = t if anchor is None or progress else tick
            expected_roots = sorted(set(roots) | set(expected['progression_roots']))[-512:]
            if (relation['enter_observed'] != expected['observed_at'] or relation['enter_progress_tick'] != expected_tick
                    or relation['enter_roots'] != expected_roots
                    or relation['enter_dwell'] > prior_dwell + increment + 1e-7
                    or relation['state'] == 'supported' and relation['enter_dwell'] < 5):
                reject('invalid_pc_support', 'only progressing original inputs at trusted consumer ticks may earn entry dwell')
        return t

    def _outputs(self, commit, grant, publications, seq, t):
        decision, engagements = super()._outputs(commit, grant, publications, seq, t)
        relations = commit['checkpoint']['state']['engagement']['reducer']['state']['relations'].values()
        for relation in relations:
            chosen = relation.get('chosen_clause')
            if not chosen or chosen.get('qualification') != 'qualified_pc_input' or relation['state'] not in {'candidate', 'supported'}:
                continue
            selected = next(row for row in engagements if row['person_ref'] == relation['person_ref'] and row['subject_ref'] == relation['subject_ref'])
            support = selected['support']
            if (selected['observed_through'] != chosen['observed_at']
                    or support['progression_through_at'] != chosen['observed_at']
                    or support['progression_start_at'] != relation['enter_observed'] - relation['enter_dwell']):
                reject('invalid_pc_support', 'public PC support cannot replace original input time with snapshot time')
        return decision, engagements
