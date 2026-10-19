"""Authored config11 fixture through public commissioning/worker interfaces."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import subprocess
import sys

from live_test_helpers import MANIFEST, METHOD, NOW, PROFILE


OTHER_ACTOR='manual-other-operator'
OTHER_TOKEN='manual-other-operator-token'


def manual_fixture(*,second_actor=False,room_mutator=None,actor_ref=None):
    from live_conservative_test_helpers import conservative_fixture
    from live_test_helpers import reference
    from schema_registry import schema_digest
    root = Path(__file__).resolve().parents[3]
    if str(root / 'tools/den-live-runtime') not in sys.path:
        sys.path.insert(0, str(root / 'tools/den-live-runtime'))
    from room_configuration import upgrade_configuration
    service, grants, config = conservative_fixture()
    room = json.loads(subprocess.check_output(['node', '-e',
        "process.stdout.write(JSON.stringify(require('./tools/fixtures/home-live-room.cjs').policy()))"], cwd=root, text=True))
    room['registration'] = deepcopy(config['delivery_policy']['registration'])
    def current(value):
        if isinstance(value, dict):
            for key in value:
                if key == 'valid_until': value[key] = NOW + 180
                else: current(value[key])
        elif isinstance(value, list):
            for row in value: current(row)
    current(room)
    if room_mutator is not None: room_mutator(room)
    clarification = dict(schema='home-live-clarification-policy-1', version=1,
        proposal_ttl_s=30, max_pending=1, max_history=128, repeat_policy='one_per_scope',
        material_methods=[deepcopy(METHOD)], interaction_methods=[deepcopy(METHOD)])
    manual = dict(schema='home-live-manual-policy-1', version=1, policy_ref=reference('authored-manual-policy'),
        room_policy_ref=deepcopy(room['policy_ref']), valid_until=NOW+180, max_duration_s=120,
        operating_not_after=NOW+120, max_active_holds=28, max_pending_per_domain=1, intent_capacity=128,
        resource_limits=dict(derived_event_bytes=262144,worker_commit_bytes=1048576))
    service, config = upgrade_configuration(service, config, room, clarification_policy=clarification, manual_policy=manual)
    MANIFEST.update(service['manifest_ref'])
    retired = {name+'-6' for name in ('home-live-shadow-decision','home-live-worker-checkpoint','home-live-worker-commit','home-live-input-cut')}
    schemas = {name+'-10' for name in ('home-live-shadow-decision','home-live-worker-checkpoint','home-live-worker-commit','home-live-input-cut')}
    schemas |= {'home-live-room-output-2','home-live-room-snapshot-2','home-live-manual-policy-1',
                'home-live-manual-control-state-1','home-live-room-control-status-1',
                'home-live-intent-event-4','home-live-intent-event-3','home-live-intent-event-2','home-live-person-lifecycle-1'}
    for grant in grants:
        grant['payload_types'] = [name for name in grant['payload_types'] if name not in retired]
        if 'worker_commit' in grant['operations']:
            grant['payload_types'] += sorted(schemas)
        elif 'intent' in grant['operations']:
            grant['payload_types'] += ['home-live-intent-event-3','home-live-intent-event-4','home-live-shadow-decision-10']
        if {'intent','worker_commit'} & set(grant['operations']):
            grant['revisions'] = list(dict.fromkeys([v for v in grant['revisions'] if v not in {'6.0','8.0','9.0'}] + ['3.0','4.0','10.0']))
        grant['payload_types'] = list(dict.fromkeys(grant['payload_types']))
        grant['schema_pins'] = {name: schema_digest(name, profile=PROFILE) for name in grant['payload_types']}
        for binding in grant['live_bindings']: binding['manifest_ref'] = deepcopy(service['manifest_ref'])
    if second_actor:
        other=deepcopy(next(grant for grant in grants if 'intent' in grant['operations']))
        other.update(principal_id='manual-other-principal',producer_id='manual-other-producer',
                     expected_actor_ref=OTHER_ACTOR,token_sha256=hashlib.sha256(OTHER_TOKEN.encode()).hexdigest())
        grants.append(other)
    if actor_ref is not None:
        next(grant for grant in grants if 'intent' in grant['operations'])['expected_actor_ref']=actor_ref
    return service, grants, config
