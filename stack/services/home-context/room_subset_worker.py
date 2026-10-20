"""Config13 only: nullable assessed-room execution with unchanged PC proofs.

The raw policy2 remains the commissioning/checkpoint identity. Its detached
execution view is used only at the existing independent Room task boundary.
This module grants no command, hardware or optical authority.
"""
from copy import deepcopy

from journal import digest
from live_scene import validate_scene_worker_grant
from live_worker import reject
from pc_support_worker import PcSupportWorkerBoundary, validate_pc_worker_policy
from room_delivery import room_policy_view, validate_room_output
from room_worker import RoomLiveDeliveryWorkerBoundary
from schema_registry import LIVE_ROOM_SUBSET_VERSIONS, LIVE_ROOM_SUBSET_PUBLICATIONS


SUBSET_TYPES = frozenset(LIVE_ROOM_SUBSET_VERSIONS)


def validate_room_subset_worker_policy(manifest, registry):
    worker = manifest['worker']
    if (manifest.get('schema') != 'home-live-ingress-manifest-13'
            or type(manifest.get('version')) is not int or manifest['version'] != 13
            or worker.get('room_subset_mode') is not True
            or not registry.room_subset_interface_digest
            or manifest.get('room_subset_interface_digest') != registry.room_subset_interface_digest
            or worker.get('room_subset_schema_pins') != registry.room_subset_schema_pins
            or worker.get('room_policy', {}).get('schema') != 'home-live-room-policy-2'
            or type(worker.get('room_policy', {}).get('version')) is not int or worker['room_policy']['version'] != 2):
        raise ValueError('exact assessed room subset config13 commissioning required')
    registry.validate(worker['room_policy'])
    base = deepcopy(manifest)
    del base['room_subset_interface_digest']
    del base['worker']['room_subset_schema_pins']
    del base['worker']['room_subset_mode']
    # Preserve all PC original/reducer/source validation. The sole opt-in is
    # its downstream semantic Room policy2 validation, never a schema bypass.
    validate_pc_worker_policy(base, registry, allow_assessed_subset=True)


def validate_room_subset_grant(grant, config):
    shared = {'home-live-room-output-1', 'home-live-room-snapshot-1',
              'home-live-person-lifecycle-1', 'home-live-intent-event-2'}
    if grant.payload_types & (SUBSET_TYPES - shared - LIVE_ROOM_SUBSET_PUBLICATIONS) and 'worker_commit' not in grant.operations:
        raise ValueError('private room subset state requires the sole worker capability')
    if 'home-live-shadow-decision-12' in grant.payload_types and 'home-live-geometry-1' not in grant.payload_types:
        raise ValueError('complete assessed room decision requires separate geometry authority')


def validate_room_subset_worker_grant(grant, config):
    validate_scene_worker_grant(grant, config)
    if not SUBSET_TYPES <= grant.payload_types:
        raise ValueError('room subset worker requires its complete explicit successor closure')


class RoomSubsetWorkerBoundary(PcSupportWorkerBoundary):
    # Inherit PC proof behavior, not its private decision/checkpoint11 dialect.
    schema_types = RoomLiveDeliveryWorkerBoundary.schema_types | SUBSET_TYPES
    output_types = frozenset({'home-live-engagement-state-1', 'home-live-shadow-decision-12'})
    decision_type = 'home-live-shadow-decision-12'
    checkpoint_type = 'home-live-worker-checkpoint-12'
    commit_type = 'home-live-worker-commit-12'
    cut_type = 'home-live-input-cut-12'
    version = 12
    worker_version = 'home-live-context-worker-12.0.0'

    def configuration_description(self, nested):
        value = super().configuration_description(nested)
        value.update(version=self.worker_version, room_subset_mode=True,
            room_subset_schema_pins=self.policy['room_subset_schema_pins'],
            room_subset_interface_digest=self.j.schemas.room_subset_interface_digest)
        return value

    def _checkpoint(self, cp, commit, next_seq, old, now):
        if (cp['room_subset_schema_pins'] != self.policy['room_subset_schema_pins']
                or cp['room_subset_policy_digest'] != digest(self.policy['room_policy'])):
            reject('checkpoint_conflict', 'room subset checkpoint differs from raw assessed policy/schema')
        return super()._checkpoint(cp, commit, next_seq, old, now)

    def _room_fixtures(self):
        return room_policy_view(self.policy['room_policy'], allow_assessed_subset=True)['fixtures']

    def _task_inventory(self, fixtures):
        return [fixture['fixture_id'] for fixture in fixtures if fixture['task_policy'] is not None]

    def validate_room_output(self, **kwargs):
        return validate_room_output(**kwargs, allow_assessed_subset=True)
