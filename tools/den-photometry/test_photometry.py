"""Tests for den-photometry's owner flow (rung 7). Synthetic data and a fake camera on 127.0.0.1 only.

    python -B -m unittest discover -s tools/den-photometry -v

No camera, no Jetson, no Home Assistant and no other host is contacted, and no
light is written: the fake answers only the meter build's camera endpoints.
"""
import contextlib
import csv
import http.client
import io
import json
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import card
import meter
import photometry
import sweep
import synthetic as syn

HERE = Path(__file__).resolve().parent
SCHEMAS = HERE.parent.parent / "docs/architecture/live-v1/schemas.json"
CARD_RECT, CARD_BOX = (300, 150, 339, 189), "305 155 334 184"
SURFACE_RECT, SURFACE_BOX = (360, 150, 419, 189), "365 155 414 184"
CAMERA_PATHS = {"/health", "/frame.npz", "/meter", "/meter/release"}


def quiet(*_args, **_kwargs):
    pass


def scene(card_nc, surface_nc=None):
    rects = [(CARD_RECT, card_nc)] + ([(SURFACE_RECT, surface_nc)] if surface_nc is not None else [])
    return {"background": 20.0, "rects": rects}


def room_example():
    data = json.loads(SCHEMAS.read_text(encoding="utf-8"))
    return next(r for r in data["records"] if r["id"] == "room_model")["example"]


class Workspace:
    """A photometry folder, a passing Stage 0 sweep and a token, all in a temporary folder outside git."""

    def __init__(self, tmp, method=None, den=None):
        self.tmp = Path(tmp)
        self.method, self.den = method or syn.photometry_method(), den or syn.den_method()
        self.root = self.tmp / "den-photometry"
        self.root.mkdir()
        self.token = self.tmp / "private" / "camera-token.txt"
        self.token.parent.mkdir()
        self.token.write_text(syn.TOKEN)
        self.stage0 = self.tmp / "den-light" / "stage0-20261108" / "sweep-1"
        self.stage0.mkdir(parents=True)
        self.write_response(syn.stage0_response(self.den))
        self.room = self.tmp / "room_model.jsonl"
        self.room.write_text(json.dumps(room_example()) + "\n", encoding="utf-8")

    def write_response(self, response):
        (self.stage0 / "response.json").write_text(json.dumps(response), encoding="utf-8")

    def new_day(self, date, method=None):
        return photometry.new_day(self.root, self.stage0, self.token, method or self.method, self.den, date)

    def capture(self, fake, day, point, illuminant="bulbs", *, method=None, blinds="closed",
                light="four bulbs at full"):
        camera = sweep.Camera(fake.url, syn.TOKEN)
        return photometry.capture(day, camera, self.token, method or self.method, self.den, point=point,
                                  illuminant=illuminant, blinds=blinds, light=light, say=quiet)

    def score(self, method=None, location=None):
        return photometry.score_root(self.root, self.room, self.token, method or self.method, self.den, location,
                                     say=quiet)


@contextlib.contextmanager
def workspace(**kwargs):
    with tempfile.TemporaryDirectory() as tmp:
        yield Workspace(tmp, **kwargs)


def rows(day):
    return list(csv.reader((day / "worksheet.csv").read_text(encoding="utf-8").splitlines()))


def type_row(day, n, **cells):
    """The owner types into worksheet.csv (saved with CRLF, as Notepad on Windows does)."""
    table = rows(day)
    header = table[0]
    for row in table[1:]:
        if row[0] == str(n):
            for key, value in cells.items():
                row[header.index(key)] = value
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerows(table)
    (day / "worksheet.csv").write_bytes(out.getvalue().encode("utf-8"))


class HostLog:
    """Records every HTTP connection the code opens."""

    def __init__(self):
        self.hosts = []
        real = http.client.HTTPConnection
        log = self

        class Recording(real):
            def __init__(self, host, port=None, *args, **kwargs):
                log.hosts.append((host, port))
                super().__init__(host, port, *args, **kwargs)

        self.patch = mock.patch.object(http.client, "HTTPConnection", Recording)


class FakeCase(unittest.TestCase):
    def setUp(self):
        self.fake = syn.FakeCamera(scene(100.0))
        self.addCleanup(self.fake.close)

    def set_scene(self, value, auto, surface=None):
        self.fake.scene, self.fake.auto = scene(value, surface), auto


# ---- refusals before anything -------------------------------------------------------------

class RefusalTests(FakeCase):
    def test_self_check_needs_no_camera_and_no_data(self):
        out = []
        self.assertEqual(photometry.main(["--self-check"], say=out.append), 0)
        self.assertIn("ROUND TRIP PASS", out[0])
        self.assertIn("k_L 0.100000", out[0])
        self.assertIn("E back 174.533 lx", out[0])
        self.assertIn("meter_tolerance_lx", out[1])
        self.assertEqual(self.fake.requests, [])

    def test_a_day_a_capture_and_a_score_refuse_while_a_line_is_open(self):
        open_method = card.load_method()
        with workspace() as ws:
            with self.assertRaisesRegex(sweep.Refused, "meter_tolerance_lx"):
                ws.new_day("20261110", method=open_method)
            self.assertEqual(list(ws.root.iterdir()), [])
            day = ws.new_day("20261110")
            with self.assertRaisesRegex(sweep.Refused, "k_L_scope"):
                ws.capture(self.fake, day, "desk", method=open_method)
            with self.assertRaisesRegex(sweep.Refused, "meter_tolerance_lx"):
                ws.score(method=open_method)
            self.assertEqual(sorted(p.name for p in day.iterdir()), ["day.json", "worksheet.csv"])
            self.assertEqual(sorted(p.name for p in ws.root.iterdir()), ["day-20261110"])
        self.assertEqual(self.fake.requests, [], "nothing asked the camera")
        out = []
        self.assertEqual(photometry.main(["--new-day", "C:\\nowhere", "--stage0-sweep", "C:\\nowhere"],
                                         say=out.append), 2)
        self.assertIn("Refused", out[0])

    def test_rung_8s_open_lines_also_refuse(self):
        with workspace() as ws:
            den = ws.den
            ws.den = meter.Method(den.values, ("poll_s",), den.file_sha256, den.block_sha256)
            with self.assertRaisesRegex(sweep.Refused, "rung 8's METHOD-V1 still has open lines: poll_s"):
                ws.new_day("20261110")

    def test_new_day_pins_a_passing_stage0_sweep(self):
        with workspace() as ws:
            day = ws.new_day("20261110")
            record = json.loads((day / "day.json").read_text(encoding="utf-8"))
            parameters, digest = syn.linear_parameters(ws.den)
            self.assertEqual(record["normalisation"]["sha256"], digest)
            self.assertEqual(record["normalisation"]["version"], "meter-norm/v1")
            self.assertEqual(record["method_block_sha256"], ws.method.block_sha256)
            self.assertEqual(record["den_light_method_block_sha256"], ws.den.block_sha256)
            self.assertEqual(rows(day), [list(photometry.COLUMNS)])
            with self.assertRaisesRegex(sweep.Refused, "already exists"):
                ws.new_day("20261110")

    def test_new_day_refuses_a_stage0_sweep_it_cannot_trust(self):
        with workspace() as ws:
            ws.write_response(syn.stage0_response(ws.den, outcome="fail"))
            with self.assertRaisesRegex(sweep.Refused, "did not pass"):
                ws.new_day("20261110")
            bad = syn.stage0_response(ws.den)
            bad["meter_norm"]["parameters"]["curves"]["g"][128] = 1.5
            ws.write_response(bad)
            with self.assertRaisesRegex(sweep.Refused, "do not hash"):
                ws.new_day("20261110")
            ws.write_response(syn.stage0_response(ws.den, method_stage0_block_sha256="0" * 64))
            with self.assertRaisesRegex(sweep.Refused, "Stage 0 block"):
                ws.new_day("20261110")
            ws.write_response(syn.stage0_response(ws.den))
            (ws.stage0 / "recheck-1.json").write_text(json.dumps({"outcome": "fail"}))
            with self.assertRaisesRegex(sweep.Refused, "recheck-1.json"):
                ws.new_day("20261110")
            (ws.stage0 / "recheck-1.json").unlink()
            ws.den = syn.den_method(white_balance_kelvin=5000)
            ws.write_response(syn.stage0_response(syn.den_method()))
            with self.assertRaisesRegex(sweep.Refused, "no longer matches"):
                ws.new_day("20261110")
            self.assertEqual(list(ws.root.iterdir()), [])

    def test_a_photometry_folder_must_be_away_from_git_onedrive_and_the_token(self):
        with workspace() as ws:
            for folder, words in [(HERE, "git checkout"), (Path("relative"), "absolute"),
                                  (ws.token.parent, "token")]:
                with self.assertRaisesRegex(sweep.Refused, words):
                    photometry.check_folder(folder, ws.token, "photometry folder")
            onedrive = ws.tmp / "OneDrive" / "den"
            onedrive.mkdir(parents=True)
            with self.assertRaisesRegex(sweep.Refused, "OneDrive"):
                photometry.check_folder(onedrive, ws.token, "photometry folder")

    def test_one_photometry_folder_keeps_one_method_and_one_normalisation(self):
        with workspace() as ws:
            ws.new_day("20261110")
            other = syn.photometry_method()
            other = card.Method(other.values, (), other.file_sha256, "f" * 64)
            with self.assertRaisesRegex(sweep.Refused, "another METHOD-V1 block"):
                ws.new_day("20261111", method=other)
            curves = {c: [z / 100 for z in range(256)] for c in meter.CHANNELS}
            parameters, digest = meter.meter_norm_parameters(ws.den, curves)
            response = syn.stage0_response(ws.den)
            response["meter_norm"].update(parameters=parameters, parameters_sha256=digest)
            ws.write_response(response)
            with self.assertRaisesRegex(sweep.Refused, "other meter-norm/v1 parameters"):
                ws.new_day("20261111")


# ---- a capture -------------------------------------------------------------------------------

class CaptureTests(FakeCase):
    def test_a_capture_holds_three_exposures_releases_and_calls_only_the_camera(self):
        self.set_scene(100.0, (10, 100))
        log = HostLog()
        with workspace() as ws, log.patch:
            day = ws.new_day("20261110")
            folder, record, manual = ws.capture(self.fake, day, "counter-1")
            self.assertIsNone(manual)
            self.assertEqual(record["outcome"], "kept", record.get("detail"))
            self.assertEqual([(h["exposure_us"], h["iso"], h["white_balance_kelvin"]) for h in record["holds"]],
                             [(10, 100, 4000), (5, 100, 4000), (10, 100, 4000)])
            self.assertEqual(record["auto_pick"]["exposure_us"], 10)
            self.assertTrue(record["release"]["released"])
            self.assertFalse(record["off_hold"])
            on_disk = json.loads((folder / "capture.json").read_text(encoding="utf-8"))
            self.assertEqual(on_disk["outcome"], "kept")
            self.assertEqual((on_disk["point"], on_disk["illuminant"], on_disk["light"]),
                             ("counter-1", "bulbs", "four bulbs at full"))
            self.assertEqual(len(list((folder / "frames").iterdir())), 6)
            preview = (folder / "preview.bmp").read_bytes()
            self.assertEqual((preview[:2], len(preview)), (b"BM", 54 + 640 * 360 * 3))
            table = rows(day)
            self.assertEqual(table[1][:5], ["1", "counter-1", "bulbs", "closed", "four bulbs at full"])
            self.assertEqual(table[1][6:10], ["", "", "", ""], "the owner's columns are left blank")
        self.assertEqual({host for host, _ in log.hosts}, {"127.0.0.1"})
        self.assertEqual({port for _, port in log.hosts}, {self.fake.port})
        self.assertLessEqual({path for _, path in self.fake.requests}, CAMERA_PATHS)
        posts = [path for method, path in self.fake.requests if method == "POST"]
        self.assertEqual(posts[-1], "/meter/release")
        self.assertFalse(self.fake.held)

    def test_a_light_change_during_the_capture_voids_it(self):
        self.set_scene(100.0, (10, 100))
        self.fake.scene_after_posts = (2, scene(60.0))
        with workspace() as ws:
            day = ws.new_day("20261110")
            folder, record, _ = ws.capture(self.fake, day, "desk")
            self.assertEqual((record["outcome"], record["void_reason"]), ("void", "repeat_mismatch"))
            self.assertTrue(record["frames_deleted"])
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["capture.json"])
            self.assertEqual(len(rows(day)), 1, "a void adds no worksheet row")
        self.assertEqual([p for m, p in self.fake.requests if m == "POST"][-1], "/meter/release")

    def test_ctrl_c_before_the_repeat_voids_and_still_releases(self):
        class Interrupted(photometry.Capture):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.on_frame = lambda step, n: self.on_ctrl_c(signal.SIGINT, None) if step == 2 else None

        self.set_scene(100.0, (10, 100))
        with workspace() as ws, mock.patch.object(photometry, "Capture", Interrupted):
            day = ws.new_day("20261110")
            folder, record, _ = ws.capture(self.fake, day, "desk")
            self.assertEqual((record["outcome"], record["void_reason"]),
                             ("void", "interrupted_or_errored_before_repeat"))
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["capture.json"])
        self.assertEqual([p for m, p in self.fake.requests if m == "POST"][-1], "/meter/release")

    def test_frames_that_never_reach_the_hold_are_kept_but_never_count(self):
        self.set_scene(100.0, (10, 100))
        self.fake.latency = 10 ** 6  # the camera never applies the hold
        with workspace() as ws:
            day = ws.new_day("20261110")
            _, record, _ = ws.capture(self.fake, day, "counter-1")
            self.assertEqual(record["outcome"], "kept")
            self.assertTrue(record["off_hold"])
            type_row(day, 1, card_box=CARD_BOX, E_before_lx="174.533", E_after_lx="174.533")
            _, report = ws.score()
            self.assertEqual(report["unused"], [{"capture": "20261110-1",
                                                 "reason": "card box: a kept frame is off its hold (rung 8's gain "
                                                           "agreement)"}])

    def test_a_capture_refuses_before_any_hold(self):
        with workspace() as ws:
            day = ws.new_day("20261110")
            self.set_scene(100.0, (1, 100))
            with self.assertRaisesRegex(sweep.Refused, "too bright"):
                ws.capture(self.fake, day, "desk")
            self.set_scene(100.0, (10, 100))
            for kwargs, words in [({"illuminant": "candles"}, "--illuminant"), ({"blinds": "half"}, "--blinds"),
                                  ({"light": ""}, "--light"), ({"light": "a\nb"}, "--light")]:
                with self.assertRaisesRegex(sweep.Refused, words):
                    ws.capture(self.fake, day, "desk", **kwargs)
            with self.assertRaisesRegex(sweep.Refused, "--point"):
                ws.capture(self.fake, day, "Desk A")
            self.fake.held = True
            with self.assertRaisesRegex(sweep.Refused, "held"):
                ws.capture(self.fake, day, "desk")
            self.assertEqual(sorted(p.name for p in day.iterdir()), ["day.json", "worksheet.csv"])
        self.assertNotIn("POST", {m for m, _ in self.fake.requests})

    def test_a_day_made_under_another_block_refuses_a_capture(self):
        with workspace() as ws:
            day = ws.new_day("20261110")
            other = card.Method(ws.method.values, (), ws.method.file_sha256, "e" * 64)
            with self.assertRaisesRegex(sweep.Refused, "another METHOD-V1 block"):
                ws.capture(self.fake, day, "desk", method=other)

    def test_a_row_the_worksheet_cannot_take_is_printed_instead(self):
        self.set_scene(100.0, (10, 100))
        with workspace() as ws:
            day = ws.new_day("20261110")
            with mock.patch.object(photometry, "append_row", return_value="1,desk,bulbs,closed,x,now,,,,,"):
                _, record, manual = ws.capture(self.fake, day, "desk")
            self.assertEqual(record["outcome"], "kept")
            self.assertTrue(manual.startswith("1,desk"))
            (day / "worksheet.csv").write_bytes(b"capture,point")  # no final newline, as Notepad may save
            self.assertIsNone(photometry.append_row(day, {"capture": 2, "point": "sofa"}))
            self.assertEqual((day / "worksheet.csv").read_bytes().split(b"\r\n")[1], b"2,sofa,,,,,,,,,")


# ---- the worksheet and the score --------------------------------------------------------------

class ScoreCase(FakeCase):
    def two_days(self, ws, *, day2=True):
        """The fit at counter-1, its reflectance, desk and sofa; then a daylight day at desk and sofa."""
        day1 = ws.new_day("20261110")
        plan = [(day1, "counter-1", 100.0, None, (10, 100), "bulbs", ("174.533", "174.533")),
                (day1, "counter-1", 100.0, 194.4444, (6, 100), "bulbs", None),
                (day1, "desk", 50.0, None, (20, 100), "bulbs", ("87.3", "87.2")),
                (day1, "sofa", 25.0, None, (40, 100), "bulbs", ("44", "43.8"))]
        if day2:
            day = ws.new_day("20261111")
            plan += [(day, "desk", 80.0, None, (16, 100), "daylight", ("140", "141")),
                     (day, "sofa", 40.0, None, (32, 100), "daylight", ("70", "70"))]
        for day, point, value, surface, auto, illuminant, reading in plan:
            self.set_scene(value, auto, surface)
            folder, record, _ = ws.capture(self.fake, day, point, illuminant,
                                           blinds="open" if illuminant == "daylight" else "closed",
                                           light="daylight only" if illuminant == "daylight" else "four bulbs")
            self.assertEqual(record["outcome"], "kept", record.get("detail"))
            cells = {"card_box": CARD_BOX}
            if reading:
                cells.update(E_before_lx=reading[0], E_after_lx=reading[1])
            if surface:
                cells["surface_box"] = SURFACE_BOX
            type_row(day, record["capture"], **cells)
        return day1


class ScoreTests(ScoreCase):
    def test_two_days_pass_and_write_the_records(self):
        with workspace() as ws:
            self.two_days(ws)
            folder, report = ws.score(location=(48.43, -123.37))
            self.assertEqual(report["gate"], "pass", report["result"]["held_out"])
            self.assertEqual(report["unused"], [])
            self.assertEqual(sorted(p.name for p in folder.iterdir()),
                             ["calibration_record.jsonl", "light_state.jsonl", "report.json", "room_model.jsonl"])
            records = [json.loads(line) for line in (folder / "calibration_record.jsonl").read_text().splitlines()]
            k = records[0]
            self.assertEqual(round(k["value"], 6), 0.1, "the fit card reads 100 nc at 174.533 lx")
            self.assertEqual(k["inputs"]["C_card_nc"], 100.0)
            self.assertEqual(k["inputs"]["E_meter_lx"], 174.533)
            self.assertEqual(k["inputs"]["exposure"], {"time_ms": 0.01, "gain": 1.0, "black_level": None})
            self.assertEqual(k["inputs"]["illuminant"]["class"], "bulbs")
            self.assertEqual(k["checks"]["round_trip"]["result"], "pass")
            self.assertEqual(k["checks"]["exposure_invariance"]["result"], "pass")
            held = k["checks"]["held_out"]
            self.assertEqual((held["result"], held["n_points"], held["tolerance_lx"]), ("pass", 4, 10))
            desk = next(p for p in held["points"] if p["capture"] == "20261110-3")
            self.assertAlmostEqual(desk["E_hat_lx"], 87.2665, delta=0.001)
            self.assertAlmostEqual(desk["error_lx"], 87.2665 - 87.25, delta=0.001)
            rho = records[1]
            self.assertEqual(rho["parameter"], "RoomModel.surfaces[counter-1].reflectance.value")
            self.assertAlmostEqual(rho["value"], 0.35, delta=0.01)
            room = json.loads((folder / "room_model.jsonl").read_text())
            self.assertEqual(room["version"], "den-v4-p1")
            self.assertEqual(room["cameras"][0]["photometric"]["status"], "measured")
            self.assertEqual(room["cameras"][0]["photometric"]["k_L"], k["value"])
            counter = next(s for s in room["surfaces"] if s["surface_id"] == "counter-1")
            self.assertEqual(counter["reflectance"]["calibration_id"], rho["version"])
            states = [json.loads(line) for line in (folder / "light_state.jsonl").read_text().splitlines()]
            self.assertEqual(len(states), 6)
            self.assertTrue(all(s["anchored"] and s["room_version"] == "den-v4-p1" for s in states))
            self.assertEqual({s["surface_estimates"][0]["unit"] for s in states}, {"lx"})
            self.assertTrue(all(set(s["sun"]) == {"azimuth", "elevation"} for s in states))
            report_text = (folder / "report.json").read_text()
            for private in ("48.43", "123.37"):
                self.assertNotIn(private, report_text, "the location is never stored")

    def test_one_day_is_incomplete_and_claims_no_lux(self):
        with workspace() as ws:
            self.two_days(ws, day2=False)
            out = []
            with mock.patch.object(card, "load_method", return_value=ws.method), \
                    mock.patch.object(meter, "load_method", return_value=ws.den):
                code = photometry.main(["--score", str(ws.root), "--room-model", str(ws.room),
                                        "--token-file", str(ws.token)], say=out.append)
            self.assertEqual(code, 1)
            self.assertIn("RUNG 7 GATE: INCOMPLETE", out)
            self.assertTrue(any("fewer than 2" in line for line in out))
            folder = ws.root / "score-1"
            room = json.loads((folder / "room_model.jsonl").read_text())
            self.assertEqual((room["cameras"][0]["photometric"]["k_L"], room["cameras"][0]["photometric"]["status"]),
                             (None, "uncalibrated"))
            states = [json.loads(line) for line in (folder / "light_state.jsonl").read_text().splitlines()]
            self.assertFalse(any(s["anchored"] for s in states))
            self.assertEqual({s["surface_estimates"][0]["unit"] for s in states}, {"relative"})
            self.assertTrue(all(s["sun"] is None for s in states))

    def test_a_second_score_supersedes_the_first(self):
        with workspace() as ws:
            self.two_days(ws)
            ws.score()
            folder, _ = ws.score()
            k = json.loads((folder / "calibration_record.jsonl").read_text().splitlines()[0])
            self.assertEqual((k["version"], k["supersedes"], k["supersedes_method"]),
                             ("cal-den-oak-kL-2", "cal-den-oak-kL-1", "meter"))

    def test_a_reading_out_of_tolerance_fails(self):
        with workspace() as ws:
            day1 = self.two_days(ws)
            type_row(day1, 3, E_before_lx="70", E_after_lx="70")
            _, report = ws.score()
            self.assertEqual(report["gate"], "fail")
            self.assertEqual(report["result"]["held_out"]["result"], "fail")

    def test_unused_rows_are_listed_with_their_reason(self):
        with workspace() as ws:
            day1 = self.two_days(ws)
            type_row(day1, 4, E_before_lx="40", E_after_lx="48")
            type_row(day1, 3, card_box="290 140 349 199")  # well over the card's edge, into the background
            _, report = ws.score()
            unused = {u["capture"]: u["reason"] for u in report["unused"]}
            self.assertIn("the light changed", unused["20261110-4"])
            self.assertEqual(report["gate"], "fail")  # desk now reads low: a box drawn over the edge


class WorksheetTests(ScoreCase):
    def test_rows_the_score_cannot_pair_are_refused(self):
        with workspace() as ws:
            day1 = self.two_days(ws, day2=False)
            type_row(day1, 3, card_box="")
            with self.assertRaisesRegex(sweep.Refused, "no card_box"):
                ws.score()
            type_row(day1, 3, card_box=CARD_BOX, point="sofa")
            with self.assertRaisesRegex(sweep.Refused, "the worksheet's point"):
                ws.score()
            type_row(day1, 3, point="desk", card_box="1 2 3")
            with self.assertRaisesRegex(sweep.Refused, "four whole numbers"):
                ws.score()
            type_row(day1, 3, card_box=CARD_BOX, E_before_lx="lots")
            with self.assertRaisesRegex(sweep.Refused, "not a number"):
                ws.score()
            type_row(day1, 3, E_before_lx="87.3")
            table = rows(day1)
            (day1 / "worksheet.csv").write_text("\n".join(",".join(r) for r in table[:-1]) + "\n")
            with self.assertRaisesRegex(sweep.Refused, "capture 4 has no row"):
                ws.score()
            self.assertFalse((ws.root / "score-1").exists())

    def test_the_worksheet_reads_crlf_lf_semicolons_and_a_bom(self):
        with workspace() as ws:
            day = ws.new_day("20261110")
            base = [list(photometry.COLUMNS), ["1", "desk", "bulbs", "closed", "two bulbs", "now", "87,5", "88",
                                               CARD_BOX, "", ""]]
            for delimiter, ending, bom in [(",", "\n", b""), (",", "\r\n", b"\xef\xbb\xbf"), (";", "\r\n", b"")]:
                out = io.StringIO()
                csv.writer(out, delimiter=delimiter, lineterminator=ending).writerows(base)
                (day / "worksheet.csv").write_bytes(bom + out.getvalue().encode("utf-8"))
                row = photometry.read_worksheet(day)[1]
                self.assertEqual((row["point"], row["card_box"]), ("desk", CARD_BOX))
                self.assertEqual(photometry.number(row["E_before_lx"], "x"), 87.5)
            (day / "worksheet.csv").write_text("capture,place\n")
            with self.assertRaisesRegex(sweep.Refused, "column names"):
                photometry.read_worksheet(day)

    def test_a_room_model_without_the_surface_is_refused(self):
        with workspace() as ws:
            self.two_days(ws)
            room = room_example()
            room["surfaces"] = [s for s in room["surfaces"] if s["surface_id"] != "counter-1"]
            ws.room.write_text(json.dumps(room))
            with self.assertRaisesRegex(sweep.Refused, "no surface counter-1"):
                ws.score()
            ws.room.write_text("not json")
            with self.assertRaisesRegex(sweep.Refused, "RoomModel"):
                ws.score()


class MainTests(FakeCase):
    def test_main_captures_and_says_what_to_type(self):
        self.set_scene(50.0, (20, 100))
        with workspace() as ws, mock.patch.object(card, "load_method", return_value=ws.method), \
                mock.patch.object(meter, "load_method", return_value=ws.den):
            out = []
            self.assertEqual(photometry.main(["--new-day", str(ws.root), "--stage0-sweep", str(ws.stage0),
                                              "--token-file", str(ws.token), "--date", "20261110"],
                                             say=out.append), 0)
            day = ws.root / "day-20261110"
            args = ["--capture", str(day), "--point", "desk", "--illuminant", "bulbs", "--blinds", "closed",
                    "--light", "front left and front right at full", "--camera", self.fake.url,
                    "--token-file", str(ws.token)]
            self.assertEqual(photometry.main(args, say=out.append), 0)
            self.assertTrue(any(line.startswith("KEPT: capture 1 at desk") for line in out), out)
            self.assertNotIn(syn.TOKEN, "\n".join(out))
            self.assertEqual(photometry.main(args[:2] + ["--point", "desk"], say=out.append), 2)
            self.assertEqual(photometry.main(["--score", str(ws.root), "--token-file", str(ws.token)],
                                             say=out.append), 2)
            self.assertEqual(photometry.main(["--score", str(ws.root), "--room-model", str(ws.room),
                                              "--latitude", "48"], say=out.append), 2)


class SourceTests(unittest.TestCase):
    def test_the_tool_reaches_the_camera_only_through_rung_8s_client(self):
        for name in ("card.py", "photometry.py"):
            text = (HERE / name).read_text(encoding="utf-8")
            for word in ("urllib", "socket", "HTTPConnection", "requests", "/api/", "light.turn", "subprocess"):
                self.assertNotIn(word, text, f"{name} mentions {word}")
        text = (HERE / "photometry.py").read_text(encoding="utf-8")
        self.assertEqual(text.count("sweep.Camera("), 1)

    def test_den_light_is_imported_not_copied(self):
        self.assertEqual(Path(meter.__file__).resolve().parent, (HERE.parent / "den-light").resolve())
        self.assertEqual(Path(sweep.__file__).resolve().parent, (HERE.parent / "den-light").resolve())
        self.assertTrue(issubclass(photometry.Capture, sweep.Sweep))
        for name in ("hold_step", "post_meter", "renew_if_due", "next_frame", "do_release", "wait_still",
                     "countdown", "delete_frames"):
            self.assertNotIn(name, photometry.Capture.__dict__, f"{name} is rung 8's, not overridden")


if __name__ == "__main__":
    unittest.main()
