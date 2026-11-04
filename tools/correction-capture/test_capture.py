"""correction-capture tests, on synthetic exports only. From the worktree root:
python -B -m unittest discover -s tools/correction-capture -p "test_*.py"
"""
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import capture  # noqa: E402
import events  # noqa: E402  (den-core, put on the path by capture)
import sources  # noqa: E402

L0, L1, L2, L3 = events.LIGHTS
STATUS = "sensor.den_lighting_control"
USER = "0F" * 16  # a recorder user id, as hex
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()  # 19:00 in Victoria on Oct 28
WARM = {"on": True, "brightness": 100, "kelvin": 2700}
DIM = {"on": True, "brightness": 40, "kelvin": 2700}
OFF = {"on": False, "brightness": None, "kelvin": None}


class Den:
    """A synthetic den_lighting history, written in the three recorder export formats."""

    def __init__(self, start=T0, **now):
        self.status, self.bulbs, self.commands = [], [], []
        self.revision, self.n = 100, 0
        self.now = {"automatic": True, "game": False, "occupancy": "on", "holds": {}, "provisional": []}
        self.row(start, **now)

    def context(self):
        self.n += 1
        return "%032X" % (0x0190000000000000000000000000 + self.n)

    def row(self, t, revision=None, **change):
        self.now.update(change)
        self.revision = self.revision + 1 if revision is None else revision
        n = self.now
        attrs = {"automatic": n["automatic"], "gaming_enabled": True, "gaming_session": n["game"],
                 "gaming_signal": "playing" if n["game"] else "idle", "occupancy": n["occupancy"],
                 "revision": self.revision, "error": None, "gaming_entity": "sensor.pc_gaming",
                 "holds": {e: {"until": "game_ends" if n["game"] else "room_vacant_2_minutes", "target": scene}
                           for e, scene in n["holds"].items()},
                 "occupancy_source": "raw_person_count", "pending_fixtures": [],
                 "provisional_fixtures": list(n["provisional"]), "friendly_name": "Den lighting control",
                 "icon": "mdi:lightbulb-auto-outline"}
        self.status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})

    def unavailable(self, t):
        self.status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "unavailable",
                            "attributes": {"restored": True, "friendly_name": "Den lighting control"}})

    def hold(self, t, scenes, provisional=()):
        self.row(t, holds={**self.now["holds"], **scenes},
                 provisional=sorted(set(self.now["provisional"]) | set(provisional)))

    def settle(self, t, *lights):
        """den_lighting's read-back: provisional holds on lights end; listed lights stay held."""
        holds = {e: s for e, s in self.now["holds"].items() if e not in self.now["provisional"] or e in lights}
        self.row(t, holds=holds, provisional=[])

    def bulb(self, t, e, on, context=None, user=False, parent=None):
        context = context or self.context()
        self.bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off",
                           "context_id": context, "context_user_id": USER if user else "",
                           "context_parent_id": parent or ""})
        return context

    def command(self, t, e, target):
        """den_lighting's light call: its own state row during the call, then the event when the call returns."""
        context = self.bulb(t - 0.3, e, target["on"])
        data = {"entity_id": e, "revision": self.revision, "target": target, "context_id": sources.ulid(context)}
        self.commands.append({"time_fired_ts": t, "event_data": data})
        return context

    def write(self, folder):
        paths = []
        for name, rows in (("status", self.status), ("lights", self.bulbs), ("commands", self.commands)):
            path = Path(folder) / (name + ".jsonl")
            path.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            paths.append([path])
        return paths


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cph-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_den(self, den, **kwargs):
        record, replay = capture.capture(*den.write(self.tmp), **kwargs)
        return record, replay

    def strata(self, record):
        return record["planning"]["strata"]


def evening():
    """One hour: den_lighting's fade, the app, a switch, den_lighting's own write, an automation, a rowless hold."""
    den = Den()
    # den_lighting fades L0 on: a context-less report mid-fade is a provisional hold, ended by read-back.
    den.command(T0 + 100, L0, WARM)
    den.bulb(T0 + 101, L0, True)
    den.hold(T0 + 101.05, {L0: DIM}, provisional=[L0])
    den.settle(T0 + 105)
    # The HA app: the hold opens at the call, the bulb row carries the user.
    den.hold(T0 + 200, {L1: WARM})
    den.bulb(T0 + 200.3, L1, True, user=True)
    # A switch or dimmer: a new context, no ids, then the hold.
    den.bulb(T0 + 300, L2, False)
    den.hold(T0 + 300.01, {L2: OFF})
    # den_lighting's own write, taken for a touch: only its own row is near.
    den.command(T0 + 399.9, L3, WARM)
    den.hold(T0 + 400.05, {L3: DIM})
    # Another automation: its run is the parent context.
    den.hold(T0 + 500, {L0: OFF})
    den.bulb(T0 + 500.2, L0, False, parent="AB" * 16)
    # A held target that changes with no bulb row at all (a call that left the bulb as it was).
    den.hold(T0 + 600, {L3: WARM})
    den.row(T0 + 3600)
    return den


class WhoMadeIt(Case):

    def test_app_switch_automation_and_den_lightings_own(self):
        record, _ = self.run_den(evening())
        by_entity = {c["changes"][0]["entity_id"]: c for c in record["corrections"]}
        self.assertEqual({e: c["source"] for e, c in by_entity.items()},
                         {L1: "app", L2: "switch_or_dimmer", L0: "automation", L3: "unknown"})
        self.assertEqual(by_entity[L1]["ha_context"]["user_id"], "present")
        self.assertIsNone(by_entity[L2]["ha_context"]["user_id"])
        skipped = [(c["entity_id"], c["reason"], c["source"]) for c in record["not_counted"]]
        self.assertEqual(skipped, [(L0, "own_fade_report", None), (L3, "own_fade_report", "den_lighting")])
        no_game = self.strata(record)["no_game"]
        self.assertEqual((no_game["people"]["count"], no_game["every_hold"]["count"]), (2, 4))
        self.assertEqual(no_game["occupied_h"], 1.0)
        self.assertEqual(no_game["people"]["per_occupied_h"], 2.0)
        self.assertEqual(record["exposure"]["corrections_total"], 2)

    def test_before_and_after(self):
        record, _ = self.run_den(evening())
        changes = {c["changes"][0]["entity_id"]: c["changes"][0] for c in record["corrections"]}
        self.assertEqual((changes[L3]["before"], changes[L3]["before_from"], changes[L3]["after"]),
                         (DIM, "hold", WARM))
        self.assertEqual((changes[L0]["before"], changes[L0]["before_from"]), (WARM, "den_lighting_command"))
        self.assertEqual((changes[L1]["before"], changes[L1]["before_from"]), (None, None))
        self.assertEqual(changes[L1]["hold"], "opened")

    def test_a_switch_against_den_lightings_fade_is_a_person(self):
        den = Den()
        den.command(T0 + 100, L0, WARM)
        den.bulb(T0 + 101, L0, True)  # its fade report
        den.hold(T0 + 101.05, {L0: DIM}, provisional=[L0])
        den.bulb(T0 + 103, L0, False)  # a hand turns it off mid-fade: still provisional
        den.hold(T0 + 103.01, {L0: OFF}, provisional=[L0])
        den.settle(T0 + 108, L0)  # read-back finds it off, not WARM: a real hold
        den.row(T0 + 3600)
        record, _ = self.run_den(den)
        [c] = record["corrections"]
        self.assertEqual((c["source"], c["changes"][0]["hold"]), ("switch_or_dimmer", "confirmed"))
        self.assertEqual([x["reason"] for x in record["not_counted"]], ["own_fade_report"])

    def test_a_fade_alone_counts_nothing(self):
        den = Den()
        den.command(T0 + 100, L0, DIM)
        den.bulb(T0 + 100.8, L0, True)  # level reports on the way down, with new contexts
        den.hold(T0 + 100.85, {L0: {"on": True, "brightness": 70, "kelvin": 2700}}, provisional=[L0])
        den.bulb(T0 + 101.6, L0, True)
        den.hold(T0 + 101.65, {L0: {"on": True, "brightness": 50, "kelvin": 2700}}, provisional=[L0])
        den.settle(T0 + 105)  # read-back finds DIM: the provisional hold ends
        # A late report of its own after read-back, which den_lighting took for a touch.
        den.command(T0 + 200, L1, WARM)
        den.bulb(T0 + 207, L1, True)
        den.hold(T0 + 207.01, {L1: WARM})
        den.row(T0 + 3600)
        record, _ = self.run_den(den)
        self.assertEqual(record["corrections"], [])
        self.assertEqual([(c["entity_id"], c["source"]) for c in record["not_counted"]],
                         [(L0, None), (L1, "den_lighting")])
        self.assertEqual(record["planning"]["not_counted_by_reason"], {"own_fade_report": 2})


class Actions(Case):
    def test_one_context_across_two_bulbs_is_one_action(self):
        den = Den()
        den.hold(T0 + 100, {L0: WARM, L1: WARM})
        app = den.context()
        den.bulb(T0 + 100.2, L0, True, app, user=True)
        den.bulb(T0 + 100.4, L1, True, app, user=True)
        # A scene from the app whose reports on two bulbs come 5.5 s apart: still one context.
        scene = den.context()
        den.hold(T0 + 200, {L2: DIM})
        den.bulb(T0 + 200.1, L2, True, scene, user=True)
        den.hold(T0 + 205.5, {L3: DIM})
        den.bulb(T0 + 205.6, L3, True, scene, user=True)
        den.row(T0 + 3600)
        record, _ = self.run_den(den)
        self.assertEqual([(c["action"]["grouping"], c["action"]["n_changes"]) for c in record["corrections"]],
                         [("one HA context", 2), ("one HA context", 2)])
        self.assertEqual(record["corrections"][0]["ha_context"]["user_id"], "present")
        self.assertEqual(self.strata(record)["no_game"]["people"]["count"], 2)

    def test_switch_reports_within_settle_are_one_action(self):
        den = Den()
        den.bulb(T0 + 100, L0, False)
        den.hold(T0 + 100.01, {L0: OFF})
        den.bulb(T0 + 101.5, L1, False)
        den.hold(T0 + 101.51, {L1: OFF})
        den.bulb(T0 + 200, L2, False)
        den.hold(T0 + 200.01, {L2: OFF})
        den.bulb(T0 + 210, L2, True)
        den.hold(T0 + 210.01, {L2: WARM})
        den.row(T0 + 3600)
        record, _ = self.run_den(den)
        self.assertEqual([(c["action"]["grouping"], c["action"]["n_changes"]) for c in record["corrections"]],
                         [("within 3 s", 2), ("one change", 1), ("one change", 1)])
        self.assertEqual(record["corrections"][2]["changes"][0]["hold"], "replaced")


class NotCounted(Case):
    def test_restart_restored_hold_is_not_counted(self):
        for marker in ("unavailable", "revision"):
            with self.subTest(marker=marker):
                den = Den()
                den.bulb(T0 + 100, L0, False)
                den.hold(T0 + 100.01, {L0: OFF})
                den.row(T0 + 900)
                if marker == "unavailable":
                    den.unavailable(T0 + 1000)
                den.row(T0 + 1060, revision=2)  # restored from storage after the restart
                den.row(T0 + 2860)
                folder = self.tmp / marker
                folder.mkdir()
                record, _ = capture.capture(*den.write(folder))
                self.assertEqual(len(record["corrections"]), 1)
                self.assertEqual([(c["entity_id"], c["reason"]) for c in record["not_counted"]],
                                 [(L0, "restored_after_restart")])
                self.assertEqual(record["span"]["restarts"], 1)
                self.assertEqual(self.strata(record)["no_game"]["occupied_h"], 0.75)
                self.assertEqual(record["planning"]["restart_gap_h"], round(160 / 3600, 4))

    def test_a_fade_cut_by_a_restart_is_not_confirmed_later(self):
        den = Den()
        den.command(T0 + 100, L0, WARM)
        den.bulb(T0 + 101, L0, True)
        den.hold(T0 + 101.05, {L0: DIM}, provisional=[L0])
        den.row(T0 + 160, revision=1, provisional=[])  # HA restarted mid-fade; the hold came back from storage
        den.row(T0 + 400)
        den.row(T0 + 3600)
        record, _ = self.run_den(den)
        self.assertEqual(record["corrections"], [])
        self.assertEqual([c["reason"] for c in record["not_counted"]], ["own_fade_report", "restored_after_restart"])

    def test_held_at_export_start_and_outside_exposure(self):
        den = Den(holds={L3: DIM})
        den.row(T0 + 600, occupancy="off")
        den.bulb(T0 + 700, L0, True)
        den.hold(T0 + 700.01, {L0: WARM})
        den.row(T0 + 800, occupancy="on")
        den.row(T0 + 1000, automatic=False)
        den.bulb(T0 + 1100, L1, True)
        den.hold(T0 + 1100.01, {L1: WARM})
        den.row(T0 + 1300, automatic=True)
        record, replay = self.run_den(den)
        self.assertEqual(record["corrections"], [])
        self.assertEqual([(c["entity_id"], c["reason"]) for c in record["not_counted"]],
                         [(L3, "held_at_export_start"), (L0, "outside_exposure"), (L1, "outside_exposure")])
        self.assertEqual(record["not_counted"][1]["at"]["occupancy"], "off")
        self.assertEqual(self.strata(record)["no_game"]["occupied_h"], round(800 / 3600, 4))
        # A replay still holds all three bulbs, as the live controller did.
        self.assertEqual([e["data"]["entity_id"] for e in replay], [L3, L0, L1])


class Exposure(Case):
    def test_game_and_no_game_split(self):
        den = Den()
        den.bulb(T0 + 1800, L0, False)
        den.hold(T0 + 1800.01, {L0: OFF})
        den.row(T0 + 3600, game=True, holds={})
        den.hold(T0 + 5400, {L1: DIM})
        den.bulb(T0 + 5400.2, L1, True, user=True)
        den.row(T0 + 7200, game=False, holds={})
        den.row(T0 + 10800)
        record, _ = self.run_den(den)
        strata = self.strata(record)
        self.assertEqual((strata["no_game"]["occupied_h"], strata["game"]["occupied_h"]), (2.0, 1.0))
        self.assertEqual((strata["no_game"]["people"]["count"], strata["game"]["people"]["count"]), (1, 1))
        self.assertEqual([c["mode_stratum"] for c in record["corrections"]], ["no_game", "game"])
        self.assertEqual(record["exposure"]["per_session"], [
            {"session_id": "2026-10-28/no_game", "occupied_h": 2.0, "occupancy_source": "den_lighting",
             "mode_stratum": "no_game", "corrections": 1},
            {"session_id": "2026-10-28/game", "occupied_h": 1.0, "occupancy_source": "den_lighting",
             "mode_stratum": "game", "corrections": 1}])
        self.assertEqual(record["exposure"]["occupied_h_total"], 3.0)
        self.assertEqual(strata["no_game"]["occupied_h_by_occupancy_source"], {"raw_person_count": 2.0})

    def test_blocks_split_at_20_utc(self):
        start = datetime(2026, 10, 29, 19, 0, tzinfo=timezone.utc).timestamp()
        den = Den(start)
        den.row(start + 7200)
        record, _ = self.run_den(den)
        self.assertEqual([(s["session_id"], s["occupied_h"]) for s in record["exposure"]["per_session"]],
                         [("2026-10-28/no_game", 1.0), ("2026-10-29/no_game", 1.0)])

    def test_exposure_block_matches_eval_record_field_for_field(self):
        record, _ = self.run_den(evening())
        exposure = record["exposure"]
        self.assertEqual(set(exposure), {"per_session", "occupied_h_total", "corrections_total", "phi_hat",
                                         "phi_hat_status"})
        for session in exposure["per_session"]:
            self.assertEqual(set(session), {"session_id", "occupied_h", "occupancy_source", "mode_stratum",
                                            "corrections"})
        self.assertIsNone(exposure["phi_hat"])

    def test_phi_hat(self):
        sessions = [{"occupied_h": 1.0, "mode_stratum": "no_game", "corrections": c} for c in (0, 2)]
        self.assertEqual(capture.phi_hat(sessions)[0], 2.0)
        self.assertIsNone(capture.phi_hat(sessions[:1])[0])


class Gate(unittest.TestCase):
    def setUp(self):
        self.constants = capture.load_rule()[0]["constants"]

    def test_garwood_matches_published_values(self):
        for k, (lo, hi) in {0: (0.0, 3.6889), 1: (0.0253, 5.5716), 5: (1.6235, 11.6683),
                            10: (4.7954, 18.3904), 50: (37.1110, 65.9188)}.items():
            got = capture.garwood(k)
            self.assertAlmostEqual(got[0], lo, places=3)
            self.assertAlmostEqual(got[1], hi, places=3)

    def test_each_verdict_boundary(self):
        c = self.constants
        self.assertEqual(capture.verdict(19.999, 500, 500, c), "insufficient")
        self.assertNotEqual(capture.verdict(20.0, 500, 500, c), "insufficient")
        self.assertEqual(capture.decide(20.0, 300.0, 300.0, c), "within_budget")
        self.assertEqual(capture.decide(20.0, 300.0001, 300.0, c), "undetermined")
        self.assertEqual(capture.decide(20.0, None, 300.0, c), "undetermined")
        self.assertEqual(capture.decide(20.0, None, 300.0001, c), "beyond_budget")

    def test_verdicts_from_counts(self):
        c = self.constants
        self.assertEqual(capture.verdict(100, 0, 10, c), "beyond_budget")  # 148 / (18.39 / 100) = 805 h
        self.assertEqual(capture.verdict(100, 80, 80, c), "within_budget")  # 148 / (63.4 / 100) = 233 h
        self.assertEqual(capture.verdict(100, 40, 60, c), "undetermined")
        self.assertEqual(capture.verdict(20, 0, 0, c), "beyond_budget")  # 148 / (3.69 / 20) = 802 h

    def test_planning_numbers(self):
        r = capture._rate(30, 60.0, 14.0, self.constants)
        self.assertEqual(r["per_occupied_h"], 0.5)
        self.assertEqual(r["hours_per_arm"], 296.0)
        self.assertEqual(r["weeks_one_den"], round(2 * 296 / 30, 4))  # 30 occupied h a week
        self.assertIsNone(capture._rate(0, 60.0, 14.0, self.constants)["hours_per_arm"])

    def test_rule_is_pinned(self):
        rule, digest = capture.load_rule()
        self.assertEqual((rule["id"], digest), ("cph-budget-v0", capture.RULE_SHA256))
        with tempfile.TemporaryDirectory() as tmp:
            edited = Path(tmp) / "cph-budget-v0.json"
            rule["constants"]["max_hours_per_arm"] = 400
            edited.write_text(json.dumps(rule), "utf-8")
            with self.assertRaises(ValueError):
                capture.load_rule(edited)


class Output(Case):
    def test_writes_outside_checkouts_without_user_ids(self):
        den = evening()
        out = self.tmp / "out"
        with redirect_stdout(io.StringIO()) as printed:
            [status], [lights], [commands] = den.write(self.tmp)
            capture.main(["--status", str(status), "--lights", str(lights), "--commands", str(commands),
                          "--out", str(out), "--events"])
        self.assertIn("verdict: insufficient", printed.getvalue())
        text = (out / "exposure.json").read_text("ascii")
        self.assertNotIn(sources.ulid(USER), text)
        self.assertNotIn(USER, text)
        record = json.loads(text)
        self.assertEqual((record["schema"], record["rule"]["sha256"]), ("den-exposure-1", capture.RULE_SHA256))
        lines = (out / "corrections.jsonl").read_text("ascii").splitlines()
        self.assertEqual(len(lines), 4)
        for line in lines:
            event = json.loads(line)
            events.validate(event)
            self.assertEqual(event["kind"], "correction")
        with self.assertRaises(ValueError):
            capture.write(out, record)  # never overwrites
        with self.assertRaises(ValueError):
            capture.write(HERE / "out", record)  # never inside a checkout

    def test_overlapping_exports_count_once(self):
        den = evening()
        status, lights, commands = den.write(self.tmp)
        once, _ = capture.capture(status, lights, commands)
        twice, _ = capture.capture(status * 2, lights * 2, commands * 2)
        self.assertEqual(once["corrections"], twice["corrections"])
        self.assertEqual(once["exposure"], twice["exposure"])

    def test_bad_rows_name_their_line(self):
        den = evening()
        status, lights, commands = den.write(self.tmp)
        with open(status[0], "a", encoding="ascii") as handle:
            handle.write('{"last_updated_ts": 1, "entity_id": "x", "state": "on"}\n')
        with self.assertRaisesRegex(ValueError, "line %d is not a status" % (len(den.status) + 1)):
            capture.capture(status, lights, commands)

    def test_installed_files(self):
        checkout = capture.CHECKOUT_DEN_LIGHTING
        sums = self.tmp / "installed.sha256"
        lines = ["%s  %s" % (events.sha256_file(p), p.name) for p in sorted(checkout.iterdir()) if p.is_file()]
        sums.write_text("\n".join(lines) + "\n", "ascii")
        result = capture.installed_files(sums)
        self.assertTrue(result["matches_checkout"])
        self.assertEqual(result["checkout_version"], "1.0.4")
        sums.write_text("\n".join(lines[1:] + ["0" * 64 + "  policy.py"]) + "\n", "ascii")
        result = capture.installed_files(sums)
        self.assertFalse(result["matches_checkout"])
        self.assertIn("policy.py", result["differs"])


if __name__ == "__main__":
    unittest.main()
