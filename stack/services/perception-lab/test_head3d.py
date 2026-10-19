import math
import unittest

import numpy as np

import head3d
from depth_association import camera_model, project

K_LEFT = [[396.8, 0, 315.79], [0, 396.81, 212.4], [0, 0, 1]]
K_RGB = [[485.78, 0, 312.6], [0, 485.47, 182.76], [0, 0, 1]]
# Victoria's EEPROM chain, rounded: RGB sits 3.77 cm left of rectified left.
RGB_FROM_LEFT = {'rotation': [[1, 0, 0], [0, 1, 0], [0, 0, 1]], 'translation_m': [-0.0377, 0.0004, 0.0005]}


def synthetic_model():
    frames = {'depth': {'transformation_valid': True, 'intrinsics': K_LEFT, 'width': 640, 'height': 400},
              'rgb': {'transformation_valid': True, 'intrinsics': K_RGB, 'width': 640, 'height': 360,
                      'source_distortion_coefficients': [0.0]*14}}
    calibration = {'id': 'synthetic', 'mount_revision': 'synthetic', 'rgb_from_rectified_left': RGB_FROM_LEFT,
                   'reference_intrinsics': {'rectified_left': K_LEFT, 'rgb': K_RGB}}
    return camera_model({'frames': frames}, calibration, measuring_reprojection=True)


def render(model, spheres=(), wall_z=3.0, noise_mm=0.0, seed=0):
    """Depth (uint16 mm) of spheres [(centre, radius)] in front of a fronto-parallel wall."""
    rays = model['rays']
    depth = np.full(rays.shape[:2], wall_z)
    norm2 = (rays**2).sum(axis=2)
    for centre, radius in spheres:
        c = np.asarray(centre, dtype=float)
        b = rays @ c
        disc = b*b-norm2*(c @ c-radius*radius)
        hit = disc >= 0
        t = np.where(hit, (b-np.sqrt(np.maximum(disc, 0)))/norm2, np.inf)
        depth = np.minimum(depth, t)
    if noise_mm:
        depth = depth+np.random.default_rng(seed).normal(0, noise_mm/1000, depth.shape)
    return np.round(depth*1000).astype(np.uint16)


def rgb_pixel(model, point):
    transform = model['rgb_from_left']
    q = transform[:3, :3] @ np.asarray(point, dtype=float)+transform[:3, 3]
    uv, ok = project(q[None, :], model['rgb_k'], model['rgb_distortion'])
    return [float(uv[0, 0]), float(uv[0, 1])]


def head_axes(centre, yaw_deg, pitch_deg=0.0):
    """Head turned yaw_deg toward image right from facing the camera; level camera."""
    up = np.array([0.0, -1.0, 0.0])
    toward = -np.asarray(centre, dtype=float)
    toward = toward-(toward @ up)*up
    toward /= np.linalg.norm(toward)
    flat = math.cos(math.radians(yaw_deg))*toward+math.sin(math.radians(yaw_deg))*np.array([1.0, 0, 0])
    forward = math.cos(math.radians(pitch_deg))*flat+math.sin(math.radians(pitch_deg))*up
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    head_up = np.cross(right, forward)
    return right, forward, head_up, toward


def scene(model, centre, yaw_deg, *, pitch_deg=0.0, hide_ears=False, merged_far_ear=True, noise_mm=0.0):
    right, forward, up, toward = head_axes(centre, yaw_deg, pitch_deg)
    c = np.asarray(centre, dtype=float)
    landmarks = {j: c+x*right+y*forward+z*up for j, (x, y, z) in head3d.HEAD_TEMPLATE.items()}
    # Torso keeps facing the camera; shoulders 19 cm below the ears (level
    # head: nose-to-shoulder-line ratio ~0.45, as calibrated on 0dc34636).
    torso_right = np.cross(toward, np.array([0.0, -1.0, 0.0]))
    shoulders = {head3d.LEFT_SHOULDER: c-0.19*torso_right+np.array([0, 0.19, 0]),
                 head3d.RIGHT_SHOULDER: c+0.19*torso_right+np.array([0, 0.19, 0])}
    # A 7 cm skull sphere with each landmark a small bump on its surface.
    spheres = [(c+0.01*forward, 0.07)]
    for joint, point in landmarks.items():
        radius = 0.012
        spheres.append((point+radius*point/np.linalg.norm(point), radius))
    depth = render(model, spheres, noise_mm=noise_mm)
    if hide_ears:
        # Dark hair: no stereo depth around either ear.
        v, u = np.mgrid[0:depth.shape[0], 0:depth.shape[1]]
        for joint in (head3d.LEFT_EAR, head3d.RIGHT_EAR):
            x, y, z = landmarks[joint]
            eu, ev = K_LEFT[0][0]*x/z+K_LEFT[0][2], K_LEFT[1][1]*y/z+K_LEFT[1][2]
            depth[(u-eu)**2+(v-ev)**2 <= 12**2] = 0
    joints = []
    near_ear = head3d.RIGHT_EAR if yaw_deg > 0 else head3d.LEFT_EAR
    for joint, point in {**landmarks, **shoulders}.items():
        pixel = rgb_pixel(model, point)
        if abs(yaw_deg) >= 30 and joint in (head3d.LEFT_EAR, head3d.RIGHT_EAR) and joint != near_ear:
            if not merged_far_ear:
                continue
            pixel = rgb_pixel(model, landmarks[near_ear])  # RTMPose puts the hidden ear on the near one
        joints.append({'joint_id': joint, 'pixel': pixel, 'status': 'supported'})
    return {'joints': joints}, depth, forward


class LiftTests(unittest.TestCase):
    def test_rgb_pixel_lifts_to_the_surface_it_sees(self):
        model = synthetic_model()
        depth = render(model, [], wall_z=2.0)
        target = np.array([0.3, -0.1, 2.0])
        lifter = head3d.DepthLifter(depth, model)
        sample = lifter.lift(*rgb_pixel(model, target))
        self.assertEqual(sample['status'], 'measured')
        self.assertLess(np.linalg.norm(np.asarray(sample['xyz'])-target), 0.01)

    def test_roi_lifter_matches_the_full_frame_and_refuses_holes(self):
        model = synthetic_model()
        depth = render(model, [], wall_z=2.0)
        pixel = rgb_pixel(model, [0.0, 0.0, 2.0])
        full = head3d.DepthLifter(depth, model).lift(*pixel)
        roi = head3d.DepthLifter(depth, model, rgb_roi=(pixel[0]-10, pixel[1]-10, pixel[0]+10, pixel[1]+10))
        self.assertLess(len(roi.points), 5000)
        self.assertLess(np.linalg.norm(np.asarray(roi.lift(*pixel)['xyz'])-np.asarray(full['xyz'])), 1e-9)
        self.assertEqual(head3d.DepthLifter(np.zeros_like(depth), model).lift(*pixel)['status'], 'unknown')


class HeadPoseTests(unittest.TestCase):
    def setUp(self):
        self.model = synthetic_model()

    def assertDirection(self, got, want, tolerance_deg):
        self.assertLess(head3d._angle_deg(got, want), tolerance_deg)

    def test_frontal_head_fits_the_template_from_depth(self):
        pose, depth, forward = scene(self.model, [0.1, 0.0, 1.5], 0.0, noise_mm=2)
        head = head3d.estimate_head(pose, head3d.DepthLifter(depth, self.model))
        self.assertEqual((head['status'], head['method']), ('estimated', 'face_template_3d'))
        self.assertDirection(head['forward'], forward, 10)
        self.assertLess(np.linalg.norm(np.asarray(head['head_xyz'])-[0.1, 0.0, 1.5]), 0.03)
        self.assertEqual(head['provenance']['kind'], 'head_pose_not_eye_gaze')

    def test_turned_head_with_merged_far_ear_uses_the_near_ear(self):
        pose, depth, forward = scene(self.model, [0.1, 0.0, 1.5], 50.0)
        joints = head3d.resolve_ears(head3d.joints_of(pose))
        self.assertIn(head3d.RIGHT_EAR, joints)
        self.assertNotIn(head3d.LEFT_EAR, joints)
        head = head3d.estimate_head(pose, head3d.DepthLifter(depth, self.model))
        self.assertEqual(head['method'], 'face_template_3d')
        self.assertDirection(head['forward'], forward, 10)
        # Turned toward image right: forward gains +x in the camera frame.
        self.assertGreater(head['forward'][0], 0.5)

    def test_ear_depth_holes_fall_back_to_the_2d_prior(self):
        pose, depth, forward = scene(self.model, [0.1, 0.0, 1.5], 50.0, hide_ears=True)
        head = head3d.estimate_head(pose, head3d.DepthLifter(depth, self.model))
        self.assertEqual((head['method'], head['fusion']), ('yaw_prior_2d', 'prior_only'))
        self.assertIn(head3d.RIGHT_EAR, head['refused_joints'])
        self.assertGreater(head['forward'][0], 0.3)
        self.assertLess(head3d._angle_deg(head['forward'], forward), 40)

    def test_no_face_depth_is_unknown(self):
        pose, depth, _ = scene(self.model, [0.1, 0.0, 1.5], 0.0)
        head = head3d.estimate_head(pose, head3d.DepthLifter(np.zeros_like(depth), self.model))
        self.assertEqual((head['status'], head['reason']), ('unknown', 'no_face_depth'))

    def test_template_fit_rejects_implausible_geometry(self):
        points = {j: list(v) for j, v in {head3d.NOSE: (0, 0, 1.0), head3d.LEFT_EYE: (0.3, 0, 1.0),
                                           head3d.LEFT_EAR: (0, 0.3, 1.3), head3d.RIGHT_EAR: (0.4, 0.4, 0.8)}.items()}
        self.assertEqual(head3d.fit_head(points)['status'], 'unknown')
        self.assertEqual(head3d.fit_head({head3d.NOSE: [0, 0, 1], head3d.LEFT_EYE: [0, 0, 1],
                                          head3d.RIGHT_EYE: [0, 0, 1]})['reason'], 'needs_nose_ear_and_three_face_joints')


ANCHORS = [{'id': 'anchor-monitor', 'label': 'monitor', 'category': 'monitor', 'pos': [3.0, 1.0, 1.2], 'radius_m': 0.3},
           {'id': 'anchor-painting', 'label': 'painting', 'category': 'painting', 'pos': [1.0, 3.0, 1.6], 'size_m': [0.9, 0.05, 0.7]},
           {'id': 'anchor-reading_chair', 'label': 'reading_chair', 'category': 'reading_chair', 'pos': [1.0, 1.0, 0.8], 'radius_m': 0.6}]


def head_room(forward, head=(1.0, 1.0, 1.2)):
    f = np.asarray(forward, dtype=float)
    f /= np.linalg.norm(f)
    return {'head_m': list(head), 'forward': list(f), 'pitch_deg': math.degrees(math.asin(f[2]))}


class ConeTests(unittest.TestCase):
    def test_best_anchor_in_the_cone_and_the_chair_you_sit_in_is_skipped(self):
        result = head3d.look_target(head_room([1, 0, 0]), ANCHORS)
        self.assertEqual(result['looking_at'], 'monitor')
        self.assertNotIn('reading_chair', [c['anchor'] for c in result['candidates']])
        self.assertEqual(head3d.look_target(head_room([0, 1, 0.2]), ANCHORS)['looking_at'], 'painting')
        # 15 deg off the monitor's edge is still inside a 20 deg cone.
        self.assertEqual(head3d.look_target(head_room([math.cos(0.4), math.sin(0.4), 0]), ANCHORS)['looking_at'], 'monitor')

    def test_elsewhere_lap_or_hands_and_ambiguity(self):
        self.assertEqual(head3d.look_target(head_room([-1, 0, 0]), ANCHORS)['looking_at'], 'elsewhere')
        self.assertEqual(head3d.look_target(head_room([-0.5, 0, -1]), ANCHORS)['looking_at'], 'lap_or_hands')
        window = {'id': 'anchor-window', 'label': 'window', 'pos': [3.0, 1.15, 1.2], 'radius_m': 0.3}
        self.assertEqual(head3d.look_target(head_room([1, 0, 0]), ANCHORS+[window])['reason'], 'ambiguous')
        # Same-group neighbours (keyboard below the monitor) are not a conflict.
        keyboard = {'id': 'anchor-keyboard_area', 'label': 'keyboard_area', 'pos': [2.9, 1.0, 1.0], 'radius_m': 0.2}
        self.assertEqual(head3d.look_target(head_room([1, 0, 0]), ANCHORS+[keyboard])['looking_at'], 'monitor')

    def test_anchor_words_strip_numbered_suffixes(self):
        self.assertEqual(head3d.anchor_word({'label': 'window_2'}), 'window')
        self.assertEqual(head3d.anchor_word({'label': 'keyboard_area'}), 'keyboard_area')
        self.assertEqual(head3d.anchor_word({'category': 'monitor', 'label': 'x'}), 'monitor')


class StabilityTests(unittest.TestCase):
    def test_steady_shifting_and_insufficient(self):
        stability = head3d.Stability()
        self.assertEqual(stability.update(0.0, 'monitor', [1, 0, 0]), 'insufficient')
        stability.update(0.5, 'monitor', [1, 0.02, 0])
        self.assertEqual(stability.update(1.0, 'monitor', [1, -0.02, 0]), 'steady')
        stability.update(1.5, 'painting', [0, 1, 0])
        self.assertEqual(stability.update(2.0, 'window', [-1, 1, 0]), 'shifting')
        # Old samples leave the 3 s window.
        for t in (6.0, 6.5, 7.0):
            state = stability.update(t, 'painting', [0, 1, 0])
        self.assertEqual(state, 'steady')
        with self.assertRaises(ValueError):
            stability.update(1.0, 'painting')


class EndToEndTests(unittest.TestCase):
    def test_camera_frame_head_reaches_a_room_anchor(self):
        model = synthetic_model()
        # Level camera 1.2 m above the floor at the room origin, looking along +Y.
        camera_to_room = [[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 1.2], [0, 0, 0, 1]]
        centre = [0.2, 0.0, 1.6]  # seated head, slightly image right of the camera axis
        pose, depth, forward = scene(model, centre, 45.0)
        # The monitor sits where this head points, 1.5 m away (room frame).
        m = np.asarray(camera_to_room, dtype=float)
        monitor = m[:3, :3] @ (np.asarray(centre)+1.5*forward)+m[:3, 3]
        anchors = [{'id': 'anchor-monitor', 'label': 'monitor', 'pos': list(monitor), 'radius_m': 0.3},
                   {'id': 'anchor-painting', 'label': 'painting', 'pos': [-1.5, 3.0, 1.6], 'radius_m': 0.4}]
        stability = head3d.Stability()
        for t in (0.0, 0.5, 1.0):
            result = head3d.looking_at(pose, depth, model, camera_to_room, anchors, stability=stability, t=t)
        self.assertEqual(result['looking_at'], 'monitor')
        self.assertEqual(result['looking_at_stability'], 'steady')
        self.assertEqual(result['head']['method'], 'face_template_3d')


if __name__ == '__main__':
    unittest.main()
