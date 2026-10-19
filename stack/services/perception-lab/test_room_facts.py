"""Room facts (rule room-facts-1) and the den-facts-8 wire: the site map
binding, zone membership, looking_at, hands_near, posture and motion from a
synthetic room map and synthetic stereo depth; the live controller stage; the
wire's allowlist, support table and facts keys; the egress policy; and that
nothing changes without a room map or on den-facts-6/7. Temp stores and
synthetic bundles only; no camera, network or model."""
from copy import deepcopy
import io
import json
import math
import time
import unittest
from unittest.mock import patch

import numpy as np

import head3d
import jev_wire as wire
import room_facts as rf
from jev_evidence import QWEN_EGRESS, project
from jev_policy import LabJevPolicy
from reasoning import canonical, digest
from spatial_validation import mount_signature
from test_head3d import render, rgb_pixel
from test_room_mapper import CALIBRATION, metadata

# The camera 1.3 m above the floor at the room origin (beside the monitor, as
# in the den), heading +Y and pitched 10 degrees down (room frame
# z_up_metric_floor0).
PITCH = math.radians(10)
CAMERA_TO_ROOM = np.array([[1, 0, 0, 0], [0, -math.sin(PITCH), math.cos(PITCH), 0],
                           [0, -math.cos(PITCH), -math.sin(PITCH), 1.3], [0, 0, 0, 1.0]])
ROOM_TO_CAMERA = np.linalg.inv(CAMERA_TO_ROOM)
WALL_M = 6.0
TARGETS = [
    # A free-text name that must never leave: only its category word may.
    {'id': 'anchor-desk_chair', 'name': "Grandpa's old chair", 'category': 'desk_chair', 'shape': 'box',
     'pos': [0.3, 1.8, 0.5], 'size_m': [0.6, 0.6, 1.0]},
    {'id': 'anchor-desk', 'name': 'desk', 'category': 'desk', 'shape': 'box', 'pos': [0.3, 1.2, 0.72], 'size_m': [1.2, 0.5, 0.06]},
    {'id': 'anchor-keyboard_area', 'name': 'keyboard_area', 'category': 'keyboard_area', 'shape': 'point',
     'pos': [0.3, 1.35, 0.76], 'radius_m': 0.15},
    {'id': 'anchor-monitor', 'name': 'monitor', 'category': 'monitor', 'shape': 'point', 'pos': [0.9, 1.0, 1.1], 'radius_m': 0.3},
    {'id': 'anchor-tv', 'name': 'the big telly', 'category': 'tv', 'shape': 'point', 'pos': [-1.6, 1.0, 1.2], 'radius_m': 0.5},
    {'id': 'anchor-sofa', 'name': 'sofa', 'category': 'sofa', 'shape': 'box', 'pos': [-1.5, 3.5, 0.45], 'size_m': [2.0, 0.9, 0.9]},
    {'id': 'anchor-reading_chair', 'name': 'reading_chair', 'category': 'reading_chair', 'shape': 'box',
     'pos': [-0.7, 2.3, 0.5], 'size_m': [0.8, 0.8, 1.0]},
    {'id': 'anchor-window', 'name': 'window', 'category': 'window', 'shape': 'box', 'pos': [1.5, 4.0, 1.5], 'size_m': [1.2, 0.1, 1.2]},
    {'id': 'anchor-gizmo', 'name': 'secret gizmo 42', 'category': 'gizmo', 'shape': 'point', 'pos': [3.0, 0.5, 1.0], 'radius_m': 0.2},
]
ZONES = [{'id': 'zone-door', 'name': 'doorway', 'floor_polygon': [[0.6, 2.4], [1.2, 2.4], [1.2, 3.0], [0.6, 3.0]]},
         {'id': 'zone-rug', 'name': 'Rug', 'floor_polygon': [[-3, 4.2], [3, 4.2], [3, 6], [-3, 6]]},
         {'id': 'zone-x', 'name': 'mystery nook', 'floor_polygon': [[3, 3], [4, 3], [4, 4], [3, 4]]}]


def calibration_model():
    """The stage's own camera model for the synthetic bundle metadata."""
    stage = rf.RoomFactsStage.__new__(rf.RoomFactsStage)
    stage.models, stage.room = {}, {'revision': '1'}
    return stage._model(metadata(), CALIBRATION)


MODEL = calibration_model()
EMPTY = render(MODEL, [], wall_z=WALL_M)


def site_map(targets=TARGETS, zones=ZONES, **camera):
    registration = {'source_frame': 'rectified_left', 'target_frame': 'z_up_metric_floor0', 'method': 'mesh_icp',
                    'status': 'fitted', 'matrix': CAMERA_TO_ROOM.tolist(),
                    'calibration_content_id': rf.calibration_content_id(CALIBRATION),
                    'mount_signature': mount_signature(EMPTY), **camera}
    model = {'revision': '1', 'targets': deepcopy(targets), 'zones': deepcopy(zones),
             'meta': {'frame': 'z_up_metric_floor0', 'units': 'metres', 'source': 'room_mapper', 'site_id': 'victoria',
                      'camera_id': 'den', 'camera_registration': registration}}
    return {'schema': 'den-site-map-1', 'model': model,
            'binding': {'site_id': 'victoria', 'map_id': 'victoria-den', 'revision': '1', 'units': 'metres',
                        'model_sha256': digest(model)}}


ROOM = rf.load_site_map(site_map(), site_id='victoria', camera_id='den')


def cam(point):
    return ROOM_TO_CAMERA[:3, :3] @ np.asarray(point, dtype=float)+ROOM_TO_CAMERA[:3, 3]


def unit(v):
    v = np.asarray(v, dtype=float)
    return v/np.linalg.norm(v)


def person(joints, head, look_at, *, objects=(), extra=(), pixels=None):
    """(projected-style record, depth) for one person: body joints {coco id:
    (room point, sphere radius)} rendered as spheres, a template head at
    `head` facing `look_at`, and optional objects [(label, room centre,
    radius)] rendered and boxed in the image."""
    spheres, pix = [], []
    for joint, (point, radius) in joints.items():
        centre = cam(point)
        spheres.append((centre, radius))
        pix.append((joint, rgb_pixel(MODEL, centre)))
    forward = unit(np.asarray(look_at, dtype=float)-head)
    right = unit(np.cross(forward, [0, 0, 1]))
    up = np.cross(right, forward)
    centre = np.asarray(head, dtype=float)
    spheres.append((cam(centre+0.01*forward), 0.07))
    face = {}
    for joint, (x, y, z) in head3d.HEAD_TEMPLATE.items():
        point = cam(centre+x*right+y*forward+z*up)
        spheres.append((point+0.012*point/np.linalg.norm(point), 0.012))
        face[joint] = point
    boxes = []
    for label, point, radius in objects:
        c = cam(point)
        spheres.append((c, radius))
        u, v = rgb_pixel(MODEL, c)
        size = 485*radius/c[2]
        boxes.append((label, {'x': u-size, 'y': v-size, 'w': 2*size, 'h': 2*size}))
    spheres += list(extra)
    depth = render(MODEL, spheres, wall_z=WALL_M)
    k = MODEL['depth_k']
    for joint, point in face.items():
        # A landmark the skull hides (the far ear of a turned head) is not
        # given to the pose: RTMPose reports it on the near ear, which
        # head3d.resolve_ears merges; leaving it out is the same geometry.
        u, v = int(round(k[0, 0]*point[0]/point[2]+k[0, 2])), int(round(k[1, 1]*point[1]/point[2]+k[1, 2]))
        if 0 <= u < 640 and 0 <= v < 400 and depth[v, u] >= 1000*point[2]-20:
            pix.append((joint, rgb_pixel(MODEL, point)))
    us, vs = [p[0] for _, p in pix], [p[1] for _, p in pix]
    x0, y0 = max(0.0, min(us)-25), max(0.0, min(vs)-25)
    box = {'x': x0, 'y': y0, 'w': min(639.0, max(us)+25)-x0, 'h': min(359.0, max(vs)+25)-y0}
    record = {'objects': [{'label': 'person', 'instance_ref': 'object-0', 'pixel_box': box}]
              + [{'label': label, 'instance_ref': 'object-%d' % (i+1), 'pixel_box': b} for i, (label, b) in enumerate(boxes)],
              'pose_instances': [{'layout': 'coco17', 'instance_ref': 'object-0',
                                  'joints': [{'joint_id': j, 'pixel': list(p), 'status': 'supported'} for j, p in (pixels or pix)]}],
              'quality': {'image_width': 640, 'image_height': 360}}
    return record, depth


def seated(x=0.3, y=1.8, *, look_at=(0.9, 1.0, 1.1), wrists=((0.15, 1.35, 0.8), (0.45, 1.35, 0.8)), objects=()):
    """Seated upright: hips 0.5 m, shoulders 1.06 m, head 1.25 m above the floor."""
    joints = {5: ([x-0.19, y, 1.06], .06), 6: ([x+0.19, y, 1.06], .06), 11: ([x-0.12, y, 0.5], .07),
              12: ([x+0.12, y, 0.5], .07), 13: ([x-0.28, y-0.4, 0.5], .06), 14: ([x+0.28, y-0.4, 0.5], .06)}
    joints.update({9 + i: (list(w), .035) for i, w in enumerate(wrists)})
    return person(joints, [x, y, 1.25], look_at, objects=objects)


def standing(x=0.9, y=2.7, look_at=(-1.6, 1.0, 1.2)):
    """Standing: hips 0.95 m, shoulders 1.5 m, head 1.7 m above the floor."""
    joints = {5: ([x-0.19, y, 1.5], .06), 6: ([x+0.19, y, 1.5], .06), 11: ([x-0.12, y, 0.95], .07),
              12: ([x+0.12, y, 0.95], .07), 13: ([x-0.12, y, 0.5], .06), 14: ([x+0.12, y, 0.5], .06)}
    return person(joints, [x, y, 1.7], look_at)


def facts(record, depth, times=(1.0, 2.0, 3.0), track=None):
    track = track or rf.ArmTrack()
    for t in times:
        words = rf.frame_facts(record, depth, MODEL, ROOM, track, t)
    return words


class SiteMapTests(unittest.TestCase):
    def test_categories_become_words_and_names_never_do(self):
        self.assertEqual(sorted(s['word'] for s in ROOM['seats']), ['armchair', 'desk_chair', 'sofa'])
        self.assertEqual(sorted(a['word'] for a in ROOM['areas']), ['doorway', 'floor'])  # the nook has no category
        self.assertEqual(sorted(s['word'] for s in ROOM['surfaces']), ['desk_surface', 'keyboard'])
        # Seats are not look targets; an uncategorized target is other_object.
        self.assertEqual(sorted(a['category'] for a in ROOM['anchors']),
                         sorted(['desk', 'keyboard_area', 'monitor', 'tv', 'window', 'other_object']))
        text = json.dumps(ROOM)
        for name in ("Grandpa", 'telly', 'gizmo', 'secret', 'Rug', 'rug', 'mystery', 'anchor-', 'zone-'):
            self.assertNotIn(name, text)

    def test_the_map_must_be_bound_registered_and_ours(self):
        document = site_map()
        forged = deepcopy(document)
        forged['model']['targets'][0]['pos'] = [9, 9, 0.5]
        cases = {'digest': forged, 'schema': dict(document, schema='den-site-map-0'),
                 'status': site_map(status='accepted'), 'method': site_map(method='guess'),
                 'frame': site_map(source_frame='rgb'), 'calibration': site_map(calibration_content_id=None),
                 'mount': site_map(mount_signature=None),
                 'reflection': site_map(matrix=(CAMERA_TO_ROOM @ np.diag([-1, 1, 1, 1])).tolist()),
                 'scale': site_map(matrix=(CAMERA_TO_ROOM @ np.diag([2, 2, 2, 1])).tolist())}
        for name, value in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                rf.load_site_map(value)
        with self.assertRaises(ValueError):
            rf.load_site_map(document, camera_id='hall')
        self.assertEqual(rf.load_site_map(site_map(method='floor_only'))['registration_method'], 'floor_only')

    def test_live_config_block_is_exact(self):
        self.assertEqual(rf.check_config({'enabled': True, 'site_map_file': '/x/site-map-r001.json'})['enabled'], True)
        for bad in ({}, {'enabled': 'yes', 'site_map_file': '/x'}, {'enabled': True, 'site_map_file': 'rel.json'},
                    {'enabled': True, 'site_map_file': '/x', 'extra': 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                rf.check_config(bad)


class ZoneTests(unittest.TestCase):
    def test_seat_volumes_then_floor_zones_then_elsewhere(self):
        self.assertEqual(rf.zone_word([0.3, 1.8, 0.75], ROOM), 'desk_chair')
        self.assertEqual(rf.zone_word([0.7, 1.8, 0.75], ROOM), 'desk_chair')       # within the 15 cm margin
        self.assertEqual(rf.zone_word([-1.2, 3.5, 0.7], ROOM), 'sofa')
        self.assertEqual(rf.zone_word([-0.7, 2.3, 0.8], ROOM), 'armchair')        # reading_chair -> armchair
        self.assertEqual(rf.zone_word([0.9, 2.7, 1.1], ROOM), 'doorway')
        self.assertEqual(rf.zone_word([0.0, 5.0, 1.1], ROOM), 'floor')            # the rug zone
        self.assertEqual(rf.zone_word([3.5, 3.5, 1.1], ROOM), 'elsewhere_in_room')  # uncategorized zone
        self.assertEqual(rf.zone_word([1.3, 0.3, 1.1], ROOM), 'elsewhere_in_room')
        self.assertEqual(rf.zone_word(None, ROOM), 'unknown')

    def test_standing_high_above_a_seat_is_not_in_it(self):
        self.assertEqual(rf.zone_word([0.3, 1.8, 2.2], ROOM), 'elsewhere_in_room')

    def test_nearest_of_two_overlapping_seats(self):
        room = {'areas': [], 'seats': [{'word': 'armchair', 'centre': [0, 0, 0.5], 'half': [0.4, 0.4, 0.5], 'radius': None},
                                       {'word': 'sofa', 'centre': [1.0, 0, 0.45], 'half': [0.5, 0.4, 0.45], 'radius': None}]}
        self.assertEqual(rf.zone_word([0.45, 0, 0.7], room), 'armchair')     # 5 cm from both: the nearer centre
        self.assertEqual(rf.zone_word([0.47, 0, 0.7], room), 'sofa')         # 3 cm from the sofa, 7 cm from the chair


class PostureMotionTests(unittest.TestCase):
    def test_posture_from_3d_heights(self):
        sit = {'shoulder': [0, 0, 1.0], 'hip': [0, 0, 0.5], 'knee': [0, -0.4, 0.5], 'head': [0, 0, 1.25]}
        stand = {'shoulder': [0, 0, 1.45], 'hip': [0, 0, 0.95], 'knee': [0, 0, 0.5], 'head': [0, 0, 1.7]}
        lie = {'shoulder': [0, 0, 0.55], 'hip': [0.5, 0, 0.5], 'knee': [0.9, 0, 0.5], 'head': [-0.2, 0, 0.6]}
        self.assertEqual(rf.posture_word(sit), 'sitting')
        self.assertEqual(rf.posture_word(stand), 'standing')
        self.assertEqual(rf.posture_word(lie), 'lying')
        self.assertEqual(rf.posture_word({'shoulder': [0, 0, 1.05]}), 'sitting')
        self.assertEqual(rf.posture_word({'shoulder': [0, 0, 1.4]}), 'standing')
        self.assertEqual(rf.posture_word({'head': [0, 0, 1.65]}), 'standing')
        self.assertEqual(rf.posture_word({'shoulder': [0, 0, 1.0], 'hip': [0, 0, 0.5]}), 'sitting')
        self.assertEqual(rf.posture_word({}), 'unknown')
        self.assertEqual(rf.posture_word({'shoulder': [0, 0, 0.6]}), 'unknown')

    def test_motion_over_three_seconds(self):
        still = [(t, [1.0+0.01*(t % 2), 2.0, 0.8]) for t in (0.0, 1.0, 2.0, 3.0)]
        small = [(t, [1.0+0.05*t, 2.0, 0.8]) for t in (0.0, 1.0, 2.0, 3.0)]
        moving = [(t, [1.0+0.3*t, 2.0, 0.8]) for t in (0.0, 1.0, 2.0, 3.0)]
        self.assertEqual(rf.motion_word(still, 3.0), 'still')
        self.assertEqual(rf.motion_word(small, 3.0), 'small')
        self.assertEqual(rf.motion_word(moving, 3.0), 'moving')
        self.assertEqual(rf.motion_word(moving[-1:], 3.0), 'unknown')          # one sample
        self.assertEqual(rf.motion_word(moving[-2:], 3.0), 'unknown')          # 1 s span
        # Samples older than the window are ignored: a walk that ended 5 s ago is still now.
        old = [(0.0, [0.0, 0.0, 0.8])] + [(t, [2.0, 2.0, 0.8]) for t in (5.0, 6.0, 7.0)]
        self.assertEqual(rf.motion_word(old, 7.0), 'still')


class FrameTests(unittest.TestCase):
    def test_seated_at_the_desk_facing_the_monitor_hands_on_the_keyboard(self):
        record, depth = seated()
        words = facts(record, depth)
        self.assertEqual((words['people'], words['position'], words['posture']), ('one', 'desk_chair', 'sitting'))
        self.assertEqual((words['looking_at'], words['looking_at_stability']), ('monitor', 'steady'))
        self.assertEqual(words['hands_near'], ['desk_surface', 'keyboard'])
        self.assertEqual(words['motion'], 'still')
        self.assertEqual(words['history']['segments'], [{'position': 'desk_chair', 'looking_at': 'monitor', 'span': 'under_10_s',
                                                         'order': 'current'}])
        self.assertIsNotNone(rf.projection(words))

    def test_a_book_near_the_hand_is_lifted_to_3d(self):
        # Reading in the armchair: hands hold a book in the lap, the head bowed toward it.
        near = seated(-0.7, 2.3, wrists=((-0.85, 2.0, 0.9), (-0.55, 2.0, 0.9)), look_at=(-0.7, 2.02, 0.8),
                      objects=[('book', (-0.7, 1.98, 0.93), 0.07), ('cup', (0.0, 2.3, 0.6), 0.05)])
        words = facts(*near)
        self.assertEqual((words['position'], words['posture']), ('armchair', 'sitting'))
        self.assertEqual(words['hands_near'], ['book'])   # the cup is 0.7 m from either wrist
        self.assertEqual(words['looking_at'], 'lap_or_hands')

    def test_standing_in_the_doorway_looking_at_the_tv(self):
        record, depth = standing(look_at=(-1.6, 1.2, 1.2))
        words = facts(record, depth)
        self.assertEqual((words['position'], words['posture'], words['looking_at']), ('doorway', 'standing', 'tv'))

    def test_walking_is_moving(self):
        track = rf.ArmTrack()
        for t, x in ((1.0, 0.0), (2.0, 0.45), (3.0, 0.9)):
            words = rf.frame_facts(*standing(x=x), MODEL, ROOM, track, t)
        self.assertEqual(words['motion'], 'moving')
        self.assertEqual(words['posture'], 'standing')

    def test_no_depth_or_no_single_person_is_unknown(self):
        record, depth = seated()
        blind = facts(record, np.zeros_like(depth))
        self.assertEqual((blind['position'], blind['posture'], blind['looking_at'], blind['hands_near']),
                         ('unknown', 'unknown', 'unknown', []))
        two = deepcopy(record)
        two['objects'].append({'label': 'person', 'instance_ref': 'object-9', 'pixel_box': {'x': 1, 'y': 1, 'w': 20, 'h': 40}})
        self.assertEqual({k: v for k, v in facts(two, depth).items() if k in rf.UNKNOWN},
                         dict(rf.UNKNOWN, looking_at_stability='shifting'))
        self.assertEqual(facts(two, depth)['people'], 'multiple')

    def test_storyline_opens_a_segment_only_after_two_frames(self):
        track = rf.ArmTrack()
        desk = seated()
        sofa_look = seated(look_at=(-1.6, 1.0, 1.2))
        for t, scene in ((1.0, desk), (2.0, desk), (3.0, sofa_look), (4.0, desk), (5.0, sofa_look), (6.0, sofa_look)):
            words = rf.frame_facts(*scene, MODEL, ROOM, track, t)
        segments = words['history']['segments']
        self.assertEqual([(s['looking_at'], s['order']) for s in segments], [('monitor', 'just_before'), ('tv', 'current')])

    def test_out_of_order_frames_do_not_rewind_the_track(self):
        track = rf.ArmTrack()
        record, depth = seated()
        rf.frame_facts(record, depth, MODEL, ROOM, track, 5.0)
        words = rf.frame_facts(record, depth, MODEL, ROOM, track, 4.0)
        self.assertEqual(words['looking_at_stability'], 'unknown')
        self.assertEqual(len(track.history), 1)


class ProjectionTests(unittest.TestCase):
    def test_only_registered_words_pass(self):
        words = facts(*seated())
        self.assertEqual(rf.projection(words), words)
        forged = [dict(words, position='Grandpa'), dict(words, position=[0.3, 2.5, 0.75]), dict(words, motion=0.02),
                  dict(words, hands_near=['keyboard', 'keyboard']), dict(words, hands_near=['a pencil']),
                  dict(words, torso=[0.3, 2.5, 0.75]), dict(words, schema='room-facts-0'), {k: v for k, v in words.items() if k != 'posture'},
                  dict(words, history=dict(words['history'], segments=[dict(words['history']['segments'][0], span=12.0)])),
                  dict(words, history=dict(words['history'], extra=True)), None, []]
        for value in forged:
            with self.subTest(value=str(value)[:60]):
                self.assertIsNone(rf.projection(value))

    def test_jev_evidence_projects_only_the_words(self):
        record = room_record('7', 99.0, facts(*seated()))
        projected = project([record])
        self.assertEqual(projected[0]['room_facts'], record['room_facts'])
        self.assertTrue(projected[0]['evidence_roots'][0].startswith('root-'))
        bad = dict(record, room_facts=dict(record['room_facts'], position='kitchen'))
        self.assertEqual(project([bad]), [])


# --- den-facts-8 ---------------------------------------------------------------------

def room_record(ident, t, words, arm='a', session='s'):
    return {'id': str(ident), 'kind': 'observation', 'site_id': 'victoria', 'camera_id': 'den', 'session_id': session,
            'arm': arm, 'generation': 1, 'source_kind': 'room.facts', 'observed_at': t, 'available_at': t + .3,
            'valid_until': t + 2.5, 'frame_id': 'frame-%s' % t, 'capture_epoch': 'capture-1',
            'evidence_roots': [str(ident)], 'room_facts': deepcopy(words)}


def words_for(**changes):
    base = {'schema': rf.RECORD_SCHEMA, 'people': 'one', 'position': 'desk_chair', 'looking_at': 'monitor',
            'looking_at_stability': 'steady', 'hands_near': ['desk_surface', 'keyboard'], 'posture': 'sitting',
            'motion': 'still', 'history': {'window': 'last_60_s', 'earlier_segments_omitted': False, 'segments': [
                {'position': 'armchair', 'looking_at': 'lap_or_hands', 'span': 'about_20_s', 'order': 'just_before'},
                {'position': 'desk_chair', 'looking_at': 'monitor', 'span': 'under_10_s', 'order': 'current'}]}}
    return {**base, **changes}


from test_jev_wire import BINDINGS, frame as wire_frame, ha, qwen  # noqa: E402
from test_qwen_wiring import FACTS  # noqa: E402

REGION = {'edge': 'image_right', 'edge_margin_frac': 0.03, 'min_width_frac': 0.35, 'min_top_frac': 0.08}
SEATED_BOX = (660, 150, 620, 570)  # test_desk_seat: desk_seat on 1280x720 frames
NEUTRAL = {**FACTS, 'held_object': 'none', 'book_state': 'not_visible', 'gaze_target': 'elsewhere', 'typing_visible': 'no',
           'activity_hypothesis': 'resting', 'visible_basis': ['person_visible']}


def frames(box=None, n=3, cutoff=100.0):
    records = []
    for i in range(n):
        record = wire_frame('f%d' % i, cutoff - n + i)
        if box is not None:
            record['objects'][0]['pixel_box'] = dict(zip('xywh', box))
        records.append(record)
    return records


def sidecar(words=None, *, box=None, recency='under_15s', room_t=99.0, facts_=NEUTRAL, cutoff=100.0, room=True):
    raw = frames(box) + [ha('h', cutoff - 1, recency=recency)]
    projected = project(raw, ha_bindings=BINDINGS)
    shared = project(qwen(facts_, cutoff), ha_bindings=BINDINGS) if facts_ else []
    if words is not None:
        shared += project([room_record('r1', room_t, words)])
    return wire.sidecar(projected, shared, None, [wire.frame_facts(r) for r in projected if r['source_kind'] == 'perception.output'],
                        cutoff, room_facts=room)


def state8(side, **kw):
    return wire.build_state(side, cutoff=100.0, wire_version=wire.WIRE_VERSION_8, **kw)


class WireTests(unittest.TestCase):
    def test_room_words_ride_under_now_with_a_room_storyline(self):
        state = state8(sidecar(words_for()))
        wire.validate_state(state)
        now = state['now']
        self.assertEqual({k: now[k] for k in wire.ROOM_NOW_KEYS},
                         {'room_facts': 'current', 'position': 'desk_chair', 'position_method': 'room_map_3d',
                          'looking_at': 'monitor', 'looking_at_stability': 'steady', 'hands_near': ['desk_surface', 'keyboard'],
                          'posture': 'sitting', 'motion': 'still'})
        self.assertEqual([(s['position'], s['looking_at'], s['order']) for s in state['room_storyline']['segments']],
                         [('armchair', 'lap_or_hands', 'just_before'), ('desk_chair', 'monitor', 'current')])
        self.assertEqual((state['schema'], state['guide']), (wire.WIRE_VERSION_8, wire.GUIDE_8))
        self.assertIn('head pose, not eye gaze', state['guide']['now'])

    def test_support_lines_come_from_the_table(self):
        state = state8(sidecar(words_for()))
        work = state['support']['computer_work']
        self.assertEqual(work['room_line'], ['hands_near_keyboard', 'looking_at_monitor', 'position_desk_chair'])
        self.assertEqual((work['room_conflicts'], work['pc_input_role'], work['independent']),
                         ([], 'counts_camera_cue_at_pc', ['pc_input_counts']))
        self.assertEqual(state['support']['reading']['room_conflicts'], ['looking_at_monitor'])
        self.assertEqual(state['support']['watching'], {'room_line': [], 'room_conflicts': ['looking_at_monitor']})
        tv = state8(sidecar(words_for(position='sofa', looking_at='tv', hands_near=[])))['support']
        self.assertEqual((tv['watching']['room_line'], tv['computer_work']['room_conflicts']), (['looking_at_tv'], ['looking_at_tv']))
        self.assertEqual(tv['computer_work']['pc_input_role'], 'recent_not_observed_at_pc')
        reading = state8(sidecar(words_for(position='armchair', looking_at='lap_or_hands', hands_near=['book'])))['support']
        self.assertEqual(reading['reading']['room_line'], ['hands_near_book+looking_at_lap_or_hands'])
        self.assertEqual(reading['computer_work']['room_conflicts'], ['hands_near_book'])

    def test_a_book_in_hand_at_the_desk_blocks_the_pc_cue(self):
        work = state8(sidecar(words_for(hands_near=['book', 'keyboard'])))['support']['computer_work']
        self.assertEqual(work['room_conflicts'], ['hands_near_book'])
        self.assertEqual(work['pc_input_role'], 'recent_not_observed_at_pc')

    def test_the_table_is_data(self):
        rows = wire.ROOM_SUPPORT + ({'activity': 'cooking', 'relation': 'supports', 'when': {'position': 'standing_area'},
                                     'pc_cue': False},)
        wire._check_room_table(rows)
        with patch.object(wire, 'ROOM_SUPPORT', rows), patch.object(wire, 'ROOM_ACTIVITIES', wire.ROOM_ACTIVITIES + ('cooking',)):
            lines, _ = wire.room_lines({'position': 'standing_area', 'looking_at': 'unknown', 'hands_near': [],
                                        'posture': 'standing', 'motion': 'still'})
        self.assertEqual(lines['cooking'], {'room_line': ['position_standing_area'], 'room_conflicts': []})
        for bad in ({'activity': 'x', 'relation': 'supports', 'when': {'position': 'kitchen'}, 'pc_cue': False},
                    {'activity': 'x', 'relation': 'maybe', 'when': {'position': 'sofa'}, 'pc_cue': False},
                    {'activity': 'x', 'relation': 'supports', 'when': {'coordinates': 'sofa'}, 'pc_cue': False},
                    {'activity': 'x', 'relation': 'supports', 'when': {}, 'pc_cue': False}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                wire._check_room_table((bad,))

    def test_without_a_current_3d_fact_the_image_rule_is_the_fallback(self):
        # No room.facts record: with the site desk_seat region the image rule
        # places the person (desk_seat is the desk chair), and says so.
        state = state8(sidecar(None, box=SEATED_BOX), desk_seat=REGION)
        wire.validate_state(state)
        now = state['now']
        self.assertEqual((now['room_facts'], now['position'], now['position_method'], now['looking_at']),
                         ('none', 'desk_chair', 'image_edge_rule', 'unknown'))
        self.assertEqual(state['support']['computer_work']['room_line'], ['position_desk_chair'])
        self.assertEqual(state['support']['computer_work']['pc_input_role'], 'counts_camera_cue_at_pc')
        self.assertNotIn('room_storyline', state)
        bare = state8(sidecar(None, box=SEATED_BOX))
        self.assertEqual((bare['now']['position'], bare['now']['position_method']), ('unknown', 'none'))
        self.assertEqual(bare['support']['computer_work']['room_line'], [])
        # A stale record (over 3 s before the latest frame) is not current.
        stale = state8(sidecar(words_for(), room_t=95.0), desk_seat=REGION)
        self.assertEqual((stale['now']['room_facts'], stale['now']['position_method']), ('none', 'image_edge_rule'))
        # With a current 3D fact the map wins over the image rule.
        mapped = state8(sidecar(words_for(position='sofa'), box=SEATED_BOX), desk_seat=REGION)
        self.assertEqual((mapped['now']['position'], mapped['now']['position_method']), ('sofa', 'room_map_3d'))

    def test_no_camera_frame(self):
        side = wire.sidecar([], [], None, [], 100.0, room_facts=True)
        state = state8(side, desk_seat=REGION)
        wire.validate_state(state)
        self.assertEqual((state['now']['room_facts'], state['now']['position'], state['now']['position_method']),
                         ('none', 'unknown', 'none'))

    def test_every_budget_level_validates(self):
        side = sidecar(words_for())
        for level in wire.LEVELS:
            state = state8(side, budget_level=level)
            wire.validate_state(state)
            self.assertEqual(state['now']['looking_at'], 'monitor')
            self.assertEqual('room_storyline' in state, level < 3)
        self.assertEqual(len(state8(side, budget_level=2)['room_storyline']['segments']), 2)

    def test_no_coordinate_name_or_free_text_can_leak(self):
        state = state8(sidecar(words_for()))
        forged = []
        for key, value in (('position', [0.3, 2.5, 0.8]), ('position', 'Grandpa'), ('looking_at', 0.5),
                           ('hands_near', ['keyboard', 0.12]), ('hands_near', ['secret gizmo']), ('motion', None),
                           ('posture', 'crouching'), ('position_method', 'lidar')):
            bad = deepcopy(state)
            bad['now'][key] = value
            forged.append(bad)
        extra = deepcopy(state)
        extra['now']['torso_m'] = [0.3, 2.5, 0.8]
        forged.append(extra)
        story = deepcopy(state)
        story['room_storyline']['segments'][0]['distance_m'] = 1.2
        forged.append(story)
        support = deepcopy(state)
        support['support']['computer_work']['room_line'] = ['looking_at_monitor', 'anchor-monitor']
        forged.append(support)
        for bad in forged:
            with self.subTest(), self.assertRaises(ValueError):
                wire.validate_state(bad)
        # And the builder never emits a number anywhere.
        def walk(value):
            if isinstance(value, dict):
                return all(walk(v) for v in value.values())
            if isinstance(value, list):
                return all(walk(v) for v in value)
            return isinstance(value, (str, bool))
        self.assertTrue(walk(state))

    def test_prepare_admits_the_room_record_only_on_den_facts_8(self):
        records = project(frames() + [ha('h', 99.0, recency='under_15s')], ha_bindings=BINDINGS)
        shared = project([room_record('r1', 99.0, words_for())])
        kwargs = dict(site_id='victoria', session_id='s', arm='a', generation=1, cutoff=100.0, window_seconds=10,
                      model='jev-1.13.0')
        local8, packet8, binding8 = wire.prepare(records, shared, None, wire_version=wire.WIRE_VERSION_8, **kwargs)
        self.assertEqual([r['source_kind'] for r in local8['shared']], ['room.facts'])
        self.assertEqual(packet8['state']['now']['position'], 'desk_chair')
        self.assertEqual(binding8['wire_version'], wire.WIRE_VERSION_8)
        self.assertEqual(wire.PROJECTION_VERSIONS[wire.WIRE_VERSION_8], 'structured-facts-11')

    def test_den_facts_6_and_7_are_byte_identical_with_a_room_record_offered(self):
        records = project(frames(SEATED_BOX) + [ha('h', 99.0, recency='under_15s')], ha_bindings=BINDINGS)
        room = project([room_record('r1', 99.0, words_for())])
        kwargs = dict(site_id='victoria', session_id='s', arm='a', generation=1, cutoff=100.0, window_seconds=10,
                      model='jev-1.13.0')
        for version, extra in ((wire.WIRE_VERSION, {}), (wire.WIRE_VERSION_7, {'desk_seat': REGION})):
            with self.subTest(version):
                without = wire.prepare(records, [], None, wire_version=version, **extra, **kwargs)
                with_room = wire.prepare(records, room, None, wire_version=version, **extra, **kwargs)
                self.assertEqual(canonical(without), canonical(with_room))
                self.assertNotIn('room_facts', with_room[1]['state']['now'])

    def test_registered_schemas_and_digests(self):
        self.assertEqual(wire.WIRE_VERSIONS, ('den-facts-6', 'den-facts-7', 'den-facts-8'))
        self.assertEqual(wire.ROOM_WIRES, ('den-facts-8',))
        from test_desk_seat import SCHEMA_DIGESTS
        self.assertEqual({w: wire.schema_digest(w) for w in wire.WIRE_VERSIONS}, SCHEMA_DIGESTS)
        # The v6/v7 question sets ride den-facts-8; v8 (den-facts-7 wording) does not.
        self.assertTrue(wire.question_wire_ok(wire.QUESTION_VERSION, wire.WIRE_VERSION_8))
        self.assertTrue(wire.question_wire_ok(wire.QUESTION_VERSION_V7, wire.WIRE_VERSION_8))
        self.assertFalse(wire.question_wire_ok(wire.QUESTION_VERSION_10, wire.WIRE_VERSION_8))


class FactsKeyTests(unittest.TestCase):
    def test_each_room_word_moves_both_keys(self):
        base = state8(sidecar(words_for()))
        for version in wire.FACTS_KEY_VERSIONS:
            for key, value in (('position', 'sofa'), ('looking_at', 'tv'), ('hands_near', ['book']), ('posture', 'standing'),
                               ('motion', 'moving'), ('room_facts', 'none')):
                changed = deepcopy(base)
                changed['now'][key] = value
                self.assertNotEqual(wire.facts_key(changed, version), wire.facts_key(base, version), (version, key))

    def test_the_coarse_key_ignores_flicker(self):
        base = state8(sidecar(words_for()))
        for key, value in (('looking_at_stability', 'shifting'), ('motion', 'small'), ('position_method', 'image_edge_rule')):
            changed = deepcopy(base)
            changed['now'][key] = value
            self.assertEqual(wire.facts_key_coarse(changed), wire.facts_key_coarse(base), key)
            self.assertNotEqual(wire.facts_key(changed), wire.facts_key(base), key)
        shuffled = deepcopy(base)
        shuffled['room_storyline']['segments'][0]['span'] = 'about_40_s'
        self.assertEqual(wire.facts_key(shuffled), wire.facts_key(base))       # spans never key
        reordered = deepcopy(base)
        reordered['now']['hands_near'] = ['keyboard', 'desk_surface']
        self.assertEqual(wire.facts_key_coarse(reordered), wire.facts_key_coarse(base))

    def test_keys_of_other_wires_are_unchanged(self):
        side = sidecar(words_for(), box=SEATED_BOX)
        for version, extra in ((wire.WIRE_VERSION, {}), (wire.WIRE_VERSION_7, {'desk_seat': REGION})):
            plain = wire.build_state(sidecar(None, box=SEATED_BOX, room=False), cutoff=100.0, wire_version=version, **extra)
            offered = wire.build_state(side, cutoff=100.0, wire_version=version, **extra)
            for key_version in wire.FACTS_KEY_VERSIONS:
                self.assertEqual(wire.facts_key(offered, key_version), wire.facts_key(plain, key_version))


import test_jev_policy_v6 as policy_fixtures  # noqa: E402


class PolicyTests(policy_fixtures.V6Case):
    """The gate needs the den-facts-8 grant, re-derives the room.facts record
    from its ledger row and recomputes the state."""

    def setUp(self):
        super().setUp()
        self.policy['structured_sources'] = [QWEN_EGRESS, wire.WIRE_VERSION, wire.WIRE_VERSION_8]
        self.room_id = self.store.append_event(self.sid, 'room.facts', {
            'site_id': 'victoria', 'camera_id': 'den', 'arm': 'a', 'generation': 1, 'status': 'completed', 'rule': rf.RULE,
            'observed_at': 99.0, 'available_at': 99.3, 'valid_until': 101.5, 'frame_id': 'frame-99.0',
            'capture_epoch': 'capture-1', 'evidence_roots': [str(self.ids[99.0])], 'source_event_id': self.ids[99.0],
            'map': {'map_id': 'victoria-den', 'revision': '1', 'model_sha256': '0' * 64}, 'pairing_skew_s': 0.1,
            'result': {'room_facts': words_for(position='armchair', looking_at='lap_or_hands', hands_near=['phone'])}})

    def request8(self, rid='r8', wire_version=wire.WIRE_VERSION_8):
        from room_facts import facts_context
        records = project(self.records(), ha_bindings=policy_fixtures.BINDINGS)
        rooms = facts_context(self.store, self.store.get_session(self.sid), 100.0, generation=1, arms=('a',))['a']
        shared = project(rooms)
        side, packet, binding = wire.prepare(records, shared, None, site_id='victoria', session_id=self.sid, arm='a',
                                             generation=1, cutoff=100.0, window_seconds=60, model='jev-1.13.0',
                                             ring=wire.FrameRing(), wire_version=wire_version)
        context = {'request_id': rid, 'site_id': 'victoria', 'session_id': self.sid, 'arm': 'a', 'generation': 1,
                   **{k: v for k, v in binding.items() if k not in ('packet_bytes', 'estimated_tokens')}}
        self.store.db.execute('INSERT OR REPLACE INTO lab_reasoning_requests VALUES(?,?,?)', (rid, self.sid, 'dispatching'))
        return context, packet, side

    def test_den_facts_8_admits_under_its_own_grant(self):
        context, packet, side = self.request8()
        self.assertEqual([r['source_kind'] for r in side['shared']], ['room.facts'])
        self.assertEqual((packet['state']['now']['position'], packet['state']['support']['phone_use']['room_line']),
                         ('armchair', ['hands_near_phone+looking_at_lap_or_hands']))
        self.assertTrue(self.gate(context, packet, 'send', local=side)['allowed'])
        row = self.store.db.execute('SELECT wire_version FROM lab_jev_egress_facts').fetchone()
        self.assertEqual(row[0], wire.WIRE_VERSION_8)
        self.assertTrue(self.gate(context, packet, 'publish', local=side)['allowed'])

    def test_other_grants_do_not_cover_den_facts_8(self):
        self.policy['structured_sources'] = [QWEN_EGRESS, wire.WIRE_VERSION, wire.WIRE_VERSION_7]
        self.refused(*self.request8(), message='Packet wire not permitted')

    def test_a_forged_room_word_or_record_is_refused(self):
        context, packet, side = self.request8()
        forged = deepcopy(packet)
        forged['state']['now']['looking_at'] = 'monitor'
        self.refused(context, forged, side)
        # A sidecar record that is not its ledger row (re-sealed so only the
        # ledger re-derivation can catch it).
        tampered = deepcopy(side)
        tampered['shared'][0]['room_facts']['looking_at'] = 'monitor'
        packet2 = deepcopy(packet)
        packet2['state'] = wire.build_state(tampered, cutoff=100.0, storyline_seconds=context['storyline_seconds'],
                                            budget_level=context['budget_level'], wire_version=wire.WIRE_VERSION_8)
        self.refused(dict(context, evidence_digest=digest(tampered)), packet2, tampered)

    def test_a_room_record_on_another_wire_is_refused(self):
        context, packet, side = self.request()      # den-facts-6
        smuggled = deepcopy(side)
        smuggled['shared'].append(self.request8('r9')[2]['shared'][0])
        self.refused(dict(context, evidence_digest=digest(smuggled)), packet, smuggled)


# --- Live stage -------------------------------------------------------------------------

import test_paired_runtime as runtime_fixtures  # noqa: E402


def bundle_bytes(depth, stamp, epoch='epoch-1', *, compressed=False):
    meta = dict(metadata(stamp), session=epoch)
    buffer = io.BytesIO()
    (np.savez_compressed if compressed else np.savez)(
        buffer, depth_mm=depth, left=np.zeros((400, 640), np.uint8), right=np.zeros((400, 640), np.uint8),
        rgb_nv12=np.full((540, 640), 128, np.uint8), metadata_json=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8))
    return buffer.getvalue()


class FakeCamera:
    def __init__(self, calibration=CALIBRATION):
        self.calibration, self.reads = dict(calibration), []

    def _read(self, path):
        self.reads.append(path)
        if path == '/calibration':
            return json.dumps(self.calibration).encode(), ''
        raise AssertionError('the stage reads bundles through its pump only')


class StageTests(unittest.TestCase):
    tearDown = runtime_fixtures.PairedRuntimeTests.tearDown

    def setUp(self):
        runtime_fixtures.PairedRuntimeTests.setUp(self)
        self.now = 100.0
        self.store.clock = lambda: self.now
        self.sid = self.session['id']
        self.camera = FakeCamera()
        self.stage = rf.RoomFactsStage(self.store, ROOM, camera_id='den', client=self.camera)
        self.generation = 1
        self.pump = self.stage.pump(self.session, active=lambda: True, generation=lambda: self.generation)
        self.record, self.depth = seated()

    def output(self, t, stamp, arm='a', generation=1, epoch='epoch-1', frame=True):
        frame_id = 'frame-%s-%s' % (arm, t)
        media = []
        if frame:
            item = self.store.add_media(self.sid, b'jpeg-%s-%s' % (arm.encode(), str(t).encode()), 'image/jpeg', {
                'stream': 'rgb', 'frame_id': frame_id, 'capture_epoch': epoch, 'source_timestamp_seconds': stamp})
            media = [item['id']]
        result = {k: deepcopy(self.record[k]) for k in ('objects', 'pose_instances')}
        result['quality'] = {'image_width': 640, 'image_height': 360}
        return self.store.append_event(self.sid, 'perception.output', {
            'site_id': 'victoria', 'camera_id': 'den', 'session_id': self.sid, 'arm': arm, 'generation': generation,
            'status': 'completed', 'observed_at': t, 'available_at': t + .1, 'valid_until': t + 2.5, 'frame_id': frame_id,
            'capture_epoch': epoch, 'media_ids': media, 'result': result})

    def facts_rows(self):
        rows = self.store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='room.facts' ORDER BY id",
                                     (self.sid,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def test_both_arms_are_paired_with_one_shared_depth_sample(self):
        self.output(99.0, 10.02, 'a')
        self.output(99.0, 10.05, 'b')
        self.now = 99.4
        self.assertEqual(self.stage.ingest(self.sid, bundle_bytes(self.depth, 10.0)), {'state': 'active', 'written': 2})
        rows = self.facts_rows()
        self.assertEqual([r['arm'] for r in rows], ['a', 'b'])
        words = rows[0]['result']['room_facts']
        self.assertEqual((words['position'], words['looking_at'], words['posture']), ('desk_chair', 'monitor', 'sitting'))
        self.assertEqual(rows[0]['map'], {'map_id': 'victoria-den', 'revision': '1', 'model_sha256': ROOM['model_sha256']})
        self.assertEqual((rows[0]['observed_at'], rows[0]['valid_until'], rows[0]['available_at']), (99.0, 101.5, 99.4))
        self.assertEqual(self.camera.reads, ['/calibration'])          # once per capture epoch
        # The shared context the den-facts-8 scheduler offers: newest valid per arm.
        context = rf.facts_context(self.store, self.store.get_session(self.sid), 99.5, generation=1)
        self.assertEqual(sorted(context), ['a', 'b'])
        self.assertEqual(context['a'][0]['room_facts'], words)
        self.assertEqual(context['a'][0]['source_kind'], 'room.facts')
        self.assertEqual(rf.facts_context(self.store, self.store.get_session(self.sid), 99.3, generation=1), {})  # not yet available
        status = self.stage.status(self.sid)
        self.assertEqual((status['state'], status['written'], status['bundles']), ('active', 2, 1))

    def test_a_detection_waits_for_its_bundle_and_then_gives_up(self):
        self.output(99.0, 10.6, 'a')
        self.now = 99.2
        self.assertEqual(self.stage.ingest(self.sid, bundle_bytes(self.depth, 10.0))['written'], 0)   # 0.6 s ahead: wait
        self.assertEqual(self.stage.ingest(self.sid, bundle_bytes(self.depth, 10.5))['written'], 1)   # 0.1 s: paired
        self.output(99.5, 20.0, 'a')
        self.stage.ingest(self.sid, bundle_bytes(self.depth, 21.0))                                   # already passed
        self.assertEqual(self.stage.status(self.sid)['no_depth_in_window'], 1)
        self.output(99.6, 10.7, 'a', epoch='epoch-0')                                                 # another capture epoch
        self.stage.ingest(self.sid, bundle_bytes(self.depth, 10.7))
        self.assertEqual(len(self.facts_rows()), 1)

    def test_other_generations_and_frames_without_rgb_media_are_skipped(self):
        self.output(99.0, 10.0, 'a', generation=2)
        self.output(99.0, 10.0, 'b', frame=False)
        self.stage.ingest(self.sid, bundle_bytes(self.depth, 10.0))
        self.assertEqual(self.facts_rows(), [])
        self.assertEqual(self.stage.status(self.sid)['rgb_frame_unavailable'], 1)
        self.generation = None                                   # before activation: nothing is read as current
        self.output(99.1, 10.1, 'a')
        self.stage.ingest(self.sid, bundle_bytes(self.depth, 10.1))
        self.assertEqual(self.facts_rows(), [])

    def test_a_different_calibration_or_a_moved_camera_stops_the_facts(self):
        self.camera.calibration['eeprom'] = {**CALIBRATION['eeprom'], 'boardName': 'other'}
        self.output(99.0, 10.0, 'a')
        self.assertEqual(self.stage.ingest(self.sid, bundle_bytes(self.depth, 10.0)), {'state': 'calibration_differs_from_map'})
        self.assertEqual(self.facts_rows(), [])
        moved = rf.RoomFactsStage(self.store, ROOM, camera_id='den', client=FakeCamera())
        moved.pump(self.session, active=lambda: True, generation=lambda: 1)
        near_wall = render(MODEL, [], wall_z=2.5)
        for i in range(rf.MOUNT_CHANGED_CHECKS):
            outcome = moved.ingest(self.sid, bundle_bytes(near_wall, 10.0 + i))
        self.assertEqual(outcome, {'state': 'camera_moved_since_map'})
        self.assertEqual(moved.status(self.sid)['state'], 'camera_moved_since_map')

    def test_the_owner_in_view_does_not_fail_the_mount_check(self):
        self.stage.ingest(self.sid, bundle_bytes(self.depth, 10.0))
        self.assertEqual(self.stage.sessions[self.sid].mount, 'verified')

    def test_the_pump_is_a_2_hz_bundle_pump(self):
        self.assertEqual(self.pump.interval, rf.SAMPLE_INTERVAL_S)
        self.assertFalse(self.pump.thread.is_alive())


class RuntimeTests(unittest.TestCase):
    """No map: byte-identical runtime behaviour. With a map: the stage's pump
    joins the bundle pumps and den-facts-8 scheduling offers its records."""
    setUp = runtime_fixtures.PairedRuntimeTests.setUp
    tearDown = runtime_fixtures.PairedRuntimeTests.tearDown

    def runtime(self, **kw):
        from service_runtime import ServiceRuntime

        class Recorder:
            def start(self, session):
                return {'state': 'recording'}
        return ServiceRuntime(self.store, {'den': Recorder()}, lambda *a: {'state': 'blocked', 'reason': 'fixture'},
                              lambda request: None, **kw)

    def test_no_map_starts_no_pump_and_offers_nothing(self):
        runtime = self.runtime()
        try:
            self.assertIsNone(runtime.room_facts)
            runtime.start_session(self.session)
            self.assertEqual(runtime.bundle_pumps, {})
            self.assertEqual(runtime._room_facts_context(self.session, 100.0, 1), {})
            self.assertEqual(runtime._room_facts_status(self.session['id']), {})
        finally:
            runtime.close()

    def test_a_map_adds_one_room_pump_and_offers_only_to_den_facts_8(self):
        stage = rf.RoomFactsStage(self.store, ROOM, camera_id='den', client=FakeCamera())
        factory = type('Factory', (), {'wire': wire.WIRE_VERSION_7})()
        runtime = self.runtime(room_facts=stage, reasoning_factory=None)
        try:
            runtime.start_session(self.session)
            self.assertEqual(list(runtime.bundle_pumps), [self.session['id']])
            self.assertEqual(runtime._room_facts_status(self.session['id'])['room_facts_status']['state'], 'mount_unverified')
            runtime.reasoning_factory = factory
            self.assertEqual(runtime._room_facts_context(self.session, 100.0, 1), {})
            factory.wire = wire.WIRE_VERSION_8
            with patch('room_facts.facts_context', return_value={'a': ['r']}) as read:
                self.assertEqual(runtime._room_facts_context(self.session, 100.0, 1), {'a': ['r']})
            read.assert_called_once()
        finally:
            runtime.close()

    def test_room_facts_need_the_inference_runtime(self):
        from service_runtime import ServiceRuntime
        stage = rf.RoomFactsStage(self.store, ROOM, camera_id='den', client=FakeCamera())
        with self.assertRaises(ValueError):
            ServiceRuntime(self.store, {}, lambda *a: None, lambda r: None, recording_only=True, room_facts=stage)


class ConfigTests(unittest.TestCase):
    def test_live_config_accepts_only_the_exact_block_and_absence_changes_nothing(self):
        import live_detector_app as app
        self.assertIsNone(app.room_facts_map({}, site_id='victoria', camera_id='den'))
        self.assertIsNone(app.room_facts_map({'room_facts': {'enabled': False, 'site_map_file': '/x.json'}},
                                             site_id='victoria', camera_id='den'))
        room = app.room_facts_map({'room_facts': {'enabled': True, 'site_map_file': '/x.json'}}, site_id='victoria',
                                  camera_id='den', read=lambda path: site_map())
        self.assertEqual(room['revision'], '1')
        with self.assertRaises(ValueError):
            app.room_facts_map({'room_facts': {'enabled': True, 'site_map_file': '/x.json'}}, site_id='victoria',
                               camera_id='den', read=lambda path: dict(site_map(), schema='other'))

    def test_den_facts_8_provisioning(self):
        from managed_jev import reasoning_factory
        config = {'schema_version': 1, 'policy_file': 'policy', 'credential_file': 'key', 'response_version': 'jev-1.13.0'}
        with patch('managed_jev.private_json', side_effect=lambda p: dict(config, wire=wire.WIRE_VERSION_8)):
            factory = reasoning_factory('config')
        self.assertEqual((factory.wire, factory.desk_seat), (wire.WIRE_VERSION_8, None))
        with patch('managed_jev.private_json', side_effect=lambda p: dict(config, wire=wire.WIRE_VERSION_8, desk_seat=REGION,
                                                                         questions='v7')):
            self.assertEqual(reasoning_factory('config').desk_seat, REGION)
        with patch('managed_jev.private_json', side_effect=lambda p: dict(config, wire=wire.WIRE_VERSION_8, questions='v8')):
            with self.assertRaises(ValueError):
                reasoning_factory('config')


class CostTests(unittest.TestCase):
    """Measured on this host (report it; the Jetson's cores are slower): one
    ~790 KB compressed camera NPZ fetched and decoded, and one frame's facts."""

    def test_fetch_decode_and_facts_cost(self):
        rng = np.random.default_rng(0)
        # A representative bundle: smooth room depth with sensor noise and
        # holes, textured stereo pairs, an NV12 frame; ~790 KB compressed.
        depth = (EMPTY.astype(np.int32) + rng.normal(0, 40, EMPTY.shape)).clip(0, 65535).astype(np.uint16)
        depth[rng.random(depth.shape) < 0.08] = 0
        left = (rng.random((400, 640)) * 40 + 108).astype(np.uint8)
        right = np.roll(left, 12, axis=1)
        nv12 = (rng.random((540, 640)) * 8 + 124).astype(np.uint8)
        meta = dict(metadata(10.0), session='epoch-1')
        buffer = io.BytesIO()
        np.savez_compressed(buffer, depth_mm=depth, left=left, right=right, rgb_nv12=nv12,
                            metadata_json=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8))
        data = buffer.getvalue()
        self.assertTrue(700_000 <= len(data) <= 900_000, len(data))
        runs = 20
        # The stage's own fetch path (LocalPreviewClient._read) over loopback HTTP.
        import http.client
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from preview_client import LocalPreviewClient

        class Owner(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Owner)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            client = LocalPreviewClient(lambda: 'x' * 32, connection_factory=lambda host, port, timeout: http.client.HTTPConnection(
                '127.0.0.1', server.server_address[1], timeout=timeout))
            started = time.perf_counter()
            for _ in range(runs):
                fetched, _ = client._read('/frame.npz')
            fetch_ms = 1000 * (time.perf_counter() - started) / runs
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(fetched, data)
        started = time.perf_counter()
        for _ in range(runs):
            decoded, _ = rf.decode_bundle(data)
        decode_ms = 1000 * (time.perf_counter() - started) / runs
        started = time.perf_counter()
        for _ in range(runs):
            with np.load(io.BytesIO(data)) as archive:
                _ = [archive[k] for k in archive.files]
        full_ms = 1000 * (time.perf_counter() - started) / runs
        record, scene_depth = seated()
        track = rf.ArmTrack()
        started = time.perf_counter()
        for i in range(runs):
            rf.frame_facts(record, scene_depth, MODEL, ROOM, track, float(i))
        facts_ms = 1000 * (time.perf_counter() - started) / runs
        # 2 Hz fetch and decode plus two arms' ~1 Hz facts, as a share of one core.
        share = (2 * (fetch_ms + decode_ms) + 2 * facts_ms) / 1000
        print('\nroom facts cost: npz %d KB, loopback fetch %.1f ms, depth+metadata decode %.1f ms (all members %.1f ms), '
              'facts %.1f ms/frame, ~%.1f%% of one core at 2 Hz sampling and two ~1 Hz arms'
              % (len(data) // 1024, fetch_ms, decode_ms, full_ms, facts_ms, 100 * share))
        self.assertEqual(decoded.shape, (400, 640))
        self.assertLess(fetch_ms + decode_ms, 250)
        self.assertLess(facts_ms, 500)


if __name__ == '__main__':
    unittest.main()
