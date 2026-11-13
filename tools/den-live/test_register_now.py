"""Tests for register_now.py (registration v2), scan_pick.mjs and the method file (live den view, step 2). Stdlib unittest.

    python -B -X utf8 tools\\den-live\\test_register_now.py

No camera, LA or ssh. The depth check, the edge guard, the distance check and the held-out check run on synthetic depth
and synthetic rooms. The parts on renders need a prepared den-sim set (DEN_SIM_SET, else
%USERPROFILE%\\den-sim\\prepared\\fbc7bda3115e) and are skipped without it: test_sofa_check's fake camera serves the
set's marks take (an empty rendered room, perfect depth), or a copy of it with its depth scaled; den-register's own
ClickSession clicks nine features found on the render's depth; the scan page is the real scan_pick.mjs on the authored
room, saved over HTTP with those features' room positions through the set's own registration; and the held-out check
reads a points.ply made from the render the same way. The scan page's tests need Node. Everything written is under
TEMP.
"""
import atexit
import contextlib
import csv
import io
import json
import math
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_sofa_check as ts  # noqa: E402  its fake camera, and its no-ssh audit hook
import register_now as rn  # noqa: E402
import sofa_check as sc  # noqa: E402

import numpy as np  # noqa: E402

SET = ts.SET
FIT = SET / 'e9e0a238f4bd'
MARKS = SET / '9083b764abfe'
NODE = shutil.which('node')
TREE = sc.archive(Path(tempfile.gettempdir()) / 'den-live-test-tools', 'register')
R = rn.den_register(TREE)
SITE = 'den-test-site'   # the held-out check's content packs, made per test; never the real site


TMP = Path(tempfile.mkdtemp(prefix='den-live-test-'))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)   # synthetic frames and copies only; nothing is kept


def scratch(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix, dir=TMP))


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def method_copy(folder, **numbers):
    """The committed method file with some den-live numbers changed, as a new file."""
    text = rn.METHOD_FILE.read_text(encoding='utf-8')
    for name, value in numbers.items():
        text, n = re.subn(r'^%s = [0-9.]+' % name, '%s = %r' % (name, value), text, flags=re.M)
        assert n == 1, name
    path = Path(folder) / rn.METHOD_FILE.name   # the same name: the summary names the method by it
    path.write_text(text, encoding='utf-8')
    return path


LIVE = rn.read_live(rn.METHOD_FILE.read_text(encoding='utf-8'))
GATE = 0.15   # the method file's gate_m: the distance check stops only past it


# ---- the method file ------------------------------------------------------------------------------------------

class MethodFile(unittest.TestCase):
    def test_den_register_reads_it_with_every_slot_closed_and_the_owners_numbers(self):
        m, live = rn.load_method(R)
        self.assertEqual(m.open, {})
        self.assertEqual(m['gate_m'], 0.15)   # the owner, 2026-11-13: far features within 15 cm
        self.assertEqual((m['min_fit_pairs'], m['min_checks']), (6, 3))
        self.assertEqual(m['depth_window_px'], 3)
        self.assertEqual(live['sofa_check_max_m'], 0.10)   # each sofa check within 10 cm
        self.assertEqual((live['held_within_min'], live['held_bin_max_m']), (0.75, 0.10))
        self.assertEqual((live['scale_bend_max_pts'], live['scale_none_pct']), (5.0, 1.5))
        self.assertEqual(live['require_source_correction'], 1)
        self.assertEqual(set(live), set(rn.LIVE_KEYS))
        self.assertEqual(rn.METHOD_FILE.name, 'REGISTER-LIVE-20261113.md')

    def test_no_tape_is_in_the_method_file(self):
        """The tapes are typed at the console and kept in the private receipt only."""
        self.assertNotIn('tape_m', rn.METHOD_FILE.read_text(encoding='utf-8').split('```den-live-v2')[1])

    def test_the_old_method_file_stays_for_old_receipts(self):
        old = HERE / 'REGISTER-LIVE-20261111.md'
        self.assertEqual(R.method.load(old)['gate_m'], 0.10)

    def test_den_registers_own_method_file_still_has_open_slots(self):
        """The control: den-register's own REGISTER-V1 would refuse a scored fit, which is why this file exists."""
        self.assertTrue(R.method.load().open)

    def test_a_bad_den_live_block_is_refused(self):
        good = rn.METHOD_FILE.read_text(encoding='utf-8')
        for broken in (good.replace('sofa_check_max_m = 0.10', 'sofa_check_max_m = open'),
                       good.replace('require_source_correction = 1', 'require_source_correction = 2'),
                       good.replace('own_scale_band = 0.05', ''),
                       good.replace('floor_band_m = 0.03', 'floor_band_m = 0.03\nfloor_band_m = 0.04'),
                       good.replace('held_within_min = 0.75', 'held_within_min = 7.5'),
                       good.replace('edge_rounds = 3', 'edge_rounds = 1.5'),
                       good + '\n```den-live-v2\nsofa_check_max_m = 1\n```\n'):
            with self.assertRaises(ValueError):
                rn.read_live(broken)
        self.assertEqual(rn.read_live(good)['floor_min_share'], 0.10)


# ---- the take median and its copy -----------------------------------------------------------------------------------

def members(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {info.filename: z.read(info) for info in z.infolist()}


def write_take(folder, rows, datas, take_doc=None):
    """A den-take-1 folder of these frames: frames.csv with their own hashes, take.json, a /calibration reply."""
    (folder / 'frames').mkdir(parents=True)
    out = []
    for row, data in zip(rows, datas):
        (folder / row['file']).write_bytes(data)
        out.append(dict(row, npz_sha256=rn.sha256_bytes(data), bytes=str(len(data))))
    text = io.StringIO()
    writer = csv.DictWriter(text, list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(out)
    (folder / 'frames.csv').write_text(text.getvalue(), encoding='utf-8', newline='')
    (folder / 'take.json').write_text(json.dumps(take_doc or json.loads((MARKS / 'take.json').read_text(encoding='utf-8'))),
                                      encoding='utf-8')
    (folder / 'camera-calibration.json').write_text('{}', encoding='utf-8')
    return folder


def marks_rows(count=None):
    rows = list(csv.DictReader(io.StringIO((MARKS / 'frames.csv').read_text(encoding='utf-8'))))
    return rows[:count] if count else rows


def depth_of(data):
    return np.frombuffer(bytes(R.scene.read_bundle(data)['depth']), dtype='<u2').reshape(rn.HEIGHT, rn.WIDTH)


@unittest.skipUnless((MARKS / 'frames.csv').is_file(), 'needs a prepared den-sim set')
class MedianCopy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Five frames whose depth at three pixels is set: valid in 3 of 5 (1000, 1010, 1040), in 2 of 5, and in all
        five but one frame far off (an outlier the median ignores)."""
        rows = marks_rows(5)
        values = {(10, 20): [1000, 0, 1010, 0, 1040], (11, 20): [1000, 0, 0, 1020, 0],
                  (12, 20): [2000, 2002, 2004, 2006, 9000]}

        def frame(i):
            def depth(old):
                new = old.copy().reshape(rn.HEIGHT, rn.WIDTH)
                for (v, u), series in values.items():
                    new[v, u] = series[i]
                return new
            return rn.rewrite_depth(R, (MARKS / rows[i]['file']).read_bytes(), depth)
        cls.source = write_take(scratch('den-live-take-') / 'raw', rows, [frame(i) for i in range(5)])

    def test_the_median_of_valid_depth_and_half_the_frames(self):
        plane, k, n = rn.median_plane(R, self.source, LIVE['median_min_valid'])
        self.assertEqual(n, 5)
        self.assertEqual(plane[10, 20], 1010)   # the middle of 1000, 1010, 1040: the zeros don't count
        self.assertEqual(plane[11, 20], 0)      # valid in 2 of 5, under half
        self.assertEqual(plane[12, 20], 2004)   # one frame far off moves nothing
        self.assertEqual(set(k), {'fx', 'fy', 'cx', 'cy'})

    def test_every_bundle_holds_the_median_times_the_scale_and_nothing_else_changes(self):
        plane, _, n = rn.median_plane(R, self.source, LIVE['median_min_valid'])
        target = scratch('den-live-copy-') / 'raw-x0.97-m'
        self.assertEqual(rn.median_copy(R, self.source, target, plane, 0.97, {'file': 'X.md', 'sha256': 'f' * 64}, n), 5)
        copied, frames = R.scene.read_take(target)
        expected = np.where(plane > 0, np.clip(np.rint(plane * 0.97), 1, 65535), 0)
        for row in frames:
            _, _, data, _ = R.scene.load_frame(target, row['n'])   # each bundle's hash against the new frames.csv
            np.testing.assert_array_equal(depth_of(data), expected)
            before = members((self.source / row['file']).read_bytes())
            after = members(data)
            self.assertEqual(set(before), set(after))
            for name in before:
                if name != 'depth_mm.npy':
                    self.assertEqual(before[name], after[name], name)
        self.assertEqual(depth_of((target / frames[0]['file']).read_bytes())[10, 20], round(1010 * 0.97))
        self.assertEqual((copied['derived']['depth_scale'], copied['derived']['median_of_frames']), (0.97, 5))
        self.assertEqual(copied['take'], 'raw-x0.97-m')
        self.assertEqual(rn.scaled_name(self.source, 0.97).name, 'raw-x0.97-m')
        self.assertEqual(rn.scaled_name(self.source, 1.0).name, 'raw-x1-m')

    def test_a_copy_stopped_part_way_never_blocks_the_next(self):
        """Ctrl+C on the second frame leaves only <name>.tmp, never a copy that looks finished; a copy cut short by an
        older paste (a folder with no take.json) is cleared too. The next copy writes whole and reads as a take."""
        plane, _, n = rn.median_plane(R, self.source, LIVE['median_min_valid'])
        method = {'file': 'X.md', 'sha256': 'f' * 64}
        target = scratch('den-live-copy-') / 'raw-x1-m'
        real, calls = rn.rewrite_depth, []

        def stopped(*args):
            calls.append(1)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return real(*args)
        with unittest.mock.patch.object(rn, 'rewrite_depth', stopped):
            with self.assertRaises(KeyboardInterrupt):
                rn.median_copy(R, self.source, target, plane, 1.0, method, n)
        self.assertFalse(target.exists(), 'nothing that looks like a copy')
        self.assertTrue(target.with_name('raw-x1-m.tmp').is_dir())
        self.assertEqual(rn.median_copy(R, self.source, target, plane, 1.0, method, n), 5)
        self.assertFalse(target.with_name('raw-x1-m.tmp').exists())
        self.assertEqual(R.scene.read_take(target)[0]['take'], 'raw-x1-m')
        shutil.rmtree(target)
        (target / 'frames').mkdir(parents=True)   # an older paste's copy, cut short before its take.json
        self.assertEqual(rn.median_copy(R, self.source, target, plane, 1.0, method, n), 5)
        self.assertEqual(len(R.scene.read_take(target)[1]), 5)

    def test_scale_bundle_changes_only_the_depth_values(self):
        data = (self.source / marks_rows(1)[0]['file']).read_bytes()
        old, new = depth_of(data), depth_of(rn.scale_bundle(R, data, 0.83))
        np.testing.assert_array_equal(new[old == 0], 0)
        np.testing.assert_array_equal(new[old > 0], np.clip(np.rint(old[old > 0] * 0.83), 1, 65535))


# ---- the camera service's own depth correction ------------------------------------------------------------------

CORRECTED = {'factor': 0.889, 'measured': '2026-11-11', 'calibration_sha256': 'c' * 64, 'applied': True}


def with_metadata(data, change):
    """The bundle with its metadata_json changed, as the camera service writes it (a fresh .npy header)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, 'w') as target:
        for info in source.infolist():
            raw = source.read(info)
            if info.filename == 'metadata_json.npy':
                meta = json.loads(R.check._npy(raw, info.filename)[2].decode('utf-8'))
                change(meta)
                buffer = io.BytesIO()
                np.lib.format.write_array(buffer, np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8), allow_pickle=False)
                raw = buffer.getvalue()
            target.writestr(info, raw)
    return out.getvalue()


def small_take(changes):
    """A den-take-1 take of the marks take's first frames, frame i's metadata changed by changes[i]."""
    rows = marks_rows(len(changes))
    return write_take(scratch('den-live-source-') / 'take', rows,
                      [with_metadata((MARKS / row['file']).read_bytes(), change) for row, change in zip(rows, changes)])


@unittest.skipUnless((MARKS / 'frames.csv').is_file(), 'needs a prepared den-sim set')
class SourceCorrection(unittest.TestCase):
    corrected = staticmethod(lambda meta: meta.update(depth_correction=dict(CORRECTED)))
    off = staticmethod(lambda meta: meta.update(depth_correction={'applied': False, 'factor': 1.0,
                                                                  'reason': 'calibration differs from the one the factor was measured with'}))
    missing = staticmethod(lambda meta: meta.pop('depth_correction', None))

    def check(self, changes, require=1):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            found = rn.check_source(R, small_take(changes), {**LIVE, 'require_source_correction': require})
        return found, out.getvalue()

    def test_every_frame_corrected_goes_on(self):
        found, text = self.check([self.corrected] * 4)
        self.assertEqual((found['applied'], found['frames'], found['factors']), (4, 4, [0.889]))
        self.assertEqual(found['calibrations'], ['c' * 64])
        self.assertIn('Depth corrected at the camera: x0.889 on all 4 frames.', text)

    def test_one_frame_off_stops(self):
        with self.assertRaises(rn.Stop) as stop:
            self.check([self.corrected, self.corrected, self.off, self.corrected])
        self.assertIn('3 of 4 say applied', str(stop.exception))
        self.assertIn('applied is false: calibration differs', str(stop.exception))

    def test_frames_from_before_the_switch_stop(self):
        with self.assertRaises(rn.Stop) as stop:
            self.check([self.missing] * 3)
        self.assertIn('0 of 3 say applied', str(stop.exception))
        self.assertIn('no depth_correction record', str(stop.exception))

    def test_two_factors_stop(self):
        other = lambda meta: meta.update(depth_correction={**CORRECTED, 'factor': 0.9})
        with self.assertRaises(rn.Stop) as stop:
            self.check([self.corrected, other])
        self.assertIn('2 different factors', str(stop.exception))

    def test_not_required_only_says_so(self):
        found, text = self.check([self.missing] * 2, require=0)
        self.assertEqual(found['applied'], 0)
        self.assertIn('not required by this method file', text)


# ---- the depth check: the floor, the facing wall and the two tapes ---------------------------------------------------

HEIGHT_TAPE, WALL_TAPE = 1.37, 5.0   # room_points' own lens height and facing wall: synthetic, not the den's


def room_points(height, pitch_deg, seed=1, floor_n=6000, table_n=1500, wall_at=5.0):
    """Camera-frame points (+y down) of a floor `height` below the lens, a table top 0.45 m above the floor, and a
    wall wall_at ahead, square to the floor, with the camera pitched down."""
    rng = np.random.default_rng(seed)
    floor = np.column_stack((rng.uniform(-2, 2, floor_n), np.zeros(floor_n), rng.uniform(0.5, wall_at, floor_n)))
    table = np.column_stack((rng.uniform(-0.5, 0.5, table_n), np.full(table_n, 0.45), rng.uniform(1.5, 2.5, table_n)))
    wall = np.column_stack((rng.uniform(-2, 2, 3000), rng.uniform(0, 2.5, 3000), np.full(3000, wall_at)))
    world = np.vstack((floor, table, wall))   # world: +x right, +y up from the floor, +z ahead
    pitch = math.radians(pitch_deg)
    # camera axes in world terms, pitched down: forward tilts below the horizontal
    fwd = np.array([0, -math.sin(pitch), math.cos(pitch)])
    down = np.array([0, -math.cos(pitch), -math.sin(pitch)])
    right = np.array([1.0, 0, 0])
    rel = world - np.array([0, height, 0])
    cam = np.column_stack((rel @ right, rel @ down, rel @ fwd))
    return cam + rng.normal(0, 0.004, cam.shape)


TAPES = {'lens_height_m': HEIGHT_TAPE, 'facing_wall_m': WALL_TAPE}


class DepthCheck(unittest.TestCase):
    def test_true_depth_needs_no_scale(self):
        check = rn.scale_check(room_points(1.37, 11), TAPES, LIVE)
        self.assertEqual(check['result'], 'none')
        self.assertEqual(check['depth_scale'], 1.0)
        self.assertLess(abs(check['floor_pct']), 0.5)
        self.assertLess(abs(check['wall_pct']), 0.5)
        self.assertAlmostEqual(check['floor_wall_deg'], 90, delta=1)

    def test_three_percent_long_gives_a_scale(self):
        check = rn.scale_check(room_points(1.37, 11) * 1.03, TAPES, LIVE)
        self.assertEqual(check['result'], 'scale')
        self.assertAlmostEqual(check['floor_pct'], 3.0, delta=0.4)
        self.assertAlmostEqual(check['wall_pct'], 3.0, delta=0.4)
        self.assertAlmostEqual(check['depth_scale'], 0.971, delta=0.002)
        self.assertEqual(rn.depth_line(check), 'Depth check: floor %+.1f%%, wall %+.1f%%, bend %.1f points: scale x%g applied'
                         % (check['floor_pct'], check['wall_pct'], check['bend_pts'], check['depth_scale']))

    def test_one_percent_off_is_within_the_no_scale_band(self):
        check = rn.scale_check(room_points(1.37, 11) * 0.99, TAPES, LIVE)
        self.assertEqual((check['result'], check['depth_scale']), ('none', 1.0))

    def test_a_seven_point_bend_stops(self):
        """The floor reads 3.5% long and the facing wall 3.5% short: their mean is right, and no one scale fixes both."""
        points = room_points(1.37, 11)
        bent = {'lens_height_m': HEIGHT_TAPE / 1.035, 'facing_wall_m': WALL_TAPE / 0.965}
        check = rn.scale_check(points, bent, LIVE)
        self.assertEqual(check['result'], 'stop')
        self.assertAlmostEqual(check['bend_pts'], 7.0, delta=0.4)
        self.assertLess(abs(check['mean_pct']), 0.5, 'the mean alone would say no scale')
        self.assertIn('STOP: the depth bends more than a scale can fix', rn.depth_line(check))
        self.assertNotIn('depth_scale', check)

    def test_more_than_ten_percent_off_stops_rather_than_clamping(self):
        far = rn.scale_check(room_points(1.37, 11) * 1.15, TAPES, LIVE)
        self.assertEqual(far['result'], 'stop')   # 15% long: the wall isn't within 10% of its tape at all
        self.assertIn("can't find the facing wall within 10%", far['why'])
        # the floor 13.9% long and the wall 9% long: a bend under 5 points, but a mean past what den-live will scale
        check = rn.scale_check(room_points(1.37, 11), {'lens_height_m': HEIGHT_TAPE / 1.139, 'facing_wall_m': WALL_TAPE / 1.09}, LIVE)
        self.assertEqual(check['result'], 'stop')
        self.assertIn('more than den-live will scale', check['why'])

    def test_no_facing_wall_or_no_floor_says_so(self):
        points = room_points(1.37, 11)
        no_wall = points[points[:, 2] < 3.0]
        self.assertEqual(rn.scale_check(no_wall, TAPES, LIVE)['why'], "the depth check can't find the facing wall within 10% of your wall tape")
        wall = np.column_stack((np.linspace(-2, 2, 500), np.linspace(0.4, 2, 500), np.full(500, 4.0)))
        self.assertEqual(rn.scale_check(wall, TAPES, LIVE)['why'], "the depth check can't find the floor in the camera's view")
        self.assertEqual(rn.depth_line({'result': 'stop', 'why': 'x'}), 'Depth check: STOP: x.')

    def test_a_cabinet_front_with_more_support_doesnt_stand_in_for_the_wall(self):
        """A wardrobe front 0.3 m in front of the wall, wider and taller in view, out-supports it. The wall is still the
        farthest strong plane near the tape, so the check measures it, not the wardrobe."""
        world = np.random.default_rng(7)
        front = np.column_stack((world.uniform(-1.0, 1.0, 2400), world.uniform(0.3, 2.4, 2400), np.full(2400, 4.7)))
        pitch = math.radians(11)   # room_points' camera: 1.37 m up, pitched 11 degrees down, the wall 5.0 m ahead
        fwd, down = np.array([0, -math.sin(pitch), math.cos(pitch)]), np.array([0, -math.cos(pitch), -math.sin(pitch)])
        rel = front - np.array([0, 1.37, 0])
        cabinet = np.column_stack((rel[:, 0], rel @ down, rel @ fwd)) + world.normal(0, 0.004, rel.shape)
        plain = room_points(1.37, 11)
        behind = np.zeros(len(plain), bool)
        behind[-3000:] = np.abs(plain[-3000:, 0]) < 1.0 * 5.0 / 4.7   # room_points' last 3000 are the wall: the front hides these
        scene = np.vstack((plain[~behind], cabinet))
        floor = rn.floor_plane(scene, LIVE)
        wall_left = 3000 - int(behind.sum())
        self.assertGreater(len(cabinet), wall_left, 'the wardrobe out-supports the wall')
        self.assertGreaterEqual(wall_left, len(cabinet) / 2, 'and the wall is still strong')
        wall = rn.wall_plane(scene, floor, WALL_TAPE, LIVE)
        self.assertAlmostEqual(wall['distance_m'], WALL_TAPE, delta=0.03)
        check = rn.scale_check(scene, TAPES, LIVE)
        self.assertEqual(check['result'], 'none')
        self.assertLess(abs(check['wall_pct']), 0.6)
        # the control: with the wall out of the band, the wardrobe is what's left, 6% short of the tape
        self.assertAlmostEqual(rn.wall_plane(scene, floor, 4.7, {**LIVE, 'wall_tape_band': 0.05})['distance_m'], 4.7, delta=0.03)


class Floor(unittest.TestCase):
    def test_height_and_pitch(self):
        plane = rn.floor_plane(room_points(1.37, 11), LIVE)
        self.assertAlmostEqual(plane['height_m'], 1.37, delta=0.01)
        self.assertAlmostEqual(plane['pitch_down_deg'], 11, delta=0.5)
        self.assertAlmostEqual(plane['roll_deg'], 0, delta=0.5)

    def test_a_table_with_more_points_than_the_floor_still_loses_to_it(self):
        """A table out-supports the floor, the floor is still strong, the lower wins. Best support alone would measure
        the table, 0.92 m below the lens."""
        plane = rn.floor_plane(room_points(1.37, 11, floor_n=3000, table_n=6000), LIVE)
        self.assertAlmostEqual(plane['height_m'], 1.37, delta=0.01)

    def test_no_level_plane(self):
        wall = np.column_stack((np.linspace(-2, 2, 500), np.linspace(0.4, 2, 500), np.full(500, 4.0)))
        self.assertIsNone(rn.floor_plane(wall, LIVE))


class Tapes(unittest.TestCase):
    def ask(self, *answers):
        typed = iter(answers)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tapes = rn.ask_tapes(lambda prompt: next(typed))
        return tapes, out.getvalue()

    def test_two_tapes_in_metres_never_echoed(self):
        tapes, text = self.ask('1,423', '4.187')
        self.assertEqual(tapes, {'lens_height_m': 1.423, 'facing_wall_m': 4.187})
        self.assertNotIn('1.423', text)
        self.assertNotIn('4.187', text)

    def test_a_tape_that_isnt_metres_is_asked_again_then_stops(self):
        tapes, text = self.ask('137', 'one', '1.4', '4.2')
        self.assertEqual(tapes['lens_height_m'], 1.4)
        self.assertEqual(text.count("That isn't the lens's height"), 2)
        with self.assertRaises(rn.Stop):
            self.ask('x', 'y', 'z')


# ---- the edge guard ----------------------------------------------------------------------------------------------

def floor_plane_depth(height=1.4, fy=400.0, cy=200.0):
    """A level camera's depth of an endless floor `height` below it: z = fy height / (v - cy) below the horizon."""
    v = np.arange(rn.HEIGHT, dtype=np.float64)[:, None] * np.ones((1, rn.WIDTH))
    with np.errstate(divide='ignore'):
        z = np.where(v > cy + 5, fy * height / (v - cy), 0.0)
    return z


class EdgeGuard(unittest.TestCase):
    def test_a_slanted_floor_and_an_inside_corner_are_clear(self):
        z = floor_plane_depth()
        v = 200 + round(400 * 1.4 / 3.0)   # the floor 3 m away: about 1.6 cm of depth a pixel
        spread, depth = rn.window_spread(z, 320, v, 3)
        self.assertAlmostEqual(depth, 3.0, delta=0.05)
        self.assertLess(spread, 0.01)
        raw = np.percentile(z[v - 3:v + 4, 317:324], 90) - np.percentile(z[v - 3:v + 4, 317:324], 10)
        self.assertGreater(raw, max(0.04, 0.02 * 3.0), 'the control: the raw spread would name a good floor click')
        corner = z.copy()
        corner[:v, :] = 3.0   # a wall rising from the floor at 3 m: an inside corner, continuous depth
        self.assertLess(rn.window_spread(corner, 320, v, 3)[0], max(0.04, 0.02 * 3.0))

    def test_an_outer_edge_is_named(self):
        z = np.full((rn.HEIGHT, rn.WIDTH), 3.2)
        z[:, :320] = 2.6   # a door frame's edge with the wall 60 cm behind it
        spread, depth = rn.window_spread(z, 320, 200, 3)
        self.assertGreater(spread, max(0.04, 0.02 * depth))
        self.assertEqual(rn.edge_line(['f3']), 'Edge guard: f3 sits on a depth edge: click a little inside one surface.')
        self.assertEqual(rn.edge_line(['f3', 'c2']), 'Edge guard: f3 and c2 sit on a depth edge: click a little inside one surface.')
        self.assertEqual(rn.edge_line([]), 'Edge guard: all clear.')

    def test_too_little_depth_says_nothing(self):
        self.assertIsNone(rn.window_spread(np.zeros((rn.HEIGHT, rn.WIDTH)), 5, 5, 3))


# ---- the distance check -------------------------------------------------------------------------------------------

def nine_pairs(seed=4, noise=0.0, scale=1.0):
    """Nine features: camera points and the same points in a room frame, turned and moved, depth scaled by scale."""
    rng = np.random.default_rng(seed)
    cam = rng.uniform([-1.5, -1.0, 1.5], [1.5, 1.2, 4.5], (9, 3))
    a = math.radians(40)
    rot = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    room = cam @ rot.T + [2.0, -1.0, 1.3] + rng.normal(0, noise, (9, 3))
    return {pid: (list(c * scale), list(r)) for pid, c, r in zip(rn.IDS, cam, room)}


class DistanceCheck(unittest.TestCase):
    def test_nine_features_that_agree(self):
        found = rn.distance_check(nine_pairs(noise=0.01), LIVE, GATE)
        self.assertEqual((found['features'], found['pairs'], found['named']), (9, 36, []))
        self.assertLess(found['pair_median_m'], 0.03)
        self.assertEqual(rn.distance_line(found, LIVE), 'Distance check: all nine agree within 6 cm.')

    def test_two_swapped_scan_picks_are_named_worst_first(self):
        pairs = nine_pairs()
        pairs['f2'], pairs['f5'] = (pairs['f2'][0], pairs['f5'][1]), (pairs['f5'][0], pairs['f2'][1])
        found = rn.distance_check(pairs, LIVE, GATE)
        self.assertEqual(sorted(found['named']), ['f2', 'f5'])
        self.assertGreaterEqual(found['median_m'][found['named'][0]], found['median_m'][found['named'][1]])
        line = rn.distance_line(found, LIVE)
        self.assertRegex(line, r'^Distance check: f[25] disagrees with \d of 8 others \(median \d+ cm\); f[25] disagrees '
                               r'with \d of 8 others \(median \d+ cm\): re-pick f[25] and f[25] or re-click them, then '
                               r'paste with --again\.$')

    def test_one_wrong_feature_leaves_the_others_clear(self):
        pairs = nine_pairs()
        pairs['c1'] = (pairs['c1'][0], [v + 0.5 for v in pairs['c1'][1]])
        self.assertEqual(rn.distance_check(pairs, LIVE, GATE)['named'], ['c1'])

    def test_a_wrong_depth_scale_names_most_and_the_features_own_scale_says_why(self):
        pairs = nine_pairs(scale=0.83)
        found = rn.distance_check(pairs, LIVE, GATE)
        self.assertGreater(len(found['named']), 4)
        own = rn.own_scale([{'id': pid, 'camera_xyz_m': c, 'room_xyz_m': r} for pid, (c, r) in pairs.items()])
        self.assertTrue(rn.depth_scale_to_blame(own, LIVE, 0.15))

    def test_a_far_feature_10_cm_long_is_noted_not_stopped(self):
        """The farthest feature's depth 10 cm long along its own ray: inside the owner's 15 cm gate, so a note in
        centimetres, and the paste goes on."""
        pairs = nine_pairs()
        far = max(pairs, key=lambda pid: np.linalg.norm(pairs[pid][0]))
        camera = np.array(pairs[far][0])
        pairs[far] = (list(camera * (1 + 0.10 / np.linalg.norm(camera))), pairs[far][1])
        found = rn.distance_check(pairs, LIVE, GATE)
        self.assertEqual((found['named'], found['noted']), ([], [far]))
        self.assertGreater(found['median_m'][far], LIVE['distance_max_m'])
        self.assertEqual(rn.distance_line(found, LIVE), 'Distance check: all nine agree within 15 cm. A note, no stop: %s is %.0f '
                         'cm off the others (median over its 8 pairs).' % (far, found['median_m'][far] * 100))

    def test_errors_the_owner_allows_rarely_stop_the_paste(self):
        """The review's cases on 200 synthetic layouts each: one far feature 10 cm long, or a +-2% range bend with
        perfect clicks. At a 6 cm stop most of them stopped; at the 15 cm gate none does."""
        stops = {'far': 0, 'bend': 0}
        for seed in range(200):
            pairs = nine_pairs(seed=seed)
            far = max(pairs, key=lambda pid: np.linalg.norm(pairs[pid][0]))
            camera = np.array(pairs[far][0])
            long = dict(pairs, **{far: (list(camera * (1 + 0.10 / np.linalg.norm(camera))), pairs[far][1])})
            stops['far'] += bool(rn.distance_check(long, LIVE, GATE)['named'])
            ranges = {pid: np.linalg.norm(c) for pid, (c, _) in pairs.items()}
            near, deep = min(ranges.values()), max(ranges.values())
            bent = {pid: (list(np.array(c) * (1 - 0.02 + 0.04 * (ranges[pid] - near) / (deep - near))), r)
                    for pid, (c, r) in pairs.items()}
            stops['bend'] += bool(rn.distance_check(bent, LIVE, GATE)['named'])
        self.assertEqual(stops, {'far': 0, 'bend': 0})


# ---- the held-out check -----------------------------------------------------------------------------------------

K = {'fx': 400.0, 'fy': 400.0, 'cx': 320.0, 'cy': 200.0}
BOX = (0.0, 4.0, 0.0, 5.0, 2.6)   # a synthetic room: x 0-4 m, y 0-5 m, floor z = 0, ceiling 2.6 m


def look_pose(position, yaw_deg=0.0, pitch_deg=15.0):
    """room_from_camera for a camera at position looking along +y (turned yaw_deg), pitched down."""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    forward = np.array([math.sin(yaw) * math.cos(pitch), math.cos(yaw) * math.cos(pitch), -math.sin(pitch)])
    right = np.array([math.cos(yaw), -math.sin(yaw), 0.0])
    down = np.cross(forward, right)
    M = np.eye(4)
    M[:3, :3] = np.column_stack((right, down, forward))
    M[:3, 3] = position
    return M


def box_points(step=0.02, table=None):
    """Points on the box's six faces every `step` m, and a table top (x0, x1, y0, y1, z) if given, solid to the floor."""
    x0, x1, y0, y1, top = BOX
    xs, ys, zs = np.arange(x0, x1, step), np.arange(y0, y1, step), np.arange(0, top, step)
    faces = []
    gx, gy = np.meshgrid(xs, ys)
    faces += [np.column_stack((gx.ravel(), gy.ravel(), np.full(gx.size, z))) for z in (0.0, top)]
    gx, gz = np.meshgrid(xs, zs)
    faces += [np.column_stack((gx.ravel(), np.full(gx.size, y), gz.ravel())) for y in (y0, y1)]
    gy, gz = np.meshgrid(ys, zs)
    faces += [np.column_stack((np.full(gy.size, x), gy.ravel(), gz.ravel())) for x in (x0, x1)]
    if table:
        tx0, tx1, ty0, ty1, tz = table
        gx, gy = np.meshgrid(np.arange(tx0, tx1, step / 2), np.arange(ty0, ty1, step / 2))
        faces.append(np.column_stack((gx.ravel(), gy.ravel(), np.full(gx.size, tz))))
        gx, gz = np.meshgrid(np.arange(tx0, tx1, step / 2), np.arange(0, tz, step / 2))
        faces.append(np.column_stack((gx.ravel(), np.full(gx.size, ty0), gz.ravel())))
    return np.vstack(faces)


def box_depth(pose, table=None):
    """The camera's depth (z along its axis, metres) of the box, ray-cast: the camera's own view, independent of the
    scan's points."""
    v, u = np.mgrid[0:rn.HEIGHT, 0:rn.WIDTH]
    rays = np.stack(((u - K['cx']) / K['fx'], (v - K['cy']) / K['fy'], np.ones(u.shape)), -1)   # z = 1 in the camera
    world = rays @ pose[:3, :3].T
    origin = pose[:3, 3]
    x0, x1, y0, y1, top = BOX
    best = np.full(u.shape, np.inf)
    planes = [(0, x0), (0, x1), (1, y0), (1, y1), (2, 0.0), (2, top)]
    boxes = [((x0, x1), (y0, y1), (0.0, top))]
    if table:
        tx0, tx1, ty0, ty1, tz = table
        planes += [(2, tz, (tx0, tx1), (ty0, ty1)), (1, ty0, (tx0, tx1), (0, tz))]
    for item in planes:
        axis, at = item[0], item[1]
        with np.errstate(divide='ignore', invalid='ignore'):
            t = (at - origin[axis]) / world[..., axis]
        hit = origin + world * t[..., None]
        ok = t > 0.05
        others = [a for a in range(3) if a != axis]
        if len(item) == 2:
            (lo0, hi0), (lo1, hi1) = [boxes[0][a] for a in others]
        else:
            (lo0, hi0), (lo1, hi1) = item[2], item[3]
        ok &= (hit[..., others[0]] >= lo0 - 1e-6) & (hit[..., others[0]] <= hi0 + 1e-6)
        ok &= (hit[..., others[1]] >= lo1 - 1e-6) & (hit[..., others[1]] <= hi1 + 1e-6)
        best = np.where(ok & (t < best), t, best)
    return np.where(np.isfinite(best), best, 0.0)


class HeldOut(unittest.TestCase):
    pose = look_pose([2.0, 0.3, 1.4], yaw_deg=5)

    def check(self, measured_pose, scan_pose=None, table=None, measured=None):
        expected, stable = rn.expected_depth(box_points(table=table), scan_pose if scan_pose is not None else measured_pose, K, LIVE)
        return rn.held_out_numbers(expected, stable, box_depth(measured_pose, table) if measured is None else measured, LIVE)

    def test_the_true_pose_passes(self):
        found = self.check(self.pose)
        self.assertTrue(found['ok'], found)
        self.assertGreater(found['within_noise'], 0.95)
        self.assertLess(abs(found['worst_bin_m']), 0.03)
        self.assertGreater(found['pixels'], 20000)
        self.assertEqual(rn.held_line(found), 'Held-out take: %.0f%% of floor and upper-wall pixels within noise; worst '
                                              'range bin %+.1f cm.' % (found['within_noise'] * 100, found['worst_bin_m'] * 100))

    def test_a_shifted_pose_is_not_qualified(self):
        shifted = self.pose.copy()
        shifted[:3, 3] += [0.0, 0.15, 0.10]   # the registration 15 cm forward and 10 cm up of where the camera is
        found = self.check(self.pose, scan_pose=shifted)
        self.assertFalse(found['ok'])
        self.assertIsNotNone(found['why'])
        self.assertGreater(abs(found['worst_bin_m']), 0.10)

    def test_furniture_hides_the_floor_behind_it(self):
        """A table in the room and in the scan: the floor behind it isn't compared with the table's depth."""
        table = (1.2, 2.8, 2.0, 2.8, 0.75)
        found = self.check(self.pose, table=table)
        self.assertTrue(found['ok'], found)
        self.assertGreater(found['within_noise'], 0.95)

    def test_a_scale_off_by_five_percent_shows_in_the_far_bins(self):
        found = self.check(self.pose, measured=box_depth(self.pose) * 1.05)
        self.assertFalse(found['ok'])
        self.assertGreater(found['worst_bin_m'], 0.10)

    def test_too_few_stable_pixels_cant_pass(self):
        found = self.check(self.pose, measured=np.zeros((rn.HEIGHT, rn.WIDTH)))
        self.assertEqual((found['ok'], found['within_noise']), (False, None))
        self.assertIn('floor and upper-wall pixels to compare', found['why'])


def ply_bytes(points):
    """points as Home's points.ply: binary little-endian x, y, z float32 and an intensity byte."""
    header = ('ply\nformat binary_little_endian 1.0\ncomment homeapt-points v1 zup metric\nelement vertex %d\n'
              'property float x\nproperty float y\nproperty float z\nproperty uchar intensity\nend_header\n' % len(points))
    body = np.zeros(len(points), dtype=[('x', '<f4'), ('y', '<f4'), ('z', '<f4'), ('i', 'u1')])
    body['x'], body['y'], body['z'], body['i'] = points[:, 0], points[:, 1], points[:, 2], 7
    return header.encode('ascii') + body.tobytes()


def write_packs(folder, revision, ply, site=SITE, tamper=False):
    """A content packs folder with one active pack holding points.ply, as Home's packs lay it out."""
    pack = Path(folder) / site / revision
    pack.mkdir(parents=True, exist_ok=True)
    (pack / 'points.ply').write_bytes(ply + (b'x' if tamper else b''))
    (pack / 'content-pack.json').write_text(json.dumps({'assets': {'points.ply': {'path': 'points.ply', 'bytes': len(ply),
                                                                                   'sha256': rn.sha256_bytes(ply)}}}), encoding='utf-8')
    (Path(folder) / site / 'ACTIVE').write_text(revision + '\n', encoding='utf-8')
    return Path(folder)


class ScanPoints(unittest.TestCase):
    def test_homes_points_ply_reads(self):
        points = box_points(step=0.5)
        np.testing.assert_allclose(rn.read_ply(ply_bytes(points)), points.astype(np.float32), atol=1e-6)
        with self.assertRaises(ValueError):
            rn.read_ply(b'ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nend_header\n0\n')

    def test_the_active_pack_must_be_the_picks_revision_and_its_own_file(self):
        ply = ply_bytes(box_points(step=0.5))
        packs = write_packs(scratch('den-live-packs-'), 'rev-2', ply)
        points, why = rn.scan_points(packs, SITE, 'rev-2')
        self.assertIsNone(why)
        self.assertEqual(points.shape[1], 3)
        self.assertEqual(rn.scan_points(packs, SITE, 'rev-1'), (None, 'the active scan is rev-2, but the picks were made on rev-1'))
        tampered = write_packs(scratch('den-live-packs-'), 'rev-2', ply, tamper=True)
        self.assertEqual(rn.scan_points(tampered, SITE, 'rev-2')[1], "the scan's points.ply doesn't match its pack's manifest")
        self.assertEqual(rn.scan_points(scratch('den-live-packs-'), SITE, 'rev-2')[1],
                         "the scan's active content pack has no readable points.ply")


# ---- the summary block, exactly in step 02-2's format ----------------------------------------------------------------

class Summary(unittest.TestCase):
    def receipt(self, **changes):
        receipt = {
            'method': {'file': 'REGISTER-LIVE-20261113.md'}, 'scan_revision': 'local-2026-10-15-den-view-r1-v4',
            'source_correction': {'frames': 98, 'applied': 98, 'factors': [0.889]},
            'scale_check': {'result': 'scale', 'floor_pct': 3.21, 'wall_pct': 2.44, 'bend_pts': 0.77, 'depth_scale': 0.972},
            'clicked': list(rn.IDS), 'edge_guard': {'named': [], 'reclicked': ['f3']},
            'distance_check': {'features': 9, 'named': [], 'pair_median_m': 0.012},
            'fit': {'result': 'registered', 'transform_id': 'den-oak@2026-11-13.1',
                    'numbers': {'n_fit': 6, 'n_check': 3, 'residual_min_m': 0.004, 'residual_max_m': 0.031,
                                'loo_max_m': 0.052, 'checks_m': {'c1': 0.021, 'c2': 0.034, 'c3': 0.018}, 'error_bound_m': 0.052}},
            'held_out': {'within_noise': 0.912, 'worst_bin_m': -0.043}, 'qualified': True}
        receipt.update(changes)
        return receipt

    def test_a_qualified_block(self):
        m, live = rn.load_method(R)
        self.assertEqual(rn.summary_lines(self.receipt(), m, live), [
            'Method REGISTER-LIVE-20261113, scan local-2026-10-15-den-view-r1-v4.',
            'Depth corrected at the camera: x0.889 on all 98 frames.',
            'Depth check: floor +3.2%, wall +2.4%, bend 0.8 points; den-live scale x0.972.',
            'Clicks: 9 of 9 with depth. Edge guard: re-clicked f3.',
            'Distance check: 9 agree; median pair difference 1.2 cm.',
            'Fit: 6 fit + 3 check. Residuals 0.4-3.1 cm. Leave-one-out worst 5.2 cm. Sofa checks 2.1, 3.4, 1.8 cm.',
            'REGISTERED den-oak@2026-11-13.1: gate 15 cm, error bound 5.2 cm.',
            'Held-out take: 91% of floor and upper-wall pixels within noise; worst range bin -4.3 cm.',
            'QUALIFIED: yes'])

    def test_no_scale_and_a_reason_not_to_qualify(self):
        m, live = rn.load_method(R)
        receipt = self.receipt(qualified=False, why_not='sofa check c2 is 12.0 cm, over 10 cm', held_out=None,
                               scale_check={'result': 'none', 'floor_pct': -0.4, 'wall_pct': 0.6, 'bend_pts': 1.0, 'depth_scale': 1.0})
        lines = rn.summary_lines(receipt, m, live)
        self.assertIn('Depth check: floor -0.4%, wall +0.6%, bend 1.0 points; den-live scale none.', lines)
        self.assertEqual(lines[-1], 'QUALIFIED: no, because sofa check c2 is 12.0 cm, over 10 cm.')
        self.assertFalse(any(line.startswith('Held-out') for line in lines))


# ---- what reaches the console from den-register --------------------------------------------------------------------

class Privacy(unittest.TestCase):
    def test_a_den_register_line_with_the_cameras_place_never_reaches_the_console(self):
        """Planted lines as den-register's run_fit prints them (print(..., file=out)): a refusal that puts the camera
        42 cm below the floor, the mount check's reach, the camera's place and a click's pixel. None of their numbers
        is passed on; the residuals and the reason codes are."""
        planted = {'camera_below_floor': 'the fit puts the camera -42.0 cm from the floor; check that x, y and z follow '
                                         'the room frame (right-handed, z up)',
                   'depth_missing': 'f3: 10 of 49 pixels near (123.4, 200.1) have depth'}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            lines = rn.Lines()
            print('REFUSED', file=lines)
            for reason, detail in planted.items():
                print(f'  {reason}: {detail}', file=lines)
            print('  fit   f1       residual 3.0 cm, leave-one-out 4.1 cm', file=lines)
            print('  camera at x 1.234, y -2.345, z 1.402 m in the room frame', file=lines)
            print('  But the mount check could not confirm it: the farthest of these marks is 2.87 m from the camera, '
                  'and it needs 3 m, because a turn shows most far away. Click or add a cross further off and fit again.', file=lines)
            print('Next: python -B tools\\den-register\\register.py fit x --take y', file=lines)
        text = out.getvalue()
        for number in ('42', '123.4', '200.1', '1.234', '2.345', '1.402', '2.87'):
            self.assertNotIn(number, text)
        self.assertIn('  camera_below_floor: the fit puts the camera (withheld) from the floor;', text)
        self.assertIn('  depth_missing: f3: 10 of 49 pixels near (the click) have depth', text)
        self.assertIn('  fit   f1       residual 3.0 cm, leave-one-out 4.1 cm', text, 'errors in centimetres pass')
        self.assertNotIn('camera at', text)
        self.assertNotIn('Next:', text)

    def test_a_den_register_refusal_outside_the_fit_is_scrubbed_too(self):
        self.assertEqual(rn.scrub('click_outside_frame: (700.5, -3.0) is outside the 640x400 frame'),
                         'click_outside_frame: (the click) is outside the 640x400 frame')
        self.assertIsNone(rn.scrub('  camera at x 1.0, y 2.0, z 3.0 m in the room frame'))


# ---- the features' own scale -----------------------------------------------------------------------------------------

class OwnScale(unittest.TestCase):
    def pairs(self, scale, noise=0.0):
        rng = np.random.default_rng(3)
        cam = rng.uniform(-2, 2, (6, 3)) + [0, 0, 3]
        a = math.radians(30)
        rot = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
        room = scale * cam @ rot.T + [1, 2, 0.5] + rng.normal(0, noise, (6, 3))
        return [{'id': 'p%d' % i, 'camera_xyz_m': list(c), 'room_xyz_m': list(r)} for i, (c, r) in enumerate(zip(cam, room))]

    def test_recovers_the_scale(self):
        self.assertAlmostEqual(rn.own_scale(self.pairs(1.0))['scale'], 1.0, places=6)
        found = rn.own_scale(self.pairs(1 / 0.83))
        self.assertAlmostEqual(found['scale'], 1 / 0.83, places=6)
        self.assertLess(found['worst_m'], 1e-6)
        self.assertAlmostEqual(rn.own_scale(self.pairs(1.0, noise=0.03))['scale'], 1.0, delta=0.05)

    def test_blames_the_scale_only_when_every_feature_fits_with_it(self):
        scaled = rn.own_scale(self.pairs(1 / 0.83))
        self.assertTrue(rn.depth_scale_to_blame(scaled, LIVE, 0.10))
        one_off = self.pairs(1.0)
        one_off[0]['room_xyz_m'] = [v + (0.8 if i == 0 else 0) for i, v in enumerate(one_off[0]['room_xyz_m'])]
        found = rn.own_scale(one_off)
        self.assertEqual(found['worst_id'], one_off[0]['id'])
        self.assertFalse(rn.depth_scale_to_blame(found, LIVE, 0.10))
        self.assertFalse(rn.depth_scale_to_blame(rn.own_scale(self.pairs(1.0)), LIVE, 0.10))

    def test_too_few_or_on_a_line(self):
        self.assertIsNone(rn.own_scale(self.pairs(1.0)[:2]))
        line = [{'id': str(t), 'camera_xyz_m': [t, 0, 3], 'room_xyz_m': [t, 1, 0]} for t in range(5)]
        self.assertIsNone(rn.own_scale(line))


# ---- the worksheet ------------------------------------------------------------------------------------------------

class Worksheet(unittest.TestCase):
    def test_den_register_reads_it_with_scan_pick_labelled_in_memory(self):
        work = scratch('den-live-work-')
        path = rn.ensure_worksheet(R, work)
        sheet = R.worksheet.load(path, need_positions=False)
        self.assertEqual([p['id'] for p in sheet['points']], list(rn.IDS))
        self.assertEqual([p['role'] for p in sheet['points']], ['fit'] * 6 + ['check'] * 3)
        self.assertEqual({p['measured_with'] for p in sheet['points']}, {'scan-pick'})
        self.assertEqual(R.register.METHOD, 'scan-pick + fit')
        # the control: without the in-memory label, den-register refuses scan-pick
        saved = R.worksheet.TOOLS
        R.worksheet.TOOLS = ('tape', 'laser')
        try:
            with self.assertRaises(R.scene.Refused):
                R.worksheet.load(path, need_positions=False)
        finally:
            R.worksheet.TOOLS = saved

    def test_a_worksheet_already_there_is_kept_and_a_six_point_one_replaced(self):
        work = scratch('den-live-work-')
        path = rn.ensure_worksheet(R, work)
        path.write_text(path.read_text(encoding='utf-8').replace('away from c1 and c2', 'kept'), encoding='utf-8')
        rn.ensure_worksheet(R, work)
        self.assertIn('kept', path.read_text(encoding='utf-8'))
        sheet = json.loads(path.read_text(encoding='utf-8'))
        sheet['points'] = sheet['points'][:6]   # registration v1's worksheet
        path.write_text(json.dumps(sheet), encoding='utf-8')
        rn.ensure_worksheet(R, work)
        self.assertEqual(len(json.loads(path.read_text(encoding='utf-8'))['points']), 9)


# ---- the scan page's harness ----------------------------------------------------------------------------------------

class Page:
    """scan_pick.mjs on the authored room, in its own process."""

    def __init__(self, work, take, mode):
        self.port = free_port()
        self.origin = 'http://127.0.0.1:%d' % self.port
        self.proc = subprocess.Popen([NODE, str(rn.PICK_PAGE), '--work', str(work), '--take', str(take), '--mode', mode,
                                      '--room', 'authored', '--tmp', str(TMP), '--port', str(self.port)],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8')
        line = self.proc.stdout.readline()
        assert line.startswith('ready '), line
        page = self.get('/?apartmentRedesign=1&worldZoom=off')[1].decode('utf-8')
        self.token = re.search(r'__scanPick=\{token:"([0-9a-f]+)"\}', page).group(1)

    def get(self, path, host=None):
        request = urllib.request.Request(self.origin + path, headers={'Host': host} if host else {})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def post(self, path, body, origin=None, token=None):
        request = urllib.request.Request(self.origin + path, data=json.dumps(body).encode(), method='POST', headers={
            'Content-Type': 'application/json', 'Origin': origin or self.origin, 'X-Scan-Pick': self.token if token is None else token})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b'{}')

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        out = self.proc.stdout.read() if not self.proc.stdout.closed else ''
        self.proc.stdout.close()
        self.proc.wait(10)
        return out


REF = {'asset_name': 'mesh.glb', 'asset_sha256': 'a' * 64}


def picks(points, **override):
    return {pid: {'point_m': override.get(pid, [1.0 + i, 0.5, 0.0]), 'surface_ref': REF} for i, (pid, _, _) in enumerate(points)}


@unittest.skipUnless(NODE, 'needs Node')
class ScanPage(unittest.TestCase):
    def setUp(self):
        self.work = scratch('den-live-pick-')
        self.take = self.work / 'takes' / 'T-scaled'
        self.take.mkdir(parents=True)
        (self.take / 'take.json').write_text('{}', encoding='utf-8')
        rn.ensure_worksheet(R, self.work)
        self.page = Page(self.work, self.take, 'pick')

    def tearDown(self):
        self.page.close()

    def sheet(self):
        return json.loads((self.work / 'worksheet.json').read_text(encoding='utf-8'))

    def test_state_lists_the_nine_points_unpicked(self):
        status, body = self.page.get('/__pick/state')
        state = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual([p['id'] for p in state['points']], list(rn.IDS))
        self.assertFalse(any(p['picked'] for p in state['points']))
        self.assertNotIn('token', state)
        self.assertEqual(sorted(state['binding']), sorted(['content_revision', 'registration_version', 'manifest_digest',
                                                            'frame_digest', 'canonical_collision_digest']))

    def test_refusals(self):
        self.assertEqual(self.page.get('/__pick/state', host='localhost:%d' % self.page.port)[0], 403)
        self.assertEqual(self.page.get('/elsewhere')[0], 503)
        self.assertEqual(self.page.post('/__pick/save', {'picks': picks(rn.POINTS)}, origin='http://evil.example')[0], 403)
        self.assertEqual(self.page.post('/__pick/save', {'picks': picks(rn.POINTS)}, token='0' * 36)[0], 403)
        self.assertEqual(self.page.post('/__pick/done', {})[0], 409)
        one_short = picks(rn.POINTS)
        del one_short['c2']
        for body, error in (({'picks': one_short}, 'not_picked:c2'),
                            ({'picks': {**picks(rn.POINTS), 'zz': {'point_m': [0, 0, 0], 'surface_ref': REF}}}, 'not_a_point:zz'),
                            ({'picks': picks(rn.POINTS, f1=[0, 0, float('1e9')])}, 'bad_point:f1'),
                            ({'picks': {**picks(rn.POINTS), 'f2': {'point_m': [0, 0, 0], 'surface_ref': {'asset_name': 'x'}}}}, 'bad_surface:f2'),
                            ({}, 'picks_missing')):
            self.assertEqual(self.page.post('/__pick/save', body), (422, {'ok': False, 'error': error}))
        self.assertFalse(any(p['room_xyz_m'] for p in self.sheet()['points']), 'a refused save writes nothing')

    def test_save_writes_scan_picks_and_exits(self):
        status, body = self.page.post('/__pick/save', {'picks': picks(rn.POINTS, f1=[1.23456789, 2, 0])})
        self.assertEqual((status, body), (200, {'ok': True, 'saved': 9}))
        self.assertEqual(self.page.proc.wait(10), 0)
        sheet = self.sheet()
        f1 = sheet['points'][0]
        self.assertEqual(f1['room_xyz_m'], [1.234568, 2, 0])
        self.assertEqual(f1['measured_with'], 'scan-pick')
        self.assertTrue(f1['note'].startswith('scan-pick on mesh.glb aaaaaaaaaaaa'))
        self.assertRegex(sheet['measured_on'], r'^\d{4}-\d{2}-\d{2}$')
        R.worksheet.load(self.work / 'worksheet.json')   # den-register reads it, positions and all
        self.assertNotIn('1.23', self.page.close(), 'the harness prints no coordinate')

    def test_a_second_save_may_keep_earlier_picks(self):
        self.page.post('/__pick/save', {'picks': picks(rn.POINTS)})
        self.page.close()
        self.page = Page(self.work, self.take, 'pick')
        state = json.loads(self.page.get('/__pick/state')[1])
        self.assertTrue(all(p['picked'] for p in state['points']))
        self.assertEqual(self.page.post('/__pick/save', {'picks': {'c1': {'point_m': [9, 9, 0], 'surface_ref': REF}}})[0], 200)
        sheet = self.sheet()
        self.assertEqual(sheet['points'][rn.IDS.index('c1')]['room_xyz_m'], [9, 9, 0])
        self.assertEqual(sheet['points'][0]['room_xyz_m'], [1.0, 0.5, 0.0])

    def test_a_pick_from_another_scan_revision_is_no_pick(self):
        self.page.post('/__pick/save', {'picks': picks(rn.POINTS)})
        self.page.close()
        sheet = self.sheet()
        sheet['points'][0]['note'] = sheet['points'][0]['note'].rsplit('content revision ', 1)[0] + 'content revision older-r0'
        (self.work / 'worksheet.json').write_text(json.dumps(sheet), encoding='utf-8')
        self.page = Page(self.work, self.take, 'pick')
        state = json.loads(self.page.get('/__pick/state')[1])
        self.assertEqual([p['picked'] for p in state['points']], [False] + [True] * 8)
        self.assertEqual(self.page.post('/__pick/save', {'picks': {}}), (422, {'ok': False, 'error': 'not_picked:f1'}))


@unittest.skipUnless(NODE, 'needs Node')
class ScanPageFolder(unittest.TestCase):
    def test_a_folder_inside_any_git_checkout_is_refused(self):
        checkout = scratch('den-live-checkout-')
        (checkout / '.git').mkdir()
        work = checkout / 'deep' / 'register'
        work.mkdir(parents=True)
        rn.ensure_worksheet(R, work)
        (work / 'take').mkdir()
        (work / 'take' / 'take.json').write_text('{}', encoding='utf-8')
        done = subprocess.run([NODE, str(rn.PICK_PAGE), '--work', str(work), '--take', str(work / 'take'), '--room', 'authored',
                               '--port', str(free_port())], capture_output=True, encoding='utf-8', timeout=60)
        self.assertEqual(done.returncode, 2)
        self.assertIn('outside every git checkout', done.stdout)


# ---- end to end, on the rendered marks take -------------------------------------------------------------------------

FAST = rn.Timing(lead_s=0.2, window_s=4.0, poll_s=0.2)
TARGETS = {'f1': (0.2, 0.85), 'f2': (0.8, 0.85), 'f3': (0.2, 0.5), 'f4': (0.8, 0.5), 'f5': (0.2, 0.15), 'f6': (0.8, 0.15),
           'c1': (0.35, 0.65), 'c2': (0.5, 0.7), 'c3': (0.65, 0.65)}   # where in the picture each feature is looked for


def set_pose():
    """The set's own registration of its invented camera: room_from_camera, 4 x 4. The renders' truth, near enough."""
    revision = json.loads((FIT / 'room-model.jsonl').read_text(encoding='utf-8').strip().splitlines()[-1])
    return np.array(revision['cameras'][0]['pose']['room_from_camera'], dtype=np.float64)


def render_features(plane, k, pose):
    """Nine features on the render's depth: for each, the pixel nearest its target whose window is flat (well clear of
    the edge guard), and its room position through the set's registration, from the depth den-register will read."""
    depth = np.rint(plane) / 1000
    out = {}
    for pid, (fu, fv) in TARGETS.items():
        cu, cv = int(fu * rn.WIDTH), int(fv * rn.HEIGHT)
        best = None
        for radius in range(0, 60, 2):
            for v in range(cv - radius, cv + radius + 1, 2):
                for u in range(cu - radius, cu + radius + 1, 2):
                    if max(abs(u - cu), abs(v - cv)) != radius or not (4 <= u < rn.WIDTH - 4 and 4 <= v < rn.HEIGHT - 4):
                        continue
                    window = depth[v - 3:v + 4, u - 3:u + 4]
                    if (window > 0).all() and rn.window_spread(depth, u, v, 3)[0] < 0.005:
                        best = (u, v)
                        break
                if best:
                    break
            if best:
                break
        u, v = best
        z = float(np.median(depth[v - 3:v + 4, u - 3:u + 4]))
        camera = np.array([(u - k['cx']) * z / k['fx'], (v - k['cy']) * z / k['fy'], z])
        out[pid] = {'u': float(u), 'v': float(v), 'room': list(pose[:3, :3] @ camera + pose[:3, 3])}
    return out


def edge_pixel(plane):
    """A pixel on the render whose window straddles two surfaces by far: an outer edge."""
    depth = plane / 1000
    for v in range(40, rn.HEIGHT - 40, 3):
        for u in range(40, rn.WIDTH - 40, 3):
            found = rn.window_spread(depth, u, v, 3)
            if found and found[0] > 0.25 and (depth[v - 3:v + 4, u - 3:u + 4] > 0).all():
                return u, v
    return None


def render_tapes(plane, k, pose):
    """The lens's height above the render's floor and its distance, square, to the wall it faces, from the set's
    registration: the tapes an owner would type."""
    points = rn.camera_points(plane, k) @ pose[:3, :3].T + pose[:3, 3]
    forward = pose[:3, :3] @ [0, 0, 1.0]
    axis = int(np.argmax(np.abs(forward[:2])))
    sign = np.sign(forward[axis])
    along = points[(points[:, 2] > 0.5) & (points[:, 2] < 1.8)][:, axis] * sign
    far = np.percentile(along, 99)
    wall = float(np.median(along[along > far - 0.05]))
    return float(pose[2, 3]), abs(wall - pose[axis, 3] * sign)


def scaled_take(factor):
    """The marks take's usable frames with their depth times factor, as a take the fake camera serves."""
    folder = TMP / ('marks-x%g' % factor)
    if not (folder / 'check-1.json').is_file():
        check = json.loads((MARKS / 'check-1.json').read_text(encoding='utf-8'))
        spans = [(s['first_n'], s['last_n']) for s in check['usable']['segments']]
        rows = [r for r in marks_rows() if any(a <= int(r['n']) <= b for a, b in spans)]
        datas = [rn.scale_bundle(R, (MARKS / r['file']).read_bytes(), factor) for r in rows]
        rows = [dict(r, n=str(i)) for i, r in enumerate(rows, 1)]   # a take numbers its frames from 1
        write_take(folder, rows, datas)
        check['usable']['segments'] = [{**check['usable']['segments'][0], 'first_n': 1, 'last_n': len(rows)}]
        (folder / 'check-1.json').write_text(json.dumps(check), encoding='utf-8')
    return folder


@unittest.skipUnless(NODE and (FIT / 'room-model.jsonl').is_file() and (MARKS / 'check-1.json').is_file(),
                     'needs Node and a prepared den-sim set')
class OnRenders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.camera = ts.FakeCamera(FIT / 'camera-calibration.json', MARKS)
        cls.pose = set_pose()
        plane, cls.k, _ = rn.median_plane(R, scaled_take(1.0), 0.5)
        cls.plane = plane
        cls.features = render_features(plane, cls.k, cls.pose)
        cls.clicks = {pid: {'u': f['u'], 'v': f['v']} for pid, f in cls.features.items()}
        cls.room = {pid: f['room'] for pid, f in cls.features.items()}
        cls.height, cls.wall = render_tapes(plane, cls.k, cls.pose)
        scan = rn.camera_points(plane, cls.k, step=1) @ cls.pose[:3, :3].T + cls.pose[:3, 3]
        cls.ply = ply_bytes(scan)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.camera.assert_allowed_paths()   # step 2's takes: /frame.npz and /calibration only
        finally:
            cls.camera.close()

    def setUp(self):
        self.root = scratch('den-live-root-')
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.token_file = self.root / 'camera-token.txt'
        self.token_file.write_text(self.camera.token, encoding='ascii')
        self.camera.show(MARKS)
        self.seen = {}
        self.ply_now = self.ply
        self.method = method_copy(self.root, require_source_correction=0)

    def work(self):
        return self.root / 'private' / 'register'

    def receipt(self, n=None):
        paths = sorted(self.work().glob('register-now-*.json'), key=rn.receipt_number)
        return json.loads((paths[-1] if n is None else self.work() / ('register-now-%d.json' % n)).read_text(encoding='utf-8'))

    def clicker(self, rounds=None, skip=()):
        """den-register's own ClickSession, clicking the nine features; rounds[i] moves some clicks in round i."""
        calls = []

        def click(work, take):
            self.seen['take'] = take
            changes = (rounds or [{}])[min(len(calls), len(rounds or [{}]) - 1)]
            calls.append(1)
            n = R.register.frame_number(take, None, work)
            m, _ = rn.load_method(R, self.method)
            session = R.register.ClickSession(work, take, n, R.worksheet.load(work / 'worksheet.json', need_positions=False), m)
            for pid, c in self.clicks.items():
                if pid not in skip:
                    u, v = changes.get(pid, (c['u'], c['v']))
                    session.click({'id': pid, 'u': u, 'v': v})
            session.done()
        click.calls = calls
        return click

    def kept_clicks(self, work, take):
        self.seen['take'] = take

    def picker(self, moved=None, only=None):
        """The real scan page on the authored room, saved over HTTP with the features' room positions. It also lays
        out this test's content packs, on the scan revision the page says, for the held-out check."""
        def pick(work, take):
            port = free_port()

            def opener(url):
                origin = url.rstrip('/')
                with urllib.request.urlopen(origin + '/__pick/state', timeout=20) as response:
                    self.seen['pick_state'] = json.loads(response.read())
                if self.ply_now is not None:
                    write_packs(self.root / 'packs', self.seen['pick_state']['binding']['content_revision'], self.ply_now)
                with urllib.request.urlopen(origin + '/?apartmentRedesign=1&worldZoom=off', timeout=20) as response:
                    token = re.search(r'__scanPick=\{token:"([0-9a-f]+)"\}', response.read().decode()).group(1)
                chosen = {pid: {'point_m': (moved or {}).get(pid, xyz), 'surface_ref': REF} for pid, xyz in self.room.items()
                          if only is None or pid in only}
                request = urllib.request.Request(origin + '/__pick/save', data=json.dumps({'picks': chosen}).encode(), method='POST',
                                                 headers={'Content-Type': 'application/json', 'Origin': origin, 'X-Scan-Pick': token})
                with urllib.request.urlopen(request, timeout=20) as response:
                    self.seen['saved'] = json.loads(response.read())
            self.assertEqual(rn.scan_page(work, take, 'pick', opener=opener, extra=('--room', 'authored', '--tmp', str(TMP), '--port', str(port))), 0)
        return pick

    def shower(self):
        port = free_port()

        def opener(url):
            origin = url.rstrip('/')
            with urllib.request.urlopen(origin + '/__pick/state', timeout=20) as response:
                self.seen['show'] = json.loads(response.read())['show']
            with urllib.request.urlopen(origin + '/?apartmentRedesign=1&worldZoom=off', timeout=20) as response:
                token = re.search(r'__scanPick=\{token:"([0-9a-f]+)"\}', response.read().decode()).group(1)
            request = urllib.request.Request(origin + '/__pick/done', data=b'{}', method='POST',
                                             headers={'Content-Type': 'application/json', 'Origin': origin, 'X-Scan-Pick': token})
            urllib.request.urlopen(request, timeout=20).read()
        return lambda work, take: rn.scan_page(work, take, 'show', opener=opener, extra=('--room', 'authored', '--tmp', str(TMP), '--port', str(port)))

    def paste(self, again=False, tapes=None, **kw):
        typed = iter(['%.6f' % value for value in (tapes or (self.height, self.wall))])
        self.seen['asked'] = 0

        def ask(prompt):
            self.seen['asked'] += 1
            return next(typed)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rn.main(['--again'] if again else [], root=self.root, camera_url=self.camera.url, token_file=self.token_file,
                           timing=FAST, speak=lambda text: self.seen.setdefault('spoken', []).append(text), wait=time.sleep,
                           console=lambda: True, method_file=self.method, ask=ask, packs=self.root / 'packs', site=SITE, **kw)
        text = out.getvalue()
        self.assertEqual(text, text.encode('ascii', 'replace').decode('ascii'), 'the output is ASCII')
        self.assertNotIn('camera at x', text, "den-register's line with the camera's place isn't passed on")
        for value in (self.height, self.wall):
            self.assertNotIn('%.3f' % value, text, 'no tape is printed')
            self.assertNotIn('%.2f m' % value, text, 'no tape is printed')
        return code, text

    def summary(self, text):
        return text[text.index('Summary'):]

    def qualified(self, **kw):
        """A first paste on true depth that qualifies."""
        code, text = self.paste(clicker=self.clicker(), picker=self.picker(), shower=lambda work, take: None, **kw)
        self.assertEqual(code, 0, text)
        return text

    def assert_block(self, text, qualified):
        """The block's lines, in 02-2's order and shapes; numbers only."""
        block = [line.strip() for line in self.summary(text).splitlines()[1:] if line.strip()]
        block = block[:next(i for i, line in enumerate(block) if line.startswith('QUALIFIED:')) + 1]
        shapes = [r'^Method REGISTER-LIVE-20261113, scan authored-\S+\.$',
                  r'^Depth correction at the camera: not required by this method file \(\d+ of \d+ frames say applied\)\.$',
                  r'^Depth check: floor [+-]\d+\.\d%, wall [+-]\d+\.\d%, bend \d+\.\d points; den-live scale (none|x[01]\.\d+)\.$',
                  r'^Clicks: 9 of 9 with depth\. Edge guard: (clear|re-clicked \S+)\.$',
                  r'^Distance check: 9 agree; median pair difference \d+\.\d cm\.$',
                  r'^Fit: 6 fit \+ 3 check\. Residuals \d+\.\d-\d+\.\d cm\. Leave-one-out worst \d+\.\d cm\. Sofa checks '
                  r'\d+\.\d, \d+\.\d, \d+\.\d cm\.$',
                  r'^REGISTERED den-oak@\d{4}-\d{2}-\d{2}\.\d+: gate 15 cm, error bound \d+\.\d cm\.$',
                  r'^Held-out take: \d+% of floor and upper-wall pixels within noise; worst range bin [+-]\d+\.\d cm\.$',
                  r'^QUALIFIED: yes$' if qualified else r'^QUALIFIED: no, because .+\.$']
        self.assertEqual(len(block), len(shapes), block)
        for line, shape in zip(block, shapes):
            self.assertRegex(line, shape)

    # ---- the plan's acceptance list

    def test_a_clean_pass_registers_qualifies_and_shows_the_nine_features(self):
        code, text = self.paste(clicker=self.clicker(), picker=self.picker(), shower=self.shower())
        self.assertEqual(code, 0, text)
        self.assert_block(text, qualified=True)
        self.assertIn('den-live scale none.', self.summary(text))
        self.assertIn('Edge guard: clear.', self.summary(text))
        self.assertEqual(self.seen['saved'], {'ok': True, 'saved': 9})
        self.assertEqual(self.seen['asked'], 2)
        show = self.seen['show']
        self.assertEqual(len(show['features']), 9)
        self.assertLess(max(f['apart_m'] for f in show['features']), 0.02)
        self.assertEqual(self.seen['spoken'][-1], 'Done. You can come back.')
        self.assertIn('Leave the view again. Recording the held-out take in 0 seconds.', self.seen['spoken'])
        receipt = self.receipt(1)
        self.assertEqual(receipt['schema'], 'den-live-register-now-2')
        self.assertEqual((receipt['fit']['result'], receipt['qualified'], receipt['depth_scale']), ('registered', True, 1.0))
        self.assertTrue(receipt['take'].endswith('-x1-m'))
        self.assertTrue(receipt['scan_revision'].startswith('authored-'))
        self.assertEqual(sorted(receipt['sofa_check_errors_m']), ['c1', 'c2', 'c3'])
        self.assertLess(max(receipt['sofa_check_errors_m'].values()), 0.02)
        self.assertTrue(receipt['held_out']['ok'])
        self.assertGreater(receipt['held_out']['within_noise'], 0.9)
        self.assertEqual(set(receipt['scale_check']) >= {'floor_pct', 'wall_pct', 'bend_pts'}, True)
        self.assertAlmostEqual(receipt['tapes']['lens_height_m'], self.height, places=5)   # kept, privately
        take = json.loads((self.seen['take'] / 'take.json').read_text(encoding='utf-8'))
        self.assertEqual((take['derived']['depth_scale'], take['take']), (1.0, receipt['take']))
        models = R.records.read_jsonl(self.work() / 'room-model.jsonl', 'x')
        self.assertEqual(R.records.resolve(models, receipt['fit']['transform_id'])['entry']['mount']['take'], receipt['take'])

    def test_an_injected_three_percent_scale_is_measured_applied_registered_and_qualified(self):
        self.camera.show(scaled_take(1.03))
        code, text = self.paste(clicker=self.clicker(), picker=self.picker(), shower=lambda work, take: None)
        self.assertEqual(code, 0, text)
        self.assertRegex(text, r'Depth check: floor \+[23]\.\d%, wall \+[23]\.\d%, bend 0\.\d points: scale x0\.97\d applied')
        self.assert_block(text, qualified=True)
        receipt = self.receipt()
        self.assertAlmostEqual(receipt['depth_scale'], 1 / 1.03, delta=0.003)
        self.assertEqual(receipt['scale_check']['result'], 'scale')
        self.assertTrue(receipt['take'].endswith('-x%g-m' % receipt['depth_scale']))
        self.assertTrue(receipt['qualified'])

    def test_a_seven_point_bend_stops_before_any_click(self):
        clicked = []
        code, text = self.paste(tapes=(self.height / 1.037, self.wall / 0.963), clicker=lambda w, t: clicked.append(1),
                                picker=lambda w, t: clicked.append(2), shower=lambda w, t: clicked.append(3))
        self.assertEqual(code, 2)
        self.assertRegex(text, r'Depth check: floor \+3\.\d%, wall -3\.\d%, bend [67]\.\d points: STOP: the depth bends '
                               r'more than a scale can fix')
        self.assertIn('QUALIFIED: no, because the depth bends more than a scale can fix.', self.summary(text))
        self.assertIn('register: stopped: depth check: the depth bends more than a scale can fix. Nothing was clicked.', text)
        self.assertEqual(clicked, [])
        receipt = self.receipt(1)
        self.assertEqual((receipt['scale_check']['result'], receipt['qualified'], receipt['depth_scale']), ('stop', False, None))
        self.assertFalse((self.work() / 'room-model.jsonl').exists())

    def test_two_swapped_scan_picks_are_named_by_the_distance_check(self):
        moved = {'f2': self.room['f5'], 'f5': self.room['f2']}
        code, text = self.paste(clicker=self.clicker(), picker=self.picker(moved=moved), shower=self.fail_if_called)
        self.assertEqual(code, 1, text)
        self.assertRegex(text, r'Distance check: f[25] disagrees with \d of 8 others \(median \d+ cm\); f[25] disagrees with '
                               r'\d of 8 others \(median \d+ cm\): re-pick f[25] and f[25] or re-click them, then paste with --again\.')
        self.assertRegex(self.summary(text), r'Distance check: f[25] and f[25] disagree \(median \d+, \d+ cm\)\.')
        self.assertIn('QUALIFIED: no, because the distance check named f', self.summary(text))
        self.assertNotIn('REGISTERED', text)
        self.assertFalse((self.work() / 'room-model.jsonl').exists(), 'stopped before the fit')

    def test_a_click_on_an_outer_edge_is_named_and_clicked_again(self):
        edge = edge_pixel(self.plane)
        self.assertIsNotNone(edge, 'the render has an outer edge')
        clicker = self.clicker(rounds=[{'f3': edge}, {}])
        code, text = self.paste(clicker=clicker, picker=self.picker(), shower=lambda w, t: None)
        self.assertEqual(code, 0, text)
        self.assertIn('Edge guard: f3 sits on a depth edge: click a little inside one surface.', text)
        self.assertIn("The camera's picture opens again: click f3 again, then press Done.", text)
        self.assertEqual(len(clicker.calls), 2)
        self.assertIn('Edge guard: re-clicked f3.', self.summary(text))
        self.assert_block(text, qualified=True)

    def test_an_edge_click_left_on_the_edge_stops_after_three_rounds(self):
        edge = edge_pixel(self.plane)
        clicker = self.clicker(rounds=[{'f3': edge}])
        code, text = self.paste(clicker=clicker, picker=self.fail_if_called, shower=self.fail_if_called)
        self.assertEqual(code, 2)
        self.assertEqual(len(clicker.calls), 3)
        self.assertIn('the edge guard still names f3: click a little inside one surface, then paste with --again', text)
        self.assertIn('Edge guard: f3 on a depth edge.', self.summary(text))

    def test_a_registration_off_by_a_shift_is_registered_but_not_qualified(self):
        """Every scan pick moved by the same 5, 10 and 8 cm: the pairs keep their distances and the fit has nothing to
        say, so it registers a camera that far from where it is. The held-out take sees the floor and the walls at the
        wrong depths."""
        moved = {pid: list(np.array(xyz) + [0.05, 0.10, 0.08]) for pid, xyz in self.room.items()}
        code, text = self.paste(clicker=self.clicker(), picker=self.picker(moved=moved), shower=lambda w, t: None)
        self.assertEqual(code, 1, text)
        self.assertIn('Distance check: all nine agree within 6 cm.', text)
        self.assert_block(text, qualified=False)
        self.assertRegex(self.summary(text), r'Sofa checks 0\.0, 0\.0, 0\.0 cm\.')
        self.assertIn('QUALIFIED: no, because the held-out take has', self.summary(text))
        receipt = self.receipt()
        self.assertEqual((receipt['fit']['result'], receipt['qualified'], receipt['held_out']['ok']), ('registered', False, False))
        self.assertTrue(receipt['held_out']['take'].endswith('-held'))

    def test_a_sofa_check_over_ten_cm_isnt_qualified_though_the_gate_passes(self):
        """c2 picked 12 cm off, along the direction that disturbs its pair distances least: den-register's 0.15 m gate
        passes, register_now's 0.10 m sofa check doesn't."""
        pairs = {pid: (list(self.pose[:3, :3].T @ (np.array(r) - self.pose[:3, 3])), r) for pid, r in self.room.items()}
        best = None
        for theta in np.linspace(0, math.pi, 13):
            for phi in np.linspace(0, 2 * math.pi, 24, endpoint=False):
                d = np.array([math.sin(theta) * math.cos(phi), math.sin(theta) * math.sin(phi), math.cos(theta)])
                trial = dict(pairs, c2=(pairs['c2'][0], list(np.array(pairs['c2'][1]) + 0.12 * d)))
                median = rn.distance_check(trial, LIVE, GATE)['median_m']['c2']
                if best is None or median < best[0]:
                    best = (median, list(np.array(self.room['c2']) + 0.12 * d))
        self.assertLess(best[0], LIVE['distance_max_m'], 'the distance check passes it')
        code, text = self.paste(clicker=self.clicker(), picker=self.picker(moved={'c2': best[1]}), shower=lambda w, t: None)
        self.assertEqual(code, 1, text)
        self.assertRegex(self.summary(text), r'REGISTERED den-oak@\S+: gate 15 cm')
        self.assertRegex(self.summary(text), r'Sofa checks \d\.\d, 1[12]\.\d, \d\.\d cm\.')
        self.assertRegex(self.summary(text), r'QUALIFIED: no, because sofa check c2 is 1[12]\.\d cm, over 10 cm\.')
        receipt = self.receipt()
        self.assertEqual((receipt['fit']['result'], receipt['qualified']), ('registered', False))
        self.assertGreater(receipt['sofa_check_errors_m']['c2'], 0.10)
        self.assertLess(receipt['sofa_check_errors_m']['c2'], 0.15)
        self.assertNotIn('held_out', receipt, 'no held-out take after a failed sofa check')

    # ---- the rest of the paste

    def test_frames_without_the_cameras_correction_stop_before_any_click(self):
        """The committed method requires the camera service's correction; rendered frames carry no record."""
        self.method = rn.METHOD_FILE
        code, text = self.paste(clicker=self.fail_if_called, picker=self.fail_if_called, shower=self.fail_if_called)
        self.assertEqual(code, 2)
        self.assertIn("register: stopped: the camera's depth isn't corrected at its source on every frame: 0 of", text)
        self.assertIn('no depth_correction record', text)
        receipt = self.receipt(1)
        self.assertNotIn('scale_check', receipt)
        self.assertFalse(receipt['qualified'])

    def test_a_scale_the_tapes_miss_is_blamed_on_the_scale(self):
        """Depth 17% short and tapes that agree with it: the depth check sees nothing, and every pair distance is 17%
        short, so the distance check names most features and their own scale says it's the depth, not the clicks."""
        self.camera.show(scaled_take(0.83))
        code, text = self.paste(tapes=(self.height * 0.83, self.wall * 0.83), clicker=self.clicker(), picker=self.picker(),
                                shower=self.fail_if_called)
        self.assertEqual(code, 1, text)
        self.assertIn('points: no scale', text)
        self.assertRegex(text, r"Every feature fits with their own scale, 1\.2\d\d: it's the depth scale or a tape, which "
                               r"re-clicking won't fix\.")
        self.assertRegex(self.summary(text), r'QUALIFIED: no, because the features. own scale is 1\.2\d\d')

    def test_again_keeps_the_take_and_its_tapes_after_a_bad_pick(self):
        bad = [v + (0.4 if i == 0 else 0) for i, v in enumerate(self.room['c1'])]
        code, text = self.paste(clicker=self.clicker(), picker=self.picker(moved={'c1': bad}), shower=self.fail_if_called)
        self.assertEqual(code, 1, text)
        self.assertIn('Distance check: c1 disagrees', text)
        take = self.seen['take']
        code, text = self.paste(again=True, clicker=self.kept_clicks, picker=self.picker(only={'c1'}), shower=lambda w, t: None)
        self.assertEqual(code, 0, text)
        self.assertEqual(self.seen['asked'], 0, 'the tapes come from the first paste')
        self.assertIn('Using the take %s again, with its tapes.' % take.name[:-len('-x1-m')], text)
        self.assertIn('The last paste named c1', text)
        self.assertEqual(self.seen['take'], take)
        self.assertEqual(self.seen['saved'], {'ok': True, 'saved': 9})
        self.assertIn('QUALIFIED: yes', self.summary(text))

    def test_again_after_the_method_changed_makes_a_new_copy_and_carries_the_clicks(self):
        """A 3% long take with a method that takes no scale under 5%, then the committed one: --again measures the
        same take again, scales it, copies it and carries the nine clicks over."""
        self.camera.show(scaled_take(1.03))
        self.method = method_copy(self.root, require_source_correction=0, scale_none_pct=5.0)
        self.paste(clicker=self.clicker(), picker=self.picker(), shower=lambda w, t: None)
        first = self.seen['take']
        self.assertTrue(first.name.endswith('-x1-m'))
        self.method = method_copy(self.root, require_source_correction=0)
        code, text = self.paste(again=True, clicker=self.kept_clicks, picker=self.picker(only=set()), shower=lambda w, t: None)
        self.assertEqual(code, 0, text)
        self.assertNotEqual(self.seen['take'], first)
        self.assertRegex(self.seen['take'].name, r'-x0\.97\d-m$')
        self.assertIn('9 clicks carried over', text)
        self.assertIn('Clicked 9 of 9 features', text)
        self.assertIn('QUALIFIED: yes', self.summary(text))

    def test_again_after_a_pass_with_nothing_changed_is_no_refusal(self):
        self.qualified()
        spoken = len(self.seen['spoken'])
        code, text = self.paste(again=True, clicker=self.kept_clicks, picker=self.picker(only=set()), shower=self.fail_if_called)
        self.assertEqual(code, 0, text)
        self.assertIn('Already registered as den-oak@', text)
        self.assertIn("It's qualified already: nothing changed, nothing to do.", text)
        self.assertIn('QUALIFIED: yes', self.summary(text))
        self.assertNotIn('Re-click', text)
        self.assertEqual(len(self.seen['spoken']), spoken, 'no take, no held-out take')
        self.assertTrue(self.receipt()['qualified'])

    def test_a_held_out_take_can_be_taken_again_for_the_same_registration(self):
        """A held-out check that couldn't run (the scan's points.ply didn't read): --again with the same clicks and picks
        keeps the registration and takes the held-out take."""
        self.ply_now = None
        code, text = self.paste(clicker=self.clicker(), picker=self.picker(), shower=lambda w, t: None)
        self.assertEqual(code, 1)
        self.assertIn("QUALIFIED: no, because the held-out take couldn't be checked: the scan's active content pack has no "
                      "readable points.ply.", self.summary(text))
        transform = self.receipt()['fit']['transform_id']
        self.ply_now = self.ply
        code, text = self.paste(again=True, clicker=self.kept_clicks, picker=self.picker(only=set()), shower=lambda w, t: None)
        self.assertEqual(code, 0, text)
        self.assertIn('Already registered as %s' % transform, text)
        receipt = self.receipt()
        self.assertEqual((receipt['fit']['result'], receipt['fit']['transform_id'], receipt['qualified']), ('registered', transform, True))

    def test_a_fresh_paste_starts_the_picks_afresh(self):
        self.qualified()
        self.assertTrue(all(p['picked'] is False for p in self.seen['pick_state']['points']))
        self.paste(clicker=self.clicker(), picker=self.picker(), shower=lambda w, t: None)
        self.assertTrue(all(p['picked'] is False for p in self.seen['pick_state']['points']), 'the second paste kept old picks')

    def test_the_click_page_stopped_or_a_feature_unclicked_stops_before_the_scan(self):
        code, text = self.paste(clicker=lambda w, t: 1, picker=self.fail_if_called, shower=self.fail_if_called)
        self.assertEqual(code, 2)
        self.assertIn('the click page stopped before Done', text)
        clicker = self.clicker(skip={'c2'})
        code, text = self.paste(again=True, clicker=clicker, picker=self.fail_if_called, shower=self.fail_if_called)
        self.assertEqual(code, 2)
        self.assertEqual(len(clicker.calls), 3, 'the page opened again for the missing row')
        self.assertIn('c2 not clicked with depth', text)

    def test_a_den_register_refusal_says_its_reason(self):
        def utf16_then_click(work, take):
            sheet = work / 'worksheet.json'
            sheet.write_bytes(sheet.read_text(encoding='utf-8').encode('utf-16'))   # what PowerShell 5.1's > writes
            R.worksheet.load(sheet, need_positions=False)   # the click page's first read
        code, text = self.paste(clicker=utf16_then_click, picker=self.fail_if_called, shower=self.fail_if_called)
        self.assertEqual(code, 2)
        self.assertIn('den-register refused, not_utf8', text)

    def fail_if_called(self, *args):
        raise AssertionError('a page opened that should not have')


class Wiring(unittest.TestCase):
    def test_no_console_touches_nothing(self):
        root = scratch('den-live-root-')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rn.main([], root=root, console=lambda: False), 2)
        self.assertIn('no console', out.getvalue())
        self.assertEqual(list(root.iterdir()), [])

    def test_again_with_no_take(self):
        root = scratch('den-live-root-')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rn.main(['--again'], root=root, console=lambda: True), 2)
        self.assertIn('no earlier take', out.getvalue())

    def test_a_missing_token_says_where_it_goes_before_any_question(self):
        root = scratch('den-live-root-')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rn.main([], root=root, console=lambda: True, token_file=root / 'missing.txt',
                                     camera_url='http://127.0.0.1:9', ask=lambda prompt: self.fail('asked')), 2)
        self.assertIn("den-take's README, step 2", out.getvalue())
        self.assertFalse((root / 'private' / 'register' / 'takes').exists())


if __name__ == '__main__':
    unittest.main()
