"""The den's labels (room_labels.py) and their two routes on the stream server (stream.py). Standard library only.

    python -B -m unittest test_room_labels      (from tools\\den-live)
"""
import copy
import http.client
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import room_labels as rl  # noqa: E402
import stream as people_stream  # noqa: E402


def label(name="desk", kind="table", centre=(1.0, 2.0, 0.375), size=(1.4, 0.7, 0.75), yaw=0.0, **over):
    slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    return {"id": over.pop("id", "l-%s-1" % slug), "name": name, "kind": kind,
            "box": {"centre_m": list(centre), "size_m": list(size), "yaw_deg": yaw},
            "on_wall": over.pop("on_wall", False), "made_by": "owner", "status": over.pop("status", "confirmed"),
            "updated_at": None, **over}


def doc(labels=None, revision=0, content_revision="rev-a"):
    return {**rl.empty(content_revision), "revision": revision, "labels": [label()] if labels is None else labels}


# A small den: a desk along x = 0.05..0.75 (front +x), its chair, a 3D printer behind the desk's end, a bookshelf, a
# sofa against the far wall, a painting above it and a reading corner by the window.
DEN = [
    label("desk", "table", (0.4, 1.4, 0.375), (1.4, 0.7, 0.75), 270),
    label("desk chair", "seat", (1.15, 1.4, 0.5), (0.55, 0.6, 1.0), 270),
    label("3D printer", "machine", (0.3, 2.65, 0.85), (0.46, 0.46, 0.5), 270),
    label("bookshelf", "storage", (0.75, 3.8, 0.9), (0.9, 0.35, 1.8), 180),
    label("sofa", "seat", (2.6, 3.45, 0.425), (2.0, 0.9, 0.85), 180),
    label("blue painting", "picture", (2.6, 3.955, 1.55), (1.0, 0.03, 0.7), 180, on_wall=True),
    label("window", "opening", (4.25, 3.975, 1.45), (1.2, 0.05, 1.3), 180, on_wall=True),
    label("reading corner", "area", (4.2, 2.5, 0.01), (1.4, 1.4, 0.02), 0),
]


class Validate(unittest.TestCase):
    def test_a_good_document_passes_and_yaw_is_normalised(self):
        good, reason = rl.validate(doc([label(yaw=-90)]))
        self.assertIsNone(reason)
        self.assertEqual(good["labels"][0]["box"]["yaw_deg"], 270.0)
        self.assertEqual(rl.validate(rl.empty())[1], None)

    def test_each_broken_field_is_refused_in_the_same_words_as_home(self):
        cases = [
            ({**doc(), "extra": 1}, "key_unknown"),
            ({**doc(), "schema": "x"}, "schema_wrong"),
            ({**doc(), "room_id": "kitchen"}, "scope_wrong"),
            ({**doc(), "content_revision": "r" * 129}, "field_invalid"),
            ({**doc(), "revision": -1}, "revision_invalid"),
            ({**doc(), "revision": True}, "revision_invalid"),
            ({**doc(), "labels": {}}, "labels_invalid"),
            (doc([label(name="x %d" % i, id="l-x-%d" % i) for i in range(101)]), "too_many_labels"),
            (doc([{**label(), "colour": "red"}]), "key_unknown"),
            (doc([label(id="L-Desk")]), "id_invalid"),
            (doc([label(), label(name="other desk", id="l-desk-1")]), "id_duplicate"),
            (doc([label(name="x" * 41)]), "name_too_long"),
            (doc([label(name="desk!")]), "name_invalid"),
            (doc([label(name="desk ")]), "name_invalid"),
            (doc([label(), label(name="DESK", id="l-desk-2")]), "name_duplicate"),
            (doc([label(kind="robot")]), "kind_unknown"),
            (doc([label(size=(0, 1, 1))]), "box_invalid"),
            (doc([label(centre=(0, 0, float("nan")))]), "box_invalid"),
            (doc([{**label(), "on_wall": 1}]), "field_invalid"),
            (doc([label(status="maybe")]), "field_invalid"),
        ]
        for value, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(rl.validate(value), (None, reason))

    def test_a_document_over_64_kb_is_too_large(self):
        long_revision = doc([label(name="item %d" % i, id="l-item-%d" % i, updated_at="2026-11-12T20:00:00.000000+00:00") for i in range(100)])
        self.assertLess(len(json.dumps(long_revision)), rl.MAX_BYTES)   # 100 ordinary labels fit
        bloated = copy.deepcopy(long_revision)
        for item in bloated["labels"]:
            item["box"]["centre_m"] = [1.0000000000001, 2.0000000000001, 0.3750000000001]
            item["box"]["size_m"] = [1.4000000000001, 0.7000000000001, 0.7500000000001]
        self.assertIn(rl.validate(bloated)[1], (None, "too_large"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="den-labels-"))
        self.said = []
        self.store = rl.Store(self.folder, now=lambda: 1794500000.0, say=self.said.append)

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_a_missing_file_is_no_labels(self):
        self.assertEqual(self.store.get(), rl.empty())

    def test_a_save_bumps_the_revision_and_a_stale_one_gets_the_current_document(self):
        status, saved = self.store.put(doc())
        self.assertEqual((status, saved["revision"], saved["saved_at"]), (200, 1, "2026-11-12T16:13:20Z"))
        self.assertFalse((self.folder / "room-labels.json.new").exists())
        status, current = self.store.put(doc(revision=0))
        self.assertEqual((status, current["revision"]), (409, 1))
        self.assertEqual(rl.Store(self.folder).get(), saved)   # a fresh store reads what was saved

    def test_an_invalid_save_is_refused_and_changes_nothing(self):
        self.assertEqual(self.store.put({**doc(), "schema": "x"}), (400, {"ok": False, "error": "schema_wrong"}))
        self.assertEqual(self.store.get()["revision"], 0)

    def test_the_history_keeps_the_last_100_saves(self):
        for revision in range(103):
            self.assertEqual(self.store.put(doc(revision=revision))[0], 200)
        lines = (self.folder / "room-labels-history.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 100)
        self.assertEqual(json.loads(lines[-1])["revision"], 103)

    def test_a_bad_file_is_set_aside_and_it_starts_empty(self):
        (self.folder / "room-labels.json").write_text("{not json", encoding="utf-8")
        store = rl.Store(self.folder, now=lambda: 1794500000.0, say=self.said.append)
        self.assertEqual(store.get(), rl.empty())
        self.assertTrue((self.folder / "room-labels.bad-20261112T161320.json").exists())
        self.assertIn("set aside", self.said[-1])

    def test_confirmed_leaves_out_suggestions(self):
        self.store.put(doc([label(), label("lamp", "lamp", id="l-lamp-1", status="suggested")]))
        self.assertEqual([l["name"] for l in self.store.confirmed()], ["desk"])


class Line(unittest.TestCase):
    def test_the_phrase_from_where_a_person_is(self):
        cases = [
            ((1.15, 1.4), "on the desk chair, at the desk"),
            ((0.95, 2.05), "at the desk, beside the desk chair and the 3D printer"),
            ((2.6, 3.3), "on the sofa, beside the blue painting"),
            ((4.2, 2.5), "in the reading corner, beside the sofa"),
            ((2.5, 1.0), None),
        ]
        for xy, expected in cases:
            with self.subTest(xy=xy):
                self.assertEqual(rl.near_phrase(DEN, xy), expected)

    def test_suggested_labels_and_openings_are_never_named(self):
        den = [label("desk", "table", (0.4, 1.4, 0.375), (1.4, 0.7, 0.75), 270, status="suggested"),
               label("door", "opening", (1.2, 1.0, 1.0), (0.9, 0.05, 2.0), 0)]
        self.assertIsNone(rl.near_phrase(den, (1.15, 1.4)))

    def test_the_article_rule_and_the_160_character_cut(self):
        mine = [label("My workbench", "table", (0, 0, 0.45), (1.2, 0.6, 0.9)), label("a plant", "other", (0, 0.9, 0.45), (0.4, 0.4, 0.9), id="l-plant-1")]
        self.assertEqual(rl.near_phrase(mine, (0, 0.5)), "at My workbench, beside a plant")
        long_names = [label("x" * 40, "table", (0, 0, 0.4), id="l-a-1"), label("y" * 40, "machine", (0, 0.8, 0.4), id="l-b-1"),
                      label("z" * 40, "storage", (0.8, 0, 0.4), id="l-c-1"), label("w" * 40, "seat", (0, 0, 0.4), (2, 2, 0.8), id="l-d-1")]
        phrase = rl.near_phrase(long_names, (0, 0))
        self.assertLessEqual(len(phrase), 160)
        self.assertFalse(phrase.endswith((",", " ")))

    def test_line_for_the_ask(self):
        den = doc(DEN)
        self.assertEqual(rl.line_for(den, {"xy_m": [0.95, 2.05]}, scan_revision="rev-a"),
                         "Seated at the desk, beside the desk chair and the 3D printer.")
        self.assertIsNone(rl.line_for(den, {"xy_m": [0.95, 2.05]}, scan_revision="rev-b"))   # placed on another scan
        self.assertIsNone(rl.line_for(den, {"xy_m": [2.5, 1.0]}))                            # nothing near
        self.assertIsNone(rl.line_for(den, {"xy_m": [float("nan"), 1.0]}))
        self.assertIsNone(rl.line_for(None, {"xy_m": [1, 1]}))
        self.assertIsNone(rl.line_for({**den, "schema": "x"}, {"xy_m": [0.95, 2.05]}))

    def test_footprint_and_front(self):
        desk = DEN[0]
        fx, fy = rl.front_unit(desk)
        self.assertAlmostEqual(fx, 1.0)
        self.assertAlmostEqual(fy, 0.0, places=9)
        xs = sorted(round(x, 3) for x, _y in rl.footprint(desk))
        self.assertEqual((xs[0], xs[-1]), (0.05, 0.75))
        self.assertEqual(rl.foot_distance(desk, (0.4, 1.4)), 0.0)
        self.assertAlmostEqual(rl.foot_distance(desk, (1.15, 1.4)), 0.4)


class Routes(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="den-labels-routes-"))
        self.stream = people_stream.PeopleStream(port=0, labels=rl.Store(self.folder, say=lambda *_: None)).start()
        self.path = people_stream.BASE + "den/labels"

    def tearDown(self):
        self.stream.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    def call(self, method, body=None, headers=None, path=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.stream.port, timeout=5)
        try:
            data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode("utf-8")
            sent = {"Host": "127.0.0.1:%d" % self.stream.port, **({"Content-Type": "application/json"} if data is not None else {}), **(headers or {})}
            connection.request(method, path or self.path, body=data, headers=sent)
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"null")
        finally:
            connection.close()

    def test_get_then_save_then_a_stale_save(self):
        self.assertEqual(self.call("GET"), (200, rl.empty()))
        status, saved = self.call("PUT", doc())
        self.assertEqual((status, saved["revision"]), (200, 1))
        self.assertEqual(self.call("GET")[1], saved)
        status, current = self.call("PUT", doc(revision=0))
        self.assertEqual((status, current["revision"]), (409, 1))

    def test_refusals(self):
        self.assertEqual(self.call("PUT", {**doc(), "schema": "x"}), (400, {"ok": False, "error": "schema_wrong"}))
        self.assertEqual(self.call("PUT", b"{not json"), (400, {"ok": False, "error": "json_invalid"}))
        self.assertEqual(self.call("PUT", doc(), headers={"Content-Type": "text/plain"}), (415, {"ok": False, "error": "content_type"}))
        self.assertEqual(self.call("PUT", doc(), path=self.path + "?x=1"), (400, {"ok": False, "error": "query"}))
        self.assertEqual(self.call("PUT", doc(), path=people_stream.BASE + "den/state"), (405, {"ok": False, "error": "method"}))
        self.assertEqual(self.call("PUT", doc(), headers={"Host": "elsewhere:1"}), (403, {"ok": False, "error": "host"}))

    def headers_only(self, length=None):
        """A save's headers with no body sent: the length checks answer before any body is read."""
        connection = http.client.HTTPConnection("127.0.0.1", self.stream.port, timeout=5)
        try:
            connection.putrequest("PUT", self.path, skip_host=True)
            connection.putheader("Host", "127.0.0.1:%d" % self.stream.port)
            connection.putheader("Content-Type", "application/json")
            if length is not None:
                connection.putheader("Content-Length", str(length))
            connection.endheaders()
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_a_save_with_no_length_or_over_64_kb_is_refused_unread(self):
        self.assertEqual(self.headers_only(), (411, {"ok": False, "error": "length_required"}))
        self.assertEqual(self.headers_only(65537), (413, {"ok": False, "error": "too_large"}))

    def test_no_store_answers_503(self):
        bare = people_stream.PeopleStream(port=0).start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", bare.port, timeout=5)
            connection.request("GET", self.path, headers={"Host": "127.0.0.1:%d" % bare.port})
            response = connection.getresponse()
            self.assertEqual((response.status, json.loads(response.read())), (503, {"ok": False, "error": "labels_unavailable"}))
            connection.close()
        finally:
            bare.close()


if __name__ == "__main__":
    unittest.main()
