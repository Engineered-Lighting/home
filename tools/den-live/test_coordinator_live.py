"""Tests for the den lighting coordinator in den-live (coordinator plan, step 3): the Home Assistant link's mapping,
the service, its routes on the people stream, the settings block, and the release scan of its pinned tree.

    python -B -m unittest discover -s tools\\den-live -p "test_coordinator_live.py"

Stand-ins only: no camera, no Home Assistant, no fixture. The coordinator's core is the real pinned tree (git archive
of tools/den-core and the lighting brain at refs.json's den-coordinator pin).
"""
import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import coordinator_live as cl  # noqa: E402
import c12_fc13  # noqa: E402
import c12_replay  # noqa: E402
import ha_link  # noqa: E402
import release  # noqa: E402
import run_local as rl  # noqa: E402
import run_service  # noqa: E402
import sofa_check as sc  # noqa: E402
import stream  # noqa: E402

LIGHTS = ["light.lamp_a", "light.lamp_b"]
TREES = Path(tempfile.mkdtemp(prefix="den-coordinator-trees-"))
CORE = None


def setUpModule():
    global CORE
    CORE = cl.load_core(sc.archive(TREES, "coordinator"))


def tearDownModule():
    shutil.rmtree(TREES, ignore_errors=True)


class FakeHa:
    def __init__(self, states=None, fail=False):
        self.calls, self.states, self.fail = [], states or {}, fail

    def call(self, domain, service, data):
        if self.fail:
            raise ha_link.HaError("Home Assistant didn't answer")
        self.calls.append((domain, service, json.loads(json.dumps(data))))

    def state(self, entity):
        return self.states.get(entity)


class NoHa:
    def call(self, *_args):
        raise AssertionError("C12 preview made a Home Assistant service call")


def den_status(occupancy="on", automatic=True, correction=None, gaming=False, signal="unknown", gaming_enabled=True):
    attrs = {"automatic": automatic, "occupancy": occupancy, "gaming_session": gaming, "gaming_signal": signal,
             "gaming_enabled": gaming_enabled}
    if correction:
        attrs["last_correction"] = correction
    return {cl.STATUS_ENTITY: {"state": "occupancy", "attributes": attrs}, cl.SWITCH_ENTITY: {"state": "on", "attributes": {}}}


def seated_pose(x=1.2, y=1.0):
    j = {"left_shoulder": [x - 0.2, y, 0.95], "right_shoulder": [x + 0.2, y, 0.95], "left_hip": [x - 0.15, y, 0.45],
         "right_hip": [x + 0.15, y, 0.45], "left_eye": [x - 0.03, y + 0.08, 1.2], "right_eye": [x + 0.03, y + 0.08, 1.2],
         "nose": [x, y + 0.1, 1.18], "left_wrist": [x - 0.1, y + 0.3, 0.65], "right_wrist": [x + 0.1, y + 0.3, 0.65]}
    return {name: [xyz, "supported"] for name, xyz in j.items()}


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class LoadTest(unittest.TestCase):
    def test_den_lives_own_body_never_stands_in_for_den_cores(self):
        stand_in = type(sys)("body")   # den-live's own body.py, already imported
        saved = sys.modules.get("body")
        sys.modules["body"] = stand_in
        try:
            dc, _learn = cl.load_core(TREES / next(p.name for p in TREES.iterdir() if p.name.startswith("coordinator-")))
            self.assertTrue(hasattr(dc.body, "seated"), "the core got its own body")
            self.assertIs(sys.modules["body"], stand_in, "den-live's body is put back")
            self.assertNotIn("den_coordinator", sys.modules)
        finally:
            if saved is None:
                sys.modules.pop("body", None)
            else:
                sys.modules["body"] = saved


class MappingTest(unittest.TestCase):
    def test_joints_map_to_den_core_confidences(self):
        got = cl.joints3d({"nose": [[1, 2, 3], "supported"], "left_wrist": [[0, 0, 1], "uncertain"], "bad": [[1, 2], "supported"],
                           "worse": [[1, 2, 3], "rejected"]})
        self.assertEqual(got, {"nose": [1.0, 2.0, 3.0, 1.0], "left_wrist": [0.0, 0.0, 1.0, 0.5]})

    def test_ha_event_on_needs_the_switch_and_den_lightings_automatic(self):
        self.assertTrue(cl.ha_event(den_status())["on"])
        self.assertFalse(cl.ha_event(den_status(automatic=False))["on"])
        off = den_status()
        off[cl.SWITCH_ENTITY]["state"] = "off"
        self.assertFalse(cl.ha_event(off)["on"])
        self.assertFalse(cl.ha_event({})["on"])

    def test_ha_event_occupancy_gaming_and_corrections(self):
        self.assertEqual(cl.ha_event(den_status(occupancy="unknown"))["occupied"], None)
        self.assertEqual(cl.ha_event(den_status(occupancy="off"))["occupied"], False)
        self.assertTrue(cl.ha_event(den_status(gaming=True))["game_running"])
        self.assertFalse(cl.ha_event(den_status())["game_running"])   # an unread PC sensor is no game: learning goes on
        self.assertFalse(cl.ha_event(den_status(gaming_enabled=False))["game_running"])
        record = {"seq": 3, "entity_id": "light.lamp_a"}
        self.assertEqual(cl.ha_event(den_status(correction=record))["last_correction"], record)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp(prefix="den-coordinator-state-"))
        self.ha, self.clock = FakeHa(), Clock()

    def tearDown(self):
        shutil.rmtree(self.state, ignore_errors=True)

    def service(self, **kw):
        return cl.CoordinatorService(CORE, state_dir=self.state, lights=LIGHTS, ha=self.ha, clock=self.clock, **kw)

    def preview_service(self):
        fixture = json.loads((HERE / "fixtures" / "c12-preview-golden.json").read_text())
        profile = dict(fixture["profile"], axes=[{"v_max": 90, "a_max": 300, "j_max": 1200}] * 2)
        rows = {"F3": dict(profile=profile, start_joints_cdeg=[0, -3000], aim_origin_m=[1.8, 0.2, 2.5],
                           horizontal_world_reference=[0, 1, 0], registration_revision="synthetic",
                           real=True, services={"aim_move": "c12_fixture.aim_move", "stop": "c12_fixture.stop"})}
        self.ha = NoHa()
        s = self.service(fixtures=rows, preview=True)
        s.route_settings({"pre_aim": False, "shift_hold_s": 0})
        return s

    def preview_person(self, s, track="reader", epoch=1, seconds=0.25, capture=None):
        for _ in range(round(seconds * 4)):
            self.clock.t += 0.25
            s.observe({"tracks": [{"track_id": track, "state": "confirmed"}]},
                      {"session": epoch, "capture_ts": self.clock.t if capture is None else capture,
                       "poses": {track: seated_pose()}})

    def reading(self, s, track="reader", epoch=1):
        s.person_state({"track_id": track, "capture_epoch": epoch, "observed_at": self.clock.t,
                        "activity_valid_until": self.clock.t + 10,
                        "focused_engagement": {"state": "engaged", "kind": "reading"},
                        "field_support": {"focused_engagement": {
                            "support_status": "observed", "last_support_capture_ts": self.clock.t}}})

    def preview_row(self, s):
        return s.route_state()[1]["c12_preview"]["fixtures"]["F3"]

    def test_preview_is_local_and_stop_survives_restart(self):
        s = self.preview_service()
        s.on_ha(den_status())
        self.preview_person(s, seconds=3)
        self.reading(s)
        self.preview_person(s, seconds=2)
        self.assertGreater(self.preview_row(s)["spot_level"], 0)
        self.assertEqual(s.route_stop({})[0], 200)
        self.assertEqual(self.preview_row(s)["spot_level"], 0)
        restarted = self.preview_service()
        self.assertTrue(restarted.route_state()[1]["stopped"])
        self.assertNotEqual(s.preview.run_id, restarted.preview.run_id)
        self.assertEqual(restarted.route_switch({"on": False})[0], 200)
        self.assertEqual(restarted.route_switch({"on": True})[0], 200)
        self.assertTrue(restarted.route_state()[1]["stopped"])
        self.assertEqual(restarted.route_resume({})[0], 200)
        self.assertFalse(restarted.route_state()[1]["stopped"])
        self.assertEqual(restarted.route_desk({"on": True})[0], 409)
        self.assertEqual(self.preview_row(restarted)["spot_level"], 0)
        self.assertTrue(all(not row.get("sent") for row in self.lines("fixture_calls")))

    def test_preview_rebinds_at_same_aim_after_lapse_and_rejects_replacement(self):
        s = self.preview_service()
        self.preview_person(s, seconds=3)
        self.reading(s)
        self.preview_person(s, seconds=2)
        first = self.preview_row(s)["move_seq"]
        s.handle("tick")
        renew = self.lines("fixture_calls")[-1]
        self.assertEqual((renew["action"], renew["move_seq"]), ("keep_lit", first))
        self.preview_person(s, seconds=3.25)  # fresh posture with no renewal ticks
        self.assertTrue(any(row["action"] == "expired" for row in self.lines("fixture_calls")))
        self.preview_person(s, seconds=2)
        self.assertNotEqual(self.preview_row(s)["move_seq"], first)
        self.preview_person(s, track="replacement")
        self.assertEqual(self.preview_row(s)["spot_level"], 0)
        self.reading(s, epoch=2, track="replacement")
        self.assertEqual(self.preview_row(s)["spot_level"], 0)
        self.assertNotIn("replacement", s.coordinator.states)

    def test_preview_with_frozen_pose_does_not_light_on_late_arrival(self):
        s = self.preview_service()
        self.preview_person(s, seconds=3)
        self.reading(s)
        # Advance past both model arrival and the lease without sampling intermediate state.
        self.clock.t += 4
        self.assertEqual(self.preview_row(s)["spot_level"], 0)
        self.assertTrue(any(row["action"] == "expired" for row in self.lines("fixture_calls")))

    def test_preview_recovers_after_refusal_without_consuming_a_sequence(self):
        s = self.preview_service()
        self.preview_person(s, seconds=3)
        with patch.object(c12_fc13, "encode", side_effect=c12_fc13.Refusal("test_refusal")):
            self.reading(s)
        self.assertEqual(self.lines("fixture_calls")[-1]["status"], "refused")
        self.assertIsNone(self.preview_row(s)["move_seq"])
        self.preview_person(s, seconds=2)
        self.assertEqual(self.preview_row(s)["move_seq"], 0)
        self.assertGreater(self.preview_row(s)["spot_level"], 0)

    def test_preview_reports_incomplete_trace_when_the_append_fails(self):
        s = self.preview_service()
        self.preview_person(s, seconds=3)
        with patch.object(s.preview, "write", return_value=False):
            self.reading(s)
        self.assertFalse(s.route_state()[1]["c12_preview"]["trace_complete"])

    @unittest.skipUnless(os.environ.get("C12_TEST_FIRMWARE_ZIP"), "local pinned firmware ZIP not supplied")
    def test_preview_sender_trace_replays_with_pinned_firmware(self):
        s = self.preview_service()
        self.preview_person(s, seconds=3)
        self.reading(s)
        self.preview_person(s, seconds=2)
        s.handle("tick")
        displayed = self.preview_row(s)
        s.route_stop({})
        s.route_resume({})
        trace = self.lines("fixture_calls")
        move = trace[displayed["move_event_index"] - 1]
        self.assertEqual(move["frame"]["seq"], displayed["move_seq"])
        self.assertEqual(move["decision_id"], displayed["move_decision_id"])
        self.assertEqual(move["effective"]["spot_level"], displayed["spot_level"])
        end = s.route_state()[1]["c12_preview"]
        self.assertTrue(end["trace_complete"])
        self.assertEqual(end["event_index"], trace[-1]["event_index"])
        data = io.BytesIO("".join(json.dumps(row) + "\n" for row in trace).encode())
        codec, _ = c12_replay.firmware_codec(os.environ["C12_TEST_FIRMWARE_ZIP"])
        counts, groups = c12_replay.verify_trace(data, codec, collect_sim=True)
        self.assertGreaterEqual(counts["encoded_only"], 4)
        executable, digest = os.environ.get("C12_TEST_SIM"), os.environ.get("C12_TEST_SIM_SHA256")
        if executable and digest:
            self.assertEqual(c12_replay.replay_simulator(executable, digest, groups), counts["encoded_only"])

    def someone_seated(self, s):
        """One person, seated for 3 s: learning needs exactly one person in the den."""
        for _ in range(12):
            self.clock.t += 0.25
            s.observe({"tracks": [{"track_id": "e1-1", "state": "confirmed"}]},
                      {"capture_ts": self.clock.t - 0.5, "poses": {"e1-1": seated_pose()}})

    def lines(self, name):
        files = list((self.state / "days").glob("*/coordinator-%s.jsonl" % name))
        return [json.loads(line) for f in files for line in f.read_text(encoding="utf-8").splitlines()]

    def test_a_correction_is_learned_and_becomes_a_bulb_plan(self):
        s = self.service()
        s.on_ha(den_status())
        self.someone_seated(s)
        record = {"seq": 1, "entity_id": "light.lamp_a", "at": self.clock.t, "user_id": "owner",
                  "before": {"on": True, "brightness": 200, "kelvin": 2700}, "after": {"on": True, "brightness": 100, "kelvin": 2700}}
        s.on_ha(den_status(correction=record))
        plans = [c for c in self.ha.calls if c[:2] == ("den_lighting", "coordinator_plan")]
        self.assertEqual(plans[-1][2]["scenes"], {"light.lamp_a": {"on": True, "brightness": 150, "kelvin": 2700}})
        self.assertTrue((self.state / "coordinator" / "learned.json").exists())
        self.assertTrue(self.lines("corrections"))
        self.assertTrue(self.lines("plans"))
        s.on_ha(den_status(correction=record))   # the same record again: counted once
        self.assertEqual(s.counts["learned"], 1)

    def test_nothing_learned_sends_nothing(self):
        s = self.service()
        s.on_ha(den_status())
        self.assertFalse([c for c in self.ha.calls if c[0] == "den_lighting"])

    def test_the_observer_passes_the_event_through_and_feeds_people(self):
        s = self.service()
        event = {"seq": 7, "tracks": [{"track_id": "e1-3", "state": "confirmed"}, {"track_id": "e1-4", "state": "coasting"}]}
        frame = {"capture_ts": self.clock.t - 0.5, "poses": {"e1-3": seated_pose()}}
        self.assertIs(s.observe(event, frame), event)
        self.assertEqual(list(s.coordinator.tracks), ["e1-3"])
        self.assertIs(s.observe(event, None), event)   # an event with no frame feeds nothing

    def test_fixture_calls_are_written_down_and_only_real_fixtures_are_sent(self):
        rows = {"F2": {"aim_origin_m": [1.8, 0.2, 2.5], "horizontal_world_reference": [0, 1, 0]},
                "F3": {"aim_origin_m": [1.0, 0.3, 2.5], "horizontal_world_reference": [0, 1, 0], "real": True,
                       "status": "installed", "services": {"aim_move": "c12_fixture.aim_move"},
                       "service_target": {"entity_id": "light.fixture_f3_spot"}}}
        s = self.service(fixtures=rows)
        s.on_ha(den_status())
        s.handle("fixture", {"fixture_id": "F3", "status": "installed"})
        for k in range(16):
            self.clock.t += 0.25
            s.observe({"tracks": [{"track_id": "e1-1", "state": "confirmed"}]},
                      {"capture_ts": self.clock.t - 0.5, "poses": {"e1-1": seated_pose()}})
        calls = self.lines("fixture_calls")
        self.assertTrue(calls)
        self.assertTrue(all(c["sent"] is False for c in calls if c["fixture_id"] != "F3"))
        sent = [c for c in self.ha.calls if c[0] == "c12_fixture"]
        self.assertTrue(all(c[2]["entity_id"] == "light.fixture_f3_spot" for c in sent))
        self.assertTrue(all(not set(cl.LOG_ONLY) & set(c[2]) for c in sent), "log-only fields stay in the log")
        self.assertTrue(all("predicted_s" in c for c in calls if c["action"] == "aim_move"))
        self.assertEqual(len(sent), len([c for c in calls if c["fixture_id"] == "F3" and c["action"] == "aim_move"]))

    def test_routes(self):
        s = self.service()
        self.assertEqual(s.route_switch({"on": True})[0], 200)
        self.assertEqual(self.ha.calls[-1], ("switch", "turn_on", {"entity_id": cl.SWITCH_ENTITY}))
        self.assertEqual(s.route_switch({"on": "yes"})[0], 400)
        status, body = s.route_settings({"move_style": "dim", "pre_aim": False})
        self.assertEqual((status, body["settings"]["move_style"]), (200, "dim"))
        self.assertEqual(json.loads((self.state / "coordinator" / "settings.json").read_text())["pre_aim"], False)
        for bad in ({"move_style": "wiggle"}, {"pre_aim": 1}, {"fade_in_s": "slow"}, {"fade_in_s": -1}, {"colour": "red"}):
            self.assertEqual(s.route_settings(bad)[0], 400, bad)
        self.assertEqual(s.coordinator.settings["move_style"], "dim")   # a refused setting changes nothing
        self.assertEqual(s.route_desk({"on": True})[1]["until"], self.clock.t + cl.DESK_WINDOW_S)
        status, state = s.route_state()
        self.assertEqual((status, state["settings"]["move_style"], state["desk_until"]), (200, "dim", self.clock.t + cl.DESK_WINDOW_S))
        self.assertIn("home_assistant", state)

    def test_settings_survive_a_restart(self):
        self.service().route_settings({"fade_in_s": 2.0})
        self.assertEqual(self.service().coordinator.settings["fade_in_s"], 2.0)

    def test_clear_keeps_the_old_file_and_sends_an_empty_plan(self):
        s = self.service()
        s.on_ha(den_status())
        self.someone_seated(s)
        record = {"seq": 1, "entity_id": "light.lamp_a", "at": self.clock.t,
                  "before": {"on": True, "brightness": 200, "kelvin": 2700}, "after": {"on": True, "brightness": 100, "kelvin": 2700}}
        s.on_ha(den_status(correction=record))
        status, body = s.route_clear()
        self.assertEqual(status, 200)
        self.assertTrue((self.state / "coordinator" / body["kept_as"]).exists())
        s.on_ha(den_status(correction=record))
        plans = [c for c in self.ha.calls if c[:2] == ("den_lighting", "coordinator_plan")]
        self.assertEqual(plans[-1][2]["scenes"], {})

    def test_a_failed_send_is_counted_and_says_why_without_the_url(self):
        self.ha.fail = True
        s = self.service()
        self.assertEqual(s.route_switch({"on": True})[0], 502)
        self.assertEqual(s.counts["send_failed"], 1)
        self.assertNotIn("http", s.problem)


class RoutesOverHttpTest(unittest.TestCase):
    def setUp(self):
        self.out = stream.PeopleStream(port=0)
        self.out.routes.update({("GET", "/coordinator"): lambda _b: (200, {"ok": True}),
                                ("POST", "/coordinator/switch"): lambda b: (200, {"got": b}),
                                ("POST", "/coordinator/boom"): lambda b: 1 / 0})
        self.out.start()

    def tearDown(self):
        self.out.close()

    def request(self, method, path, body=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.out.port, timeout=5)
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        headers = {"Host": host or "127.0.0.1:%d" % self.out.port, "Content-Type": "application/json"}
        connection.request(method, "/api/den-live/v1/homes/den" + path, body=data, headers=headers)
        try:
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"null")
        finally:
            connection.close()

    def test_get_and_post_routes(self):
        self.assertEqual(self.request("GET", "/coordinator"), (200, {"ok": True}))
        self.assertEqual(self.request("POST", "/coordinator/switch", {"on": True}), (200, {"got": {"on": True}}))

    def test_refusals(self):
        self.assertEqual(self.request("POST", "/coordinator/switch", {"on": True}, host="evil:1")[0], 403)
        self.assertEqual(self.request("POST", "/nope", {})[0], 501)   # as before routes existed
        self.assertEqual(self.request("POST", "/coordinator/switch", b"{not json")[0], 400)
        self.assertEqual(self.request("POST", "/coordinator/boom", {})[1], {"ok": False, "error": "ZeroDivisionError"})
        self.assertEqual(self.request("GET", "/state")[0], 200)   # the stream's own paths are unchanged


class SettingsBlockTest(unittest.TestCase):
    FIXED = {"ha_url": "https://ha.example:8123", "token": "t0ken"}

    def test_the_block_and_its_refusals(self):
        ok, why = run_service.coordinator_settings({"coordinator": {"lights": LIGHTS, "utc_offset_h": -8}}, self.FIXED)
        self.assertIsNone(why)
        self.assertEqual((ok["lights"], ok["utc_offset_h"], ok["switch_entity"]), (LIGHTS, -8.0, cl.SWITCH_ENTITY))
        for block, fixed in (({}, self.FIXED), ({"lights": ["switch.x"]}, self.FIXED), ({"lights": LIGHTS}, None),
                             ({"lights": LIGHTS, "utc_offset_h": 99}, self.FIXED), ({"lights": LIGHTS * 2}, self.FIXED)):
            self.assertIsNone(run_service.coordinator_settings({"coordinator": block}, fixed)[0], block)
        self.assertIsNone(run_service.coordinator_settings({}, self.FIXED)[0])

    def test_preview_starter_needs_no_ha_credentials_or_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixtures.json"
            fixture = json.loads((HERE / "fixtures" / "c12-preview-golden.json").read_text())
            profile = dict(fixture["profile"], axes=[{"v_max": 90, "a_max": 300, "j_max": 1200}] * 2)
            path.write_text(json.dumps({"F3": dict(profile=profile, start_joints_cdeg=[0, 0],
                aim_origin_m=[0, 0, 2], horizontal_world_reference=[1, 0, 0], registration_revision="synthetic")}))
            settings, why = run_service.coordinator_settings(
                {"coordinator": {"preview": True, "fixtures_file": str(path)}}, None)
            self.assertIsNone(why)
            with patch.object(ha_link, "HaClient", side_effect=AssertionError("preview initialized HA")):
                start = run_service.coordinator_starter(settings, None, state=Path(tmp), tree=sc.archive(TREES, "coordinator"))
                service, close = start()
                try:
                    self.assertIsNotNone(service.preview)
                    self.assertIsNone(service.ha)
                finally:
                    close()


class ReleaseScanTest(unittest.TestCase):
    def test_the_coordinator_tree_is_pinned_and_passes_the_scan(self):
        self.assertIn("coordinator", rl.TREES)
        files = {p: v for p, v in release.tree_files().items() if v[1] == "coordinator"}
        self.assertIn("stack/services/lighting-brain/lighting_brain/aim.py", {v[2] for v in files.values()})
        texts, known = release.scan(files, [(rl.den_site(), "the den's site id")])
        self.assertGreater(texts, 10)
        self.assertEqual(known, 5)   # the lighting brain's den fixture ids, allowed for that blob only


if __name__ == "__main__":
    unittest.main()
