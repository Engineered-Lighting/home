import io
import json
import unittest
import numpy as np
import test_paired_runtime as fixtures
import test_room_context as maps
from bundle_ingest import BundleIngestor
from depth_association import (MAX_REGIONS, DepthAssociator, SessionTracks, associate, camera_model,
                               eeprom_rgb_from_rectified_left, floor_contact, frame_projection, person_regions, region_mask,
                               reprojection_residuals, rgb_pixels)
from depth_clock import DepthClock
from spatial_consumer import SpatialConsumer
from spatial_validation import mount_signature
from service_runtime import ServiceRuntime

KD = [[450, 0, 319.5], [0, 450, 199.5], [0, 0, 1]]
KRGB = [[500, 0, 319.5], [0, 500, 179.5], [0, 0, 1]]
BASELINE = [-.0375, 0, 0]
CLOCK = {'id':'clock-1', 'drift_ppm':50, 'max_delta_s':1, 'semantics_bound_s':.002, 'semantics_receipt':'semantics-1'}
# Camera 1.2 m above the floor; x right, z forward -> map x, y; -y -> map z.
REGISTRATION = [[1, 0, 0, 1], [0, 0, 1, 0], [0, -1, 0, 1.2], [0, 0, 0, 1]]


def scene(*, person=(-.25, .25, -.5, .7), z=2.0, wall=4.0, holes=0.0):
    """Ray-cast rectified-left depth (mm) of a person slab in front of a wall."""
    v, u = np.mgrid[0:400, 0:640]
    x, y = (u-319.5)/450*z, (v-199.5)/450*z
    hit = (x >= person[0]) & (x <= person[1]) & (y >= person[2]) & (y <= person[3])
    depth = np.where(hit, z*1000, wall*1000).astype(np.uint16)
    if holes:
        depth[hit & (np.random.default_rng(3).random(depth.shape) < holes)] = 0
    return depth


def rgb_box(person=(-.25, .25, -.5, .7), z=2.0):
    x0, x1 = (500*(person[0]+BASELINE[0])/z+319.5, 500*(person[1]+BASELINE[0])/z+319.5)
    y0, y1 = 500*person[2]/z+179.5, 500*person[3]/z+179.5
    return {'x':x0, 'y':y0, 'w':x1-x0, 'h':y1-y0}


def calibration(content_id='content', **changes):
    return {'site_id':'victoria', 'camera_id':'den', 'id':content_id, 'status':'validated', 'mount_revision':'mount-1',
            'validation_receipt':'sha256:depth', 'frame':'rectified_left', 'width':640, 'height':400,
            'error_bound_m':.03, 'intrinsics':{'fx':450, 'fy':450, 'cx':319.5, 'cy':199.5},
            'reference_intrinsics':{'rectified_left':KD, 'rgb':KRGB},
            'rgb_from_rectified_left':{'rotation':np.eye(3).tolist(), 'translation_m':BASELINE},
            'reprojection':{'receipt':'sha256:reprojection', 'p95_px':1.2, 'max_px':2.0}, **changes}


def frames(**changes):
    base = {'sequence':7, 'host_monotonic_timestamp_seconds':10, 'transformation_valid':True}
    return {'depth':{**base, 'device_timestamp_seconds':50.21, 'width':640, 'height':400, 'intrinsics':KD},
            'left':{**base, 'device_timestamp_seconds':50.21, 'width':640, 'height':400, 'intrinsics':KD},
            'right':{**base, 'device_timestamp_seconds':50.21, 'width':640, 'height':400, 'intrinsics':KD},
            'rgb':{**base, 'device_timestamp_seconds':50.206, 'width':640, 'height':360, 'intrinsics':KRGB,
                   'source_distortion_coefficients':[0.0]*14}, **changes}


class GeometryTests(unittest.TestCase):
    def model(self, **changes):
        return camera_model({'frames':frames()}, calibration(**changes))

    def test_person_region_maps_to_slab_depth_not_the_wall(self):
        result = associate(scene(), self.model(), {'kind':'box', 'box':rgb_box()})
        self.assertEqual(result['status'], 'associated')
        self.assertAlmostEqual(result['depth_m'], 2.0)
        mask = result['mask']
        self.assertTrue(np.all(scene()[mask] == 2000))
        x0, y0, x1, y1 = result['roi']
        # The 15% inset box lands inside the slab's rectified-left pixels.
        self.assertTrue(263 <= x0 and x1 <= 377 and 87 <= y0 and y1 <= 358)
        self.assertAlmostEqual(result['camera_point'][2], 2.0)

    def test_one_frame_projection_serves_every_region_with_the_same_masks(self):
        model, depth = self.model(), scene(holes=.2)
        projection = frame_projection(depth, model)
        region = {'kind':'box', 'box':rgb_box()}
        shared = associate(depth, model, region, projection=projection)
        self.assertEqual(shared['status'], 'associated')
        self.assertGreater(shared['hole_pixels'], 0)
        np.testing.assert_array_equal(shared['mask'], associate(depth, model, region)['mask'])
        # Reference: every hole projected at the region's median depth, as before.
        rgb = region_mask(region, size=model['rgb_size'])
        holes = depth == 0
        uv, ok = rgb_pixels(model, np.full(depth.shape, shared['prior_depth_m']*1000), holes)
        ur, vr = np.floor(uv[:, 0]+.5).astype(int), np.floor(uv[:, 1]+.5).astype(int)
        inside = ok & (ur >= 0) & (ur < 640) & (vr >= 0) & (vr < 360)
        found = np.zeros(len(uv), bool); found[inside] = rgb[vr[inside], ur[inside]]
        expected = np.zeros(depth.shape, bool); expected[holes] = found
        np.testing.assert_array_equal(shared['mask'] & holes, expected)
        with self.assertRaisesRegex(ValueError, 'another depth frame'):
            associate(scene(), model, region, projection=projection)

    def test_wrong_extrinsics_or_holes_abstain_instead_of_measuring_background(self):
        model = self.model(rgb_from_rectified_left={'rotation':np.eye(3).tolist(), 'translation_m':[.4, 0, 0]})
        wrong = associate(scene(), model, {'kind':'box', 'box':rgb_box()})
        self.assertNotEqual(wrong.get('depth_m'), 2.0)
        holes = associate(scene(holes=.7), self.model(), {'kind':'box', 'box':rgb_box()})
        self.assertEqual(holes['reason'], 'insufficient_valid_support')
        self.assertEqual(associate(scene(), self.model(), {'kind':'box', 'box':{'x':0, 'y':0, 'w':4, 'h':4}})['reason'],
                         'region_too_small')

    def test_model_requires_reprojection_receipt_and_stable_intrinsics(self):
        with self.assertRaisesRegex(ValueError, 'reprojection'):
            self.model(reprojection={'receipt':'x', 'p95_px':6})
        with self.assertRaisesRegex(ValueError, 'frame_intrinsics_changed'):
            camera_model({'frames':frames(rgb={**frames()['rgb'], 'intrinsics':[[505, 0, 319.5], [0, 505, 179.5], [0, 0, 1]]})},
                         calibration())
        with self.assertRaisesRegex(ValueError, 'distortion'):
            camera_model({'frames':frames(rgb={**frames()['rgb'], 'source_distortion_coefficients':[0]*8+[.1]+[0]*5})},
                         calibration())

    def test_reprojection_residuals_measure_marker_pairs(self):
        model, depth = self.model(), scene()
        pairs = []
        for u, v in ((300, 150), (320, 250), (350, 320)):
            x, y = (u-319.5)/450*2, (v-199.5)/450*2
            pairs.append({'left_pixel':[u, v], 'rgb_pixel':[500*(x+BASELINE[0])/2+319.5+3, 500*y/2+179.5]})
        residuals, missing = reprojection_residuals(model, depth, pairs+[{'left_pixel':[5, 5], 'rgb_pixel':[0, 0]}])
        self.assertEqual(len(residuals), 4)
        for value in residuals[:3]:
            self.assertAlmostEqual(value, 3.0, places=6)
        residuals, missing = reprojection_residuals(model, np.zeros((400, 640), np.uint16), pairs)
        self.assertEqual(missing, [0, 1, 2])

    def test_eeprom_chain_composes_rectification_and_extrinsics(self):
        angle = .02
        r1 = [[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]]
        eeprom = {'stereoRectificationData':{'leftCameraSocket':1, 'rightCameraSocket':2, 'rectifiedRotationLeft':r1},
                  'cameraData':[[0, {'extrinsics':{'toCameraSocket':-1}}],
                                [1, {'extrinsics':{'rotationMatrix':np.eye(3).tolist(), 'translation':{'x':-7.5, 'y':0, 'z':0},
                                                   'toCameraSocket':2}}],
                                [2, {'extrinsics':{'rotationMatrix':np.eye(3).tolist(), 'translation':{'x':3.75, 'y':.1, 'z':0},
                                                   'toCameraSocket':0}}]]}
        result = eeprom_rgb_from_rectified_left(eeprom)
        np.testing.assert_allclose(result['rotation'], np.asarray(r1).T, atol=1e-12)
        np.testing.assert_allclose(result['translation_m'], [-.0375, .001, 0], atol=1e-12)
        eeprom['cameraData'][2][1]['extrinsics']['toCameraSocket'] = -1
        with self.assertRaisesRegex(ValueError, 'chain'):
            eeprom_rgb_from_rectified_left(eeprom)

    def test_regions_prefer_masks_then_torso_and_contacts_respect_truncation(self):
        box = {'x':100, 'y':50, 'w':80, 'h':200, 'label':'person', 'instance_id':'f:0'}
        joints = [{'name':n, 'pixel':p, 'status':'supported'} for n, p in (
            ('left_shoulder', [120, 90]), ('right_shoulder', [160, 90]), ('right_hip', [155, 170]), ('left_hip', [125, 170]),
            ('left_ankle', [125, 240]), ('right_ankle', [155, 244]))]
        result = {'boxes':[box, {**box, 'instance_id':'f:1'}, {**box, 'label':'chair', 'instance_id':'f:2'}],
                  'pose_instances':[{'instance_id':'f:1', 'joints':joints}],
                  'masks':[{'instance_id':'f:0', 'polygons':[[[100, 50], [180, 50], [180, 250]]]}]}
        regions = person_regions(result, width=640, height=360)
        self.assertEqual([r['region']['kind'] for r in regions], ['polygon', 'torso'])
        # A box without an instance id never borrows another instance's mask or pose.
        anonymous = person_regions({'boxes':[{k: v for k, v in box.items() if k != 'instance_id'}],
                                    'masks':[{'polygons':[[[100, 50], [180, 50], [180, 250]]]}],
                                    'pose_instances':[{'joints':joints}]}, width=640, height=360)
        self.assertEqual(anonymous[0]['region']['kind'], 'box')
        self.assertEqual(regions[1]['contact'], {'contact_pixel':[140, 242], 'contact_kind':'ankle_midpoint', 'contact_visible':True})
        self.assertEqual(floor_contact({**box, 'h':310}, width=640, height=360)['contact_visible'], False)
        # A box bottom may be the furniture edge in front of a seated person: never visible.
        self.assertEqual(regions[0]['contact'], {'contact_pixel':[140, 250], 'contact_kind':'box_bottom_centre',
                                                 'contact_visible':False})
        self.assertIsNone(region_mask({'kind':'torso', 'points':[[1, 1], [2, 1], [2, 2], [1, 2]]}, size=(640, 360)))
        with self.assertRaises(ValueError):
            region_mask({'kind':'box', 'box':{'x':600, 'y':0, 'w':80, 'h':10}}, size=(640, 360))

    def test_session_tracks_are_anonymous_continuity_per_arm_and_method(self):
        tracks = SessionTracks('victoria', 'den')
        first = tracks.assign('s1', 'e1', 'a', 'stereo_surface', 1.0, [[0, 0, 2]])
        self.assertEqual(tracks.assign('s1', 'e1', 'a', 'stereo_surface', 1.5, [[.1, 0, 2]]), first)
        self.assertNotEqual(tracks.assign('s1', 'e1', 'b', 'stereo_surface', 1.5, [[.1, 0, 2]]), first)
        self.assertNotEqual(tracks.assign('s1', 'e1', 'a', 'stereo_surface', 9.0, [[.1, 0, 2]]), first)
        self.assertNotEqual(tracks.assign('s2', 'e1', 'a', 'stereo_surface', 9.5, [[.1, 0, 2]]), first)
        with self.assertRaises(ValueError):
            tracks.assign('s1', 'e1', 'a', 'stereo_surface', 9.0, [[.1, 0, 2]])
        for _ in range(12):
            tracks.assign(str(_), 'e', 'a', 'm', 1, [[0, 0, 1]])
        self.assertLessEqual(len(tracks.banks), 8)


class ChainTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def recorded(self):
        sid = self.session['id']
        depth = scene()
        meta = {'schema':1, 'session':'capture1', 'depth_alignment':'rectified_left', 'depth_units':'millimetres',
                'invalid_depth':0, 'captured_unix_ns':100_500_000_000,
                'captured_unix_ns_semantics':'bundle_packaging_time_not_sensor_exposure', 'frames':frames()}
        arrays = {'depth_mm':depth, 'left':np.zeros((400, 640), np.uint8), 'right':np.zeros((400, 640), np.uint8),
                  'rgb_nv12':np.zeros((540, 640), np.uint8), 'metadata_json':np.frombuffer(json.dumps(meta).encode(), np.uint8)}
        output = io.BytesIO(); np.savez_compressed(output, **arrays)
        cal = {'session':'capture1', 'depth_alignment':'rectified_left', 'depth_units':'millimetres', 'eeprom':{'d':1}}
        bundle = BundleIngestor(self.store, site_id='victoria', camera_id='den').ingest(sid, output.getvalue(), cal)
        receipt = {'schema':3, 'session':'capture1', 'timestamp_semantics':'causal_exact_frame_exposure_end',
                   'frame_device_ns':50_200_000_000, 'observed_at_unix_ns':100_400_000_000, 'uncertainty_seconds':.003,
                   'timestamp_quantization_ns':1000, 'validation_id':'causal:'+'b'*32}
        rgb = self.store.add_media(sid, b'synthetic-causal-jpeg', 'image/jpeg', {'site_id':'victoria', 'camera_id':'den',
            'capture_epoch':'capture1', 'frame_id':'capture1-0-41', 'stream':'rgb', 'format':'jpeg', 'width':640, 'height':360,
            'observed_at':100.4, 'available_at':100.45, 'valid_until':103.39, 'uncertainty_seconds':.004,
            'clock_mapping_status':'verified', 'clock_mapping_id':receipt['validation_id'], 'source_timing_receipt':receipt})
        self.calibration = calibration(bundle['metadata']['calibration_content_id'], mount_signature=mount_signature(depth))
        self.store.clock = lambda:100.6
        mapped = DepthClock(self.store, site_id='victoria', camera_id='den', clock=CLOCK,
                            calibration=self.calibration).derive(sid, bundle['id'], rgb['id'])
        return rgb, mapped

    def test_detection_to_room_position_through_the_trusted_chain(self):
        sid = self.session['id']
        rgb, mapped = self.recorded()
        self.assertEqual(mapped['metadata']['geometric_validity'], 'validated')
        room = maps.RoomContextTests(); room.setUp()
        registration = {**room.registration, 'matrix':REGISTRATION, 'calibration_version':self.calibration['id'],
                        'mount_revision':'mount-1'}
        consumer = SpatialConsumer(self.store, self.calibration, registration, room.map, association_revision='assoc-1')
        runtime = ServiceRuntime(self.store, {}, None, None, spatial_sources={'den':consumer})
        self.store.db.execute('INSERT INTO lab_experiment_state VALUES(?,?,?)', (sid, 'depth', 1)); self.store.db.commit()
        self.store.append_event(sid, 'experiment.activated', {'generation':1, 'effective_at':100,
            'spatial_policy':{'a':{'localization':'stereo', 'room_relations':True}, 'b':{'localization':'none', 'room_relations':False}}})
        # Specialist result arrives 1.5 s after the RGB exposure.
        self.store.clock = lambda:101.9
        box = {**rgb_box(), 'label':'person', 'confidence':.9, 'instance_id':'capture1-0-41:0'}
        source = {'site_id':'victoria', 'camera_id':'den', 'capture_epoch':'capture1', 'frame_id':'capture1-0-41',
                  'session_id':sid, 'observed_at':100.4, 'available_at':101.9, 'valid_until':103.39, 'media_ids':[rgb['id']],
                  'status':'completed', 'arm':'a', 'generation':1,
                  'result':{'schema_version':1, 'frame_id':'capture1-0-41', 'coordinate_space':'source_pixels', 'boxes':[box]}}
        eid = self.store.append_event(sid, 'perception.output', source)
        associator = DepthAssociator(self.store, association_revision='assoc-1', calibration=self.calibration,
                                     tracks=SessionTracks('victoria', 'den'))
        associations = associator.associations(sid, eid, source, localization='stereo', depth_media_id=mapped['id'])
        self.assertEqual([a['roi_frame'] for a in associations], ['rectified_left'])
        context = runtime.ingest_spatial(sid, associations, generation=1)
        person = context['people'][0]
        self.assertAlmostEqual(person['pos'][0], 1.0, places=1)
        self.assertAlmostEqual(person['pos'][1], 2.0, places=6)
        self.assertLess(person['uncertainty_m'], .3)
        self.assertIsNone(person['identity'])
        relation = next(m for m in context['memberships'] if m['zone_id']=='desk')
        self.assertEqual(relation['relation'], 'inside')
        # Bounded work: more person regions than the limit become one counted abstention.
        many = {**source, 'result':{**source['result'], 'boxes':[{**box, 'instance_id':f'capture1-0-41:{i}', 'confidence':.5+i/100}
                                                                  for i in range(MAX_REGIONS+3)]}}
        bounded = associator.associations(sid, eid, many, localization='stereo', depth_media_id=mapped['id'])
        self.assertEqual(bounded[0], {'association_revision':'assoc-1', 'source_event_id':eid,
                                      'abstention':'person_region_limit', 'count':3})
        self.assertEqual(len(bounded), 1+MAX_REGIONS)
        # No depth frame in the skew window: an explicit abstention, never a guess.
        missing = associator.associations(sid, eid, source, localization='stereo')
        self.assertEqual(missing, [{'association_revision':'assoc-1', 'source_event_id':eid, 'abstention':'no_depth_frame_in_window'}])


    def test_floor_contacts_need_supported_ankles_and_confident_regions_come_first(self):
        sid = self.session['id']
        rgb, _ = self.recorded()
        homography = {'id':'fh1', 'matrix':[[.01, 0, 0], [0, .01, 0], [0, 0, 1]], 'error_bound_m':.1}
        associator = DepthAssociator(self.store, association_revision='assoc-1', calibration=self.calibration,
                                     tracks=SessionTracks('victoria', 'den'), floor_homography=homography,
                                     target_frame='z_up_metric_floor0')
        boxes, poses = [], []
        for i in range(MAX_REGIONS+2):
            instance = f'capture1-0-41:{i}'
            boxes.append({'x':20+i*55, 'y':50, 'w':40, 'h':200, 'label':'person', 'confidence':(i+1)/20, 'instance_id':instance})
            poses.append({'instance_id':instance, 'joints':[{'name':n, 'pixel':[30+i*55+k*20, 245], 'status':'supported'}
                                                            for k, n in enumerate(('left_ankle', 'right_ankle'))]})
        # The most confident person has no supported ankles: its box bottom is never a floor contact.
        boxes.append({'x':300, 'y':40, 'w':60, 'h':150, 'label':'person', 'confidence':.99, 'instance_id':'capture1-0-41:seated'})
        source = {'site_id':'victoria', 'camera_id':'den', 'capture_epoch':'capture1', 'frame_id':'capture1-0-41',
                  'observed_at':100.4, 'media_ids':[rgb['id']], 'arm':'b',
                  'result':{'boxes':boxes, 'pose_instances':poses}}
        output = associator.associations(sid, 5, source, localization='floor_projection')
        base = {'association_revision':'assoc-1', 'source_event_id':5}
        self.assertEqual(output[0], {**base, 'abstention':'person_region_limit', 'count':3})
        self.assertEqual(output[1], {**base, 'abstention':'floor_contact_not_visible'})
        placed = output[2:]
        self.assertEqual(len(placed), MAX_REGIONS-1)
        self.assertEqual({a['contact_kind'] for a in placed}, {'ankle_midpoint'})
        # Kept: the highest-confidence ankle contacts (i = 3..9), most confident first.
        self.assertEqual([a['contact_pixel'][0] for a in placed], [40+i*55 for i in range(MAX_REGIONS+1, 2, -1)])


if __name__ == '__main__': unittest.main()
