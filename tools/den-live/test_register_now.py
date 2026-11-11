"""Tests for register_now.py, scan_pick.mjs and the method file (live den view, step 2). Stdlib unittest.

    python -B -X utf8 tools\\den-live\\test_register_now.py

No camera, LA or ssh. The parts on renders need a prepared den-sim set (DEN_SIM_SET, else
%USERPROFILE%\\den-sim\\prepared\\fbc7bda3115e) and are skipped without it: test_sofa_check's fake camera serves the
set's marks take (an empty rendered room with six marks, perfect depth), den-register's own ClickSession clicks the
marks where the set's own fit clicked them, and the scan page is the real scan_pick.mjs on the authored room, saved
over HTTP with the set's own room positions. The scan page's tests need Node. Everything written is under TEMP.
"""
import atexit
import contextlib
import csv
import io
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
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
    path = Path(folder) / 'REGISTER-TEST.md'
    path.write_text(text, encoding='utf-8')
    return path


# ---- the method file ------------------------------------------------------------------------------------------

class MethodFile(unittest.TestCase):
    def test_den_register_reads_it_with_every_slot_closed_and_the_gate_at_ten_cm(self):
        m, live = rn.load_method(R)
        self.assertEqual(m.open, {})
        self.assertEqual(m['gate_m'], 0.10)
        self.assertEqual(m['depth_window_px'], 3)
        self.assertEqual(live['depth_scale'], 1.0)   # the camera service corrects depth itself
        self.assertEqual(live['require_source_correction'], 1)
        self.assertEqual(live['floor_guard_fraction'], 0.05)
        self.assertEqual(live['lens_height_tape_m'], 1.37)
        self.assertEqual(set(live), set(rn.LIVE_KEYS))

    def test_den_registers_own_method_file_still_has_open_slots(self):
        """The control: den-register's own REGISTER-V1 would refuse a scored fit, which is why this file exists."""
        self.assertTrue(R.method.load().open)

    def test_a_bad_den_live_block_is_refused(self):
        good = rn.METHOD_FILE.read_text(encoding='utf-8')
        for broken in (good.replace('depth_scale = 1.0', 'depth_scale = open'),
                       good.replace('require_source_correction = 1', 'require_source_correction = 2'),
                       good.replace('own_scale_band = 0.05', ''),
                       good.replace('floor_band_m = 0.03', 'floor_band_m = 0.03\nfloor_band_m = 0.04'),
                       good.replace('depth_scale = 1.0', 'depth_scale = 0.2'),
                       good + '\n```den-live-v1\ndepth_scale = 1\n```\n'):
            with self.assertRaises(ValueError):
                rn.read_live(broken)
        self.assertEqual(rn.read_live(good)['floor_min_share'], 0.10)


# ---- the scaled copy ------------------------------------------------------------------------------------------

def members(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {info.filename: z.read(info) for info in z.infolist()}


def with_holes(data):
    """A rendered bundle has depth everywhere; a real one has holes (0). Zero every 7th depth value."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, 'w') as target:
        for info in source.infolist():
            raw = source.read(info)
            if info.filename == 'depth_mm.npy':
                _, _, body = R.check._npy(raw, info.filename)
                depth = np.frombuffer(body, dtype='<u2').copy()
                depth[::7] = 0
                raw = raw[:len(raw) - len(body)] + depth.tobytes()
            target.writestr(info, raw)
    return out.getvalue()


@unittest.skipUnless((MARKS / 'frames.csv').is_file(), 'needs a prepared den-sim set')
class ScaledBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        row = next(csv.DictReader(io.StringIO((MARKS / 'frames.csv').read_text(encoding='utf-8'))))
        cls.data = with_holes((MARKS / row['file']).read_bytes())

    def depth(self, data):
        return np.frombuffer(bytes(R.scene.read_bundle(data)['depth']), dtype='<u2')

    def test_only_the_depth_values_change(self):
        scaled = rn.scale_bundle(R, self.data, 0.83)
        before, after = members(self.data), members(scaled)
        self.assertEqual(set(before), set(after))
        for name in before:
            if name != 'depth_mm.npy':
                self.assertEqual(before[name], after[name], name)
        old, new = self.depth(self.data), self.depth(scaled)
        body = len(old) * 2
        self.assertEqual(before['depth_mm.npy'][:-body], after['depth_mm.npy'][:-body])   # the .npy header, byte for byte
        self.assertTrue((old > 0).any() and (old == 0).any(), 'the control needs both valid and invalid depth')
        np.testing.assert_array_equal(new[old == 0], 0)
        np.testing.assert_array_equal(new[old > 0], np.clip(np.rint(old[old > 0] * 0.83), 1, 65535))

    def test_scale_one_gives_the_same_depth(self):
        np.testing.assert_array_equal(self.depth(rn.scale_bundle(R, self.data, 1.0)), self.depth(self.data))

    def test_the_copy_reads_as_a_take(self):
        source, target = scratch('den-live-take-'), scratch('den-live-scaled-') / 'copy'
        rows = list(csv.DictReader(io.StringIO((MARKS / 'frames.csv').read_text(encoding='utf-8'))))[:5]
        (source / 'frames').mkdir()
        for row in rows:
            shutil.copyfile(MARKS / row['file'], source / row['file'])
        text = io.StringIO()
        writer = csv.DictWriter(text, list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
        (source / 'frames.csv').write_text(text.getvalue(), encoding='utf-8', newline='')
        take = json.loads((MARKS / 'take.json').read_text(encoding='utf-8'))
        (source / 'take.json').write_text(json.dumps(take), encoding='utf-8')
        (source / 'camera-calibration.json').write_text('{}', encoding='utf-8')
        self.assertEqual(rn.scaled_copy(R, source, target, 0.83, {'file': 'X.md', 'sha256': 'f' * 64}), 5)
        copied, frames = R.scene.read_take(target)
        self.assertEqual(len(frames), 5)
        for frame in frames:
            R.scene.load_frame(target, frame['n'])   # each bundle's hash against the new frames.csv, and it reads
        self.assertEqual(copied['derived']['depth_scale'], 0.83)
        self.assertEqual(copied['derived']['from_take'], source.name)
        self.assertEqual(copied['take'], 'copy')


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
    folder = scratch('den-live-source-')
    rows = list(csv.DictReader(io.StringIO((MARKS / 'frames.csv').read_text(encoding='utf-8'))))[:len(changes)]
    (folder / 'frames').mkdir()
    for row, change in zip(rows, changes):
        data = with_metadata((MARKS / row['file']).read_bytes(), change)
        (folder / row['file']).write_bytes(data)
        row['npz_sha256'], row['bytes'] = rn.sha256_bytes(data), str(len(data))
    text = io.StringIO()
    writer = csv.DictWriter(text, list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    (folder / 'frames.csv').write_text(text.getvalue(), encoding='utf-8', newline='')
    (folder / 'take.json').write_text((MARKS / 'take.json').read_text(encoding='utf-8'), encoding='utf-8')
    return folder


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


# ---- the floor and the features' own scale -----------------------------------------------------------------------

LIVE = rn.read_live(rn.METHOD_FILE.read_text(encoding='utf-8'))


def room_points(height, pitch_deg, seed=1, floor_n=6000, table_n=1500):
    """Camera-frame points (+y down) of a floor `height` below the lens, a table top 0.45 m above the floor, and a
    wall, with the camera pitched down."""
    rng = np.random.default_rng(seed)
    floor = np.column_stack((rng.uniform(-2, 2, floor_n), np.zeros(floor_n), rng.uniform(0.5, 5, floor_n)))
    table = np.column_stack((rng.uniform(-0.5, 0.5, table_n), np.full(table_n, 0.45), rng.uniform(1.5, 2.5, table_n)))
    wall = np.column_stack((rng.uniform(-2, 2, 3000), rng.uniform(0, 2.5, 3000), np.full(3000, 5.0)))
    world = np.vstack((floor, table, wall))   # world: +x right, +y up from the floor, +z ahead
    pitch = math.radians(pitch_deg)
    # camera axes in world terms, pitched down: forward tilts below the horizontal
    fwd = np.array([0, -math.sin(pitch), math.cos(pitch)])
    down = np.array([0, -math.cos(pitch), -math.sin(pitch)])
    right = np.array([1.0, 0, 0])
    rel = world - np.array([0, height, 0])
    cam = np.column_stack((rel @ right, rel @ down, rel @ fwd))
    return cam + rng.normal(0, 0.004, cam.shape)


class Floor(unittest.TestCase):
    def test_height_and_pitch(self):
        plane = rn.floor_plane(room_points(1.37, 11), LIVE)
        self.assertAlmostEqual(plane['height_m'], 1.37, delta=0.01)
        self.assertAlmostEqual(plane['pitch_down_deg'], 11, delta=0.5)
        self.assertAlmostEqual(plane['roll_deg'], 0, delta=0.5)

    def test_the_lowest_plane_wins_over_a_table(self):
        points = room_points(1.37, 11)
        plane = rn.floor_plane(points, LIVE)
        self.assertGreater(plane['height_m'], 1.2)   # the table is 0.92 m below the lens
        # the control: with most of the floor gone, the table is all that's left and is what's found
        lens_frame_up = points[:, 1] * math.cos(math.radians(11)) + points[:, 2] * math.sin(math.radians(11))
        table_only = points[np.abs(lens_frame_up - 0.92) < 0.05]
        self.assertAlmostEqual(rn.floor_plane(table_only, LIVE)['height_m'], 0.92, delta=0.02)

    def test_a_table_with_more_points_than_the_floor_still_loses_to_it(self):
        """The rule the review found untested: a table out-supports the floor, the floor is still strong, the lower wins.
        Best support alone would measure the table, 0.92 m below the lens."""
        plane = rn.floor_plane(room_points(1.37, 11, floor_n=3000, table_n=6000), LIVE)
        self.assertAlmostEqual(plane['height_m'], 1.37, delta=0.01)

    def test_no_level_plane(self):
        wall = np.column_stack((np.linspace(-2, 2, 500), np.linspace(0.4, 2, 500), np.full(500, 4.0)))
        self.assertIsNone(rn.floor_plane(wall, LIVE))


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

    def test_the_note_outside_the_band(self):
        found = {'scale': 1.02, 'features': 6, 'worst_m': 0.01, 'worst_id': 'f1'}
        self.assertEqual(len(rn.scale_lines(found, 0.83, LIVE)), 1)
        self.assertEqual(len(rn.scale_lines({**found, 'scale': 1.2}, 0.83, LIVE)), 2)


# ---- the worksheet ------------------------------------------------------------------------------------------------

class Worksheet(unittest.TestCase):
    def test_den_register_reads_it_with_scan_pick_labelled_in_memory(self):
        work = scratch('den-live-work-')
        path = rn.ensure_worksheet(R, work)
        sheet = R.worksheet.load(path, need_positions=False)
        self.assertEqual([p['id'] for p in sheet['points']], [pid for pid, _, _ in rn.POINTS])
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

    def test_a_worksheet_already_there_is_kept(self):
        work = scratch('den-live-work-')
        path = rn.ensure_worksheet(R, work)
        path.write_text(path.read_text(encoding='utf-8').replace('table height, away from f3', 'kept'), encoding='utf-8')
        rn.ensure_worksheet(R, work)
        self.assertIn('kept', path.read_text(encoding='utf-8'))


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

    def test_state_lists_the_points_unpicked(self):
        status, body = self.page.get('/__pick/state')
        state = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual([p['id'] for p in state['points']], [pid for pid, _, _ in rn.POINTS])
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
        self.assertEqual((status, body), (200, {'ok': True, 'saved': 6}))
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
        self.assertEqual(sheet['points'][4]['room_xyz_m'], [9, 9, 0])
        self.assertEqual(sheet['points'][0]['room_xyz_m'], [1.0, 0.5, 0.0])

    def test_a_pick_from_another_scan_revision_is_no_pick(self):
        self.page.post('/__pick/save', {'picks': picks(rn.POINTS)})
        self.page.close()
        sheet = self.sheet()
        sheet['points'][0]['note'] = sheet['points'][0]['note'].rsplit('content revision ', 1)[0] + 'content revision older-r0'
        (self.work / 'worksheet.json').write_text(json.dumps(sheet), encoding='utf-8')
        self.page = Page(self.work, self.take, 'pick')
        state = json.loads(self.page.get('/__pick/state')[1])
        self.assertEqual([p['picked'] for p in state['points']], [False] + [True] * 5)
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


def true_camera_height():
    """The set's own fit put the invented camera this high above the rendered floor (z = 0)."""
    revision = json.loads((FIT / 'room-model.jsonl').read_text(encoding='utf-8').strip().splitlines()[-1])
    return revision['cameras'][0]['pose']['room_from_camera'][2][3]


@unittest.skipUnless(NODE and (FIT / 'clicks-9083b764abfe.json').is_file() and (MARKS / 'check-1.json').is_file(),
                     'needs Node and a prepared den-sim set')
class OnRenders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.camera = ts.FakeCamera(FIT / 'camera-calibration.json', MARKS)
        cls.clicks = json.loads((FIT / 'clicks-9083b764abfe.json').read_text(encoding='utf-8'))['clicks']
        cls.room = {p['id']: p['room_xyz_m'] for p in json.loads((FIT / 'worksheet.json').read_text(encoding='utf-8'))['points']}
        cls.height = true_camera_height()

    @classmethod
    def tearDownClass(cls):
        cls.camera.close()

    def setUp(self):
        self.root = scratch('den-live-root-')
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.token_file = self.root / 'camera-token.txt'
        self.token_file.write_text(self.camera.token, encoding='ascii')
        self.seen = {}

    def work(self):
        return self.root / 'private' / 'register'

    def clicker(self, skip=()):
        """den-register's own ClickSession, clicking where the set's own fit clicked."""
        def click(work, take):
            self.seen['take'] = take
            n = R.register.frame_number(take, None, work)
            m, _ = rn.load_method(R, self.method)
            session = R.register.ClickSession(work, take, n, R.worksheet.load(work / 'worksheet.json', need_positions=False), m)
            for pid, c in self.clicks.items():
                if pid not in skip:
                    session.click({'id': pid, 'u': c['u'], 'v': c['v']})
            session.done()
        return click

    def kept_clicks(self, work, take):
        self.seen['take'] = take

    def picker(self, moved=None, only=None):
        """The real scan page on the authored room, saved over HTTP with the set's own room positions."""
        def pick(work, take):
            port = free_port()

            def opener(url):
                origin = url.rstrip('/')
                with urllib.request.urlopen(origin + '/__pick/state', timeout=20) as response:
                    self.seen['pick_state'] = json.loads(response.read())
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

    def registered(self):
        """A first paste on true depth that registers."""
        self.method = method_copy(self.root, require_source_correction=0, depth_scale=1.0, lens_height_tape_m=round(self.height, 3))
        code, text = self.paste(clicker=self.clicker(), picker=self.picker(), shower=lambda work, take: None)
        self.assertEqual(code, 0, text)
        return text

    def paste(self, again=False, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = rn.main(['--again'] if again else [], root=self.root, camera_url=self.camera.url, token_file=self.token_file,
                           timing=FAST, speak=lambda text: self.seen.setdefault('spoken', []).append(text), wait=time.sleep,
                           console=lambda: True, method_file=self.method, **kw)
        text = out.getvalue()
        self.assertEqual(text, text.encode('ascii', 'replace').decode('ascii'), 'the output is ASCII')
        return code, text

    def summary(self, text):
        return text[text.index('Summary'):]

    def test_true_depth_registers_and_the_page_shows_the_features_on_their_picks(self):
        self.method = method_copy(self.root, require_source_correction=0, depth_scale=1.0, lens_height_tape_m=round(self.height, 3))
        code, text = self.paste(clicker=self.clicker(), picker=self.picker(), shower=self.shower())
        self.assertEqual(code, 0, text)
        self.assertIn('REGISTERED den-oak@', self.summary(text))
        self.assertRegex(text, r'Floor guard: the floor is \d\.\d\d m below the lens with depth x1 ')
        self.assertIn("Features' own scale: 1.0", self.summary(text))
        self.assertEqual(self.seen['saved'], {'ok': True, 'saved': 6})
        show = self.seen['show']
        self.assertTrue(show['transform_id'].startswith('den-oak@'))
        self.assertEqual(len(show['features']), 6)
        self.assertLess(max(f['apart_m'] for f in show['features']), 0.05)
        self.assertAlmostEqual(show['camera_m'][2], self.height, delta=0.05)
        self.assertEqual(self.seen['spoken'][-1], 'Done. You can come back.')
        receipt = json.loads((self.work() / 'register-now-1.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['fit']['result'], 'registered')
        self.assertTrue(receipt['floor_guard']['ok'])
        self.assertTrue(self.seen['take'].name.endswith('-x1'))
        take = json.loads((self.seen['take'] / 'take.json').read_text(encoding='utf-8'))
        self.assertEqual(take['derived']['depth_scale'], 1.0)

    def test_the_floor_guard_stops_a_wrong_scale_before_any_click(self):
        self.method = method_copy(self.root, require_source_correction=0, depth_scale=0.83, lens_height_tape_m=round(self.height, 3))
        clicked = []
        code, text = self.paste(clicker=lambda w, t: clicked.append(1), picker=lambda w, t: clicked.append(2),
                                shower=lambda w, t: clicked.append(3))
        self.assertEqual(code, 2)
        self.assertIn('register: stopped: floor guard:', text)
        self.assertRegex(text, r'-1[67]\.\d%, past 5%')
        self.assertEqual(clicked, [])
        self.assertFalse((self.work() / 'room-model.jsonl').exists())

    def test_frames_without_the_cameras_correction_stop_before_any_click(self):
        """The committed method requires the camera service's correction; rendered frames carry no record."""
        self.method = method_copy(self.root, depth_scale=1.0, lens_height_tape_m=round(self.height, 3))
        code, text = self.paste(clicker=self.fail_if_called, picker=self.fail_if_called, shower=self.fail_if_called)
        self.assertEqual(code, 2)
        self.assertIn("register: stopped: the camera's depth isn't corrected at its source on every frame: 0 of", text)
        self.assertIn('no depth_correction record', text)
        receipt = json.loads((self.work() / 'register-now-1.json').read_text(encoding='utf-8'))
        self.assertNotIn('floor_guard', receipt)

    def test_a_scale_the_guard_misses_is_refused_by_the_rigid_fit(self):
        """Depth 17% short with a tape that agrees: the guard passes, and the rigid fit can't absorb the scale."""
        self.method = method_copy(self.root, require_source_correction=0, depth_scale=0.83, lens_height_tape_m=round(self.height * 0.83, 3))
        code, text = self.paste(clicker=self.clicker(), picker=self.picker(), shower=self.fail_if_called)
        self.assertEqual(code, 1, text)
        self.assertIn('REFUSED', self.summary(text))
        self.assertRegex(self.summary(text), r"Features' own scale: 1\.2\d\d .*\n.*outside 1 \+/- 0\.05")
        self.assertIn('fit_residual_over_gate x4', self.summary(text))
        self.assertIn("so it's the depth scale, which re-clicking won't fix", self.summary(text))

    def test_one_fit_point_picked_far_off_is_a_click_not_the_depth_scale(self):
        """The review's case: a wrong corner 0.8 m off moves the features' own scale too, and still isn't the scale."""
        self.method = method_copy(self.root, require_source_correction=0, depth_scale=1.0, lens_height_tape_m=round(self.height, 3))
        bad = [v + (0.8 if i == 0 else 0) for i, v in enumerate(self.room['f1'])]
        code, text = self.paste(clicker=self.clicker(), picker=self.picker(moved={'f1': bad}), shower=self.fail_if_called)
        self.assertEqual(code, 1, text)
        self.assertIn('Re-click or re-pick what it names', self.summary(text))
        self.assertIn('f1 sits furthest from the rest', self.summary(text))
        self.assertNotIn('depth scale, which', self.summary(text))

    def test_again_keeps_the_take_and_refits_after_one_bad_pick(self):
        self.method = method_copy(self.root, require_source_correction=0, depth_scale=1.0, lens_height_tape_m=round(self.height, 3))
        bad = [v + (0.4 if i == 0 else 0) for i, v in enumerate(self.room['c1'])]
        code, text = self.paste(clicker=self.clicker(), picker=self.picker(moved={'c1': bad}), shower=self.fail_if_called)
        self.assertEqual(code, 1, text)
        self.assertIn('REFUSED: check_over_gate. Re-click or re-pick what it names', self.summary(text))
        take = self.seen['take']
        code, text = self.paste(again=True, clicker=self.kept_clicks, picker=self.picker(only={'c1'}), shower=lambda w, t: None)
        self.assertEqual(code, 0, text)
        self.assertIn('Using the take %s again, depth x1.' % take.name[:-len('-x1')], text)
        self.assertEqual(self.seen['take'], take)
        self.assertEqual(self.seen['saved'], {'ok': True, 'saved': 6})

    def test_again_after_the_scale_changed_makes_a_new_copy_and_carries_the_clicks(self):
        self.registered()
        first = self.seen['take']
        self.method = method_copy(self.root, require_source_correction=0, depth_scale=0.9, lens_height_tape_m=round(self.height * 0.9, 3))
        code, text = self.paste(again=True, clicker=self.kept_clicks, picker=self.picker(only=set()), shower=lambda w, t: None)
        self.assertNotEqual(self.seen['take'], first)
        self.assertTrue(self.seen['take'].name.endswith('-x0.9'))
        self.assertIn('6 clicks carried over', text)
        self.assertIn('Clicked 6 of 6 features', text)
        self.assertIn('with depth x0.9', self.summary(text))
        self.assertEqual(json.loads((self.seen['take'] / 'take.json').read_text(encoding='utf-8'))['derived']['depth_scale'], 0.9)
        self.assertIn('so 1.000 on the raw depth', self.summary(text))   # 0.9 x its own 1.111: the raw depth is true

    def test_again_after_a_pass_with_nothing_changed_is_no_refusal(self):
        self.registered()
        code, text = self.paste(again=True, clicker=self.kept_clicks, picker=self.picker(only=set()), shower=self.fail_if_called)
        self.assertEqual(code, 0, text)
        self.assertIn('Already registered as den-oak@', self.summary(text))
        self.assertNotIn('Re-click', text)

    def test_a_fresh_paste_starts_the_picks_afresh(self):
        self.registered()
        self.assertTrue(all(p['picked'] is False for p in self.seen['pick_state']['points']))
        self.paste(clicker=self.clicker(), picker=self.picker(), shower=lambda w, t: None)
        self.assertTrue(all(p['picked'] is False for p in self.seen['pick_state']['points']), 'the second paste kept old picks')

    def test_the_click_page_stopped_or_a_feature_unclicked_stops_before_the_scan(self):
        self.method = method_copy(self.root, require_source_correction=0, depth_scale=1.0, lens_height_tape_m=round(self.height, 3))
        code, text = self.paste(clicker=lambda w, t: 1, picker=self.fail_if_called, shower=self.fail_if_called)
        self.assertEqual(code, 2)
        self.assertIn('the click page stopped before Done', text)
        code, text = self.paste(again=True, clicker=self.clicker(skip={'c2'}), picker=self.fail_if_called, shower=self.fail_if_called)
        self.assertEqual(code, 2)
        self.assertIn('c2 not clicked with depth', text)

    def test_a_den_register_refusal_says_its_reason(self):
        self.method = method_copy(self.root, require_source_correction=0, depth_scale=1.0, lens_height_tape_m=round(self.height, 3))

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

    def test_a_missing_token_says_where_it_goes(self):
        root = scratch('den-live-root-')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rn.main([], root=root, console=lambda: True, token_file=root / 'missing.txt',
                                     camera_url='http://127.0.0.1:9'), 2)
        self.assertIn("den-take's README, step 2", out.getvalue())
        self.assertFalse((root / 'private' / 'register' / 'takes').exists())


if __name__ == '__main__':
    unittest.main()
