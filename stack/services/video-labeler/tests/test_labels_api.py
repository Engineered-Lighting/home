"""Labels API: ontology shape, PUT/GET round trip, revision 409 + snapshot,
temp-id mapping, per-axis non-overlap, quality flag arrays, supersede
history, review transitions, and the CRITICAL concurrency rule: VLM
suggestion writes never bump the revision and never collide with a human
save. Needs httpx (starlette TestClient); sqlite-only otherwise."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from videolabeler import db, labels  # noqa: E402
from videolabeler.api import labels as labels_api  # noqa: E402

VID = "vid_lab"
URL = f"/api/video-labeler/videos/{VID}/labels"


def _review_url(seg_id):
    return f"/api/video-labeler/videos/{VID}/segments/{seg_id}/review"


def _seg(start, end, value, **extra):
    return {"start_s": start, "end_s": end, "value": value, **extra}


@pytest.fixture
def client(conn, cfg):
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO videos (id, filename, checksum, duration_s,"
            " import_status, created_at, updated_at)"
            " VALUES (?, 'lab.mp4', 'sum_lab', 30.0, 'ready', ?, ?)",
            (VID, now, now))
        conn.execute("INSERT INTO video_label_state (video_id, revision,"
                     " updated_at) VALUES (?, 0, ?)", (VID, now))
    app = FastAPI()
    app.include_router(labels_api.build_router(cfg))
    return TestClient(app)


def test_ontology_shape(client):
    doc = client.get("/api/video-labeler/labels/ontology").json()
    act = doc["axes"]["activity_primary"]
    assert len(act["values"]) == 23
    assert set(act["active"]) <= set(act["values"])
    # "other" is the M2 VLM-enum extra, NOT a canonical/active value
    assert "other" not in act["values"]
    assert "no_person" in act["active"] and "unknown" in act["active"]
    assert len(doc["axes"]["posture"]["values"]) == 14
    assert len(doc["axes"]["quality_flags"]["values"]) == 10
    assert doc["aux_axes"] == ["verb", "object_noun", "room_zone",
                               "attention_target", "motion_intensity",
                               "activity_phase", "notes"]
    assert len(doc["review_states"]) == 6
    assert doc["custom"] == []


def test_put_get_round_trip(client):
    r = client.put(URL, json={"revision": 0, "axes": {
        "activity_primary": [  # deliberately out of order: GET must sort
            _seg(10, 20, "eating_drinking", id="tmp_b"),
            _seg(0, 10, "cooking", id="tmp_a",
                 aux={"room_zone": "kitchen", "motion_intensity": "medium"}),
        ],
        "posture": [_seg(0, 20, "standing", id="tmp_c")],
        "quality": [_seg(0, 5, ["dark", "blurry"], id="tmp_d")],
    }})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == 1
    assert set(body["id_map"]) == {"tmp_a", "tmp_b", "tmp_c", "tmp_d"}

    doc = client.get(URL).json()
    assert doc["revision"] == 1
    acts = doc["axes"]["activity_primary"]
    assert [s["value"] for s in acts] == ["cooking", "eating_drinking"]  # sorted
    assert acts[0]["id"] == body["id_map"]["tmp_a"]
    assert acts[0]["start_s"] == 0 and acts[0]["end_s"] == 10
    assert acts[0]["aux"] == {"room_zone": "kitchen", "motion_intensity": "medium"}
    assert acts[0]["source"] == "human"
    assert acts[0]["review_state"] == "needs_review"
    assert acts[1]["aux"] is None
    # quality value is a STRING ARRAY; the aux key exists on activity only
    qual = doc["axes"]["quality"]
    assert qual[0]["value"] == ["dark", "blurry"]
    assert "aux" not in qual[0]
    assert doc["axes"]["posture"][0]["value"] == "standing"
    assert doc["axes"]["custom"] == []


def test_temp_ids_get_server_ids(client):
    r = client.put(URL, json={"revision": 0, "axes": {"activity_primary": [
        _seg(0, 5, "walking", id="tmp_1"), _seg(5, 10, "cooking", id="tmp_2")]}})
    id_map = r.json()["id_map"]
    assert set(id_map) == {"tmp_1", "tmp_2"}
    for sid in id_map.values():
        assert re.fullmatch(r"seg_[0-9a-f]{12}", sid)


def test_stale_revision_409_with_snapshot(client):
    assert client.put(URL, json={"revision": 0, "axes": {
        "posture": [_seg(0, 5, "standing", id="tmp_a")]}}).status_code == 200
    r = client.put(URL, json={"revision": 0, "axes": {
        "posture": [_seg(0, 5, "walking", id="tmp_b")]}})
    assert r.status_code == 409
    snap = r.json()  # full server snapshot so the client can rebase
    assert snap["revision"] == 1
    assert [s["value"] for s in snap["axes"]["posture"]] == ["standing"]
    # the refused save wrote nothing
    doc = client.get(URL).json()
    assert doc["revision"] == 1
    assert [s["value"] for s in doc["axes"]["posture"]] == ["standing"]


def test_overlap_rejected_400(client):
    r = client.put(URL, json={"revision": 0, "axes": {"activity_primary": [
        _seg(0, 10, "walking", id="tmp_1"), _seg(9.5, 15, "cooking", id="tmp_2")]}})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "label validation failed"
    assert any("overlap" in d["error"] for d in body["details"])
    assert client.get(URL).json()["axes"]["activity_primary"] == []  # atomic


def test_touching_segments_and_cross_axis_overlap_allowed(client):
    r = client.put(URL, json={"revision": 0, "axes": {
        "activity_primary": [_seg(0, 10, "walking"), _seg(10, 20, "cooking")],
        "posture": [_seg(0, 20, "standing")],  # overlaps activity: fine, own lane
    }})
    assert r.status_code == 200, r.text


def test_validation_rejections(client):
    cases = [
        ({"activity_primary": [_seg(5, 5, "walking")]}, "start_s must be < end_s"),
        ({"activity_primary": [_seg(0, 0.1, "walking")]}, "shorter"),
        ({"activity_primary": [_seg(-1, 5, "walking")]}, ">= 0"),
        ({"activity_primary": [_seg(0, 5, "flying")]}, "not a canonical"),
        ({"activity_primary": [_seg(0, 5, "walking", review_state="bogus")]},
         "review_state"),
        ({"activity_primary": [_seg(0, 5, "walking", review_state="prelabel")]},
         "reserved"),
        ({"posture": [_seg(0, 5, "standing", aux={"room_zone": "kitchen"})]},
         "activity segments only"),
        ({"activity_primary": [_seg(0, 5, "walking", aux={"bogus_key": "x"})]},
         "unknown aux"),
        ({"custom": [_seg(0, 5, "custom:nope")]}, "unknown custom label"),
        ({"custom": [_seg(0, 5, "walking")]}, "custom:<slug>"),
        ({"bogus_axis": [_seg(0, 5, "walking")]}, "unknown axis"),
    ]
    for axes, needle in cases:
        r = client.put(URL, json={"revision": 0, "axes": axes})
        assert r.status_code == 400, (axes, r.text)
        assert any(needle in d["error"] for d in r.json()["details"]), \
            (axes, r.json())


def test_quality_value_must_be_flag_array(client):
    for bad in ["dark", [], ["nope"], ["dark", "dark"]]:
        r = client.put(URL, json={"revision": 0,
                                  "axes": {"quality": [_seg(0, 5, bad)]}})
        assert r.status_code == 400, bad
    r = client.put(URL, json={"revision": 0, "axes": {
        "quality": [_seg(0, 5, ["clear", "dark"])]}})
    assert r.status_code == 200


def test_supersede_preserves_history(client, conn):
    r = client.put(URL, json={"revision": 0, "axes": {
        "activity_primary": [_seg(0, 10, "walking", id="tmp_a")]}})
    old_id = r.json()["id_map"]["tmp_a"]
    r2 = client.put(URL, json={"revision": 1, "axes": {
        "activity_primary": [_seg(0, 12, "walking", id=old_id)]}})
    assert r2.status_code == 200
    new_id = r2.json()["id_map"][old_id]  # changed rows are re-id'd + mapped
    assert new_id != old_id

    old = conn.execute("SELECT * FROM segments WHERE id = ?", (old_id,)).fetchone()
    new = conn.execute("SELECT * FROM segments WHERE id = ?", (new_id,)).fetchone()
    assert old["is_current"] == 0 and old["end_s"] == 10.0  # history preserved
    assert new["is_current"] == 1 and new["end_s"] == 12.0
    assert new["superseded_by"] == old_id  # replacement points AT the old row

    doc = client.get(URL).json()
    assert [s["id"] for s in doc["axes"]["activity_primary"]] == [new_id]


def test_unchanged_echo_is_not_superseded(client, conn):
    r = client.put(URL, json={"revision": 0, "axes": {
        "activity_primary": [_seg(0, 10, "walking", id="tmp_a")]}})
    assert r.status_code == 200
    doc = client.get(URL).json()
    r2 = client.put(URL, json={"revision": 1, "axes": {
        "activity_primary": doc["axes"]["activity_primary"]}})
    assert r2.status_code == 200
    assert r2.json()["id_map"] == {}  # echoing the doc back rewrites nothing
    assert conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 1


def test_omitted_segment_is_retired(client, conn):
    r = client.put(URL, json={"revision": 0, "axes": {"activity_primary": [
        _seg(0, 10, "walking", id="tmp_a"), _seg(10, 20, "cooking", id="tmp_b")]}})
    keep, gone = r.json()["id_map"]["tmp_a"], r.json()["id_map"]["tmp_b"]
    doc = client.get(URL).json()
    kept = [s for s in doc["axes"]["activity_primary"] if s["id"] == keep]
    r2 = client.put(URL, json={"revision": 1, "axes": {"activity_primary": kept}})
    assert r2.status_code == 200
    row = conn.execute("SELECT * FROM segments WHERE id = ?", (gone,)).fetchone()
    assert row["is_current"] == 0
    assert row["superseded_by"] is None  # delete, not replace
    assert [s["id"] for s in
            client.get(URL).json()["axes"]["activity_primary"]] == [keep]


def test_axis_subset_put_leaves_other_axes_alone(client):
    client.put(URL, json={"revision": 0, "axes": {
        "activity_primary": [_seg(0, 10, "walking", id="tmp_a")]}})
    r = client.put(URL, json={"revision": 1, "axes": {
        "posture": [_seg(0, 10, "standing", id="tmp_p")]}})
    assert r.status_code == 200
    doc = client.get(URL).json()
    assert [s["value"] for s in doc["axes"]["activity_primary"]] == ["walking"]
    assert [s["value"] for s in doc["axes"]["posture"]] == ["standing"]


def test_review_transitions(client, conn):
    r = client.put(URL, json={"revision": 0, "axes": {
        "activity_primary": [_seg(0, 10, "walking", id="tmp_a")]}})
    sid = r.json()["id_map"]["tmp_a"]
    flow = [("accept", "accepted"), ("reject", "rejected"),
            ("needs_review", "needs_review"),
            ("exclude", "excluded_from_export"), ("reopen", "needs_review")]
    for i, (action, expected) in enumerate(flow):
        rr = client.post(_review_url(sid), json={"action": action})
        assert rr.status_code == 200, (action, rr.text)
        body = rr.json()
        assert body["segment"]["review_state"] == expected
        assert body["revision"] == 2 + i  # every human review bumps
    row = conn.execute("SELECT * FROM segments WHERE id = ?", (sid,)).fetchone()
    assert row["reviewed_by"] == "human" and row["reviewed_at"] is not None
    assert client.post(_review_url(sid),
                       json={"action": "bogus"}).status_code == 400
    assert client.post(_review_url("seg_nope"),
                       json={"action": "accept"}).status_code == 404


def test_vlm_suggestions_never_bump_revision_or_conflict(client, conn):
    # human edit in flight: the client loaded the doc at revision 1
    client.put(URL, json={"revision": 0, "axes": {
        "activity_primary": [_seg(0, 10, "walking", id="tmp_a")]}})

    # background prelabel batch lands (overlapping window geometry is fine)
    ids = labels.insert_suggestion_segments(conn, VID, "activity_primary", [
        {"start_s": 0.0, "end_s": 8.0, "value": "cooking", "confidence": 0.7},
        {"start_s": 4.0, "end_s": 12.0, "value": "cooking", "confidence": 0.6},
    ])
    assert len(ids) == 2
    assert labels.get_revision(conn, VID) == 1  # NOT bumped
    doc = client.get(URL).json()
    assert doc["revision"] == 1
    assert len(doc["axes"]["activity_primary"]) == 1  # suggestions invisible

    # the human save with the pre-suggestion revision must NOT 409 ...
    r = client.put(URL, json={"revision": 1, "axes": {
        "activity_primary": [_seg(0, 11, "walking")]}})
    assert r.status_code == 200
    assert r.json()["revision"] == 2

    # ... and must not have touched the suggestion layer
    sugg = conn.execute("SELECT * FROM segments WHERE review_state = 'prelabel'"
                        ).fetchall()
    assert len(sugg) == 2
    assert all(s["is_current"] == 1 and s["source"] == "vlm" for s in sugg)


def test_review_accept_graduates_a_suggestion(client, conn):
    ids = labels.insert_suggestion_segments(conn, VID, "activity_primary", [
        {"start_s": 0.0, "end_s": 8.0, "value": "cooking", "confidence": 0.9}])
    assert client.get(URL).json()["axes"]["activity_primary"] == []
    r = client.post(_review_url(ids[0]), json={"action": "accept"})
    assert r.status_code == 200
    assert r.json()["revision"] == 1  # the human accept DOES bump
    doc = client.get(URL).json()
    segs = doc["axes"]["activity_primary"]
    assert [s["id"] for s in segs] == [ids[0]]
    assert segs[0]["review_state"] == "accepted"
    assert segs[0]["source"] == "vlm"  # provenance kept after graduation


def test_unknown_video_404(client):
    assert client.get(
        "/api/video-labeler/videos/vid_nope/labels").status_code == 404
    assert client.put("/api/video-labeler/videos/vid_nope/labels",
                      json={"revision": 0, "axes": {}}).status_code == 404

