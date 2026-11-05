"""The assembler against rung 5's gate clauses, on synthetic fixtures only."""
import copy
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import method   # noqa: E402
import records  # noqa: E402
import synth    # noqa: E402
from assemble import assemble, crop_box, has_box  # noqa: E402

M = method.load_method()          # practice: proposed values for open slots
ts = synth.ts


def store_of(scene):
    return records.Store(scene['observations'], scene['tracks'])


def scene_store(name):
    scene = synth.SCENES[name]()
    return scene, store_of(scene)


class TestStore(unittest.TestCase):
    def test_lines_are_dated_by_their_newest_observation(self):
        _, st = scene_store('two_tracks')
        self.assertEqual([l.ts for l in st.lines_of('A')][:3], [ts(0.0), ts(1.0), ts(2.0)])
        self.assertEqual(st.order, ['A', 'B'])

    def test_a_late_or_empty_line_never_dates_before_the_line_before_it(self):
        _, st = scene_store('stale_observation')
        dates = [l.ts for l in st.lines_of('A')]
        self.assertEqual(dates[-3:], [ts(5.0)] * 3)      # the 3.2 s result, then a predicted step
        self.assertEqual(dates, sorted(dates))

    def test_a_first_line_with_no_known_observation_is_counted_undated(self):
        st = records.Store([], [synth.track_line('A', 1.0, 1.0, ['o-unknown'])])
        self.assertEqual((st.undated, st.order), (1, []))

    def test_duplicate_obs_ids_are_refused(self):
        o = synth.observation('o-1', 0.0, 1.0, 1.0)
        with self.assertRaises(records.RecordError):
            records.Store([o, o], [])


class TestWindow(unittest.TestCase):
    def test_interval_is_window_s_ending_at_the_request(self):
        _, st = scene_store('two_tracks')
        b = assemble(st, M, 'A', ts(10.0))
        self.assertEqual(b['interval'], [ts(10.0) - M['window_s'], ts(10.0)])

    def test_field_names_are_the_schema_s(self):
        _, st = scene_store('missing_depth')
        b = assemble(st, M, 'A', ts(5.0))
        self.assertEqual(list(b), ['version', 'bundle_id', 'track_id', 'interval', 'frames', 'keypoint_series',
                                   'positions', 'objects', 'others', 'missing', 'association_safe'])
        self.assertEqual(set(b['frames'][0]), {'camera_id', 'ts', 'image_box', 'crop_ref'})
        self.assertEqual(set(b['keypoint_series'][0]),
                         {'obs_id', 'ts', 'camera_id', 'transform_id', 'keypoints', 'joints_room'})
        self.assertEqual(set(b['positions'][0]), {'ts', 'x', 'y', 'cov'})
        self.assertEqual(set(b['objects'][0]), {'label', 'relation', 'room_position'})

    def test_nothing_known_after_t1_changes_the_bundle(self):
        scene, st = scene_store('two_tracks')
        t1 = ts(20.0)
        early = records.Store([o for o in scene['observations'] if o['capture_ts'] <= t1],
                              [l for l in scene['tracks'] if all(st.obs[o]['capture_ts'] <= t1 for o in l['last_obs_ids'])])
        self.assertEqual(assemble(st, M, 'A', t1), assemble(early, M, 'A', t1))


class TestAligned(unittest.TestCase):
    def test_every_entry_lies_in_the_interval_and_every_keypoint_has_its_frame(self):
        for name in synth.SCENES:
            _, st = scene_store(name)
            for tid in st.order:
                for k in range(1, 30):
                    b = assemble(st, M, tid, ts(k * 0.7))
                    t0, t1 = b['interval']
                    for item in b['frames'] + b['keypoint_series'] + b['positions']:
                        self.assertTrue(t0 - 1e-6 <= item['ts'] <= t1 + 1e-6, (name, tid, item['ts']))
                    keys = {(f['camera_id'], f['ts']) for f in b['frames']}
                    for kp in b['keypoint_series']:
                        self.assertIn((kp['camera_id'], kp['ts']), keys)
                    for other in b['others']:
                        self.assertEqual(len(other['image_box']), len(b['frames']))

    def test_others_are_the_other_people_in_the_same_frames(self):
        _, st = scene_store('two_tracks')
        b = assemble(st, M, 'A', ts(10.0))
        self.assertEqual([o['track_id'] for o in b['others']], ['B'])
        self.assertEqual(b['others'][0]['image_box'][0], synth.box_at(3.2, 2.6, standing=False))

    def test_crop_pads_clips_and_widens_to_even_pixels(self):
        px = [1920, 1080]
        self.assertEqual(crop_box([924, 346, 1114, 866], px, 0.2, 16), [886, 242, 1152, 970])
        self.assertEqual(crop_box([101, 201, 301, 401], px, 0.2, 16), [60, 160, 342, 442])     # odd -> outward
        self.assertEqual(crop_box([100, 100, 110, 110], px, 0.2, 16), [84, 84, 126, 126])      # the 16 px floor
        self.assertEqual(crop_box([5, 3, 101, 99], px, 0.2, 16), [0, 0, 122, 120])              # clipped at 0
        self.assertEqual(crop_box([1800, 900, 1915, 1075], px, 0.2, 16), [1776, 864, 1920, 1080])  # clipped at the frame

    def test_one_crop_from_the_latest_boxed_frame_applies_to_every_frame(self):
        _, st = scene_store('stale_observation')
        b = assemble(st, M, 'A', ts(7.0))
        latest = max(b['frames'], key=lambda f: f['ts'])
        expect = {'box': crop_box(latest['image_box'], [1920, 1080], M['pad_fraction'], M['pad_min_px']),
                  'from_ts': latest['ts']}
        self.assertEqual(latest['ts'], ts(5.0))
        self.assertTrue(all(f['crop_ref'] == expect for f in b['frames']))

    def test_a_camera_without_frame_px_is_refused(self):
        scene, _ = scene_store('two_tracks')
        for o in scene['observations']:
            o['source']['sensor_id'] = 'den-other'
        with self.assertRaises(ValueError):
            assemble(store_of(scene), M, 'A', ts(10.0))


class TestVerbatim(unittest.TestCase):
    FIELDS = (('ts', 'capture_ts'), ('transform_id', 'transform_id'), ('keypoints', 'keypoints'),
              ('joints_room', 'joints_room'))

    def test_entries_copy_the_observation_fields_exactly(self):
        _, st = scene_store('missing_depth')
        b = assemble(st, M, 'A', ts(5.0))
        self.assertEqual(len(b['keypoint_series']), 7)
        for kp in b['keypoint_series']:
            o = st.obs[kp['obs_id']]
            for mine, theirs in self.FIELDS:
                self.assertEqual(kp[mine], o[theirs])
            self.assertEqual(kp['camera_id'], o['source']['sensor_id'])

    def test_entries_are_copies_not_references(self):
        _, st = scene_store('missing_depth')
        b = assemble(st, M, 'A', ts(5.0))
        before = copy.deepcopy(st.obs[b['keypoint_series'][0]['obs_id']])
        b['keypoint_series'][0]['joints_room'][0]['status'] = 'changed'
        b['keypoint_series'][0]['keypoints'][0][0] = -1
        self.assertEqual(st.obs[b['keypoint_series'][0]['obs_id']], before)

    def test_observations_from_ambiguous_lines_are_left_out(self):
        _, st = scene_store('crossing')
        self.assertEqual(assemble(st, M, 'A', ts(7.4))['keypoint_series'], [])
        ids = [k['obs_id'] for k in assemble(st, M, 'A', ts(10.0))['keypoint_series']]
        self.assertEqual(ids, [synth.oid('A', t) for t in (7.5, 8.0, 8.5, 9.0, 9.5, 10.0)])

    def test_an_observation_also_named_by_an_ambiguous_line_is_left_out(self):
        scene, _ = scene_store('two_tracks')
        dup = copy.deepcopy(scene['tracks'][20])      # A at 10.0
        dup['association']['status'] = 'ambiguous'
        scene['tracks'].insert(21, dup)
        ids = [k['obs_id'] for k in assemble(store_of(scene), M, 'A', ts(11.0))['keypoint_series']]
        self.assertNotIn(synth.oid('A', 10.0), ids)
        self.assertIn(synth.oid('A', 11.0), ids)

    def test_a_rejected_observation_is_left_out(self):
        scene, _ = scene_store('two_tracks')
        scene['observations'][20]['validity'] = {'status': 'rejected', 'reason': 'implausible depth'}
        rejected = scene['observations'][20]['obs_id']
        b = assemble(store_of(scene), M, 'A', ts(11.0))
        self.assertNotIn(rejected, [k['obs_id'] for k in b['keypoint_series']])

    def test_another_track_s_observations_never_enter(self):
        _, st = scene_store('two_tracks')
        ids = [k['obs_id'] for k in assemble(st, M, 'A', ts(30.0))['keypoint_series']]
        self.assertTrue(ids and all(i.startswith('o-A-') for i in ids))


class TestMissing(unittest.TestCase):
    def test_depth_in_time_is_used_and_67_ms_is_still_in_time(self):
        _, st = scene_store('missing_depth')
        b = assemble(st, M, 'A', ts(3.5))
        self.assertIn(synth.oid('A', 2.0), [k['obs_id'] for k in b['keypoint_series']])
        self.assertEqual(b['missing'], [])

    def test_late_depth_and_absent_depth_flag_depth(self):
        _, st = scene_store('missing_depth')
        self.assertEqual(assemble(st, M, 'A', ts(5.0))['missing'], ['depth'])
        self.assertEqual(assemble(st, M, 'A', ts(8.0))['missing'], ['depth'])

    def test_one_joint_on_a_plane_flags_depth(self):
        scene, _ = scene_store('two_tracks')
        scene['observations'][20]['joints_room'][9]['lift'] = 'plane:0.95'
        self.assertEqual(assemble(store_of(scene), M, 'A', ts(11.0))['missing'], ['depth'])

    def test_a_stale_source_is_flagged_and_the_late_result_is_outside_the_window(self):
        _, st = scene_store('stale_observation')
        b = assemble(st, M, 'A', ts(7.0))
        self.assertEqual(b['missing'], ['track', 'person_box', 'keypoints'])
        self.assertNotIn('o-A-late-003200', [k['obs_id'] for k in b['keypoint_series']])
        self.assertTrue(has_box(b))

    def test_a_late_result_inside_the_window_sorts_by_capture_time(self):
        _, st = scene_store('stale_observation')
        b = assemble(st, M, 'A', ts(5.5))
        ids = [k['obs_id'] for k in b['keypoint_series']]
        self.assertEqual(ids.index('o-A-late-003200'), ids.index(synth.oid('A', 3.0)) + 1)
        self.assertEqual(b['missing'], [])

    def test_a_track_that_leaves_mid_window_is_flagged_not_waited_for(self):
        _, st = scene_store('track_leaves')
        b = assemble(st, M, 'B', ts(22.0))
        self.assertEqual(b['missing'], ['track', 'person_box', 'keypoints'])
        self.assertEqual([f['ts'] for f in b['frames']], [ts(19.0), ts(20.0)])
        self.assertTrue(b['association_safe'])

    def test_a_track_born_mid_window_is_flagged(self):
        scene, _ = scene_store('two_tracks')
        st = records.Store(scene['observations'], [l for l in scene['tracks'] if l['track_id'] == 'A'
                                                   or st_ts(scene, l) >= ts(10.0)])
        self.assertEqual(assemble(st, M, 'B', ts(11.0))['missing'], ['track', 'person_box', 'keypoints'])
        self.assertEqual(assemble(st, M, 'B', ts(12.0))['missing'], [])

    def test_a_linked_object_without_a_box_flags_object_box(self):
        scene, st = scene_store('missing_depth')
        self.assertEqual(assemble(st, M, 'A', ts(3.5))['objects'][0]['room_position'], {'x': 1.9, 'y': 0.6, 'z': 0.91})
        scene['observations'] = [o for o in scene['observations'] if o['kind'] != 'object_box']
        b = assemble(store_of(scene), M, 'A', ts(3.5))
        self.assertEqual(b['objects'], [{'label': 'cutting board', 'relation': 'using', 'room_position': None}])
        self.assertEqual(b['missing'], ['object_box'])

    def test_assembly_never_reads_a_clock_or_sleeps(self):
        _, st = scene_store('missing_depth')
        boom = mock.Mock(side_effect=AssertionError('the assembler must not wait or read time'))
        with mock.patch.object(time, 'sleep', boom), mock.patch.object(time, 'time', boom), \
                mock.patch.object(time, 'monotonic', boom):
            b = assemble(st, M, 'A', ts(8.0))
        self.assertEqual(b['missing'], ['depth'])

    def test_a_track_with_nothing_in_the_window_flags_everything(self):
        _, st = scene_store('track_leaves')
        b = assemble(st, M, 'B', ts(30.0))
        self.assertEqual(b['missing'], ['track', 'person_box', 'keypoints', 'depth'])
        self.assertFalse(has_box(b))


def st_ts(scene, line):
    times = {o['obs_id']: o['capture_ts'] for o in scene['observations']}
    return max(times[o] for o in line['last_obs_ids'])


class TestAssociationSafe(unittest.TestCase):
    def meets(self, b, c0, c1):
        t0, t1 = b['interval']
        return t0 <= c1 + 1e-6 and c0 <= t1 + 1e-6

    def test_false_through_the_annotated_crossing_and_true_away_from_it(self):
        scene, st = scene_store('crossing')
        (c,) = scene['crossings']
        met = clear = 0
        for tid in ('A', 'B'):
            for k in range(0, 121):
                b = assemble(st, M, tid, ts(k * 0.1))
                if self.meets(b, *c['interval']):
                    met += 1
                    self.assertFalse(b['association_safe'], (tid, k))
                elif b['interval'][0] >= ts(7.5) - 1e-6 or b['interval'][1] < ts(4.5) - 1e-6:
                    clear += 1          # wholly before the tracker's ambiguity, or after it ended
                    self.assertTrue(b['association_safe'], (tid, k))
        self.assertGreater(met, 50)
        self.assertGreater(clear, 50)

    def test_a_status_change_inside_the_window_is_caught_from_since_ts_alone(self):
        scene, _ = scene_store('crossing')
        scene['tracks'] = [l for l in scene['tracks'] if l['association']['status'] == 'confident']
        st = store_of(scene)
        self.assertFalse(assemble(st, M, 'A', ts(9.0))['association_safe'])    # window [6, 9] holds since 7.5
        self.assertTrue(assemble(st, M, 'A', ts(10.5))['association_safe'])    # window [7.5, 10.5]

    def test_a_track_born_confident_inside_the_window_is_safe(self):
        lines = [synth.track_line('A', 1.0, 1.0, [synth.oid('A', t)], since=1.0) for t in (1.0, 1.5, 2.0)]
        obs = [synth.observation(synth.oid('A', t), t, 1.0, 1.0) for t in (1.0, 1.5, 2.0)]
        self.assertTrue(assemble(records.Store(obs, lines), M, 'A', ts(2.5))['association_safe'])

    def test_a_track_with_no_line_is_not_safe(self):
        _, st = scene_store('two_tracks')
        self.assertFalse(assemble(st, M, 'Z', ts(10.0))['association_safe'])
        self.assertFalse(assemble(st, M, 'A', ts(-5.0))['association_safe'])


if __name__ == '__main__':
    unittest.main()
