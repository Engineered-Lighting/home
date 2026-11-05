"""Tests for den-photometry's math and records (rung 7). Synthetic data only; no network.

    python -B -m unittest discover -s tools/den-photometry -v
"""
import json
import math
import re
import tempfile
import unittest
from pathlib import Path

import card
import meter
import synthetic as syn

HERE = Path(__file__).resolve().parent
SCHEMAS = HERE.parent.parent / "docs/architecture/live-v1/schemas.json"
PARAMETERS, PARAMETERS_SHA = syn.linear_parameters()
NORM = {"version": meter.METER_NORM_VERSION, "sha256": PARAMETERS_SHA}


def method(**changes):
    return syn.photometry_method(**changes)


def box(C, u=0.0, d=0.5, clipped=0.0, usable=True, problem=None):
    """A box reading as card.box_reading returns it, at C nc."""
    return {"usable": usable, "problem": problem, "C_nc": C, "u_C_nc": u, "clipped_frac": clipped,
            "invariance_pct": d, "u_invariance_pct": 0.1, "invariance_pass": d <= 3, "hold1": {}, "hold2": {}}


def cap(n, day, point, C, E=None, *, illuminant="bulbs", surface=None, m=None):
    m = m or method()
    return {"id": f"{day}-{n}", "day": day, "order": (day, 1000.0 + n, n), "point": point,
            "illuminant": illuminant, "blinds": "closed", "light": "four bulbs full", "ts": 1793000000.0 + n,
            "exposure": {"time_ms": 10.0, "gain": 1.0}, "card": box(C),
            "surface": box(surface) if surface is not None else None,
            "reading": card.meter_reading(E, E, m) if E is not None else None, "note": ""}


def full_set(m=None):
    """Two days: the fit at counter-1, desk and sofa held out, a daylight day, counter-1's reflectance."""
    m = m or method()
    k = 0.1
    lux = lambda C: math.pi * k * C / 0.18  # noqa: E731
    return [cap(1, "20261110", "counter-1", 100, 174.533, surface=194.4444, m=m),
            cap(2, "20261110", "desk", 50, lux(50) + 2, m=m),
            cap(3, "20261110", "sofa", 30, lux(30) - 1, m=m),
            cap(4, "20261110", "counter-1", 100, 174.533, m=m),
            cap(5, "20261111", "desk", 80, lux(80) + 3, illuminant="daylight", m=m),
            cap(6, "20261111", "counter-1", 120, lux(120) - 4, illuminant="daylight", m=m)]


def braces(text):
    """The names inside the first {...} of a schemas.json type string."""
    inner = text[text.index("{") + 1:text.index("}")]
    return {re.split(r"[\s:]", part.strip())[0] for part in inner.split(",") if part.strip()}


def contract(record_id):
    data = json.loads(SCHEMAS.read_text(encoding="utf-8"))
    return next(r for r in data["records"] if r["id"] == record_id)


def field_names(record):
    return {name.strip() for f in record["fields"] for name in f["name"].split("/")}


# ---- METHOD-V1 --------------------------------------------------------------------------

class MethodTests(unittest.TestCase):
    def test_method_v1_loads_with_the_owners_open_slots(self):
        m = card.load_method()
        self.assertEqual(set(m.values), set(card.METHOD_KEYS))
        for name in ("meter_tolerance_lx", "k_L_scope", "meter_class"):
            self.assertIn(name, m.open)
            self.assertIsNone(m[name], f"{name} is an empty slot")
        self.assertIn("commissioning_option", m.open)
        self.assertTrue(card.ready_problems(m))
        self.assertIn("meter_tolerance_lx", card.ready_problems(m)[0])

    def test_numbers_the_gate_and_the_ladder_fix(self):
        m = card.load_method()
        self.assertEqual((m["round_trip_C_card_nc"], m["round_trip_rho_card"], m["round_trip_E_meter_lx"],
                          m["round_trip_k_L"], m["round_trip_L_card_cd_m2"]), (100, 0.18, 174.533, 0.1, 10.0))
        self.assertEqual(m["required_points"], ("desk", "sofa"))
        self.assertEqual(m["reflectance_surfaces"], ("counter-1",))
        self.assertEqual(m["min_days"], 2)
        self.assertEqual(m["camera_id"], "den-oak")
        for name in [k for k in card.METHOD_KEYS if k.startswith("round_trip")] + ["min_days", "camera_id"]:
            self.assertNotIn(name, m.open)

    def test_crlf_and_lf_read_the_same(self):
        text = (HERE / card.METHOD_FILE).read_bytes().decode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            lf, crlf = Path(tmp, "lf.md"), Path(tmp, "crlf.md")
            lf.write_bytes(text.replace("\r\n", "\n").encode())
            crlf.write_bytes(("﻿" + text.replace("\r\n", "\n").replace("\n", "\r\n")).encode())
            a, b = card.load_method(lf), card.load_method(crlf)
        self.assertEqual((a.values, a.open, a.file_sha256, a.block_sha256),
                         (b.values, b.open, b.file_sha256, b.block_sha256))

    def test_the_parser_refuses_bad_blocks(self):
        text = (HERE / card.METHOD_FILE).read_text(encoding="utf-8")
        bad = [text.replace("min_days = 2 ", "min_days = two "),
               text.replace("min_days = 2 ", "min_days = 2.5 "),
               text.replace("camera_id = den-oak", "camera_id = "),
               text.replace("camera_id = den-oak", "camera_id = den oak"),
               text.replace("min_days = 2 ", "min_days = 2\nmin_days = 3 "),
               text.replace("min_days = 2 ", "mystery = 2 "),
               text + "\n```method-v1 photometry\nmin_days = 2\n```\n"]
        for variant in bad:
            with self.assertRaises(card.MethodError):
                card.parse_method(variant)

    def test_a_closed_method_must_still_be_usable(self):
        self.assertEqual(card.ready_problems(method()), [])
        for change, word in [({"commissioning_option": "meter-only"}, "card-and-meter only"),
                             ({"k_L_scope": "both"}, "k_L_scope"), ({"meter_tolerance_lx": 0}, "above 0"),
                             ({"daylight_class": "sun"}, "daylight_class"), ({"invariance_ratio": 2}, "below 1"),
                             ({"fit_point": "Counter"}, "not a name")]:
            problems = card.ready_problems(method(**change))
            self.assertTrue(any(word in p for p in problems), (change, problems))


# ---- the round trip and the production path ------------------------------------------------

class RoundTripTests(unittest.TestCase):
    def test_the_known_card_round_trip_recovers_the_card(self):
        trip = card.round_trip(card.load_method())
        self.assertEqual(trip["result"], "pass")
        self.assertEqual((trip["C_card_nc"], trip["rho_card"], trip["E_meter_lx"]), (100, 0.18, 174.533))
        self.assertEqual(round(trip["k_L"], 3), 0.100)
        self.assertEqual(round(trip["L_card_cd_m2"], 1), 10.0)
        self.assertAlmostEqual(trip["E_back_lx"], 174.533, places=9)
        self.assertEqual(trip["normalisation_version"], "meter-norm/v1")
        self.assertTrue(trip["normalisation_version_matches"])

    def test_the_production_functions_by_hand(self):
        k = card.fit_k_L(100, 0.18, 174.533)
        self.assertAlmostEqual(k, 0.18 * 174.533 / (math.pi * 100), places=15)
        self.assertAlmostEqual(card.luminance(k, 100), 100 * k, places=15)
        self.assertAlmostEqual(card.illuminance(k, 100, 0.18), 174.533, places=9)
        self.assertAlmostEqual(card.illuminance(10.0, 5.241, 0.35), math.pi * 52.41 / 0.35, places=9)  # A9's chain

    def test_a_normalisation_version_mismatch_fails_the_round_trip(self):
        self.assertEqual(card.round_trip(card.load_method(), ["meter-norm/v2"])["result"], "fail")
        self.assertEqual(card.round_trip(card.load_method(), ["meter-norm/v1", "meter-norm/v2"])["result"], "fail")
        self.assertEqual(card.round_trip(card.load_method(), [])["result"], "fail")

    def test_a_wrong_scale_fails_the_round_trip(self):
        m = card.load_method().replace(round_trip_k_L=0.101)
        self.assertEqual(card.round_trip(m)["result"], "fail")

    def test_the_round_trip_on_synthetic_frames(self):
        """The gate's card through meter-norm/v1: 100 nc at two exposures, 174.533 lx, k_L 0.100 and back."""
        m = method()
        scene = {"background": 20.0, "rects": [((300, 150, 339, 189), 100.0)]}
        hold1 = {"exposure_us": 10, "iso": 100, "white_balance_kelvin": 4000}  # X 0.01: code 128
        hold2 = {"exposure_us": 5, "iso": 100, "white_balance_kelvin": 4000}   # X 0.005: code 64
        box_ = card.parse_box("305 155 334 184")
        h1 = card.box_hold(syn.held_frames(scene, hold1), box_, PARAMETERS)
        h2 = card.box_hold(syn.held_frames(scene, hold2), box_, PARAMETERS)
        reading = card.box_reading(h1, h2, True, m)
        self.assertTrue(reading["usable"], reading["problem"])
        self.assertEqual(reading["C_nc"], 100.0)
        self.assertEqual((reading["invariance_pct"], reading["clipped_frac"]), (0.0, 0.0))
        k = card.fit_k_L(reading["C_nc"], 0.18, 174.533)
        self.assertEqual(round(k, 3), 0.100)
        self.assertEqual(round(card.luminance(k, reading["C_nc"]), 1), 10.0)
        self.assertAlmostEqual(card.illuminance(k, reading["C_nc"], 0.18), 174.533, places=9)


# ---- boxes, readings and uncertainties -------------------------------------------------------

class BoxTests(unittest.TestCase):
    hold1 = {"exposure_us": 10, "iso": 100, "white_balance_kelvin": 4000}
    hold2 = {"exposure_us": 5, "iso": 100, "white_balance_kelvin": 4000}

    def read(self, value, *, pedestal=0, m=None, box_text="305 155 334 184", gains=True):
        scene = {"background": 20.0, "rects": [((300, 150, 339, 189), value)]}
        b = card.parse_box(box_text)
        h1 = card.box_hold(syn.held_frames(scene, self.hold1, pedestal=pedestal), b, PARAMETERS)
        h2 = card.box_hold(syn.held_frames(scene, self.hold2, pedestal=pedestal), b, PARAMETERS)
        return card.box_reading(h1, h2, gains, m or method())

    def test_parse_box(self):
        self.assertEqual(card.parse_box(" 1 2 30 40 "), (1, 2, 30, 40))
        self.assertEqual(card.parse_box("1,2,30,40"), (1, 2, 30, 40))
        self.assertIsNone(card.parse_box("  "))
        for text in ("1 2 3", "5 5 4 9", "0 0 640 10", "0 0 10 360", "a b c d", "-1 0 5 5"):
            with self.assertRaises(ValueError):
                card.parse_box(text)

    def test_an_offset_fails_exposure_invariance(self):
        """A pedestal meter-norm/v1 does not remove reads differently at the two exposures."""
        clean, offset = self.read(100.0), self.read(100.0, pedestal=8)
        self.assertTrue(clean["invariance_pass"])
        self.assertTrue(offset["usable"])
        self.assertFalse(offset["invariance_pass"])
        self.assertGreater(offset["invariance_pct"], 3)
        # the disagreement is half of u_C
        c1, c2 = offset["hold1"]["C_nc"], offset["hold2"]["C_nc"]
        self.assertAlmostEqual(offset["u_C_nc"], abs(c1 - c2) / 2, places=12)

    def test_a_box_that_is_not_usable(self):
        self.assertIn("clipped", self.read(300.0)["problem"])           # code 255 at hold 1
        self.assertIn("mean code", self.read(5.0)["problem"])          # code 6 at hold 1, 3 at hold 2
        self.assertIn("fewer than", self.read(100.0, box_text="305 155 310 160")["problem"])
        self.assertIn("off its hold", self.read(100.0, gains=False)["problem"])
        every = self.read(300.0, m=method(clipped_frac_max=1.0, box_code_high=255))  # hold 1 all clipped
        self.assertEqual(every["problem"], "no unflagged pixel in a hold")

    def test_a_box_straddling_the_card_edge_is_caught_by_its_codes_or_not_at_all(self):
        """The box mean is the mean over the box: draw it inside the card (README)."""
        reading = self.read(100.0, box_text="290 150 339 189")
        self.assertTrue(reading["usable"])
        self.assertLess(reading["C_nc"], 100.0)

    def test_meter_reading(self):
        m = method()
        self.assertIsNone(card.meter_reading(None, None, m))
        self.assertFalse(card.meter_reading(100.0, None, m)["used"])
        self.assertFalse(card.meter_reading(0.0, 0.0, m)["used"])
        steady = card.meter_reading(100.0, 104.0, m)
        self.assertTrue(steady["used"])
        self.assertAlmostEqual(steady["E_lx"], 102.0)
        self.assertAlmostEqual(steady["u_rel"], math.sqrt(0.05 ** 2 + (4 / 204) ** 2))
        changed = card.meter_reading(100.0, 110.0, m)
        self.assertFalse(changed["used"])
        self.assertIn("the light changed", changed["problem"])

    def test_k_L_and_its_uncertainty(self):
        m = method()
        fit = card.k_L_fit(box(100.0, u=1.0), card.meter_reading(174.533, 174.533, m), m)
        self.assertAlmostEqual(fit["k_L"], card.fit_k_L(100, 0.18, 174.533))
        self.assertAlmostEqual(fit["u_rel"], math.sqrt((0.01 / 0.18) ** 2 + 0.05 ** 2 + 0.01 ** 2))

    def test_held_out_checks_are_in_lux_and_rho_card_cancels(self):
        m = method()
        fit_card, fit_reading = box(100.0), card.meter_reading(174.533, 174.533, m)
        k = card.k_L_fit(fit_card, fit_reading, m)["k_L"]
        check = card.held_out_check(fit_card, fit_reading, k, box(50.0), card.meter_reading(90.0, 90.0, m), m)
        self.assertAlmostEqual(check["E_hat_lx"], 174.533 / 2, places=9)
        self.assertAlmostEqual(check["error_lx"], 174.533 / 2 - 90, places=9)
        self.assertAlmostEqual(check["u_error_lx"], math.hypot(87.2665 * 0.05, 90 * 0.05), places=3)
        self.assertTrue(check["pass"])
        other = method(rho_card=0.2)
        k2 = card.k_L_fit(fit_card, fit_reading, other)["k_L"]
        again = card.held_out_check(fit_card, fit_reading, k2, box(50.0), card.meter_reading(90.0, 90.0, other), other)
        self.assertAlmostEqual(again["E_hat_lx"], check["E_hat_lx"], places=9)
        far = card.held_out_check(fit_card, fit_reading, k, box(50.0), card.meter_reading(110.0, 110.0, m), m)
        self.assertFalse(far["pass"])

    def test_reflectance_from_card_relative_ratios(self):
        m = method()
        one = card.reflectance([(box(194.4444), box(100.0))], m)
        self.assertAlmostEqual(one["rho"], 0.35, places=5)
        self.assertAlmostEqual(one["u_rel"], 0.01 / 0.18, places=9)
        two = card.reflectance([(box(194.4444, u=2.0), box(100.0, u=1.0)), (box(97.2222, u=1.0), box(50.0, u=0.5))], m)
        self.assertAlmostEqual(two["rho"], 0.35, places=5)
        q_u = 1.944444 * math.hypot(2 / 194.4444, 0.01)
        self.assertAlmostEqual(two["u_q"], q_u / math.sqrt(2), places=4)
        self.assertAlmostEqual(two["u_rel"], math.hypot(0.01 / 0.18, two["u_q"] / two["q"]), places=9)


# ---- which captures count, and the score -------------------------------------------------------

class SelectionTests(unittest.TestCase):
    def test_the_fit_is_day_one_at_the_fit_point_and_the_rest_are_held_out(self):
        m = method()
        result = card.score(full_set(m), m, ["meter-norm/v1"])
        group = result["groups"]["all"]
        self.assertEqual(group["fit"], "20261110-1")
        self.assertEqual([p["capture"] for p in group["points"]],
                         ["20261110-2", "20261110-3", "20261111-5", "20261111-6"])
        unused = {c["id"]: why for c, why in result["selection"]["unused"]}
        self.assertEqual(unused, {"20261110-4": "repeats the fit (same point and day)"})
        self.assertEqual(result["held_out"]["result"], "pass")
        self.assertEqual(result["held_out"]["n_points"], 4)
        self.assertAlmostEqual(result["held_out"]["residual_lx_rms"], math.sqrt((4 + 1 + 9 + 16) / 4), places=3)
        self.assertEqual(result["exposure_invariance"]["result"], "pass")
        self.assertAlmostEqual(result["reflectance"]["counter-1"]["rho"], 0.35, places=5)
        self.assertTrue(result["anchored"])
        self.assertEqual(result["gate"], "pass")

    def test_coverage_names_what_is_missing(self):
        m = method()
        caps = [c for c in full_set(m) if c["day"] == "20261110"]
        result = card.score(caps, m, ["meter-norm/v1"])
        self.assertEqual(result["held_out"]["result"], "incomplete")
        missing = " | ".join(result["held_out"]["missing"])
        self.assertIn("fewer than 2", missing)
        self.assertIn("daylight", missing)
        self.assertFalse(result["anchored"])
        self.assertEqual(result["gate"], "incomplete")
        no_sofa = [c for c in full_set(m) if c["point"] != "sofa"]
        self.assertIn("no held-out reading at sofa", card.score(no_sofa, m, ["meter-norm/v1"])["held_out"]["missing"])
        no_rho = full_set(m)
        no_rho[0]["surface"] = None
        result = card.score(no_rho, m, ["meter-norm/v1"])
        self.assertEqual(result["held_out"]["result"], "pass")
        self.assertEqual(result["reflectance_missing"], ["no usable reflectance capture of counter-1"])
        self.assertEqual(result["gate"], "incomplete")

    def test_day_one_is_the_first_day_with_any_capture(self):
        m = method()
        early = cap(9, "20261109", "desk", 50, None, m=m)
        early["card"] = None
        early["reading"] = None
        result = card.score(full_set(m) + [early], m, ["meter-norm/v1"])
        self.assertIsNone(result["groups"]["all"]["fit"])
        self.assertEqual(result["held_out"]["result"], "incomplete")

    def test_a_held_out_point_out_of_tolerance_fails(self):
        m = method()
        caps = full_set(m)
        caps[1]["reading"] = card.meter_reading(70.0, 70.0, m)  # the camera says 87.3 lx
        result = card.score(caps, m, ["meter-norm/v1"])
        self.assertEqual(result["held_out"]["result"], "fail")
        self.assertEqual(result["gate"], "fail")
        self.assertFalse(result["anchored"])

    def test_exposure_invariance_is_checked_before_scoring(self):
        m = method()
        caps = full_set(m)
        caps[2]["card"] = box(30, d=4.0)
        result = card.score(caps, m, ["meter-norm/v1"])
        self.assertEqual(result["exposure_invariance"]["result"], "fail")
        self.assertEqual(result["held_out"]["result"], "not scored")
        self.assertEqual(result["reflectance"], {})
        self.assertEqual(result["gate"], "fail")

    def test_unusable_and_untyped_captures_are_listed(self):
        m = method()
        caps = full_set(m)
        caps.append(cap(7, "20261111", "sofa", 30, None, m=m))
        caps[-1].update(card=None, reading=None)
        caps.append(cap(8, "20261111", "sofa", 30, 50, m=m))
        caps[-1]["card"] = box(30, usable=False, problem="clipped fraction 0.2 is above 0")
        caps.append(cap(9, "20261111", "sofa", 30, None, m=m))
        caps[-1]["reading"] = card.meter_reading(50.0, 60.0, m)
        unused = {c["id"]: why for c, why in card.select(caps, m)["unused"]}
        self.assertEqual(unused["20261111-7"], "nothing typed")
        self.assertIn("clipped", unused["20261111-8"])
        self.assertIn("the light changed", unused["20261111-9"])

    def test_per_illuminant_class_fits_one_scale_per_class(self):
        m = method(k_L_scope="per-illuminant-class")
        caps = full_set(m) + [cap(7, "20261111", "counter-1", 100, 180.0, illuminant="daylight", m=m),
                              cap(8, "20261112", "sofa", 30, 54.0, illuminant="daylight", m=m)]
        result = card.score(caps, m, ["meter-norm/v1"])
        self.assertEqual(sorted(result["groups"]), ["bulbs", "daylight"])
        self.assertEqual(result["groups"]["bulbs"]["fit"], "20261110-1")
        self.assertEqual(result["groups"]["daylight"]["fit"], "20261111-6")
        self.assertEqual([p["capture"] for p in result["groups"]["daylight"]["points"]],
                         ["20261111-5", "20261112-8"])
        unused = {c["id"]: why for c, why in result["selection"]["unused"]}
        self.assertEqual(unused["20261111-7"], "repeats the fit (same point and day)")


# ---- the sun ---------------------------------------------------------------------------------

class SunTests(unittest.TestCase):
    def test_solar_noon_on_the_june_solstice(self):
        sun = card.sun_position(1782043200, 48.43, 0.0)  # 2026-06-21 12:00 UTC on the prime meridian
        self.assertAlmostEqual(sun["elevation"], 90 - 48.43 + 23.44, delta=0.5)
        self.assertAlmostEqual(sun["azimuth"], 180, delta=1.5)

    def test_morning_is_east_and_afternoon_west(self):
        morning = card.sun_position(1782043200 - 4 * 3600, 48.43, 0.0)
        evening = card.sun_position(1782043200 + 4 * 3600, 48.43, 0.0)
        self.assertTrue(45 < morning["azimuth"] < 135, morning)
        self.assertTrue(225 < evening["azimuth"] < 315, evening)
        self.assertAlmostEqual(morning["elevation"], evening["elevation"], delta=1.5)
        self.assertLess(card.sun_position(1782043200 + 12 * 3600, 48.43, 0.0)["elevation"], 0)


# ---- the records -------------------------------------------------------------------------------

class RecordTests(unittest.TestCase):
    def records(self, m=None):
        m = m or method()
        caps = full_set(m)
        result = card.score(caps, m, ["meter-norm/v1"])
        by_id = {c["id"]: c for c in caps}
        k_records = card.k_L_records(result, by_id, m, NORM, 1)
        rho_records = card.reflectance_records(result, by_id, m, NORM, 1)
        room = json.loads(json.dumps(contract("room_model")["example"]))
        room_record = card.room_model_record(room, result, k_records, rho_records, m, NORM, 1)
        state = card.light_state_record(caps[1], result["groups"].get("all", {}).get("k_L"), result["anchored"], m,
                                        NORM, room_record["version"], (48.43, -123.37))
        return result, k_records, rho_records, room_record, state

    def test_calibration_record_field_names_are_the_contracts(self):
        schema = contract("calibration_record")
        fields = {f["name"]: f for f in schema["fields"]}
        allowed = field_names(schema) | {k for e in schema["example"] for k in e} | {"record", "contract"}
        _, k_records, rho_records, _, _ = self.records()
        for record in k_records + rho_records:
            extra = {k for k in record if k not in allowed
                     and not re.fullmatch(r"(confidence|residual)_[a-z]+_(1sigma|rms|margin)", k)}
            self.assertEqual(extra, set(), record["parameter"])
            self.assertEqual((record["record"], record["contract"]), ("calibration_record", "live-v1/B"))
        k = k_records[0]
        self.assertEqual(set(k["inputs"]), braces(fields["inputs"]["type"]) | {"normalisation_parameters_sha256"})
        self.assertEqual(set(k["checks"]), braces(fields["checks"]["type"]))
        example = schema["example"][0]
        self.assertEqual(set(k["inputs"]["exposure"]), set(example["inputs"]["exposure"]))
        for name in ("round_trip", "held_out", "exposure_invariance"):
            self.assertLessEqual(set(example["checks"][name]), set(k["checks"][name]), name)
        self.assertEqual((k["parameter"], k["unit"], k["method"]), (example["parameter"], example["unit"], "meter"))
        self.assertEqual(k["checks"]["round_trip"]["result"], "pass")
        self.assertEqual(k["checks"]["held_out"]["tolerance_lx"], 10)
        self.assertEqual(k["inputs"]["normalisation_version"], "meter-norm/v1")
        self.assertIsNone(k["inputs"]["exposure"]["black_level"])
        self.assertEqual(k["version"], "cal-den-oak-kL-1")

    def test_room_model_photometric_and_reflectance(self):
        result, k_records, rho_records, room, _ = self.records()
        example = contract("room_model")["example"]
        self.assertEqual(room["version"], example["version"] + "-p1")
        camera = room["cameras"][0]
        self.assertEqual(set(camera["photometric"]), set(example["cameras"][0]["photometric"]))
        self.assertEqual(camera["photometric"]["status"], "measured")
        self.assertEqual(camera["photometric"]["k_L"], k_records[0]["value"])
        self.assertEqual(camera["photometric"]["calibration_id"], "cal-den-oak-kL-1")
        counter = next(s for s in room["surfaces"] if s["surface_id"] == "counter-1")
        self.assertEqual(set(counter["reflectance"]), set(example["surfaces"][0]["reflectance"]))
        self.assertEqual(counter["reflectance"]["status"], "measured")
        self.assertAlmostEqual(counter["reflectance"]["value"], 0.35, places=5)
        self.assertEqual(counter["reflectance"]["calibration_id"], rho_records[0]["version"])
        wall = next(s for s in room["surfaces"] if s["surface_id"] == "wall-west")
        self.assertEqual(wall["reflectance"]["status"], "assumed")

    def test_an_unanchored_room_model_claims_no_scale(self):
        m = method()
        caps = [c for c in full_set(m) if c["day"] == "20261110"]
        result = card.score(caps, m, ["meter-norm/v1"])
        by_id = {c["id"]: c for c in caps}
        k_records = card.k_L_records(result, by_id, m, NORM, 2)
        room = card.room_model_record(json.loads(json.dumps(contract("room_model")["example"])), result, k_records,
                                      [], m, NORM, 2)
        photometric = room["cameras"][0]["photometric"]
        self.assertEqual((photometric["k_L"], photometric["status"]), (None, "uncalibrated"))
        self.assertEqual(photometric["calibration_id"], "cal-den-oak-kL-2")
        state = card.light_state_record(caps[1], result["groups"]["all"]["k_L"], False, m, NORM, room["version"])
        estimate = state["surface_estimates"][0]
        self.assertEqual(estimate["unit"], "relative")
        self.assertAlmostEqual(estimate["value"], math.pi * 50 / 0.18, places=6)
        self.assertFalse(state["anchored"])
        self.assertIsNone(state["sun"])

    def test_light_state_field_names_are_the_contracts(self):
        schema = contract("light_state")
        fields = {f["name"]: f for f in schema["fields"]}
        _, _, _, _, state = self.records()
        self.assertEqual(set(state) - {"record", "contract"}, field_names(schema))
        self.assertEqual(set(state["exposure"]), braces(fields["exposure"]["type"]))
        self.assertEqual(set(state["surface_estimates"][0]), braces(fields["surface_estimates"]["type"]))
        self.assertTrue(state["anchored"])
        estimate = state["surface_estimates"][0]
        self.assertEqual((estimate["surface_id"], estimate["unit"], estimate["basis"]), ("desk", "lx", "measured"))
        self.assertAlmostEqual(estimate["value"], math.pi * 0.1 * 50 / 0.18, delta=0.01)
        self.assertEqual(state["exposure"]["normalisation_version"], "meter-norm/v1")
        self.assertEqual(set(state["sun"]), {"azimuth", "elevation"})
        self.assertEqual((state["window_daylight"], state["residual"], state["blinds"]), ([], [], "closed"))

    def test_per_class_room_model_keeps_one_scale_per_class_aside(self):
        m = method(k_L_scope="per-illuminant-class")
        caps = full_set(m) + [cap(7, "20261111", "sofa", 30, math.pi * 0.1 * 30 / 0.18, illuminant="daylight", m=m),
                              cap(8, "20261112", "desk", 50, math.pi * 0.1 * 50 / 0.18, illuminant="bulbs", m=m)]
        result = card.score(caps, m, ["meter-norm/v1"])
        by_id = {c["id"]: c for c in caps}
        k_records = card.k_L_records(result, by_id, m, NORM, 1)
        self.assertEqual([r["version"] for r in k_records], ["cal-den-oak-kL-1-bulbs", "cal-den-oak-kL-1-daylight"])
        room = card.room_model_record(json.loads(json.dumps(contract("room_model")["example"])), result, k_records,
                                      [], m, NORM, 1)
        photometric = room["cameras"][0]["photometric"]
        self.assertIsNone(photometric["k_L"])
        self.assertEqual(sorted(photometric["calibration_ids_by_illuminant"]), ["bulbs", "daylight"])

    def test_records_are_plain_json(self):
        _, k_records, rho_records, room, state = self.records()
        for record in k_records + rho_records + [room, state]:
            json.dumps(record, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
