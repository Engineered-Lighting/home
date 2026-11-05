"""den-n2 tests: standard-library unittest on synthetic N1-format labels and need logs only.

    python -B -m unittest discover -s tools/den-n2 -v

Labels, fixtures and splits are built with den-label's own helpers (n1.py) in
temporary folders; need logs are written in den-core's den-replay-log-1 shape
with person_state/v1 records; den-core's check.py is replaced by a fake. No
real frame, label or log, no camera and no network.
"""
import builtins
import io
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "den-label"))
import n1  # noqa: E402
import score  # noqa: E402

BASE_UNIX = 1_793_016_000.0  # 2026-10-26 12:00 UTC
DAY = 86400.0
FPS = 4
PROTOCOL = n1.load_protocol()
GATE = score.load_gate()
FROZEN = BASE_UNIX + 30 * DAY
NEED = {"id": "need", "module": "need", "version": "need-v1-a7", "code_sha256": "a" * 64, "config": {},
        "config_sha256": "b" * 64}
LEVEL = {"fine": 0.8, "casual": 0.5, "none": 0.2}
FACTS = {"fine": ("reading", "book"), "casual": ("other", "other"), "none": ("resting", "none")}
OK_CHECK = lambda path: {"ok": True, "reason": "fake check: OK"}  # noqa: E731


def filled(**changes):
    """A GATE-V1 with every slot filled, as the development step would leave it."""
    values = dict(GATE.values)
    values.update({"scope": "qwen_only", "prompt": {"version": "lab-live-scene-prompt-1", "sha256": "c" * 64},
                   "pacing_s": 4, "onset_mode": "gated", "facts_map": {}, "confidence_table": {}, "rule_changes": {},
                   "floor.competence.fine": 0.6, "floor.competence.casual": 0.6, "floor.competence.none": 0.6,
                   "floor.fine_recall": 0.6, "floor.coverage": 0.6, "cap.early_off": 0.1, "cap.false_on": 0.1,
                   "detector": None, "s_dev": 1.0, "d_dev": 1.0, "look_test": {"jpeg_quality": 90},
                   "stand_ins": {"protocol_sha256": PROTOCOL.sha256, "pad": {"fraction": 0.2, "min_px": 16},
                                 "need": {"code_sha256": NEED["code_sha256"],
                                          "config_sha256": NEED["config_sha256"]}}})
    values.update(changes)
    return score.Gate("GATE-V1.md", "d" * 64, values)


def gate_text(gate):
    body = "".join(f"{name} = {json.dumps(value)}\n" for name, value in gate.values.items())
    return f"# GATE-V1 (test copy)\n\n```gate-v1\n{body}```\n"


# ---- in-memory sessions ---------------------------------------------------------------------

def iv(on, off, band="fine", case="sorting_mail", seg=0, onset_kind="touch", offset_kind="release", unsure=()):
    return {"on": on, "off": off, "band": band, "case": case, "seg": seg, "onset_kind": onset_kind,
            "offset_kind": offset_kind, "sure": {end: end not in unsure for end in ("onset", "offset", "band")}}


def span(kind, start, end, case=None, seg=0):
    return {"kind": kind, "case": case, "start": start, "end": end, "seg": seg}


def rec(t, state, level=None, conf=0.95, cap=None):
    return score.Rec(t, state, conf, level, None if level is None else conf, None if level is None else
                     (t - 1 if cap is None else cap))


def follower(intervals, a, b, level_of=None, delay=1.0):
    """A synthetic need v1 that follows the gold: starting delay s after each onset, engaged 5 s later,
    ending 1 s after the offset, none 20 s after that, with a fresh level (capture 1 s before)."""
    level_of = level_of or (lambda band: LEVEL[band])

    def at(t):
        for x in intervals:
            if x["on"] + delay <= t < x["off"] + 1:
                return ("starting" if t < x["on"] + delay + 5 else "engaged"), level_of(x["band"])
        for x in intervals:
            if x["off"] + 1 <= t < x["off"] + 21:
                return "ending", LEVEL["none"]
        return "none", LEVEL["none"]
    times = {round(a + 1 + 2 * i, 6) for i in range(int((b - a) / 2))}
    for x in intervals:
        times |= {x["on"] + delay, x["on"] + delay + 5, x["off"] + 1, x["off"] + 21}
    return [rec(t, *at(t)) for t in sorted(t for t in times if a <= t < b)]


def session(intervals, spans=(), records=None, segments=((0.0, 400.0),), take="t1", day=0, asks=()):
    s = score.Session(take, "development", BASE_UNIX + day * DAY, n1.local_date(BASE_UNIX + day * DAY),
                      list(segments), list(intervals), list(spans), list(asks))
    s.logs = [records if records is not None else follower([x for x in intervals if x["seg"] == j], a, b)
              for j, (a, b) in enumerate(segments)]
    return s


def ask(r, row="fine", segment=1, S=1.0, outcome="replied", people="one", status="valid"):
    activity, held = FACTS[row] if row in FACTS else row
    return {"slot": int(r), "segment": segment, "r": r, "frames": [{"n": 1, "t_s": r - 3}, {"n": 2, "t_s": r - 1.5},
                                                                  {"n": 3, "t_s": r}],
            "outcome": outcome, "S": S if outcome == "replied" else None, "attempts": 1, "asked_unix": FROZEN + 10,
            "receipt": {"status": status, "prompt_version": "lab-live-scene-prompt-1", "engine_epoch": "e1",
                        "facts": {"people_visible": people, "activity_hypothesis": activity, "held_object": held}}
            if outcome == "replied" else None}


def analysed(*args, gate=GATE, pacing=4, **kwargs):
    return score.analyse(session(*args, **kwargs), gate, pacing)


def stats(an, gate=GATE):
    return score.session_stats(an, gate)


class GateTests(unittest.TestCase):
    def test_committed_gate_has_every_slot_empty(self):
        self.assertEqual(GATE.empty_slots(), list(score.SLOTS))
        for name in score.OWNER_SLOTS:
            self.assertIsNone(GATE[name])
        self.assertEqual((GATE["cap.false_fine"], GATE["interval.rounds"], GATE["grace_s"]), (0.033, 10000, 6))

    def test_gate_reads_and_hashes_the_same_with_crlf(self):
        with tempfile.TemporaryDirectory() as tmp:
            crlf = Path(tmp) / "GATE-V1.md"
            crlf.write_bytes(score.GATE_FILE.read_bytes().replace(b"\n", b"\r\n"))
            again = score.load_gate(crlf)
        self.assertEqual((again.values, again.sha256), (GATE.values, GATE.sha256))

    def test_held_out_refuses_while_any_slot_is_null(self):
        with self.assertRaisesRegex(score.Refused, "empty slots.*scope.*floor.coverage.*look_test"):
            score.check_slots(GATE)
        score.check_slots(filled())  # detector may stay null under qwen_only
        with self.assertRaisesRegex(score.Refused, "floor.coverage"):
            score.check_slots(filled(**{"floor.coverage": None}))
        with self.assertRaisesRegex(score.Refused, "detector"):
            score.check_slots(filled(scope="stand_in"))
        with self.assertRaisesRegex(score.Refused, "onset_mode"):
            score.check_slots(filled(onset_mode="bound"))

    def test_a_gate_with_a_missing_or_null_fixed_value_is_refused(self):
        with self.assertRaisesRegex(score.Refused, "missing"):
            score.parse_gate(gate_text(filled()).replace("grace_s = 6\n", ""))
        with self.assertRaisesRegex(score.Refused, "fixed value"):
            score.parse_gate(gate_text(filled()).replace("grace_s = 6\n", "grace_s = null\n"))


class GridAndWindowTests(unittest.TestCase):
    def test_only_whole_seconds_in_a_segment_outside_uncertain_spans_are_scored(self):
        an = analysed([], [span("uncertain", 4.1, 5.2)], segments=((0.3, 10.2),))
        self.assertEqual([s.k for s in an.seconds], [1, 2, 3, 6, 7, 8, 9])
        self.assertEqual(an.excluded["uncertain_s"], 1.1)

    def test_tau_on_grace_and_tau_off_windows(self):
        intervals = [iv(10, 40), iv(70, 90, band="casual", case="wiping")]
        an = analysed(intervals, [span("quiet", 41, 69, "idle_seated"), span("quiet", 91, 200, "idle_standing")])
        st = stats(an)
        self.assertEqual(st["ratio"]["early_off"], [0, 27 + 17])  # x >= onset + 3 s, from 10 and from 70
        self.assertEqual(st["ratio"]["fine_recall"], [24, 24])  # x >= onset + 6 s
        # quiet seconds count only past tau_off: 91..149.5 lie within 60 s of the offset at 90
        self.assertEqual(st["ratio"]["false_on"], [0, 50])
        # the second interval starts inside the first one's tau_off: its seconds belong to it
        self.assertEqual(sum(1 for s in an.seconds if s.iv is intervals[1] and s.w_off), 0)
        # false-fine: casual seconds past the grace, plus none seconds outside every window
        none = sum(1 for s in an.seconds if s.iv is None and not s.w_off)
        self.assertEqual(st["ratio"]["false_fine"], [0, 14 + none])

    def test_a_fine_level_left_on_past_tau_off_is_false_fine(self):
        records = [rec(0.5, "none", 0.2), rec(11, "starting", 0.8), rec(16, "engaged", 0.8)]  # never falls
        an = analysed([iv(10, 40)], records=records, segments=((0.0, 130.0),))
        st = stats(an)
        late = [s for s in an.seconds if s.iv is None and not s.w_off]
        self.assertEqual(st["ratio"]["false_fine"], [sum(1 for s in late if s.x > 100), len(late)])
        self.assertGreater(st["ratio"]["false_fine"][0], 0)
        # the level is long stale there: it still counts for false-fine
        self.assertFalse(any(s.fresh for s in late if s.x > 100))

    def test_low_light_is_reported_apart_and_pooled_in_the_gate(self):
        an = analysed([iv(10, 40)], [span("low_light", 0, 25)])
        low, rest = score.session_stats(an, GATE, True), score.session_stats(an, GATE, False)
        both = stats(an)
        for name in score.PER_SECOND:
            self.assertEqual([low["ratio"][name][i] + rest["ratio"][name][i] for i in (0, 1)], both["ratio"][name])
        self.assertEqual(low["ratio"]["fine_recall"][1], 9)


class MarkAndCarriedTests(unittest.TestCase):
    def test_unsure_onset_is_not_timed_but_stays_a_unit(self):
        an = analysed([iv(10, 40, unsure=("onset",))])
        self.assertEqual((len(an.onsets), len(an.units)), (0, 1))

    def test_unsure_offset_leaves_early_off_only(self):
        an = analysed([iv(10, 40, unsure=("offset",))])
        self.assertEqual(stats(an)["ratio"]["early_off"], [0, 0])
        self.assertEqual((len(an.onsets), len(an.units), stats(an)["ratio"]["fine_recall"][1]), (1, 1, 24))

    def test_unsure_band_leaves_every_demand_measure_and_is_not_a_unit(self):
        an = analysed([iv(10, 40, unsure=("band",))])
        st = stats(an)
        self.assertEqual((len(an.units), st["ratio"]["fine_recall"], st["ratio"]["early_off"][1]), (0, [0, 0], 27))
        self.assertEqual(len(an.onsets), 0)  # timed onsets are units' onsets

    def test_no_touch_onsets_are_reported_apart(self):
        an = analysed([iv(10, 40, onset_kind="no_touch")])
        self.assertEqual((len(an.onsets), len(an.no_touch), an.no_touch[0]["tag"]), (0, 1, "entered"))

    def test_carried_piece_counts_its_seconds_and_takes_its_windows_from_the_segment_start(self):
        carried = iv(0.9, 30, onset_kind="carried")
        records = [rec(float(t), "engaged", 0.8) for t in range(0, 100, 2)]
        an = analysed([carried], records=records, segments=((0.0, 100.0),))
        self.assertEqual((an.units, an.onsets, an.excluded["carried_pieces"]), ([], [], 1))
        st = stats(an)
        # tau_on from the segment start (0 to 3 s), not from 0.9: x = 3.5 is counted
        self.assertIn(3, [s.k for s in an.seconds if s.iv is carried and not s.w_on])
        self.assertEqual(st["ratio"]["early_off"], [0, 27])
        self.assertEqual(st["ratio"]["fine_recall"], [24, 24])

    def test_a_carried_offset_has_no_tau_off_window(self):
        an = analysed([iv(10, 20, offset_kind="carried")], records=[rec(0.5, "none", 0.2)])
        self.assertFalse(any(s.w_off for s in an.seconds))
        an = analysed([iv(10, 20)], records=[rec(0.5, "none", 0.2)])
        self.assertEqual(sum(1 for s in an.seconds if s.w_off), 60)


class OnsetTests(unittest.TestCase):
    def onset(self, records, intervals=None, prev=True):
        intervals = intervals or ([iv(10, 40), iv(80, 110, case="still_reader")] if prev else [iv(80, 110)])
        an = analysed(intervals, records=records)
        return an, an.onsets[-1]

    def test_entered_times_the_first_starting(self):
        records = [rec(0.5, "none", 0.2), rec(83.5, "starting", 0.8)]
        an = analysed([iv(81, 110)], records=records)
        o = an.onsets[0]
        self.assertEqual((o["tag"], o["d_on"], o["w"]), ("entered", 2.5, 3.0))
        _, o = self.onset(records, prev=False)
        self.assertEqual((o["d_on"], o["w"]), (3.5, 0.0))  # a touch at a request time waits 0 s

    def test_entered_on_when_the_run_began_after_the_previous_offset(self):
        records = [rec(11, "starting", 0.8), rec(45, "ending", 0.2), rec(65, "none", 0.2),
                   rec(78, "starting", 0.8)]
        _, o = self.onset(records)
        self.assertEqual((o["tag"], o["d_on"]), ("entered_on", 0.0))

    def test_stayed_on_is_a_miss(self):
        an, o = self.onset([rec(11, "starting", 0.8), rec(16, "engaged", 0.8), rec(60, "engaged", 0.5)])
        self.assertEqual((o["tag"], o["d_on"]), ("stayed_on", math.inf))
        self.assertEqual(an.missed_intervals, 0)  # it was on, so early_off counts from its onset
        self.assertEqual(stats(an)["ratio"]["early_off"], [0, 27 + 27])

    def test_entered_from_ending(self):
        records = [rec(11, "starting", 0.8), rec(16, "engaged", 0.8), rec(41, "ending", 0.2),
                   rec(83, "engaged", 0.8)]
        _, o = self.onset(records)
        self.assertEqual((o["tag"], o["d_on"]), ("entered_from_ending", 3.0))

    def test_missed_interval_puts_every_scored_second_in_early_off(self):
        an, o = self.onset([rec(0.5, "none", 0.2), rec(111, "starting", 0.8)], prev=False)
        self.assertEqual((o["tag"], o["d_on"], an.missed_intervals), ("missed", math.inf, 1))
        self.assertEqual(stats(an)["ratio"]["early_off"], [27, 27])

    def test_a_late_start_is_onsets_error_not_early_offs(self):
        an, o = self.onset([rec(0.5, "none", 0.2), rec(90, "starting", 0.8)], prev=False)
        self.assertEqual(o["d_on"], 10)
        self.assertEqual(stats(an)["ratio"]["early_off"], [0, 20])  # counted from 90, the first on output

    def test_onset_median_counts_a_miss_as_infinite(self):
        self.assertEqual(score.median([1.0, 2.0, math.inf]), 2.0)
        self.assertEqual(score.median([1.0, math.inf]), math.inf)

    def test_d_dev_and_the_pacing_rule(self):
        onsets = [{"d_on": 3.0, "w": 2.0, "tag": "entered", "S": 1.0, "m": 0}] * 3
        self.assertEqual(score.onset_rule(onsets, 4, 3)["D"], 1.0)
        self.assertTrue(score.onset_rule(onsets, 4, 3)["fits"])
        slow = [{"d_on": 6.5, "w": 2.0, "tag": "entered", "S": 0.5, "m": 1}] * 3
        rule = score.onset_rule(slow, 4, 3)
        self.assertEqual((rule["D"], rule["fits"], rule["largest_fitting_P"]), (4.5, False, 1.5))
        missed = slow + [{"d_on": math.inf, "w": 1.0, "tag": "stayed_on", "S": None, "m": None}] * 4
        self.assertIsNone(score.onset_rule(missed, 4, 3)["largest_fitting_P"])

    def test_m_comes_from_the_request_whose_reply_made_the_output(self):
        asks = [ask(84.0, S=1.0), ask(88.0, S=1.5)]
        an = analysed([iv(83, 110)], records=[rec(0.5, "none", 0.2), rec(89.5, "starting", 0.8)], asks=asks)
        o = an.onsets[0]
        self.assertEqual((o["w"], o["d_on"], o["S"], o["m"]), (1.0, 6.5, 1.5, 1))


class RowTests(unittest.TestCase):
    def test_rows(self):
        self.assertEqual(score.row_of(ask(10), 10), "reading/book")
        self.assertEqual(score.row_of(ask(10, people="multiple"), 10), "unclear")
        self.assertEqual(score.row_of(ask(10, row=("unclear", "book")), 10), "unclear")
        self.assertEqual(score.row_of(ask(10, status="invalid"), 10), "invalid")
        self.assertEqual(score.row_of(ask(10, S=10.0), 10), "stale")
        self.assertEqual(score.row_of(ask(10, outcome="no_box"), 10), "no_box")
        self.assertEqual(score.row_of(ask(10, outcome="failed"), 10), "failed")

    def test_case_by_row_table_and_the_prompt2_trigger(self):
        intervals = [iv(10, 40), iv(110, 140, band="casual", case="wiping")]
        asks = [ask(r, row="casual") for r in range(12, 40, 4)] + [ask(r, row="casual") for r in range(112, 140, 4)]
        an = analysed(intervals, asks=asks + [ask(36.0, people="multiple")])
        table, trigger = score.row_table([an])
        self.assertEqual(table[0]["rows"], {"other/other": 7})
        self.assertEqual(table[0]["apart"], {"unclear": 1})
        self.assertEqual(trigger["pairs"], [{"fine": "sorting_mail", "other": "wiping", "other_band": "casual",
                                             "rows": ["other/other"]}])
        tie = {("still_reader", "fine"): {"reading/book": 3, "other/other": 3},
               ("idle_seated", "none"): {"other/other": 5}}
        self.assertTrue(score.prompt2_trigger(tie)["fires"])
        apart = {("still_reader", "fine"): {"reading/book": 3, "unclear": 9},
                 ("idle_seated", "none"): {"resting/none": 5, "unclear": 9}}
        self.assertFalse(score.prompt2_trigger(apart)["fires"])


class BoundAndVerdictTests(unittest.TestCase):
    def test_clopper_pearson(self):
        for (k, n), (lo, hi) in {(0, 10): (0.0, 0.308497), (5, 10): (0.187086, 0.812914),
                                 (10, 10): (0.691503, 1.0), (32, 32): (0.891119, 1.0)}.items():
            got = score.clopper_pearson(k, n)
            self.assertAlmostEqual(got[0], lo, places=5)
            self.assertAlmostEqual(got[1], hi, places=5)
        self.assertIsNone(score.clopper_pearson(0, 0))

    def test_met_failed_straddled(self):
        self.assertEqual(score.judge("floor", 0.6, 0.7, [0.6, 0.8]), "met")
        self.assertEqual(score.judge("floor", 0.6, 0.5, [0.4, 0.59]), "failed")
        self.assertEqual(score.judge("floor", 0.6, 0.6, [0.5, 0.7]), "straddled")
        self.assertEqual(score.judge("cap", 0.1, 0.05, [0.0, 0.1]), "met")
        self.assertEqual(score.judge("cap", 0.1, 0.2, [0.11, 0.3]), "failed")
        self.assertEqual(score.judge("cap", 0.1, 0.1, [0.05, 0.15]), "straddled")
        self.assertEqual(score.judge("cap", 3, math.inf, [2.0, math.inf]), "straddled")
        self.assertEqual(score.judge("floor", None, 0.5, [0.4, 0.6]), "unset")
        self.assertEqual(score.judge("floor", 0.6, None, None), "no_data")

    def test_bootstrap_is_seeded_from_the_labels_hash(self):
        parts = [{"ratio": {"early_off": [k, 10]}} for k in (0, 1, 3, 6)]
        value = score.m_ratio("early_off")
        one = score.bootstrap(parts, value, score.resamples(4, 2000, "ab" * 32), 0.95)
        self.assertEqual(one, score.bootstrap(parts, value, score.resamples(4, 2000, "ab" * 32), 0.95))
        self.assertLessEqual(one[0], 0.25)
        self.assertGreaterEqual(one[1], 0.25)

    def test_ece(self):
        bins = [[0, 0.0, 0.0] for _ in range(10)]
        bins[9] = [4, 3.8, 3.0]  # four units at 0.95, three right
        bins[5] = [2, 1.1, 2.0]  # two at 0.55, both right
        self.assertAlmostEqual(score.m_ece("demand")([{"ece": {"demand": bins}}]), (0.8 + 0.9) / 6)

    def test_verdict_precedence(self):
        def metric(status):
            return {"threshold": {"kind": "floor", "value": 0.6}, "status": status}
        met = {"fine_recall": metric("met"), "coverage": metric("met"), "dwell": {"threshold": None}}
        ok_gate, unmet_gate = [{"name": "units.fine", "met": True}], [{"name": "units.fine", "met": False}]
        no_case = {"wiping": {"status": "not_failed"}}
        self.assertEqual(score.verdict(met, no_case, ok_gate, True)[0], "feasible")
        failed = dict(met, coverage=metric("failed"))
        self.assertEqual(score.verdict(failed, no_case, unmet_gate, True)[0], "insufficient")
        self.assertEqual(score.verdict(failed, no_case, ok_gate, True)[0], "not_feasible")
        self.assertEqual(score.verdict(met, {"wiping": {"status": "failed"}}, ok_gate, True)[0], "not_feasible")
        straddled = dict(met, coverage=metric("straddled"))
        kind, reasons = score.verdict(straddled, no_case, ok_gate, True)
        self.assertEqual((kind, reasons[0]), ("insufficient", "rule 4: a gated metric straddled"))
        onset = dict(met, onset={"threshold": {"kind": "cap", "value": 3}, "status": "failed"})
        self.assertEqual(score.verdict(onset, no_case, ok_gate, False)[0], "feasible")  # report-only onset


# ---- synthetic frozen N1 folders, ask lines and need logs ----------------------------------------------

LAYOUT_END = 1420.0


def layout():
    """One staged session's gold: a quiet start, 8 fine and 8 casual 30 s tasks 40 s apart, a quiet end."""
    intervals = []
    for i in range(16):
        on = 80.0 + 70 * i
        band = "fine" if i % 2 == 0 else "casual"
        case = (("sorting_mail", "still_reader") if band == "fine" else ("wiping", "phone_at_counter"))[i // 2 % 2]
        intervals.append((on, on + 30, band, case))
    return intervals, [("quiet", 2.0, 60.0, "idle_seated"), ("quiet", 1230.0, 1400.0, "idle_standing")]


class World:
    """Frozen fixture-v1 and labels-v1 under a temporary N1 folder, the FREEZES text, and an N2 folder."""

    def __init__(self, root, takes, level_of=None):
        self.root = Path(root)
        self.n1, self.n2 = self.root / "n1", self.root / "n2"
        self.takes = takes  # [(take, script, day)], in recording order
        with mock.patch.object(n1, "now", lambda: BASE_UNIX):
            entries = n1.register(self.root / "split.json",
                                  [(take, script, BASE_UNIX + day * DAY) for take, script, day in takes])
        self.split = {e["session"]: e["split"] for e in entries}
        frames = [{"n": n, "t": (n - 1) / FPS, "t_s": repr((n - 1) / FPS)} for n in range(1, int(LAYOUT_END * FPS) + 1)]
        t_of = {f["n"]: f["t"] for f in frames}
        nth = lambda t: int(round(t * FPS)) + 1  # noqa: E731
        golds, spans_of = layout()
        intervals = [{"onset_n": nth(a), "onset_kind": "touch", "offset_n": nth(b), "offset_kind": "release",
                      "band": band, "case": case, "sure": {"onset": True, "offset": True, "band": True},
                      "by": "owner"} for a, b, band, case in golds]
        spans = [{"kind": kind, "case": case, "start_n": nth(a), "end_n": nth(b), "by": "owner"}
                 for kind, a, b, case in spans_of]
        segments = [(1, len(frames))]
        checked, checked_spans = n1.check_labels(intervals, spans, PROTOCOL, segments, t_of)
        pad = {"fraction": 0.2, "min_px": 16}
        boxes = n1.rows_csv(n1.fixture_rows([[1, [200.0, 60.0, 320.0, 340.0]]], frames, t_of, pad))
        fixture_files, label_files, fx_sessions, lb_sessions = {}, {}, [], []
        for take, script, day in takes:
            check_sha = n1.sha256_bytes(take.encode())
            fixture_files[f"boxes/{take}.csv"] = boxes
            fx_sessions.append({"take": take, "check": "check-1.json", "check_sha256": check_sha,
                                "frames_csv_sha256": "e" * 64, "segments": [[1, len(frames), 0.0, LAYOUT_END]],
                                "frames": len(frames), "boxes": {"file": f"boxes/{take}.csv",
                                                                 "sha256": n1.sha256_bytes(boxes)}})
            record = {"schema": "den-n1-session-labels-1", "take": take, "check": "check-1.json",
                      "check_sha256": check_sha, "split": self.split[take], "protocol_sha256": PROTOCOL.sha256,
                      "actor": {"by": "owner"}, "intervals": checked, "spans": checked_spans}
            label_files[f"sessions/{take}.json"] = n1.canonical(record)
            lb_sessions.append({"take": take, "split": self.split[take], "check": "check-1.json",
                                "check_sha256": check_sha, "file": f"sessions/{take}.json",
                                "sha256": n1.sha256_bytes(label_files[f"sessions/{take}.json"])})
        fixture_files["surface-map.json"] = n1.canonical({"schema": "den-n1-surface-map-1", "surfaces": []})
        fixture = {"schema": "den-n1-fixture-1", "version": 1, "frozen_unix": FROZEN - 100,
                   "protocol": {"file": PROTOCOL.file, "sha256": PROTOCOL.sha256}, "tools": {}, "person_id": "p1",
                   "image": {"width": 640, "height": 360}, "padding": dict(pad, rule="test"),
                   "surface_map": {"file": "surface-map.json",
                                   "sha256": n1.sha256_bytes(fixture_files["surface-map.json"])},
                   "sessions": fx_sessions}
        fixture_files["fixture.json"] = n1.canonical(fixture)
        label_files["split.json"] = (self.root / "split.json").read_bytes()
        label_files["remark.json"] = n1.canonical({"schema": "den-n1-remark-1", "take": takes[0][0]})
        labels = {"schema": "den-n1-labels-1", "version": 1, "frozen_unix": FROZEN,
                  "protocol": {"file": PROTOCOL.file, "sha256": PROTOCOL.sha256}, "tools": {},
                  "phone_at_counter_band": PROTOCOL.phone_band,
                  "split": {"file": "split.json", "sha256": n1.sha256_bytes(label_files["split.json"])},
                  "remark": {"file": "remark.json", "sha256": n1.sha256_bytes(label_files["remark.json"]),
                             "take": takes[0][0]},
                  "sessions": lb_sessions, "counts": {}, "n_intervals": len(golds) * len(takes)}
        label_files["labels.json"] = n1.canonical(labels)
        for name, files in (("fixture-v1", fixture_files), ("labels-v1", label_files)):
            for rel, data in files.items():
                path = self.n1 / name / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        self.freezes = self.root / "FREEZES.md"
        self.freezes.write_text(
            f"den-n1 fixture-v1 sha256={n1.sha256_bytes(fixture_files['fixture.json'])} protocol=x frozen=x\n"
            f"den-n1 labels-v1 sha256={n1.sha256_bytes(label_files['labels.json'])} protocol=x frozen=x\n",
            encoding="utf-8")
        gold_ivs = [iv(a, b, band, case) for a, b, band, case in golds]
        for take, _, _ in takes:
            self.write_need(take, gold_ivs, level_of)
        self.gate = self.root / "GATE-V1.md"
        self.gate.write_text(gate_text(filled()), encoding="utf-8")

    def write_need(self, take, gold_ivs, level_of=None, records=None):
        started = BASE_UNIX + dict((t, d) for t, _, d in self.takes)[take] * DAY
        t0 = math.floor(started)
        lines = []
        for r in range(4, int(LAYOUT_END), 4):
            band = next((x["band"] for x in gold_ivs if x["on"] <= r <= x["off"]), "none")
            lines.append(ask(float(r), row=band))
        self.write_jsonl(self.n2 / "asks" / f"{take}.jsonl", lines)
        records = records or follower(gold_ivs, 0.0, LAYOUT_END,
                                      (lambda band: LEVEL[band]) if level_of is None else level_of)
        events = [{"schema": "den-event-1", "t": t0 + r.t, "seq": i, "kind": "person_state", "source": "need",
                   "data": person_state(t0 + r.t, r, t0)} for i, r in enumerate(records)]
        header = {"schema": "den-replay-log-1", "sources": ["n2_asks"], "policies": [NEED], "tick_s": 2.0,
                  "tail_s": 150.0, "plant": False, "shown": "incumbent", "synthetic": True,
                  "assume_occupied": False, "actuation": "synthetic"}
        self.write_jsonl(self.n2 / "replay" / take / "seg-1" / "log.jsonl", [header] + events)

    @staticmethod
    def write_jsonl(path, values):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes("".join(json.dumps(v, sort_keys=True, separators=(",", ":")) + "\n"
                                 for v in values).encode("ascii"))

    def run(self, split="development", **kwargs):
        options = dict(gate_path=self.gate, freezes_path=self.freezes, checker=OK_CHECK)
        options.update(kwargs)
        return score.run(split, self.n1, self.n2, **options)

    def held_out(self):
        return [take for take, split in self.split.items() if split == "held_out"]


def person_state(t, r, t0):
    return {"schema_version": "person_state/v1", "state_id": "room@%.3f" % t, "emitted_ts": t, "track_id": "room",
            "identity": "unbound",
            "focused_engagement": {"state": r.state, "onset_ts": None, "confidence": r.eng_conf, "kind": "reading",
                                   "since_s": None},
            "visual_demand": {"level": r.level, "basis": None, "confidence": r.dem_conf}, "posture": "unknown",
            "field_support": {"visual_demand": {"last_support_capture_ts": None if r.cap is None else r.cap + t0,
                                                "value_set_ts": t, "support_status": "observed"}},
            "sources": {"focused_engagement": []}, "interpreter": {"path": "rules", "version": "need-v1-a7"},
            "flags": []}


FOUR_DAYS = [(f"take-{s}{i}", script, day) for i, (s, script, day) in
             enumerate([("a", "hard-a", 0), ("b", "hard-b", 1), ("a", "hard-a", 2), ("b", "hard-b", 3),
                        ("a", "hard-a", 4), ("b", "hard-b", 5), ("a", "hard-a", 6), ("b", "hard-b", 7)])]


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="den-n2-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def world(self, takes=FOUR_DAYS, level_of=None):
        return World(self.tmp, takes, level_of)

    def assertRefused(self, world, pattern, split="development", **kwargs):
        before = sorted((world.n2 / "eval").glob("*")) if (world.n2 / "eval").exists() else []
        with self.assertRaisesRegex(score.Refused, pattern):
            world.run(split, **kwargs)
        after = sorted((world.n2 / "eval").glob("*")) if (world.n2 / "eval").exists() else []
        self.assertEqual(before, after, "a refusal wrote a report")

    def test_development_never_opens_held_out_labels(self):
        world = self.world()
        held = world.held_out()
        self.assertEqual(len(held), 4)
        seen, real = [], io.open

        def spy(file, *args, **kwargs):
            seen.append(Path(str(file)))
            return real(file, *args, **kwargs)
        with mock.patch.object(io, "open", spy), mock.patch.object(builtins, "open", spy):
            report, _ = world.run("development")
        development = sorted(set(world.split) - set(held))
        for take in development:  # the spy sees every file it reads, so the next check means something
            self.assertIn(world.n1 / "labels-v1" / "sessions" / f"{take}.json", seen)
            self.assertIn(world.n1 / "fixture-v1" / "boxes" / f"{take}.csv", seen)
        touched = [p for p in seen for take in held if take in (p.stem, *p.parts)]
        self.assertEqual(touched, [])
        self.assertEqual(sorted(s["take"] for s in report["sessions"]), sorted(set(world.split) - set(held)))
        # with every held-out label file gone, development still scores; the held-out run does not
        for take in held:
            (world.n1 / "labels-v1" / "sessions" / f"{take}.json").unlink()
        world.run("development")
        self.assertRefused(world, "missing sessions/", "held_out")

    def test_a_perfect_follower_is_feasible(self):
        world = self.world()
        report, path = world.run("held_out")
        self.assertEqual(report["verdict"], "feasible", report["reasons"])
        self.assertEqual({g["name"]: g["n"] for g in report["sample_gates"]}["units.fine"], 32)
        self.assertEqual(report["metrics"]["onset"]["value"], 1.0)
        self.assertEqual(report["metrics"]["fine_recall"]["value"], 1.0)
        self.assertEqual(report["cases"]["sorting_mail"]["status"], "not_failed")
        self.assertEqual(json.loads(path.read_bytes()), report)
        self.assertEqual(path.read_bytes(), n1.canonical(report))
        line = score.results_line(report, path)
        self.assertRegex(line, r"^den-n2 held_out verdict=feasible eval=[0-9a-f]{64} gate=[0-9a-f]{64} "
                               r"labels=labels-v1/[0-9a-f]{64} scored=\d{4}-\d\d-\d\d sessions=4s/4d "
                               r"units=fine32/casual32 onsets=64$")

    def test_a_follower_that_never_reads_fine_is_not_feasible(self):
        world = self.world(level_of=lambda band: LEVEL["casual"])
        report, _ = world.run("held_out")
        self.assertEqual(report["verdict"], "not_feasible")
        self.assertIn("fine_recall failed", report["reasons"])
        self.assertIn("case sorting_mail failed", report["reasons"])
        self.assertEqual(report["diagnosis"]["engagement"], [])
        self.assertIn("fine_recall", report["diagnosis"]["demand"])

    def test_too_few_held_out_sessions_is_insufficient(self):
        world = self.world(takes=FOUR_DAYS[:4])
        report, _ = world.run("held_out")
        self.assertEqual(report["verdict"], "insufficient")
        self.assertIn("sample gate units.fine unmet", report["reasons"])

    def test_a_straddled_floor_is_insufficient(self):
        world = self.world()
        world.gate.write_text(gate_text(filled(**{"floor.competence.fine": 0.95})), encoding="utf-8")
        report, _ = world.run("held_out")
        self.assertEqual((report["verdict"], report["reasons"][1]), ("insufficient", "competence.fine straddled"))

    def test_development_reports_without_a_verdict(self):
        world = self.world()
        world.gate = score.GATE_FILE  # the committed gate, every slot empty
        report, path = world.run("development")
        self.assertIsNone(report["verdict"])
        self.assertEqual(report["metrics"]["fine_recall"]["status"], "unset")
        self.assertEqual(report["metrics"]["false_fine"]["status"], "met")
        self.assertEqual(report["onset_rule"]["D"], report["service"]["D"])
        self.assertTrue(report["rows"]["table"])
        self.assertEqual(report["gate"]["sha256"], GATE.sha256)
        self.assertIsNotNone(report["gate"]["commit"])
        self.assertEqual(path.parent, world.n2 / "eval")

    def test_refusals(self):
        world = self.world(takes=FOUR_DAYS[:4])
        self.assertRefused(world, "empty slots", "held_out", gate_path=score.GATE_FILE)
        self.assertRefused(world, "check.py fails", checker=lambda p: {"ok": False, "reason": "line 3 differs"})
        self.assertRefused(world, "check.py is not in", checker=None, den_core=self.tmp)
        held = world.held_out()[0]
        good = world.gate.read_text(encoding="utf-8")
        world.gate.write_text(gate_text(filled(stand_ins=dict(filled()["stand_ins"],
                                                              need={"code_sha256": "f" * 64,
                                                                    "config_sha256": "b" * 64}))), encoding="utf-8")
        self.assertRefused(world, "stand-in differs.*need's code", "held_out")
        world.gate.write_text(good, encoding="utf-8")
        asks = world.n2 / "asks" / f"{held}.jsonl"
        saved = asks.read_bytes()
        lines = [json.loads(x) for x in saved.decode().splitlines()]
        lines[5]["asked_unix"] = FROZEN - 1
        World.write_jsonl(asks, lines)
        self.assertRefused(world, "before the labels freeze", "held_out")
        asks.write_bytes(saved)
        log = world.n2 / "replay" / held / "seg-1" / "log.jsonl"
        log.rename(log.with_name("moved.jsonl"))
        self.assertRefused(world, "no replay log", "held_out")
        log.with_name("moved.jsonl").rename(log)
        with self.assertRaisesRegex(score.Refused, "inside a git checkout"):
            score.run("development", world.n1, HERE, gate_path=world.gate, freezes_path=world.freezes,
                      checker=OK_CHECK)
        world.run("held_out")  # every input restored: it scores

    def test_labels_must_match_a_freeze_line_and_the_protocol(self):
        world = self.world(takes=FOUR_DAYS[:2])
        text = world.freezes.read_text(encoding="utf-8")
        world.freezes.write_text(text.splitlines()[0] + "\n", encoding="utf-8")
        self.assertRefused(world, "no labels freeze line")
        labels_sha = n1.file_sha256(world.n1 / "labels-v1" / "labels.json")
        world.freezes.write_text(text.replace(labels_sha, "0" * 64), encoding="utf-8")
        self.assertRefused(world, "matches no freeze line")
        world.freezes.write_text(text, encoding="utf-8")
        other = n1.Protocol(PROTOCOL.file, "0" * 64, PROTOCOL.settings)
        self.assertRefused(world, "frozen under protocol", protocol=other)

    def test_den_core_check_loads_under_its_own_name(self):
        fake = self.tmp / "den-core"
        fake.mkdir()
        (fake / "check.py").write_text("def check(path):\n    return {'ok': True, 'reason': 'den-core'}\n",
                                       encoding="utf-8")
        self.assertEqual(score.load_checker(fake)("x")["reason"], "den-core")
        self.assertTrue(hasattr(sys.modules["check"], "read_bundle"))  # den-take's check is untouched

    def test_cli(self):
        world = self.world(takes=FOUR_DAYS[:2])
        out = io.StringIO()
        code = score.main(["--split", "development", "--n1", str(world.n1), "--n2", str(world.n2), "--gate",
                           str(world.gate)], out=out, checker=OK_CHECK, freezes_path=world.freezes)
        self.assertEqual(code, score.EXIT_DONE, out.getvalue())
        self.assertIn("Prompt-2 trigger:", out.getvalue())
        self.assertIn("Onset rule: D_dev 0.5", out.getvalue())
        out = io.StringIO()
        code = score.main(["--split", "held_out", "--n1", str(world.n1), "--n2", str(world.n2)], out=out,
                          checker=OK_CHECK, freezes_path=world.freezes)
        self.assertEqual(code, score.EXIT_REFUSED)
        self.assertIn("Refused, nothing written: GATE-V1 has empty slots", out.getvalue())


if __name__ == "__main__":
    unittest.main()
