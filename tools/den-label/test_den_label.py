"""den-label tests: standard-library unittest on synthetic takes only.

    python -B -m unittest discover -s tools/den-label -v

Takes are built with R's own test helpers (make_take, bundle, nv12) and checked
with R's check.py. No real frame, no camera, and no network beyond 127.0.0.1.
Staged-script fixtures (a staged script name and a started_unix) are written by
the helpers here, so tools/den-take/ is not changed before its own step.
"""
import contextlib
import http.client
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DEN_TAKE = HERE.parent / "den-take"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(DEN_TAKE))
import check  # noqa: E402
import label  # noqa: E402
import n1  # noqa: E402
import take  # noqa: E402
import test_den_take as R  # noqa: E402  R's synthetic helpers; its tests are not collected here

BASE_UNIX = 1_793_016_000.0  # a fixed recording time, 2026-10-26 12:00 UTC
DAY = 86400.0
PROTOCOL = n1.load_protocol()
_TEMPLATES = {}


def setUpModule():
    global _guard
    _guard = mock.patch.object(take.http.client, "HTTPConnection", R.LoopbackOnly)
    _guard.start()


def tearDownModule():
    _guard.stop()
    for folder in _TEMPLATES.values():
        shutil.rmtree(folder.parent, ignore_errors=True)


# ---- synthetic staged takes ------------------------------------------------------------

def template(activity=60.0, commissioning=False):
    """One make_take() take per shape, built once per run and copied for each test."""
    key = (activity, commissioning)
    if key not in _TEMPLATES:
        _TEMPLATES[key] = R.make_take(tempfile.mkdtemp(prefix="den-label-template-"), activity=activity,
                                      commissioning=commissioning)
    return _TEMPLATES[key]


def set_take_fields(folder, script, started_unix):
    """The staged-script fixture: what take.py will write once its --script change lands."""
    record = json.loads((folder / "take.json").read_text(encoding="utf-8"))
    record["script"], record["started_unix"] = script, started_unix
    take.write_json(folder / "take.json", record)


def staged_take(takes, name, script="hard-a", started_unix=BASE_UNIX, typed="none", activity=60.0):
    folder = Path(takes) / name
    shutil.copytree(template(activity), folder)
    set_take_fields(folder, script, started_unix)
    if typed is not None:
        recheck(folder, typed)
    return folder


def commissioning_take(takes, name="c1"):
    folder = Path(takes) / name
    shutil.copytree(template(30.0, commissioning=True), folder)
    recheck(folder, "none")
    return folder


def recheck(folder, typed):
    code, report, path = R.run_check(folder, typed=typed)
    return report


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="den-label-test-")).resolve()
        self.addCleanup(self.cleanup)
        self.takes = self.tmp / "takes"
        self.takes.mkdir()
        self.n1root = self.takes / "n1"

    def cleanup(self):
        for path in self.tmp.rglob("*"):
            with contextlib.suppress(OSError):
                os.chmod(path, stat.S_IRWXU)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ws(self, protocol=None):
        return n1.Workspace(self.takes, self.n1root, protocol=protocol)

    def usable(self, ws, take_id):
        state = ws.state(take_id, allow_commissioning=True)
        return n1.usable_frames(state, n1.read_frames(state.folder))

    def frame_at(self, ws, take_id, t):
        return next(f for f in self.usable(ws, take_id) if f["t"] >= t)

    def boxes_done(self, ws, take_id, box=(100, 50, 300, 340)):
        p = label.Pass(ws, "boxes", take_id, out=io.StringIO())
        frames = p.usable
        p.save({"keyframes": [[frames[0]["n"], list(box)]], "audited": [[frames[0]["n"], frames[-1]["n"]]]})
        return p

    def interval(self, ws, take_id, t0, t1, band="fine", case="sorting_mail", **extra):
        a, b = self.frame_at(ws, take_id, t0), self.frame_at(ws, take_id, t1)
        item = {"onset_n": a["n"], "onset_kind": "touch", "offset_n": b["n"], "offset_kind": "release",
                "band": band, "case": case, "sure": {"onset": True, "offset": True, "band": True}, "by": "owner"}
        item.update(extra)
        return item

    def span(self, ws, take_id, t0, t1, kind="quiet", case="idle_seated"):
        item = {"kind": kind, "start_n": self.frame_at(ws, take_id, t0)["n"],
                "end_n": self.frame_at(ws, take_id, t1)["n"], "by": "owner"}
        if kind == "quiet":
            item["case"] = case
        return item

    def labels_done(self, ws, take_id, intervals=None, spans=None, end_to_end=True, mode="labels"):
        p = label.Pass(ws, mode, take_id, out=io.StringIO())
        if intervals is None:
            intervals = [self.interval(ws, take_id, 40, 50), self.interval(ws, take_id, 60, 66, "casual", "wiping")]
        if spans is None:
            spans = [self.span(ws, take_id, 51, 58)]
        p.save({"intervals": intervals, "spans": spans, "end_to_end": end_to_end})
        return p

    def surfaces_done(self, ws, take_id):
        p = label.Pass(ws, "surfaces", take_id, out=io.StringIO())
        p.save({"reference_n": p.usable[0]["n"], "surfaces": [
            {"id": "desk", "type": "desk", "height_cm": 74, "polygon": [[10, 200], [200, 200], [200, 300]]},
            {"id": "handheld", "type": "handheld", "polygon": None, "height_cm": {"seated": 85, "standing": 110}}]})


class Server:
    """One pass served on 127.0.0.1 in a thread, with a plain http.client to talk to it."""

    def __init__(self, p):
        self.p = p
        self.server, self.token = label.make_server(p)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def request(self, method, path, body=None, headers=None, token=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sent = {"X-N1-Token": self.token} if token is True else ({"X-N1-Token": token} if token else {})
        sent.update(headers or {})
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            sent["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=data, headers=sent)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def run_main(argv, drive=None, timeout=60):
    """label.main in a thread; drive(url) plays the page's part once the server is up."""
    out, result, urls = io.StringIO(), {}, []
    ready = threading.Event()

    def on_ready(url):
        urls.append(url)
        ready.set()

    def target():
        result["code"] = label.main(argv + ["--no-browser"] if drive else argv, out=out, ready=on_ready)
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    if drive:
        while thread.is_alive() and not ready.wait(0.05):
            pass
        if urls:
            drive(Client(urls[0]))
    thread.join(timeout)
    if thread.is_alive():
        raise AssertionError("label.py did not finish:\n" + out.getvalue())
    return result.get("code"), out.getvalue()


class Client:
    def __init__(self, url):
        parts = re.fullmatch(r"http://127\.0\.0\.1:([0-9]+)/\?t=(.+)", url)
        self.port, self.token = int(parts[1]), parts[2]

    def call(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"X-N1-Token": self.token}
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if response.getheader("Content-Type") == "application/json" \
                else raw
        finally:
            connection.close()

    def state(self):
        return self.call("GET", "/api/state")[1]

    def save(self, body):
        status, reply = self.call("POST", "/api/save", body)
        if status != 200:
            raise AssertionError(f"save refused: {reply}")
        return reply

    def stop(self, reason="done"):
        return self.call("POST", "/api/stop", {"reason": reason})


# ---- tests --------------------------------------------------------------------------

class ProtocolTests(unittest.TestCase):
    def test_settings_come_from_the_one_block(self):
        self.assertEqual(set(PROTOCOL.settings), set(n1.PROTOCOL_KEYS))
        self.assertEqual(PROTOCOL.settings["phone_at_counter_band"], "casual")
        self.assertEqual((PROTOCOL.settings["pause_s"], PROTOCOL.settings["remark_days"],
                          PROTOCOL.settings["tau_off_s"]), (6, 3, 60))
        self.assertEqual(PROTOCOL.sha256, n1.file_sha256(HERE / "PROTOCOL-V1.md"))

    def test_phone_band_none_moves_the_phone_to_quiet_cases(self):
        text = (HERE / "PROTOCOL-V1.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "PROTOCOL-V1.md"
            copy.write_text(text.replace('phone_at_counter_band = "casual"', 'phone_at_counter_band = "none"'),
                            encoding="utf-8", newline="\n")
            none = n1.load_protocol(copy)
            self.assertNotEqual(none.sha256, PROTOCOL.sha256)
            self.assertNotIn("phone_at_counter", none.interval_cases)
            self.assertIn("phone_at_counter", none.quiet_cases)
            self.assertIn("tidying", none.interval_cases)
            copy.write_text(text + "\n```\nextra = 1\n```\n", encoding="utf-8", newline="\n")
            with self.assertRaises(ValueError):
                n1.load_protocol(copy)
            copy.write_text(text.replace('"casual"\n', '"sometimes"\n', 1), encoding="utf-8", newline="\n")
            with self.assertRaises(ValueError):
                n1.load_protocol(copy)


class SessionTests(Base):
    def test_only_retained_staged_takes_with_a_newest_pass_register(self):
        staged_take(self.takes, "a1", "hard-a", BASE_UNIX)
        commissioning_take(self.takes, "c1")
        free = staged_take(self.takes, "f1", "free", BASE_UNIX + 10)
        later = staged_take(self.takes, "b1", "hard-b", BASE_UNIX + 20, typed=None)
        record = json.loads((later / "take.json").read_text(encoding="utf-8"))
        record["complete"] = False
        take.write_json(later / "take.json", record)
        self.assertEqual(recheck(later, "none")["verdict"], "FAIL")
        newest_fail = staged_take(self.takes, "a2", "hard-a", BASE_UNIX + 30)
        record = json.loads((newest_fail / "take.json").read_text(encoding="utf-8"))
        record["complete"] = False
        take.write_json(newest_fail / "take.json", record)
        self.assertEqual(recheck(newest_fail, "none")["verdict"], "FAIL")  # check-1 PASS, check-2 FAIL
        ws = self.ws()
        added, notes = ws.scan()
        self.assertEqual([e["session"] for e in added], ["a1"])
        self.assertTrue(any(note.startswith("a2: not registered") for note in notes))
        self.assertNotIn("f1", json.dumps(notes) + json.dumps(added))
        self.assertTrue(free.exists())
        record["complete"] = True  # a later PASS check makes a2 retained
        take.write_json(newest_fail / "take.json", record)
        record = json.loads((later / "take.json").read_text(encoding="utf-8"))
        record["complete"] = True
        take.write_json(later / "take.json", record)
        recheck(newest_fail, "none")
        recheck(later, "none")
        added, _ = ws.scan()
        self.assertEqual([(e["session"], e["split"]) for e in added], [("b1", "development"), ("a2", "held_out")])


class NoCueDataTests(R.Workspace):
    def test_take_json_reader_returns_only_script_and_started_unix(self):
        camera = self.camera()
        code, record, _ = self.run_take(self.args(camera, script="free"), enter=lambda: True,
                                        settings=take.Settings(slate_s=1.0, hold_lead_s=0.2, say_gap_s=0.1,
                                                               away_s=0.2, free_max_s=5, health_retry_s=0.1))
        folder = Path(self.args(camera, script="free").out)
        fields = n1.read_take_fields(folder)
        self.assertEqual(set(fields), {"script", "started_unix"})
        self.assertEqual(fields["script"], "free")
        self.assertIsInstance(fields["started_unix"], float)  # a rename in take.py fails here
        self.assertFalse(n1.is_staged(fields["script"]))
        set_take_fields(folder, "hard-a", fields["started_unix"])
        self.assertEqual(n1.read_take_fields(folder), {"script": "hard-a", "started_unix": fields["started_unix"]})
        self.assertTrue(n1.is_staged("hard-a"))
        record = json.loads((folder / "take.json").read_text(encoding="utf-8"))
        record["cues"], record["slates"] = "unreadable", None
        take.write_json(folder / "take.json", record)
        self.assertEqual(n1.read_take_fields(folder)["script"], "hard-a")

    def test_no_tool_reads_cue_times(self):
        for name in ("n1.py", "label.py", "page.html"):
            source = (HERE / name).read_text(encoding="utf-8")
            for word in ('"cues"', "'cues'", "pc_mono_s", "pc_unix", "slates"):
                self.assertNotIn(word, source, f"{name} mentions {word}")


class SplitTests(Base):
    def test_per_script_alternation_in_recording_order(self):
        path = self.tmp / "split.json"
        added = n1.register(path, [("b1", "hard-b", 5.0), ("a2", "hard-a", 3.0), ("a1", "hard-a", 1.0),
                                   ("b2", "hard-b", 7.0), ("a3", "hard-a", 9.0)])
        self.assertEqual([(e["session"], e["split"]) for e in added],
                         [("a1", "development"), ("a2", "held_out"), ("b1", "development"),
                          ("b2", "held_out"), ("a3", "development")])

    def test_stable_when_sessions_are_added_and_never_reassigns(self):
        path = self.tmp / "split.json"
        first = n1.register(path, [("a1", "hard-a", 1.0), ("b1", "hard-b", 2.0), ("a2", "hard-a", 3.0)])
        added = n1.register(path, [("a0", "hard-a", 0.5), ("b2", "hard-b", 4.0), ("a1", "hard-a", 1.0)])
        self.assertEqual([(e["session"], e["split"]) for e in added], [("a0", "development"), ("b2", "held_out")])
        self.assertEqual(n1.load_split(path)[:3], first)

    def test_refuses_an_edited_split_json(self):
        path = self.tmp / "split.json"
        n1.register(path, [("a1", "hard-a", 1.0), ("a2", "hard-a", 2.0), ("b1", "hard-b", 3.0)])
        original = path.read_text(encoding="ascii")
        for old, new in (('"held_out"', '"development"'), ('"a1"', '"a9"'), ('"started_unix":3.0', '"started_unix":0.5')):
            edited = original.replace(old, new, 1)
            self.assertNotEqual(edited, original)
            path.write_text(edited, encoding="ascii")
            with self.assertRaisesRegex(n1.Refused, "edited"):
                n1.load_split(path)
        data = json.loads(original)
        del data["entries"][1]
        path.write_text(json.dumps(data), encoding="ascii")
        with self.assertRaisesRegex(n1.Refused, "edited"):
            n1.register(path, [("x", "hard-a", 9.0)])


class SecondPersonTests(Base):
    def test_the_union_test(self):
        folder = staged_take(self.takes, "s1", typed="00:40-00:45")
        ws = self.ws()
        ws.scan()
        self.surfaces_done(ws, "s1")
        p = label.Pass(ws, "boxes", "s1", out=io.StringIO())  # open before check-2 appears
        recheck(folder, "01:10")
        with self.assertRaisesRegex(n1.Refused, r"blocked.*Earlier spans: 00:40-00:45, 01:10-01:10"):
            label.Pass(ws, "boxes", "s1", out=io.StringIO())
        with self.assertRaisesRegex(n1.Refused, "check files changed"):
            p.save({"keyframes": [], "audited": []})
        with self.assertRaisesRegex(n1.Refused, "blocked"):
            ws.freeze_fixture()
        status = "\n".join(ws.status())
        self.assertIn("s1  hard-a  development  BLOCKED", status)
        self.assertNotRegex(status, r"\d\d:\d\d")
        report = recheck(folder, "01:10, 01:30")  # covers check-2, not check-1
        self.assertEqual(report["verdict"], "PASS")
        state = ws.state("s1")
        self.assertFalse(state.ok)
        self.assertEqual(state.union, "00:40-00:45, 01:10-01:10, 01:30-01:30")
        recheck(folder, "00:40-00:45, 01:10, 01:30")
        self.assertTrue(ws.state("s1").ok)
        self.assertEqual(ws.state("s1").check, "check-4.json")
        label.Pass(ws, "boxes", "s1", out=io.StringIO())

    def test_no_earlier_spans(self):
        folder = staged_take(self.takes, "s1", typed="none")
        ws = self.ws()
        ws.scan()
        self.assertEqual(ws.state("s1").union, "(no earlier spans)")
        recheck(folder, "01:10")
        state = ws.state("s1")
        self.assertTrue(state.ok)
        self.assertEqual(state.union, "01:10-01:10")
        self.assertNotIn("none", n1.format_spans([]))

    def test_clipping_in_frames(self):
        folder = staged_take(self.takes, "s1", typed="none")
        ws = self.ws()
        ws.scan()
        self.boxes_done(ws, "s1")
        frames = n1.read_frames(folder)
        t_of = {f["n"]: f["t"] for f in frames}
        pad = n1.load_pad(ws.root, PROTOCOL)
        keyframes = ws.load_draft(ws.draft_path("boxes", "s1"))["keyframes"]
        before = n1.fixture_rows(keyframes, self.usable(ws, "s1"), t_of, pad)
        main = self.interval(ws, "s1", 65, 75, "fine", "still_reader")
        self.labels_done(ws, "s1", intervals=[self.interval(ws, "s1", 40, 50), main],
                         spans=[self.span(ws, "s1", 52, 60), self.span(ws, "s1", 64, 74, "low_light")])
        self.assertTrue(ws.load_draft(ws.draft_path("labels", "s1"))["actor"]["end_to_end"])
        recheck(folder, "01:10")  # a new [69, 71) cut
        new_state = ws.state("s1")
        segments = new_state.report["usable"]["segments"]
        k = next(i for i, seg in enumerate(segments) if seg["end_t_s"] == 69.0)
        self.assertEqual(segments[k + 1]["start_t_s"], 71.0)
        code, text = run_main(["status", "--takes", str(self.takes)])  # any command applies the newer check
        self.assertEqual(code, 0, text)
        self.assertIn("s1 labels: interval still_reader at frames", text)
        self.assertIn("now tied to check-2.json", text)
        self.assertIn("end-to-end mark is cleared", text)
        draft = ws.load_draft(ws.draft_path("labels", "s1"))
        self.assertEqual((draft["check"], draft["check_sha256"]), ("check-2.json", new_state.check_sha256))
        self.assertIsNone(draft["actor"]["end_to_end"])
        pieces = sorted((x for x in draft["intervals"] if x["case"] == "still_reader"), key=lambda x: x["onset_n"])
        self.assertEqual(len(pieces), 2)
        self.assertEqual((pieces[0]["onset_n"], pieces[0]["onset_kind"]), (main["onset_n"], "touch"))
        self.assertEqual((pieces[0]["offset_n"], pieces[0]["offset_kind"]), (segments[k]["last_n"], "carried"))
        self.assertEqual(pieces[0]["offset_n"], max(f["n"] for f in frames if f["t"] < 69))
        self.assertEqual((pieces[1]["onset_n"], pieces[1]["onset_kind"]), (segments[k + 1]["first_n"], "carried"))
        self.assertEqual(pieces[1]["onset_n"], min(f["n"] for f in frames if f["t"] >= 71))
        self.assertEqual((pieces[1]["offset_n"], pieces[1]["offset_kind"]), (main["offset_n"], "release"))
        self.assertEqual(pieces[1]["onset_t_s"], t_of[pieces[1]["onset_n"]])
        for item in draft["intervals"]:
            self.assertFalse(any(69 <= t_of[n] < 71 for n in range(item["onset_n"], item["offset_n"] + 1)))
        self.assertEqual([s["kind"] for s in draft["spans"]], ["quiet", "low_light", "low_light"])
        n1.check_labels(draft["intervals"], draft["spans"], PROTOCOL, new_state.segments(), t_of)
        last_before = max((f for f in frames if f["t"] < 69), key=lambda f: f["n"])
        short = dict(main, onset_n=last_before["n"], offset_n=next(f["n"] for f in frames if f["t"] >= 69.5))
        inside = {"kind": "uncertain", "start_n": next(f["n"] for f in frames if f["t"] >= 69.2),
                  "end_n": next(f["n"] for f in frames if f["t"] >= 70.5), "by": "owner"}
        intervals, spans, changes = n1.clip_items([short], [inside], new_state.segments(), t_of)
        self.assertEqual((intervals, spans), ([], []))  # too short to keep an onset before its offset; empty
        self.assertTrue(all(change.endswith("dropped") for change in changes), changes)
        boxes = ws.load_draft(ws.draft_path("boxes", "s1"))
        self.assertEqual((boxes["check"], boxes["keyframes"]), ("check-2.json", keyframes))
        after = n1.fixture_rows(boxes["keyframes"], self.usable(ws, "s1"), t_of, pad)
        after_ns = {row[0] for row in after}
        self.assertEqual([row for row in before if row[0] in after_ns], after)
        self.assertEqual(len(before) - len(after), sum(1 for row in before if 69 <= t_of[row[0]] < 71))
        stale = dict(draft, check="check-1.json", check_sha256=n1.file_sha256(folder / "check-1.json"))
        n1.write_json(ws.draft_path("labels", "s1"), stale)
        with self.assertRaisesRegex(n1.Refused, "superseded"):
            ws.ready_session(ws.entry("s1"), "labels")

    def test_the_page_key_ends_the_pass_and_prints_the_union(self):
        staged_take(self.takes, "s1", typed="00:40-00:45")
        staged_take(self.takes, "s2", "hard-b", BASE_UNIX + 100, typed="none")
        ws = self.ws()
        ws.scan()
        for take_id, expected in (("s1", "Earlier spans: 00:40-00:45"), ("s2", "Earlier spans: (no earlier spans)")):
            seen = {}

            def drive(client):
                state = client.state()
                seen["frame"] = client.call("GET", f"/api/frame/{state['frames'][0][0]}")[0]
                client.save({"keyframes": [[state["frames"][0][0], [10, 10, 100, 200]]], "audited": []})
                seen["stop"] = client.stop("second_person")[0]
                try:
                    seen["after"] = client.call("GET", f"/api/frame/{state['frames'][1][0]}")[0]
                except OSError:
                    seen["after"] = "closed"
            code, text = run_main(["boxes", take_id, "--takes", str(self.takes)], drive)
            self.assertEqual((code, seen["frame"], seen["stop"]), (0, 200, 200))
            self.assertIn(seen["after"], (410, "closed"))  # the pass has ended: no more frames
            self.assertIn(f"Second person seen in {take_id}", text)
            self.assertIn(expected, text)
            self.assertNotRegex(text, r"\bnone\b")
            self.assertEqual(ws.load_draft(ws.draft_path("boxes", take_id))["keyframes"][0][1],
                             [10.0, 10.0, 100.0, 200.0])


class FrameServerTests(Base):
    def setUp(self):
        super().setUp()
        self.folder = staged_take(self.takes, "s1", typed="01:10")
        self.ws_ = self.ws()
        self.ws_.scan()
        self.p = label.Pass(self.ws_, "boxes", "s1", out=io.StringIO())
        self.server = Server(self.p)
        self.addCleanup(self.server.close)

    def test_serves_only_usable_frames_as_raw_nv12(self):
        frames = n1.read_frames(self.folder)
        usable = self.p.usable[5]
        status, headers, body = self.server.request("GET", f"/api/frame/{usable['n']}")
        self.assertEqual((status, len(body)), (200, 345_600))
        expected = check.read_bundle((self.folder / usable["file"]).read_bytes(), with_rgb=True)[1]
        self.assertEqual(body, expected)
        slate = frames[0]["n"]
        cut = next(f["n"] for f in frames if 69.2 < f["t"] < 70.8)
        for n in (slate, cut, frames[-1]["n"], 999999):
            self.assertEqual(self.server.request("GET", f"/api/frame/{n}")[0], 404, n)

    def test_binds_loopback_and_refuses_wrong_token_host_or_origin(self):
        self.assertEqual(self.server.server.server_address[0], "127.0.0.1")
        n = self.p.usable[0]["n"]
        self.assertEqual(self.server.request("GET", f"/api/frame/{n}", token="wrong-token")[0], 403)
        self.assertEqual(self.server.request("GET", f"/api/frame/{n}", token=None)[0], 403)
        self.assertEqual(self.server.request("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.server.request("GET", "/api/state", headers={"Host": "localhost:1"})[0], 403)
        self.assertEqual(self.server.request("POST", "/api/save", {"keyframes": [], "audited": []},
                                             headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.server.request("GET", f"/?t={self.server.token}", token=None)[0], 200)

    def test_sends_no_cors_header_and_refuses_paths_outside_the_take(self):
        responses = [self.server.request("GET", path) for path in
                     ("/", "/api/state", f"/api/frame/{self.p.usable[0]['n']}", "/api/frame/../take.json",
                      "/take.json", "/frames.csv", "/frames/000001-1000.npz", "/api/frame/1/../../check-1.json",
                      "/../../etc/passwd", "/api/frame/%2e%2e%2ftake.json")]
        responses.append(self.server.request("OPTIONS", "/api/state"))
        for status, headers, body in responses:
            self.assertFalse(any(name.lower().startswith("access-control") for name in headers), headers)
        for status, headers, body in responses[3:-1]:
            self.assertEqual(status, 404)
            self.assertNotIn(b"cues", body)
        self.assertIn("default-src 'none'", responses[0][1]["Content-Security-Policy"])
        rows = (self.folder / "frames.csv").read_text(encoding="utf-8").replace("frames/000001-", "../000001-", 1)
        (self.folder / "frames.csv").write_text(rows, encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(n1.Refused, "outside"):
            n1.read_frames(self.folder)


class BoxTests(unittest.TestCase):
    frames = [{"n": n, "t": 10.0 + 0.25 * n, "t_s": f"{10.0 + 0.25 * n:.6f}"} for n in range(1, 21)]
    t_of = {f["n"]: f["t"] for f in frames}

    def test_interpolation_is_linear_in_t_s(self):
        frames = [dict(f, t=f["t"] + (0.07 if f["n"] % 3 == 0 else 0)) for f in self.frames]  # uneven times
        t_of = {f["n"]: f["t"] for f in frames}
        boxes = n1.tight_boxes([[2, [100, 50, 200, 250]], [10, [140, 70, 260, 330]]], frames, t_of)
        for frame, box in zip(frames, boxes):
            n = frame["n"]
            if 2 <= n <= 10:
                w = (t_of[n] - t_of[2]) / (t_of[10] - t_of[2])
                self.assertEqual(box, [100 + 40 * w, 50 + 20 * w, 200 + 60 * w, 250 + 80 * w])
        self.assertIsNone(boxes[0])  # before the first keyframe
        self.assertEqual(boxes[-1], [140, 70, 260, 330])  # the last keyframe holds

    def test_absent_runs_stay_empty(self):
        boxes = n1.tight_boxes([[1, [0, 0, 50, 50]], [5, None], [9, [10, 10, 60, 60]]], self.frames, self.t_of)
        self.assertEqual(boxes[0:4], [[0, 0, 50, 50]] * 4)  # no line into an absent keyframe
        self.assertEqual(boxes[4:8], [None] * 4)
        self.assertEqual(boxes[8], [10, 10, 60, 60])

    def test_pad_and_clip_are_exact(self):
        pad = {"fraction": 0.2, "min_px": 16}
        self.assertEqual(n1.pad_box([100, 50, 200, 250], pad), [80, 10, 220, 290])
        self.assertEqual(n1.pad_box([10, 10, 30, 20], pad), [0, 0, 46, 36])
        self.assertEqual(n1.pad_box([600, 300, 640, 360], pad), [584, 284, 640, 360])
        self.assertEqual(n1.pad_box([100.4, 50.6, 200.2, 250.9], pad), [80, 10, 221, 291])
        rows = n1.fixture_rows([[1, [100, 50, 200, 250]], [3, None]], self.frames[:4], self.t_of, pad)
        self.assertEqual(rows, [[1, "10.250000", 80, 10, 220, 290], [2, "10.500000", 80, 10, 220, 290],
                                [3, "10.750000", "", "", "", ""], [4, "11.000000", "", "", "", ""]])
        self.assertEqual(n1.rows_csv(rows).decode().splitlines()[0], "n,t_s,x0,y0,x1,y1")


class PadTests(Base):
    def test_the_pad_can_only_grow(self):
        ws = self.ws()
        self.assertEqual(n1.load_pad(ws.root, PROTOCOL), {"fraction": 0.2, "min_px": 16})
        with self.assertRaisesRegex(n1.Refused, "only grow"):
            n1.set_pad(ws.root, PROTOCOL, 0.15, 16)
        with self.assertRaisesRegex(n1.Refused, "only grow"):
            n1.set_pad(ws.root, PROTOCOL, 0.3, 12)
        code, text = run_main(["boxes", "--pad", "25", "--takes", str(self.takes)])
        self.assertEqual((code, n1.load_pad(ws.root, PROTOCOL)), (0, {"fraction": 0.25, "min_px": 16}))
        (ws.root / "fixture-v1").mkdir()
        with self.assertRaisesRegex(n1.Refused, "fixed once a fixture is frozen"):
            n1.set_pad(ws.root, PROTOCOL, 0.3, 16)


class FixtureTests(Base):
    def test_one_row_per_usable_frame_one_id_and_only_whitelisted_fields(self):
        staged_take(self.takes, "s1", typed="01:10")
        commissioning_take(self.takes)
        ws = self.ws()
        ws.scan()
        self.surfaces_done(ws, "c1")
        self.boxes_done(ws, "s1")
        line, _ = ws.freeze_fixture()
        folder = ws.root / "fixture-v1"
        manifest = json.loads((folder / "fixture.json").read_bytes())
        self.assertEqual(set(manifest), {"schema", "version", "frozen_unix", "protocol", "tools", "person_id",
                                         "image", "padding", "surface_map", "sessions"})
        self.assertEqual(manifest["person_id"], "p1")
        [session] = manifest["sessions"]
        self.assertEqual(set(session), {"take", "check", "check_sha256", "frames_csv_sha256", "segments", "frames",
                                        "boxes"})
        rows = (folder / "boxes" / "s1.csv").read_text(encoding="ascii").splitlines()
        self.assertEqual(rows[0], "n,t_s,x0,y0,x1,y1")
        usable = self.usable(ws, "s1")
        self.assertEqual([int(r.split(",")[0]) for r in rows[1:]], [f["n"] for f in usable])
        self.assertEqual(session["frames"], len(usable))
        surface = json.loads((folder / "surface-map.json").read_bytes())
        self.assertEqual(set(surface), {"schema", "image", "reference", "surfaces"})
        for path in folder.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="ascii").lower()
                for word in ("hand\"", "contact", "region", "keyframe", "interval", "cue", "onset"):
                    self.assertNotIn(word, text, f"{path.name} holds {word}")
        self.assertRegex(line, r"^den-n1 fixture-v1 sha256=[0-9a-f]{64} protocol=[0-9a-f]{64} frozen=\S+ "
                               r"sessions=1 frames=\d+ pad=0.2/16px$")

    def test_refuses_an_unaudited_session_and_box_edits_after_the_label_pass_starts(self):
        staged_take(self.takes, "s1")
        commissioning_take(self.takes)
        ws = self.ws()
        ws.scan()
        self.surfaces_done(ws, "c1")
        p = label.Pass(ws, "boxes", "s1", out=io.StringIO())
        frames = p.usable
        p.save({"keyframes": [[frames[0]["n"], [1, 1, 50, 50]]], "audited": [[frames[0]["n"], frames[-2]["n"]]]})
        with self.assertRaisesRegex(n1.Refused, "not audited end to end"):
            ws.freeze_fixture()
        with self.assertRaisesRegex(n1.Refused, "only after its box audit"):
            label.Pass(ws, "labels", "s1", out=io.StringIO())
        p.save({"keyframes": [[frames[0]["n"], [1, 1, 50, 50]]], "audited": [[frames[0]["n"], frames[-1]["n"]]]})
        label.Pass(ws, "labels", "s1", out=io.StringIO())  # the label pass starts: boxes lock
        with self.assertRaisesRegex(n1.Refused, "locked"):
            p.save({"keyframes": [[frames[0]["n"], [2, 2, 50, 50]]], "audited": []})
        self.assertTrue(label.Pass(ws, "boxes", "s1", out=io.StringIO()).page_state()["locked"])
        ws.freeze_fixture()


class LabelTests(Base):
    def setUp(self):
        super().setUp()
        self.folder = staged_take(self.takes, "s1", typed="01:10")
        self.w = self.ws()
        self.w.scan()
        self.boxes_done(self.w, "s1")
        self.p = label.Pass(self.w, "labels", "s1", out=io.StringIO())

    def refused(self, pattern, intervals=(), spans=(), protocol=None):
        with self.assertRaisesRegex(n1.Refused, pattern):
            n1.check_labels(list(intervals), list(spans), protocol or PROTOCOL, self.p.segments, self.p.t_of)

    def test_every_protocol_check(self):
        w, iv = self.w, self.interval
        ok = iv(w, "s1", 40, 50)
        intervals, _ = n1.check_labels([ok], [], PROTOCOL, self.p.segments, self.p.t_of)
        self.assertEqual(intervals[0]["onset_t_s"], self.p.t_of[ok["onset_n"]])
        slate = n1.read_frames(self.folder)[0]["n"]
        self.refused("usable frames", [dict(ok, onset_n=slate)])
        self.refused("before the offset", [dict(ok, offset_n=ok["onset_n"])])
        self.refused("overlap", [ok, iv(w, "s1", 45, 55, case="wiping")])
        self.refused("crosses a cut", [iv(w, "s1", 65, 75)])
        first_n = self.p.segments[0][0]
        self.refused("carried exactly", [dict(ok, onset_kind="carried")])
        self.refused("carried exactly", [dict(ok, onset_n=first_n)])
        n1.check_labels([dict(ok, onset_n=first_n, onset_kind="carried")], [], PROTOCOL, self.p.segments, self.p.t_of)
        self.refused("fixed lists", [dict(ok, onset_kind="grab")])
        self.refused("band", [dict(ok, band="medium")])
        self.refused("case", [dict(ok, case="cooking")])
        self.refused("sure", [dict(ok, sure={"onset": True})])
        self.refused("by: owner", [{k: v for k, v in ok.items() if k != "by"}])
        self.refused("unknown", [dict(ok, hand_box=[1, 2, 3, 4])])
        self.refused("kind not in", spans=[dict(self.span(w, "s1", 52, 58), kind="busy")])
        self.refused("needs a case", spans=[dict(self.span(w, "s1", 52, 58), case="phone_at_counter")])
        self.refused("by: owner", spans=[{k: v for k, v in self.span(w, "s1", 52, 58).items() if k != "by"}])
        self.refused("overlaps", [ok], [self.span(w, "s1", 48, 58)])
        self.refused("overlaps", [], [self.span(w, "s1", 52, 58), self.span(w, "s1", 55, 60, "uncertain")])
        n1.check_labels([ok], [self.span(w, "s1", 38, 60, "low_light")], PROTOCOL, self.p.segments, self.p.t_of)

    def test_the_pause_rule_never_applies_across_a_cut(self):
        w, iv = self.w, self.interval
        a = iv(w, "s1", 40, 50)
        self.refused("pause of 6 s or less", [a, iv(w, "s1", 55.5, 60)])
        n1.check_labels([a, iv(w, "s1", 56.6, 60)], [], PROTOCOL, self.p.segments, self.p.t_of)
        n1.check_labels([a, iv(w, "s1", 55.5, 60, case="wiping")], [], PROTOCOL, self.p.segments, self.p.t_of)
        last = self.p.segments[0][1]
        before = iv(w, "s1", 60, 66)
        before = dict(before, offset_n=last, offset_kind="carried")
        after = iv(w, "s1", 71, 80)
        after = dict(after, onset_n=self.p.segments[1][0], onset_kind="carried")
        n1.check_labels([before, after], [], PROTOCOL, self.p.segments, self.p.t_of)  # 2 s apart, across a cut

    def test_phone_band_none(self):
        text = (HERE / "PROTOCOL-V1.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "PROTOCOL-V1.md"
            copy.write_text(text.replace('"casual"\n', '"none"\n', 1), encoding="utf-8", newline="\n")
            none = n1.load_protocol(copy)
        phone = self.interval(self.w, "s1", 40, 50, "casual", "phone_at_counter")
        n1.check_labels([phone], [], PROTOCOL, self.p.segments, self.p.t_of)
        self.refused("not an interval case", [phone], protocol=none)
        span = self.span(self.w, "s1", 40, 50, case="phone_at_counter")
        n1.check_labels([], [span], none, self.p.segments, self.p.t_of)
        self.refused("needs a case", [], [span])

    def test_saves_go_through_the_checks_and_freeze_needs_end_to_end(self):
        with self.assertRaisesRegex(n1.Refused, "before the offset"):
            item = self.interval(self.w, "s1", 40, 50)
            self.p.save({"intervals": [dict(item, offset_n=item["onset_n"])], "spans": [],
                         "end_to_end": False})
        self.p.save({"intervals": [self.interval(self.w, "s1", 40, 50)], "spans": [], "end_to_end": False})
        commissioning_take(self.takes)
        self.surfaces_done(self.w, "c1")
        self.w.freeze_fixture()
        with self.assertRaisesRegex(n1.Refused, "not marked labelled end to end"):
            self.w.freeze_labels()

    def test_label_mode_sends_no_boxes(self):
        server = Server(self.p)
        self.addCleanup(server.close)
        status, headers, body = server.request("GET", "/api/state")
        state = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse({"keyframes", "audited", "pad", "keyframe_times", "locked"} & set(state))
        self.assertNotIn(b"keyframe", body)
        self.assertNotIn(b"box", body)
        status, headers, body = server.request("GET", f"/api/frame/{self.p.usable[0]['n']}")
        self.assertEqual((status, headers["Content-Type"], len(body)), (200, "application/octet-stream", 345_600))


class RemarkTests(Base):
    def test_picks_the_second_development_session_waits_and_hides_the_first_labels(self):
        for i, (name, script) in enumerate((("a1", "hard-a"), ("b1", "hard-b"), ("a2", "hard-a"), ("b2", "hard-b"))):
            staged_take(self.takes, name, script, BASE_UNIX + i * 1000)
        ws = self.ws()
        ws.scan()
        self.assertEqual(ws.remark_take(), "b1")
        self.boxes_done(ws, "b1")
        with self.assertRaisesRegex(n1.Refused, "not marked end to end"):
            label.Pass(ws, "remark", out=io.StringIO())
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 10 * DAY):
            self.labels_done(ws, "b1")
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 12.9 * DAY):
            with self.assertRaisesRegex(n1.Refused, "waits 3 days"):
                label.Pass(ws, "remark", out=io.StringIO())
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 13.1 * DAY):
            p = label.Pass(ws, "remark", out=io.StringIO())
        state = p.page_state()
        self.assertEqual((state["take"], state["intervals"], state["spans"]), ("b1", [], []))
        self.assertEqual(p.draft["actor"]["pass"], "remark")

    def test_agreement_on_hand_made_cases(self):
        def iv(on, off, band="fine", case="sorting_mail", kinds=("touch", "release"), sure=(True, True, True)):
            return {"onset_t_s": on, "offset_t_s": off, "onset_kind": kinds[0], "offset_kind": kinds[1],
                    "band": band, "case": case, "sure": dict(zip(("onset", "offset", "band"), sure))}
        first = [iv(10, 20), iv(30, 40, "casual", "wiping"), iv(50, 60, kinds=("carried", "release")),
                 iv(70, 80, sure=(False, True, True)), iv(100, 104)]
        second = [iv(10.5, 19.0), iv(31, 40.5, "fine", "wiping"), iv(50, 61), iv(71, 80.25),
                  iv(102.5, 110)]  # the last overlaps 1.5 s of 4 and of 7.5: no match
        report = n1.agreement(first, second)
        self.assertEqual(report["intervals"], {"label": 5, "remark": 5, "matched": 4})
        self.assertEqual((report["band_agreement"], report["case_agreement"]), (0.75, 1.0))
        self.assertEqual((report["onset"]["n"], report["onset"]["carried"], report["onset"]["unsure"]), (2, 1, 1))
        self.assertEqual(report["onset"]["median_s"], 0.75)  # 0.5 and 1.0
        self.assertEqual((report["offset"]["n"], report["offset"]["carried"], report["offset"]["unsure"]), (4, 0, 0))
        self.assertEqual(report["offset"]["median_s"], 0.75)  # 1.0, 0.5, 1.0, 0.25
        self.assertAlmostEqual(report["offset"]["p90_s"], 1.0)


class FreezeTests(Base):
    def build(self, takes=None, names=(("a1", "hard-a"), ("b1", "hard-b"), ("a2", "hard-a"), ("b2", "hard-b"))):
        takes = takes or self.takes
        for i, (name, script) in enumerate(names):
            staged_take(takes, name, script, BASE_UNIX + (i // 2) * DAY + (i % 2) * 3600)
        commissioning_take(takes)
        ws = n1.Workspace(takes, takes / "n1")
        ws.scan()
        self.surfaces_done(ws, "c1")
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 5 * DAY):
            for name, _ in names:
                self.boxes_done(ws, name)
                self.labels_done(ws, name)
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 9 * DAY):
            p = label.Pass(ws, "remark", out=io.StringIO())
            p.save({"intervals": [self.interval(ws, "b1", 40.5, 50.2)], "spans": [], "end_to_end": True})
        return ws

    def test_canonical_bytes_give_fixed_hashes(self):
        self.assertEqual(n1.canonical({"b": 1, "a": [1.5, "x", "\u00e9"]}), b'{"a":[1.5,"x","\\u00e9"],"b":1}\n')
        self.assertEqual(n1.sha256_bytes(n1.canonical({"b": 1, "a": 2})),
                         "81103aa69250ea56e887eaab3cd9bf363d341563f05d0676be389c3e40a72871")
        with self.assertRaises(ValueError):
            n1.canonical({"x": float("nan")})

    def test_freeze_verify_versions_and_refusals(self):
        ws = self.build()
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 10 * DAY):
            fixture_line, _ = ws.freeze_fixture()
            labels_line, record, _ = ws.freeze_labels()
        self.assertRegex(labels_line, r"^den-n1 labels-v1 sha256=[0-9a-f]{64} protocol=[0-9a-f]{64} "
                                      r"split=[0-9a-f]{64} remark=[0-9a-f]{64} frozen=\S+ "
                                      r"development=2s/1d/fine2/casual2 held_out=2s/1d/fine2/casual2$")
        fixture_hash = n1.file_sha256(ws.root / "fixture-v1" / "fixture.json")
        labels_hash = n1.file_sha256(ws.root / "labels-v1" / "labels.json")
        self.assertEqual(record["gold_set"]["n1"], {"fixture": {"version": 1, "hash": fixture_hash},
                                                    "labels": {"version": 1, "hash": labels_hash}})
        self.assertEqual((record["n_intervals"], record["label_version"], record["hash"]), (8, 1, labels_hash))
        manifest = json.loads((ws.root / "labels-v1" / "labels.json").read_bytes())
        for session in manifest["sessions"]:
            self.assertEqual(set(session) >= {"take", "split", "check", "check_sha256", "file", "sha256"}, True)
            saved = json.loads((ws.root / "labels-v1" / session["file"]).read_bytes())
            self.assertEqual((saved["check"], saved["check_sha256"]), (session["check"], session["check_sha256"]))
        remark = json.loads((ws.root / "labels-v1" / "remark.json").read_bytes())
        self.assertEqual((remark["take"], remark["check"]), ("b1", "check-1.json"))
        self.assertEqual(remark["check_sha256"], n1.file_sha256(self.takes / "b1" / "check-1.json"))
        self.assertEqual(remark["agreement"]["intervals"]["matched"], 1)
        for path in (ws.root / "fixture-v1").rglob("*"):
            if path.is_file():
                self.assertFalse(path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH), path)
        with self.assertRaises(FileExistsError):
            n1.write_frozen(ws.root / "fixture-v1", {"x.txt": b"x"})
        ok, lines = ws.verify(f"{fixture_line}\n{labels_line}\n")
        self.assertTrue(ok, lines)
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 11 * DAY):
            line2, _ = ws.freeze_fixture()
        self.assertTrue(line2.startswith("den-n1 fixture-v2 "))
        target = ws.root / "fixture-v1" / "boxes" / "a1.csv"
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        data = bytearray(target.read_bytes())
        data[-3] = ord("7") if data[-3] != ord("7") else ord("8")
        target.write_bytes(bytes(data))
        ok, lines = ws.verify(f"{fixture_line}\n{labels_line}\n")
        self.assertFalse(ok)
        self.assertIn("fixture-v1", [l for l in lines if "changed" in l][0])
        ok, lines = ws.verify(fixture_line.replace(fixture_hash, "0" * 64))
        self.assertTrue(any("differs from FREEZES.md" in l for l in lines))

    def test_drafts_under_another_protocol_are_refused(self):
        ws = self.build(names=(("a1", "hard-a"), ("b1", "hard-b")))
        path = ws.draft_path("boxes", "a1")
        draft = json.loads(path.read_bytes())
        draft["protocol_sha256"] = "1" * 64
        n1.write_json(path, draft)
        with self.assertRaisesRegex(n1.Refused, "another protocol"):
            ws.freeze_fixture()
        with self.assertRaisesRegex(n1.Refused, "another protocol"):
            label.Pass(ws, "boxes", "a1", out=io.StringIO())

    def test_the_two_hashes_are_independent(self):
        same = self.tmp / "same"
        same.mkdir()
        self.build(same, names=(("a1", "hard-a"), ("b1", "hard-b")))
        hashes = {}
        for variant in ("label", "box"):
            shutil.copytree(same, self.tmp / variant)  # the same check files, drafts and split.json
        for variant in ("same", "label", "box"):
            takes = self.tmp / variant
            ws = n1.Workspace(takes, takes / "n1")
            if variant == "label":
                path = ws.draft_path("labels", "a1")
                draft = json.loads(path.read_bytes())
                draft["intervals"][0]["band"] = "casual"
                n1.write_json(path, draft)
            if variant == "box":
                path = ws.draft_path("boxes", "a1")
                draft = json.loads(path.read_bytes())
                draft["keyframes"][0][1] = [101.0, 50.0, 300.0, 340.0]
                n1.write_json(path, draft)
            with mock.patch.object(n1, "now", return_value=BASE_UNIX + 10 * DAY):
                ws.freeze_fixture()
                ws.freeze_labels()
            hashes[variant] = (n1.file_sha256(ws.root / "fixture-v1" / "fixture.json"),
                               n1.file_sha256(ws.root / "labels-v1" / "labels.json"))
        self.assertEqual(hashes["label"][0], hashes["same"][0])  # a label change leaves the fixture hash
        self.assertNotEqual(hashes["label"][1], hashes["same"][1])
        self.assertEqual(hashes["box"][1], hashes["same"][1])  # and a box change leaves the labels hash
        self.assertNotEqual(hashes["box"][0], hashes["same"][0])


class StatusTests(Base):
    def test_counts_per_split_and_band_quiet_past_tau_off_and_days_no_times(self):
        for i, (name, script) in enumerate((("a1", "hard-a"), ("b1", "hard-b"), ("a2", "hard-a"))):
            staged_take(self.takes, name, script, BASE_UNIX + i * DAY)
        ws = self.ws()
        ws.scan()
        for name in ("a1", "b1", "a2"):
            self.boxes_done(ws, name)
        self.labels_done(ws, "a1", spans=[self.span(ws, "a1", 36.5, 39.5, "quiet", "idle_standing"),
                                          self.span(ws, "a1", 51, 58), self.span(ws, "a1", 58.5, 59.5, "uncertain")])
        self.labels_done(ws, "b1")
        self.labels_done(ws, "a2")
        text = "\n".join(ws.status())
        self.assertIn("Split development: 2 sessions over 2 days; intervals fine 2, casual 2;", text)
        self.assertIn("Split held_out: 1 sessions over 1 days; intervals fine 1, casual 1;", text)
        scored = self.frame_at(ws, "a1", 39.5)["t"] - self.frame_at(ws, "a1", 36.5)["t"]  # before any interval
        self.assertIn(f"idle_standing 1 ({scored:.0f} s past 60 s)", text)
        self.assertIn("idle_seated 2 (0 s past 60 s)", text)
        self.assertNotRegex(text, r"\b\d\d:\d\d\b")
        self.assertNotIn("onset", text)
        self.assertNotIn("t_s", text)


class FolderTests(Base):
    def test_refuses_an_n1_folder_in_a_git_checkout_onedrive_or_relative(self):
        (self.tmp / "repo" / ".git").mkdir(parents=True)
        with self.assertRaisesRegex(n1.Refused, "git checkout"):
            n1.n1_folder(self.tmp / "repo" / "n1")
        with self.assertRaisesRegex(n1.Refused, "OneDrive"):
            n1.n1_folder(self.tmp / "OneDrive - Home" / "n1")
        with self.assertRaisesRegex(n1.Refused, "absolute"):
            n1.n1_folder("den-takes/n1")
        code, text = run_main(["status", "--takes", str(self.tmp / "repo")])
        self.assertEqual(code, 2)
        self.assertIn("Refused: the N1 folder is inside a git checkout", text)


class DryRunTests(Base):
    """The plan's full synthetic dry run: register, surfaces, boxes, labels, re-mark, freeze, verify."""

    def test_full_dry_run_through_label_py(self):
        names = [("2026-11-10-1", "hard-a"), ("2026-11-10-2", "hard-b"), ("2026-11-11-1", "hard-a"),
                 ("2026-11-11-2", "hard-b"), ("2026-11-12-1", "hard-a"), ("2026-11-12-2", "hard-b"),
                 ("2026-11-13-1", "hard-a"), ("2026-11-13-2", "hard-b")]
        for i, (name, script) in enumerate(names):
            staged_take(self.takes, name, script, BASE_UNIX + (i // 2) * DAY + (i % 2) * 3600)
        commissioning_take(self.takes)
        argv = ["--takes", str(self.takes)]
        code, text = run_main(["sessions"] + argv)
        self.assertEqual(code, 0, text)
        self.assertEqual(text.count("registered 2026-"), 8)
        self.assertIn("registered 2026-11-10-1 (hard-a #1): development", text)
        self.assertIn("registered 2026-11-11-1 (hard-a #2): held_out", text)
        self.assertIn("registered 2026-11-11-2 (hard-b #2): held_out", text)

        def surfaces(client):
            state = client.state()
            client.save({"reference_n": state["frames"][3][0], "surfaces": state["surfaces"] + [
                {"id": "counter", "type": "counter", "height_cm": 91, "polygon": [[400, 150], [600, 150], [600, 220]]},
                {"id": "lap", "type": "lap", "height_cm": 55, "polygon": [[200, 250], [260, 250], [260, 300]]}]})
            client.stop()
        self.assertEqual(run_main(["surfaces", "c1"] + argv, surfaces)[0], 0)
        ws = self.ws()

        def boxes(client):
            state = client.state()
            frames = state["frames"]
            self.assertEqual(client.call("GET", f"/api/frame/{frames[10][0]}")[0], 200)
            keyframes = [[frames[0][0], [100, 60, 260, 350]], [frames[60][0], [180, 40, 330, 340]],
                         [frames[120][0], None], [frames[150][0], [300, 80, 420, 330]]]
            client.save({"keyframes": keyframes, "audited": [[frames[0][0], frames[100][0]]]})
            client.save({"keyframes": keyframes, "audited": [[frames[0][0], frames[-1][0]]]})
            client.stop()

        def labels(take_id, mode="labels", shift=0.0):
            def drive(client):
                state = client.state()
                self.assertNotIn("keyframes", state)
                at = lambda t: next(n for n, ft in state["frames"] if ft >= t)
                item = lambda a, b, band, case: {"onset_n": at(a + shift), "onset_kind": "touch",
                                                 "offset_n": at(b + shift), "offset_kind": "release", "band": band,
                                                 "case": case, "sure": {"onset": True, "offset": True, "band": True},
                                                 "by": "owner"}
                client.save({"intervals": [item(37, 45, "fine", "still_reader"), item(52, 58, "fine", "sorting_mail"),
                                           item(66, 72, "casual", "wiping"), item(80, 86, "casual",
                                                                                  "phone_at_counter")],
                             "spans": [{"kind": "quiet", "case": "idle_seated", "start_n": at(46), "end_n": at(51),
                                        "by": "owner"},
                                       {"kind": "low_light", "start_n": at(60), "end_n": at(95), "by": "owner"},
                                       {"kind": "uncertain", "start_n": at(73), "end_n": at(75), "by": "owner"}],
                             "end_to_end": True})
                client.stop()
            return drive
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 4 * DAY):
            for name, _ in names:
                code, text = run_main(["boxes", name] + argv, boxes)
                self.assertEqual(code, 0, text)
                code, text = run_main(["labels", name] + argv, labels(name))
                self.assertEqual(code, 0, text)
                self.assertIn(f"Saved. labels pass on {name} ended.", text)
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 6 * DAY):
            code, text = run_main(["remark"] + argv)
            self.assertEqual(code, 2)
            self.assertIn("waits 3 days", text)
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 7.5 * DAY):
            code, text = run_main(["remark"] + argv, labels("2026-11-10-2", "remark", shift=0.3))
            self.assertEqual(code, 0, text)
            self.assertIn("remark pass on 2026-11-10-2", text)
        code, status = run_main(["status"] + argv)
        self.assertIn("Sessions: 8 registered, 0 blocked.", status)
        self.assertIn("Split development: 4 sessions over 2 days; intervals fine 8, casual 8;", status)
        self.assertIn("Split held_out: 4 sessions over 2 days; intervals fine 8, casual 8;", status)
        self.assertIn("Re-mark: 2026-11-10-2, done.", status)
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 8 * DAY):
            code, text = run_main(["freeze", "labels"] + argv)
            self.assertEqual((code, "freeze the fixture first" in text), (2, True))
            code, fixture_out = run_main(["freeze", "fixture"] + argv)
            self.assertEqual(code, 0, fixture_out)
            code, labels_out = run_main(["freeze", "labels"] + argv)
            self.assertEqual(code, 0, labels_out)
        fixture_line = next(l for l in fixture_out.splitlines() if l.startswith("den-n1 fixture-v1 "))
        labels_line = next(l for l in labels_out.splitlines() if l.startswith("den-n1 labels-v1 "))
        self.assertIn("sessions=8", fixture_line)
        self.assertIn("development=4s/2d/fine8/casual8 held_out=4s/2d/fine8/casual8", labels_line)
        record = json.loads(labels_out.splitlines()[-1])
        self.assertEqual((record["n_intervals"], record["label_version"]), (32, 1))
        self.assertEqual(record["gold_set"]["n1"]["fixture"]["version"], 1)
        remark = json.loads((ws.root / "labels-v1" / "remark.json").read_bytes())
        self.assertEqual(remark["agreement"]["intervals"], {"label": 4, "remark": 4, "matched": 4})
        self.assertEqual(remark["agreement"]["band_agreement"], 1.0)
        self.assertLess(remark["agreement"]["onset"]["median_s"], 0.6)
        ok, lines = ws.verify(fixture_line + "\n" + labels_line + "\n")
        self.assertTrue(ok, lines)
        self.assertEqual([l.endswith(": OK") for l in lines], [True, True])
        code, text = run_main(["verify"] + argv)
        self.assertEqual(code, 0, text)
        boxes_csv = (ws.root / "fixture-v1" / "boxes" / "2026-11-10-1.csv").read_text(encoding="ascii").splitlines()
        self.assertTrue(any(line.endswith(",,,,") for line in boxes_csv[1:]))  # the "not in view" run
        surface = json.loads((ws.root / "fixture-v1" / "surface-map.json").read_bytes())
        self.assertEqual([s["id"] for s in surface["surfaces"]], ["handheld", "counter", "lap"])
        self.assertEqual(surface["reference"]["take"], "c1")


# ---- round two: fixes from the second review --------------------------------------------

class SaveRefusalTests(Base):
    """Item 1: a refused save is never reported as saved, and End pass waits for an accepted one."""

    def setUp(self):
        super().setUp()
        staged_take(self.takes, "s1")
        self.w = self.ws()
        self.w.scan()
        self.boxes_done(self.w, "s1")
        self.good = self.interval(self.w, "s1", 40, 50)
        self.bad = self.interval(self.w, "s1", 52, 55)  # the same case 2 s later: the pause rule refuses it

    def test_end_pass_is_refused_while_the_last_save_failed(self):
        seen = {}

        def drive(client):
            client.save({"intervals": [self.good], "spans": [], "end_to_end": False})
            seen["save"] = client.call("POST", "/api/save", {"intervals": [self.good, self.bad], "spans": [],
                                                             "end_to_end": False})
            seen["stop"] = client.stop("done")
            if seen["stop"][0] == 200:
                return  # the pass ended on a refused save
            client.save({"intervals": [self.good], "spans": [], "end_to_end": False})
            seen["stop2"] = client.stop("done")
        code, text = run_main(["labels", "s1", "--takes", str(self.takes)], drive)
        self.assertEqual(code, 0, text)
        self.assertEqual(seen["save"][0], 400)
        self.assertEqual(seen["stop"][0], 400, seen["stop"])
        self.assertIn("last save was refused", seen["stop"][1]["error"])
        self.assertIn("pause of 6 s or less", seen["stop"][1]["error"])
        self.assertEqual(seen["stop2"][0], 200)
        self.assertIn("Saved. labels pass on s1 ended.", text)

    def test_the_second_person_key_still_ends_the_pass_and_says_what_was_not_saved(self):
        seen = {}

        def drive(client):
            client.save({"intervals": [self.good], "spans": [], "end_to_end": False})
            client.call("POST", "/api/save", {"intervals": [self.good, self.bad], "spans": [], "end_to_end": False})
            seen["stop"] = client.stop("second_person")
        code, text = run_main(["labels", "s1", "--takes", str(self.takes)], drive)
        self.assertEqual((code, seen["stop"][0]), (0, 200))
        self.assertIn("Second person seen in s1", text)
        self.assertIn("The last change was not saved (", text)
        self.assertNotIn("the draft is saved", text)
        self.assertEqual(len(self.w.load_draft(self.w.draft_path("labels", "s1"))["intervals"]), 1)


class StaleProtocolTests(Base):
    """Item 2: a git pull before the passes, and a way out for drafts made under an old protocol."""

    def test_readme_pulls_before_steps_4_and_5(self):
        text = (HERE / "README.md").read_text(encoding="utf-8")
        for step in ("## Step 4", "## Step 5"):
            section = text[text.index(step):]
            block = section[section.index("```powershell\n") + len("```powershell\n"):]
            self.assertEqual(block.splitlines()[0], "git pull", step)

    def stale(self, ws, path):
        draft = json.loads(Path(path).read_bytes())
        draft["protocol_sha256"] = "1" * 64
        n1.write_json(path, draft)
        return Path(path).read_bytes()

    def test_aside_moves_a_takes_refused_drafts_and_never_deletes_them(self):
        staged_take(self.takes, "a1")
        commissioning_take(self.takes, "c1")
        ws = self.ws()
        ws.scan()
        self.surfaces_done(ws, "c1")
        self.boxes_done(ws, "a1")
        self.labels_done(ws, "a1")
        old = {kind: self.stale(ws, ws.draft_path(kind, "a1")) for kind in ("boxes", "labels")}
        with self.assertRaisesRegex(n1.Refused, r"another protocol.*label\.py aside a1"):
            label.Pass(ws, "boxes", "a1", out=io.StringIO())
        code, text = run_main(["aside", "a1", "--takes", str(self.takes)])
        self.assertEqual(code, 0, text)
        moved = sorted((ws.root / "work" / "aside").rglob("*.json"))
        self.assertEqual([p.parent.name + "/" + p.name for p in moved], ["boxes/a1.json", "labels/a1.json"])
        self.assertEqual({p.parent.name: p.read_bytes() for p in moved}, old)
        self.assertFalse(ws.draft_path("boxes", "a1").exists() or ws.draft_path("labels", "a1").exists())
        self.assertIn("moved aside", text)
        self.assertTrue(ws.surfaces_path().exists())  # the current surface map stays where it is
        self.assertEqual(label.Pass(ws, "boxes", "a1", out=io.StringIO()).draft["keyframes"], [])
        code, text = run_main(["aside", "a1", "--takes", str(self.takes)])
        self.assertEqual(code, 2)
        self.assertIn("nothing to move aside", text)
        self.stale(ws, ws.surfaces_path())  # a surface map drawn on c1 under the old protocol goes with c1
        code, text = run_main(["aside", "c1", "--takes", str(self.takes)])
        self.assertEqual(code, 0, text)
        self.assertFalse(ws.surfaces_path().exists())
        self.assertEqual(len(list((ws.root / "work" / "aside").rglob("surfaces.json"))), 1)


class ClipKindTests(Base):
    """Item 3: an end that a new cut leaves on a segment's first or last frame becomes carried."""

    def test_an_end_on_a_new_segment_edge_becomes_carried_and_is_reported(self):
        folder = staged_take(self.takes, "s1", typed="none")
        ws = self.ws()
        ws.scan()
        self.boxes_done(ws, "s1")
        frames = n1.read_frames(folder)
        t_of = {f["n"]: f["t"] for f in frames}
        before = max((f for f in frames if f["t"] < 69), key=lambda f: f["n"])["n"]
        after = min((f for f in frames if f["t"] >= 71), key=lambda f: f["n"])["n"]
        ends_at_cut = dict(self.interval(ws, "s1", 62, 65, "casual", "wiping"), offset_n=before)
        starts_at_cut = dict(self.interval(ws, "s1", 71, 76, "fine", "still_reader"), onset_n=after)
        self.labels_done(ws, "s1", intervals=[ends_at_cut, starts_at_cut], spans=[])
        recheck(folder, "01:10")  # a new [69, 71) cut: before and after become segment edges
        code, text = run_main(["status", "--takes", str(self.takes)])
        self.assertEqual(code, 0, text)
        draft = ws.load_draft(ws.draft_path("labels", "s1"))
        by_case = {x["case"]: x for x in draft["intervals"]}
        self.assertEqual((by_case["wiping"]["offset_n"], by_case["wiping"]["offset_kind"]), (before, "carried"))
        self.assertEqual(by_case["wiping"]["onset_kind"], "touch")
        self.assertEqual((by_case["still_reader"]["onset_n"], by_case["still_reader"]["onset_kind"]),
                         (after, "carried"))
        self.assertIn(f"interval wiping at frames {by_case['wiping']['onset_n']} to {before}: offset carried", text)
        self.assertIn(f"interval still_reader at frames {after} to", text)
        self.assertIn("onset carried", text)
        n1.check_labels(draft["intervals"], draft["spans"], PROTOCOL, ws.state("s1").segments(), t_of)


class FreezeRecheckTests(Base):
    """Item 3: freeze_labels runs every save-time protocol check again."""

    def test_freeze_labels_reruns_the_label_checks(self):
        ws = FreezeTests.build(self, names=(("a1", "hard-a"), ("b1", "hard-b")))
        path = ws.draft_path("labels", "a1")
        draft = json.loads(path.read_bytes())
        draft["intervals"][0]["onset_kind"] = "carried"  # not on a segment's first frame
        n1.write_json(path, draft)
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 10 * DAY):
            ws.freeze_fixture()
            with self.assertRaisesRegex(n1.Refused, r"a1.*carried exactly"):
                ws.freeze_labels()
        self.assertEqual(n1.versions(ws.root, "labels"), [])


def browser_fixture(folder):
    """For test-page-browser.mjs: two synthetic takes, then the passes the browser plays, in order.

    Prints "URL <step> <address>" when a pass is up and "END {json}" with label.py's output when it
    ends: surfaces, boxes and labels on s1 (each pass type once more for the "!" key), then the
    re-mark of s2, whose first pass is made here."""
    takes = Path(folder) / "takes"
    takes.mkdir(parents=True)
    staged_take(takes, "s1", "hard-a", BASE_UNIX, typed="none", activity=30.0)
    staged_take(takes, "s2", "hard-b", BASE_UNIX + 3600, typed="none", activity=30.0)
    argv = ["--takes", str(takes), "--no-browser"]
    out = io.StringIO()
    if label.main(["sessions"] + argv[:2], out=out) != 0:
        raise SystemExit(out.getvalue())

    def serve(step, command):
        text = io.StringIO()
        code = label.main(command + argv, out=text, ready=lambda url: print(f"URL {step} {url}", flush=True))
        print("END " + json.dumps({"step": step, "code": code, "text": text.getvalue()}), flush=True)
        if code != 0:
            raise SystemExit(f"{step} pass failed")
    serve("surfaces-person", ["surfaces", "s1"])
    serve("boxes", ["boxes", "s1"])
    serve("boxes-person", ["boxes", "s1"])
    serve("labels", ["labels", "s1"])
    serve("labels-person", ["labels", "s1"])
    ws = n1.Workspace(takes, takes / "n1")
    with mock.patch.object(n1, "now", return_value=BASE_UNIX + DAY):
        p = label.Pass(ws, "boxes", "s2", out=io.StringIO())
        p.save({"keyframes": [[p.usable[0]["n"], [100, 50, 300, 340]]],
                "audited": [[p.usable[0]["n"], p.usable[-1]["n"]]]})
        label.Pass(ws, "labels", "s2", out=io.StringIO()).save({"intervals": [], "spans": [], "end_to_end": True})
    with mock.patch.object(n1, "now", return_value=BASE_UNIX + 5 * DAY):
        serve("remark-person", ["remark"])
    state = ws.state("s1")
    frames = n1.read_frames(state.folder)
    usable = n1.usable_frames(state, frames)
    boxes = ws.load_draft(ws.draft_path("boxes", "s1"))
    tight = n1.tight_boxes(boxes["keyframes"], usable, {f["n"]: f["t"] for f in frames})
    labels = ws.load_draft(ws.draft_path("labels", "s1"))
    print("RESULT " + json.dumps({"keyframes": boxes["keyframes"], "audited_all": n1.audited_all(boxes["audited"],
                                                                                                usable),
                                  "tight": {str(f["n"]): b for f, b in zip(usable, tight)},
                                  "intervals": labels["intervals"], "spans": labels["spans"],
                                  "end_to_end": labels["actor"]["end_to_end"] is not None,
                                  "remark_started": ws.draft_path("remark", "s2").exists()}), flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--browser-fixture"]:
        setUpModule()
        try:
            browser_fixture(sys.argv[2])
        finally:
            tearDownModule()
    else:
        unittest.main()
