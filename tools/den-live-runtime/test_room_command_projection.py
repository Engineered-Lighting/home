"""Authored numerical originals and trusted-port doubles; no native/host I/O.

The coordinator separately exercises the actual worker/Journal source. The
literal mixer table here is an independent expected-vector double, not a Home
fork of the provider's mixer or a physical photometry qualification.
"""
from copy import deepcopy
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from journal import digest
from room_committed_source import CommittedRoomSource
from room_command_projection import (CanonicalProjectionProvider, DOMAINS,
    MIXER_SOURCE_SHA256, PROVIDER_PINS, RoomCommandProjector,
    RoomProjectionRefusal, project_committed_snapshot, quantize_ambient_sample)


def pin(name):
    return dict(id=name, digest=digest(name))


def literal_mixer(level, mired):
    vectors = {(254, 556): [4095, 0, 0], (254, 286): [0, 2649, 0],
               (254, 154): [0, 0, 2537], (127, 556): [1024, 0, 0],
               (0, 556): [0, 0, 0], (254, 313): [401, 2389, 0]}
    return deepcopy(vectors[(level, mired)])


def projection_provider(mixer=literal_mixer):
    return CanonicalProjectionProvider(**PROVIDER_PINS, mixer_source_sha256=MIXER_SOURCE_SHA256,
                                       ambient_mix=mixer)


def authored_projection_case(*, task=False):
    """Pure projection fixtures only: never accepted as a live room source."""
    policy = dict(policy_ref=pin('authored-room-policy'), valid_until=120,
                  exposure=dict(valid_until=120), registration=dict(frame='z_up_metric_floor0', unit='m'), fixtures=[])
    fixtures = []
    reference = dict(stream_epoch='stream-1', cursor=1, producer_id='authored', id='original-1',
                     payload_digest='1' * 64, component_id=None)
    for i in range(4):
        fid = 'fixture-' + str(i + 1)
        f = dict(fixture_id=fid, mount_id='mount-' + str(i + 1), profile_ref=pin('profile-' + fid),
                 pose_ref=pin('pose-' + fid), valid_until=120,
                 ambient=[dict(valid_until=120) for _ in DOMAINS], task_policy=None)
        policy['fixtures'].append(f)
        domains = [dict(domain=d, status='modeled', reason='authored_current_sample',
            sample=dict(brightness_fraction=1, cct_kelvin=1800),
            desired=dict(brightness_fraction=.125, cct_kelvin=5000), valid_until=103,
            evidence_refs=[deepcopy(reference)], assessment_refs=[pin('assessment-' + d)],
            goal_id='goal-1', scene_id=None) for d in DOMAINS]
        domains.append(dict(domain='spotlight', status='unassigned', reason='fixture_not_selected',
            sample=None, desired=None, valid_until=100, evidence_refs=[], assessment_refs=[],
            goal_id=None, scene_id=None))
        fixtures.append(dict(fixture_id=fid, mount_id=f['mount_id'], profile_ref=deepcopy(f['profile_ref']), domains=domains))
    unavailable = dict(status='unavailable', reason='no_current_task')
    output = dict(policy_ref=deepcopy(policy['policy_ref']), sample_at=100,
        input_cut_digest='c' * 64, authority_generation=4, source_generations={'camera': 2},
        fixtures=fixtures, task=dict(fixture_id=None, allocation=deepcopy(unavailable),
            execution=deepcopy(unavailable), display_lease=dict(status='withdrawn', valid_until=100)))
    snapshot = dict(schema='home-accepted-room-command-source-1', version=1,
        room_commit_ref=dict(commit_id='accepted-1', commit_body_sha256='a' * 64),
        home_scope=dict(site_id='Victoria', room_id='den', session_id='authored-session', domain_epoch='epoch-1'),
        sample_at=100, checked_at=100.5, physical_permission=False, native_achieved_truth=False,
        decision=dict(t=100, input_cut=dict(cut_digest='c' * 64, source_generations={'camera': 2}), room_output=output),
        commit=dict(checkpoint=dict(state=dict(room_execution=dict(tasks=[])))))
    if task:
        f = policy['fixtures'][0]
        f['task_policy'] = dict(fixture=dict(id=f['fixture_id'], fixture_pose_ref=deepcopy(f['pose_ref']),
            origin_m=[1, 2, 3], local_to_room=[0, -1, 0, 1, 0, 0, 0, 0, 1]))
        assignment = dict(fixture_id=f['fixture_id'], fixture_pose_ref=deepcopy(f['pose_ref']),
                          pan_deg=45, tilt_deg=60, goal_id='goal-1', nominal=dict(brightness_fraction=.5, cct_kelvin=3200))
        readback = dict(fixture_id=f['fixture_id'], fixture_pose_ref=deepcopy(f['pose_ref']),
            pan_deg=12, tilt_deg=16, brightness_fraction=0, cct_kelvin=3200, phase='moving',
            pan_velocity_deg_s=4, tilt_velocity_deg_s=5, pan_acceleration_deg_s2=0, tilt_acceleration_deg_s2=0)
        plan = dict(key='b' * 64, started_at=99, from_pan_deg=0, from_tilt_deg=0,
                    to_pan_deg=45, to_tilt_deg=60, goal_id='goal-1',
                    motion=dict(started_at=99, duration_s=3, pan_coefficients=[0, 0, 0, 1, 0, 0],
                                tilt_coefficients=[0, 0, 0, 2, 0, 0]))
        state = dict(plan=plan, last_readback=deepcopy(readback), last_valid_until=103)
        snapshot['commit']['checkpoint']['state']['room_execution']['tasks'] = [dict(fixture_id=f['fixture_id'], snapshot=dict(state=state))]
        output['task'] = dict(fixture_id=f['fixture_id'],
            allocation=dict(status='allocated', assignments=[deepcopy(assignment)], valid_until=103),
            execution=dict(status='virtual', sample_at=100, readback=[deepcopy(readback)],
                samples=[dict(t=100, readback=[deepcopy(readback)])], assessment_refs=[pin('task-assessment')], valid_until=103),
            display_lease=dict(status='current', sample_at=100, valid_until=103,
                geometry_refs=[deepcopy(reference)], protection_refs=[deepcopy(reference)]))
        fixtures[0]['domains'][7].update(status='modeled', sample=dict(brightness_fraction=0, cct_kelvin=3200), valid_until=103)
    return snapshot, policy


class RoomCommandProjectionTests(unittest.TestCase):
    def project(self, case=None, **kwargs):
        s, p = authored_projection_case(**kwargs) if case is None else case
        return project_committed_snapshot(s, room_policy=p, provider=projection_provider())

    def test_all_four_members_and_full21_channels_use_samples_not_desired(self):
        s, p = authored_projection_case()
        result = self.project((s, p))
        self.assertEqual(len(result['fixtures']), 4)
        for fixture in result['fixtures']:
            self.assertEqual(len(fixture['domains']), 8)
            self.assertEqual(fixture['ambient']['logical_channels'], [4095, 0, 0] * 7)
            self.assertEqual(fixture['ambient']['changes'], [dict(zone=i, level=254, mired=556) for i in range(7)])
            self.assertFalse(fixture['ambient']['full24_power_checked'])
        self.assertEqual(result['room_commit_ref'], s['room_commit_ref'])
        self.assertEqual(result['input_cut'], s['decision']['input_cut'])
        self.assertEqual(result['fixtures'][0]['domains'][0]['original']['evidence_refs'],
                         s['decision']['room_output']['fixtures'][0]['domains'][0]['evidence_refs'])
        self.assertFalse(result['authority_created'])

    def test_literal_wnc_vectors_cover_neutral_cool_and_rounding(self):
        s, p = authored_projection_case()
        domains = s['decision']['room_output']['fixtures'][0]['domains']
        for row, kelvin in zip(domains[:3], (3500, 6500, 3200)):
            row['sample']['cct_kelvin'] = kelvin
        result = self.project((s, p))['fixtures'][0]
        self.assertEqual(result['ambient']['logical_channels'][:9], [0, 2649, 0, 0, 0, 2537, 401, 2389, 0])
        q = quantize_ambient_sample(dict(brightness_fraction=.5, cct_kelvin=1800))
        self.assertEqual((q['level'], q['mired']), (127, 556))
        self.assertEqual(q['quantization']['brightness_fraction_error'], 0)
        self.assertLessEqual(abs(q['quantization']['mired_error']), .5)
        self.assertFalse(q['quantization']['calibrated_photometry'])
        self.assertEqual(quantize_ambient_sample(dict(brightness_fraction=.25, cct_kelvin=1800))['level'], 64)

    def test_unknown_unavailable_expired_and_supported_zero_do_not_collapse(self):
        s, p = authored_projection_case()
        rows = s['decision']['room_output']['fixtures'][0]['domains']
        rows[0].update(status='unavailable', sample=None)
        rows[1]['valid_until'] = 100.5  # exclusive boundary
        rows[2]['sample']['brightness_fraction'] = 0
        rows[3].update(status='off', sample=dict(brightness_fraction=0, cct_kelvin=1800))
        rows[4]['sample']['cct_kelvin'] = 1500  # no clamping into known profile
        f = self.project((s, p))['fixtures'][0]
        self.assertEqual([d['status'] for d in f['domains'][:5]], ['unavailable', 'unavailable', 'sampled', 'sampled', 'refused'])
        self.assertEqual(f['ambient']['logical_channels'][:6], [None] * 6)
        self.assertEqual(f['ambient']['logical_channels'][6:12], [0] * 6)
        self.assertEqual([c['zone'] for c in f['ambient']['changes']], [2, 3, 5, 6])

    def test_positive_and_sampled_dark_spotlight_never_invent_test_duties(self):
        for fraction in (0, .5):
            s, p = authored_projection_case(task=True)
            read = s['decision']['room_output']['task']['execution']['readback'][0]
            read['brightness_fraction'] = fraction
            s['decision']['room_output']['task']['execution']['samples'][0]['readback'][0] = deepcopy(read)
            s['commit']['checkpoint']['state']['room_execution']['tasks'][0]['snapshot']['state']['last_readback'] = deepcopy(read)
            s['decision']['room_output']['fixtures'][0]['domains'][7]['sample']['brightness_fraction'] = fraction
            f = self.project((s, p))['fixtures'][0]
            self.assertEqual(f['domains'][7]['reason'], 'spotlight_semantic_CCT_mapping_unavailable')
            self.assertIsNone(f['domains'][7]['intent'])

    def test_retained_endpoint_and_nonidentity_basis_stay_separate_from_quintic_sample(self):
        f = self.project(task=True)['fixtures'][0]
        trace = f['task']['retained_motion']
        self.assertEqual(trace['original']['to_pan_deg'], 45)
        self.assertEqual(f['task']['sampled_virtual_readback']['pan_deg'], 12)
        self.assertEqual(trace['fixture_basis']['origin_m'], [1, 2, 3])
        self.assertEqual(trace['fixture_basis']['local_to_room'], [0, -1, 0, 1, 0, 0, 0, 0, 1])
        self.assertNotIn('target_room_m', trace)
        self.assertFalse(f['task']['command_ready'])
        self.assertFalse(f['task']['native_motion_equivalence'])

    def test_brightness_key_change_does_not_create_new_motion_trace(self):
        s, p = authored_projection_case(task=True)
        before = self.project((s, p))['fixtures'][0]['task']['retained_motion']
        s['commit']['checkpoint']['state']['room_execution']['tasks'][0]['snapshot']['state']['plan']['key'] = 'd' * 64
        s['decision']['room_output']['task']['allocation']['assignments'][0]['nominal']['brightness_fraction'] = .25
        after = self.project((s, p))['fixtures'][0]['task']['retained_motion']
        self.assertEqual(before['motion_trace_sha256'], after['motion_trace_sha256'])
        self.assertNotEqual(before['plan_key'], after['plan_key'])

    def test_changed_retained_endpoint_or_pose_ref_refuses(self):
        for change in ('endpoint', 'pose'):
            s, p = authored_projection_case(task=True)
            a = s['decision']['room_output']['task']['allocation']['assignments'][0]
            a['pan_deg' if change == 'endpoint' else 'fixture_pose_ref'] = 46 if change == 'endpoint' else pin('foreign-pose')
            with self.assertRaisesRegex(RoomProjectionRefusal, 'linkage'):
                self.project((s, p))

    def test_cut_generation_inventory_and_provider_misbinding_refuse(self):
        for mutate in (
            lambda s: s['decision']['input_cut'].update(cut_digest='f' * 64),
            lambda s: s['decision']['input_cut'].update(source_generations={'camera': 3}),
            lambda s: s['decision']['room_output']['fixtures'].reverse(),
            lambda s: s['decision']['room_output']['fixtures'][0]['domains'].pop()):
            s, p = authored_projection_case()
            mutate(s)
            with self.assertRaises(RoomProjectionRefusal): self.project((s, p))
        s, p = authored_projection_case()
        with self.assertRaisesRegex(RoomProjectionRefusal, 'provider_changed'):
            project_committed_snapshot(s, room_policy=p, provider=replace(projection_provider(), source_sha256='0' * 64))
        with self.assertRaisesRegex(RoomProjectionRefusal, 'actual_committed'):
            RoomCommandProjector(s, room_policy=p, provider=projection_provider())

    def test_actual_source_port_final_clock_withdraws_without_remixing_or_lease_extension(self):
        s, p = authored_projection_case()
        values = [deepcopy(s), deepcopy(s)]
        values[1]['checked_at'] = 103
        # Authored port double with the exact class. This is not Journal proof.
        source = object.__new__(CommittedRoomSource)
        source.journal = SimpleNamespace(lock=threading.RLock())
        source._installation = lambda: SimpleNamespace(policy=dict(room_policy=deepcopy(p)))
        source.current = lambda: values.pop(0)
        calls = []
        def mixer(level, mired):
            calls.append((level, mired))
            return literal_mixer(level, mired)
        with patch('room_command_projection.validate_room_policy'):
            projector = RoomCommandProjector(source, room_policy=p, provider=projection_provider(mixer))
        result = projector.project()
        self.assertEqual(len(calls), 28)
        self.assertEqual(result['checked_at'], 103)
        self.assertTrue(all(f['ambient']['changes'] == [] for f in result['fixtures']))
        self.assertTrue(all(f['ambient']['logical_channels'] == [None] * 21 for f in result['fixtures']))
        self.assertEqual(result['fixtures'][0]['domains'][0]['original']['valid_until'], 103)

    def test_source_commit_changes_during_projection_refuse(self):
        s, p = authored_projection_case()
        later = deepcopy(s)
        later['room_commit_ref']['commit_id'] = 'accepted-2'
        values = [s, later]
        source = object.__new__(CommittedRoomSource)
        source.journal = SimpleNamespace(lock=threading.RLock())
        source._installation = lambda: SimpleNamespace(policy=dict(room_policy=deepcopy(p)))
        source.current = lambda: values.pop(0)
        with patch('room_command_projection.validate_room_policy'):
            projector = RoomCommandProjector(source, room_policy=p, provider=projection_provider())
        with self.assertRaisesRegex(RoomProjectionRefusal, 'changed_during_projection'):
            projector.project()


if __name__ == '__main__':
    unittest.main()
